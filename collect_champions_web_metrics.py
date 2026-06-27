#!/usr/bin/env python3
"""Fetch Pokemon Champions metrics from the web into chart-friendly CSV datasets.

The current implementation targets the public Pikalytics format page for
Pokemon Champions and writes separate CSV files for:

- source usage ranks
- source common cores
- raw recent top teams
- per-team per-Pokemon set details from source teamlists
- per-Pokemon top-team performance
- aggregated pair/trio/full-team performance

Each run appends or updates a dated snapshot in each dataset, making the output
easier to chart over time than a single mixed metrics table.
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

import requests
from bs4 import BeautifulSoup


SCRIPT_DIR = Path(__file__).parent
WEB_METRICS_DIR = SCRIPT_DIR / "results" / "web_metrics"
DEFAULT_SOURCE = "pikalytics"
DEFAULT_URL = "https://pikalytics.com/pokedex/gen9championsvgc2026regma"
TOP_TEAMS_URL = "https://pikalytics.com/topteams"
TOP_TEAMS_LIMIT = 20
DEFAULT_OUTPUT = WEB_METRICS_DIR / "champions_web_metrics.csv"
USER_AGENT = "Mozilla/5.0 (compatible; ChampionsUsageExtractor/1.0)"
REQUEST_TIMEOUT = 30

COMMON_FIELDS = [
    "snapshot_date",
    "snapshot_at_utc",
    "source",
    "format_slug",
    "format_name",
    "source_url",
]

POKEMON_USAGE_FIELDS = COMMON_FIELDS + [
    "pokemon_rank",
    "pokemon_name",
    "usage_pct",
]

POKEMON_TEAM_METRICS_FIELDS = COMMON_FIELDS + [
    "pokemon_name",
    "usage_rank",
    "usage_pct",
    "sample_teams",
    "team_appearances",
    "team_appearance_pct",
    "wins",
    "losses",
    "draws",
    "matches_played",
    "win_rate",
    "avg_team_rank",
    "best_team_rank",
    "unique_authors",
    "unique_events",
    "top_teammates",
    "top_teammate_appearances",
    "top_teammate_win_rates",
]

TEAM_CORES_FIELDS = COMMON_FIELDS + [
    "core_rank",
    "core_size",
    "core_label",
    "pokemon_names",
    "usage_pct",
    "team_count",
]

TOP_TEAMS_FIELDS = COMMON_FIELDS + [
    "team_rank",
    "team_size",
    "team_label",
    "author",
    "record",
    "wins",
    "losses",
    "draws",
    "matches_played",
    "win_rate",
    "event_name",
    "event_date",
    "event_rank",
    "won_event",
    "event_player_count",
    "meta_text",
    "team_source_url",
    "event_standings_url",
    "pokemon_names",
    "pokemon_1",
    "pokemon_2",
    "pokemon_3",
    "pokemon_4",
    "pokemon_5",
    "pokemon_6",
    "pokemon_1_item",
    "pokemon_2_item",
    "pokemon_3_item",
    "pokemon_4_item",
    "pokemon_5_item",
    "pokemon_6_item",
    "pokemon_1_ability",
    "pokemon_2_ability",
    "pokemon_3_ability",
    "pokemon_4_ability",
    "pokemon_5_ability",
    "pokemon_6_ability",
    "pokemon_1_moves",
    "pokemon_2_moves",
    "pokemon_3_moves",
    "pokemon_4_moves",
    "pokemon_5_moves",
    "pokemon_6_moves",
]

TEAM_POKEMON_DETAILS_FIELDS = COMMON_FIELDS + [
    "team_rank",
    "team_size",
    "team_label",
    "author",
    "record",
    "wins",
    "losses",
    "draws",
    "matches_played",
    "win_rate",
    "event_name",
    "event_date",
    "event_rank",
    "won_event",
    "event_player_count",
    "team_source_url",
    "event_standings_url",
    "team_pokemon_names",
    "slot_index",
    "pokemon_name",
    "source_pokemon_name",
    "item",
    "ability",
    "moves",
    "move_1",
    "move_2",
    "move_3",
    "move_4",
    "teammates",
]

TEAM_COMBINATION_FIELDS = COMMON_FIELDS + [
    "combination_size",
    "combination_type",
    "combination_rank",
    "combination_label",
    "pokemon_names",
    "sample_teams",
    "team_appearances",
    "team_appearance_pct",
    "wins",
    "losses",
    "draws",
    "matches_played",
    "win_rate",
    "avg_team_rank",
    "best_team_rank",
    "unique_authors",
    "unique_events",
]

DATASET_FIELDS = {
    "pokemon_usage": POKEMON_USAGE_FIELDS,
    "pokemon_team_metrics": POKEMON_TEAM_METRICS_FIELDS,
    "team_cores": TEAM_CORES_FIELDS,
    "top_teams": TOP_TEAMS_FIELDS,
    "team_pokemon_details": TEAM_POKEMON_DETAILS_FIELDS,
    "team_combinations": TEAM_COMBINATION_FIELDS,
}

DATASET_ORDER = [
    "pokemon_usage",
    "pokemon_team_metrics",
    "team_cores",
    "top_teams",
    "team_pokemon_details",
    "team_combinations",
]

COMBINATION_TYPE_LABELS = {
    1: "pokemon",
    2: "pairing",
    3: "trio",
    4: "quartet",
    5: "quintet",
    6: "full_team",
}


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        level=level,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch Pokemon Champions usage and team metrics from the web.",
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help=f"Source URL to fetch (default: {DEFAULT_URL})",
    )
    parser.add_argument(
        "--source",
        default=DEFAULT_SOURCE,
        help=f"Logical source name to record in the CSVs (default: {DEFAULT_SOURCE})",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=(
            "Base output path. If this is 'champions_web_metrics.csv', the script writes "
            "champions_web_metrics_pokemon_usage.csv, champions_web_metrics_top_teams.csv, etc. "
            "Defaults to results/web_metrics/champions_web_metrics.csv."
        ),
    )
    parser.add_argument(
        "--snapshot-date",
        help="Override snapshot date written to the CSVs (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable debug logging",
    )
    return parser.parse_args()


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def parse_percent(text: str) -> str:
    match = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
    return match.group(1) if match else ""


def parse_int(text: str) -> str:
    match = re.search(r"(\d+)", text)
    return match.group(1) if match else ""


def safe_int(value, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def safe_float(value, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def format_decimal(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}"


def fetch_html(url: str) -> str:
    response = requests.get(
        url,
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    return response.text


def extract_format_slug(url: str) -> str:
    match = re.search(r"/pokedex/([^/?#]+)", url)
    return match.group(1) if match else ""


def extract_pokemon_name_from_href(href: str) -> str:
    match = re.search(r"/pokedex/[^/]+/([^/?#]+)", href or "")
    return match.group(1) if match else ""


def extract_event_rank(meta_text: str) -> str:
    match = re.search(r"Rank\s+#?(\d+)\s*$", meta_text or "", re.IGNORECASE)
    return match.group(1) if match else ""


def extract_event_name(meta_text: str) -> str:
    return normalize_whitespace(re.sub(r"\s*Rank\s+#?\d+\s*$", "", meta_text or "", flags=re.IGNORECASE))


def parse_record(record_text: str) -> dict:
    parts = [int(value) for value in re.findall(r"\d+", record_text or "")]
    if len(parts) < 2:
        return {
            "wins": "",
            "losses": "",
            "draws": "",
            "matches_played": "",
            "win_rate": "",
        }

    wins = parts[0]
    losses = parts[1]
    draws = parts[2] if len(parts) >= 3 else 0
    matches_played = wins + losses + draws
    win_rate = format_decimal((wins / matches_played) * 100.0) if matches_played else ""
    return {
        "wins": str(wins),
        "losses": str(losses),
        "draws": str(draws),
        "matches_played": str(matches_played),
        "win_rate": win_rate,
    }


def parse_pokemon_names(pokemon_names: str) -> list[str]:
    return [name for name in (pokemon_names or "").split(":") if name]


def normalize_ability_text(text: str) -> str:
    return normalize_whitespace(re.sub(r"^Ability:\s*", "", text or "", flags=re.IGNORECASE))


def build_slot_detail_defaults() -> dict:
    defaults = {}
    for index in range(1, 7):
        defaults[f"pokemon_{index}_item"] = ""
        defaults[f"pokemon_{index}_ability"] = ""
        defaults[f"pokemon_{index}_moves"] = ""
    return defaults


def get_format_name(soup: BeautifulSoup) -> str:
    heading = soup.find("h1")
    if heading:
        return normalize_whitespace(heading.get_text(" ", strip=True))

    title = soup.find("title")
    if title:
        text = normalize_whitespace(title.get_text(" ", strip=True))
        return re.sub(r"\s+Stats\s*\|\s*Pikalytics$", "", text)

    return ""


def build_snapshot_context(args: argparse.Namespace, soup: BeautifulSoup) -> dict:
    now_utc = datetime.now(timezone.utc)
    snapshot_date = args.snapshot_date or now_utc.date().isoformat()
    return {
        "snapshot_date": snapshot_date,
        "snapshot_at_utc": now_utc.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source": args.source,
        "format_slug": extract_format_slug(args.url),
        "format_name": get_format_name(soup),
        "source_url": args.url,
    }


def parse_pokemon_usage(soup: BeautifulSoup, snapshot: dict) -> list[dict]:
    rows = []
    for rank, anchor in enumerate(soup.select("ul#min_list > a.pokedex_entry"), start=1):
        name = normalize_whitespace(anchor.get("data-name", ""))
        if not name:
            name_node = anchor.select_one(".pokemon-name")
            name = normalize_whitespace(name_node.get_text(" ", strip=True) if name_node else "")

        usage_node = anchor.select_one("span.float-right")
        usage_pct = parse_percent(usage_node.get_text(" ", strip=True) if usage_node else "")
        if not name:
            continue

        rows.append({
            **snapshot,
            "pokemon_rank": str(rank),
            "pokemon_name": name,
            "usage_pct": usage_pct,
        })

    return rows


def parse_team_cores(soup: BeautifulSoup, snapshot: dict) -> list[dict]:
    rows = []
    for column in soup.select("div.pokedex-format-core-column"):
        header = column.select_one(".pokedex-format-core-column-header h3")
        header_text = normalize_whitespace(header.get_text(" ", strip=True) if header else "team core")
        core_size = parse_int(header_text)

        for entry in column.select(".pokedex-format-core-entry"):
            rank = parse_int(entry.select_one(".pokedex-format-core-rank").get_text(" ", strip=True))
            name_links = entry.select(".pokedex-format-core-pokemon-link")
            pokemon_names = [normalize_whitespace(link.get_text(" ", strip=True)) for link in name_links]
            if not pokemon_names:
                pokemon_names = [
                    normalize_whitespace(link.get("title", ""))
                    for link in entry.select(".pokedex-format-core-pokemon-sprite[title]")
                ]
                pokemon_names = [name for name in pokemon_names if name]

            meta_values = [
                normalize_whitespace(node.get_text(" ", strip=True))
                for node in entry.select(".pokedex-format-core-meta span")
            ]
            team_count = parse_int(meta_values[0]) if len(meta_values) >= 1 else ""
            usage_pct = parse_percent(meta_values[1]) if len(meta_values) >= 2 else ""

            if not pokemon_names:
                continue

            rows.append({
                **snapshot,
                "core_rank": rank,
                "core_size": core_size,
                "core_label": " / ".join(pokemon_names),
                "pokemon_names": ":".join(pokemon_names),
                "usage_pct": usage_pct,
                "team_count": team_count,
            })

    return rows


def parse_recent_top_teams(
    soup: BeautifulSoup,
    snapshot: dict,
    limit: int | None = None,
) -> list[dict]:
    rows = []
    for entry in soup.select(".aggregated-team-entry.topteams-team-entry"):
        if limit is not None and len(rows) >= limit:
            break

        rank_node = entry.select_one(".topteams-team-rank")
        author_node = entry.select_one(".team-author-info")
        record_node = entry.select_one(".topteams-record-badge")
        event_name_node = entry.select_one(".topteams-event-name")
        event_rank_node = entry.select_one(".topteams-event-placement")
        source_link_node = entry.select_one("a.topteams-source-link[href]")

        pokemon_names = [
            normalize_whitespace(image.get("alt", ""))
            for image in entry.select(".topteams-pokemon-collage-image[alt]")
            if normalize_whitespace(image.get("alt", ""))
        ]

        rank = parse_int(rank_node.get_text(" ", strip=True) if rank_node else "")
        author = normalize_whitespace(author_node.get_text(" ", strip=True) if author_node else "")
        record = normalize_whitespace(record_node.get_text(" ", strip=True) if record_node else "")
        event_name = normalize_whitespace(event_name_node.get_text(" ", strip=True) if event_name_node else "")
        event_rank = parse_int(event_rank_node.get_text(" ", strip=True) if event_rank_node else "")
        record_stats = parse_record(record)
        team_size = str(len(pokemon_names)) if pokemon_names else ""
        won_event = "1" if event_rank == "1" else "0"
        team_source_url = normalize_whitespace(source_link_node.get("href", "") if source_link_node else "")
        meta_text = normalize_whitespace(
            f"{event_name} Rank #{event_rank}" if event_name and event_rank else event_name
        )
        pokemon_slots = {
            f"pokemon_{index}": pokemon_names[index - 1] if index <= len(pokemon_names) else ""
            for index in range(1, 7)
        }

        rows.append({
            **snapshot,
            "team_rank": rank,
            "team_size": team_size,
            "team_label": author or " / ".join(pokemon_names),
            "author": author,
            "record": record,
            **record_stats,
            "event_name": event_name,
            "event_rank": event_rank,
            "won_event": won_event,
            "meta_text": meta_text,
            "team_source_url": team_source_url,
            "pokemon_names": ":".join(pokemon_names),
            **pokemon_slots,
            **build_slot_detail_defaults(),
        })

    return rows


def parse_team_importable_text(teamlist_text: str) -> list[dict]:
    rows = []
    blocks = [block.strip() for block in (teamlist_text or "").split("\n\n") if block.strip()]
    for index, block in enumerate(blocks, start=1):
        lines = [normalize_whitespace(line) for line in block.splitlines() if normalize_whitespace(line)]
        if not lines:
            continue

        name_line = lines[0]
        source_pokemon_name = name_line
        item = ""
        if " @ " in name_line:
            source_pokemon_name, item = [normalize_whitespace(part) for part in name_line.split(" @ ", 1)]

        ability = ""
        moves = []
        for line in lines[1:]:
            if line.lower().startswith("ability:"):
                ability = normalize_ability_text(line)
            elif line.startswith("-"):
                moves.append(normalize_whitespace(line[1:]))
            else:
                moves.append(line)

        rows.append({
            "slot_index": str(index),
            "source_pokemon_name": source_pokemon_name,
            "item": item,
            "ability": ability,
            "moves": ":".join(moves),
            "move_1": moves[0] if len(moves) >= 1 else "",
            "move_2": moves[1] if len(moves) >= 2 else "",
            "move_3": moves[2] if len(moves) >= 3 else "",
            "move_4": moves[3] if len(moves) >= 4 else "",
        })

    return rows


def parse_team_source_details_html(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    rows = []

    for index, entry in enumerate(soup.select(".teamlist .pkmn"), start=1):
        name_node = entry.select_one(".name span")
        item_node = entry.select_one(".details .item")
        ability_node = entry.select_one(".details .ability")
        move_nodes = entry.select(".attacks li")
        moves = [normalize_whitespace(node.get_text(" ", strip=True)) for node in move_nodes if normalize_whitespace(node.get_text(" ", strip=True))]

        rows.append({
            "slot_index": str(index),
            "source_pokemon_name": normalize_whitespace(name_node.get_text(" ", strip=True) if name_node else ""),
            "item": normalize_whitespace(item_node.get_text(" ", strip=True) if item_node else ""),
            "ability": normalize_ability_text(ability_node.get_text(" ", strip=True) if ability_node else ""),
            "moves": ":".join(moves),
            "move_1": moves[0] if len(moves) >= 1 else "",
            "move_2": moves[1] if len(moves) >= 2 else "",
            "move_3": moves[2] if len(moves) >= 3 else "",
            "move_4": moves[3] if len(moves) >= 4 else "",
        })

    if rows:
        return rows

    match = re.search(r"const\s+teamlist\s*=\s*`(.*?)`", html, re.DOTALL)
    if match:
        return parse_team_importable_text(match.group(1))

    return []


def derive_event_base_url(team_source_url: str) -> str:
    match = re.match(r"(https://play\.limitlesstcg\.com/tournament/[^/]+)", team_source_url or "")
    return match.group(1) if match else ""


def derive_event_details_url(team_source_url: str) -> str:
    base_url = derive_event_base_url(team_source_url)
    return f"{base_url}/details" if base_url else ""


def derive_event_standings_url(team_source_url: str) -> str:
    base_url = derive_event_base_url(team_source_url)
    return f"{base_url}/standings" if base_url else ""


def parse_event_date_text(text: str) -> str:
    text = normalize_whitespace(text)
    if not text:
        return ""

    for date_format in ("%A, %B %d, %Y", "%a, %B %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(text, date_format).date().isoformat()
        except ValueError:
            continue

    return ""


def parse_event_details_html(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    for meta_name in ("description", "og:description", "twitter:description"):
        meta_node = soup.find("meta", attrs={"name": meta_name}) or soup.find("meta", attrs={"property": meta_name})
        if not meta_node:
            continue

        meta_text = normalize_whitespace(meta_node.get("content", ""))
        if not meta_text:
            continue

        event_date = parse_event_date_text(meta_text.split(" - ", 1)[0])
        if event_date:
            return {"event_date": event_date}

    for row in soup.select("table tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) < 2:
            continue

        event_date = parse_event_date_text(cells[1].get_text(" ", strip=True))
        if event_date:
            return {"event_date": event_date}

    text = soup.get_text(" ", strip=True)
    match = re.search(
        r"\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+"
        r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+"
        r"\d{1,2},\s+\d{4}\b",
        text,
    )
    return {"event_date": parse_event_date_text(match.group(0)) if match else ""}


def parse_event_standings_html(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    for table in soup.find_all("table"):
        headers = [normalize_whitespace(node.get_text(" ", strip=True)) for node in table.select("tr th")]
        if not headers or "Place" not in headers or "Name" not in headers or "Record" not in headers:
            continue

        place_values = []
        for row in table.select("tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue

            place = safe_int(cells[0].get_text(" ", strip=True), default=0)
            player_name = normalize_whitespace(cells[1].get_text(" ", strip=True))
            if not place or not player_name:
                continue
            place_values.append(place)

        if place_values:
            return {"event_player_count": str(max(place_values))}

    return {"event_player_count": ""}


def fetch_team_source_details(url: str) -> list[dict]:
    if not url:
        return []
    html = fetch_html(url)
    return parse_team_source_details_html(html)


def fetch_event_details(url: str) -> dict:
    if not url:
        return {"event_date": ""}
    html = fetch_html(url)
    return parse_event_details_html(html)


def fetch_event_standings_details(url: str) -> dict:
    if not url:
        return {"event_player_count": ""}
    html = fetch_html(url)
    return parse_event_standings_html(html)


def fetch_event_metadata(team_source_url: str) -> dict:
    standings_url = derive_event_standings_url(team_source_url)
    metadata = {
        "event_date": "",
        "event_player_count": "",
        "event_standings_url": standings_url,
    }

    details_url = derive_event_details_url(team_source_url)
    if details_url:
        metadata.update(fetch_event_details(details_url))
    if standings_url:
        metadata.update(fetch_event_standings_details(standings_url))

    return metadata


def enrich_top_teams_with_source_details(
    top_team_rows: list[dict],
    source_detail_loader=fetch_team_source_details,
    event_metadata_loader=fetch_event_metadata,
) -> tuple[list[dict], list[dict]]:
    logger = logging.getLogger(__name__)
    enriched_rows = []
    team_pokemon_detail_rows = []
    event_detail_cache = {}

    for team_row in top_team_rows:
        enriched_row = dict(team_row)
        source_url = team_row.get("team_source_url", "")
        event_identity = derive_event_base_url(source_url) or source_url
        team_names = [
            team_row.get(f"pokemon_{index}", "")
            for index in range(1, 7)
            if team_row.get(f"pokemon_{index}", "")
        ]

        event_details = {
            "event_date": "",
            "event_player_count": "",
            "event_standings_url": derive_event_standings_url(source_url),
        }
        if event_identity:
            if event_identity not in event_detail_cache:
                try:
                    event_detail_cache[event_identity] = event_metadata_loader(source_url)
                except requests.RequestException as exc:
                    logger.warning(f"Failed to fetch event metadata for {source_url}: {exc}")
                    event_detail_cache[event_identity] = event_details
                except Exception as exc:
                    logger.warning(f"Failed to parse event metadata for {source_url}: {exc}")
                    event_detail_cache[event_identity] = event_details
            event_details = event_detail_cache[event_identity]

        enriched_row["event_date"] = event_details.get("event_date", "")
        enriched_row["event_player_count"] = event_details.get("event_player_count", "")
        enriched_row["event_standings_url"] = event_details.get("event_standings_url", "")

        slot_details = []
        if source_url:
            try:
                slot_details = source_detail_loader(source_url)
            except requests.RequestException as exc:
                logger.warning(f"Failed to fetch team source details for {source_url}: {exc}")
            except Exception as exc:
                logger.warning(f"Failed to parse team source details for {source_url}: {exc}")

        if source_url and not slot_details:
            logger.warning(f"No team source details parsed for {source_url}")

        if slot_details and team_names and len(slot_details) != len(team_names):
            logger.debug(
                "Team source slot count mismatch for %s: source=%s pikalytics=%s",
                source_url,
                len(slot_details),
                len(team_names),
            )

        for slot_detail in slot_details:
            slot_index = safe_int(slot_detail.get("slot_index"), default=0)
            if not slot_index:
                continue

            mapped_name = team_row.get(f"pokemon_{slot_index}", "") or slot_detail.get("source_pokemon_name", "")
            teammates = ":".join(
                name
                for position, name in enumerate(team_names, start=1)
                if position != slot_index and name
            )

            enriched_row[f"pokemon_{slot_index}_item"] = slot_detail.get("item", "")
            enriched_row[f"pokemon_{slot_index}_ability"] = slot_detail.get("ability", "")
            enriched_row[f"pokemon_{slot_index}_moves"] = slot_detail.get("moves", "")

            team_pokemon_detail_rows.append({
                **{field: team_row.get(field, "") for field in COMMON_FIELDS},
                "team_rank": team_row.get("team_rank", ""),
                "team_size": team_row.get("team_size", ""),
                "team_label": team_row.get("team_label", ""),
                "author": team_row.get("author", ""),
                "record": team_row.get("record", ""),
                "wins": team_row.get("wins", ""),
                "losses": team_row.get("losses", ""),
                "draws": team_row.get("draws", ""),
                "matches_played": team_row.get("matches_played", ""),
                "win_rate": team_row.get("win_rate", ""),
                "event_name": team_row.get("event_name", ""),
                "event_date": event_details.get("event_date", ""),
                "event_rank": team_row.get("event_rank", ""),
                "won_event": team_row.get("won_event", ""),
                "event_player_count": event_details.get("event_player_count", ""),
                "team_source_url": source_url,
                "event_standings_url": event_details.get("event_standings_url", ""),
                "team_pokemon_names": team_row.get("pokemon_names", ""),
                "slot_index": str(slot_index),
                "pokemon_name": mapped_name,
                "source_pokemon_name": slot_detail.get("source_pokemon_name", ""),
                "item": slot_detail.get("item", ""),
                "ability": slot_detail.get("ability", ""),
                "moves": slot_detail.get("moves", ""),
                "move_1": slot_detail.get("move_1", ""),
                "move_2": slot_detail.get("move_2", ""),
                "move_3": slot_detail.get("move_3", ""),
                "move_4": slot_detail.get("move_4", ""),
                "teammates": teammates,
            })

        enriched_rows.append(enriched_row)

    return enriched_rows, team_pokemon_detail_rows


def build_team_combination_summaries(top_team_rows: list[dict], snapshot: dict) -> list[dict]:
    sample_teams = len(top_team_rows)
    aggregates = {}

    for team_row in top_team_rows:
        pokemon_names = sorted(set(parse_pokemon_names(team_row.get("pokemon_names", ""))))
        if not pokemon_names:
            continue

        wins = safe_int(team_row.get("wins"))
        losses = safe_int(team_row.get("losses"))
        draws = safe_int(team_row.get("draws"))
        team_rank = safe_int(team_row.get("team_rank"), default=999999)
        author = team_row.get("author", "")
        event_name = team_row.get("event_name", "")

        for size in range(1, len(pokemon_names) + 1):
            for combo in combinations(pokemon_names, size):
                combo_key = ":".join(combo)
                aggregate = aggregates.setdefault(combo_key, {
                    **snapshot,
                    "combination_size": str(size),
                    "combination_type": COMBINATION_TYPE_LABELS.get(size, f"size_{size}"),
                    "combination_rank": "",
                    "combination_label": " / ".join(combo),
                    "pokemon_names": combo_key,
                    "sample_teams": str(sample_teams),
                    "team_appearances": 0,
                    "wins": 0,
                    "losses": 0,
                    "draws": 0,
                    "matches_played": 0,
                    "win_rate": "",
                    "avg_team_rank": "",
                    "best_team_rank": "",
                    "unique_authors": 0,
                    "unique_events": 0,
                    "_team_rank_total": 0,
                    "_best_team_rank": None,
                    "_authors": set(),
                    "_events": set(),
                })

                aggregate["team_appearances"] += 1
                aggregate["wins"] += wins
                aggregate["losses"] += losses
                aggregate["draws"] += draws
                aggregate["matches_played"] += wins + losses + draws
                aggregate["_team_rank_total"] += team_rank
                aggregate["_best_team_rank"] = (
                    team_rank
                    if aggregate["_best_team_rank"] is None
                    else min(aggregate["_best_team_rank"], team_rank)
                )
                if author:
                    aggregate["_authors"].add(author)
                if event_name:
                    aggregate["_events"].add(event_name)

    summaries = []
    grouped = {}
    for aggregate in aggregates.values():
        appearances = aggregate["team_appearances"]
        matches_played = aggregate["matches_played"]
        aggregate["team_appearance_pct"] = (
            format_decimal((appearances / sample_teams) * 100.0) if sample_teams else ""
        )
        aggregate["win_rate"] = (
            format_decimal((aggregate["wins"] / matches_played) * 100.0) if matches_played else ""
        )
        aggregate["avg_team_rank"] = (
            format_decimal(aggregate["_team_rank_total"] / appearances) if appearances else ""
        )
        aggregate["best_team_rank"] = str(aggregate["_best_team_rank"] or "")
        aggregate["unique_authors"] = str(len(aggregate["_authors"]))
        aggregate["unique_events"] = str(len(aggregate["_events"]))

        for field in ("team_appearances", "wins", "losses", "draws", "matches_played"):
            aggregate[field] = str(aggregate[field])

        for internal_field in ("_team_rank_total", "_best_team_rank", "_authors", "_events"):
            aggregate.pop(internal_field, None)

        size = safe_int(aggregate["combination_size"])
        grouped.setdefault(size, []).append(aggregate)

    for size, rows in grouped.items():
        rows.sort(
            key=lambda row: (
                -safe_int(row.get("team_appearances")),
                -safe_float(row.get("win_rate")),
                safe_int(row.get("best_team_rank"), default=999999),
                row.get("combination_label", ""),
            )
        )
        for rank, row in enumerate(rows, start=1):
            row["combination_rank"] = str(rank)
        summaries.extend(rows)

    summaries.sort(
        key=lambda row: (
            row.get("snapshot_date", ""),
            row.get("source", ""),
            row.get("format_slug", ""),
            safe_int(row.get("combination_size")),
            safe_int(row.get("combination_rank")),
            row.get("combination_label", ""),
        )
    )
    return summaries


def build_pokemon_team_metrics(
    combination_rows: list[dict],
    pokemon_usage_rows: list[dict],
    snapshot: dict,
) -> list[dict]:
    usage_lookup = {
        row.get("pokemon_name", ""): row
        for row in pokemon_usage_rows
        if row.get("pokemon_name")
    }
    teammate_lookup = {}
    for row in combination_rows:
        if safe_int(row.get("combination_size")) != 2:
            continue

        names = parse_pokemon_names(row.get("pokemon_names", ""))
        if len(names) != 2:
            continue

        left_name, right_name = names
        teammate_lookup.setdefault(left_name, []).append((right_name, row))
        teammate_lookup.setdefault(right_name, []).append((left_name, row))

    result = []
    for row in combination_rows:
        if safe_int(row.get("combination_size")) != 1:
            continue

        pokemon_name = row.get("combination_label", "")
        usage_row = usage_lookup.get(pokemon_name, {})
        top_teammates = sorted(
            teammate_lookup.get(pokemon_name, []),
            key=lambda item: (
                -safe_int(item[1].get("team_appearances")),
                -safe_float(item[1].get("win_rate")),
                item[0],
            ),
        )[:5]
        result.append({
            **snapshot,
            "pokemon_name": pokemon_name,
            "usage_rank": usage_row.get("pokemon_rank", ""),
            "usage_pct": usage_row.get("usage_pct", ""),
            "sample_teams": row.get("sample_teams", ""),
            "team_appearances": row.get("team_appearances", ""),
            "team_appearance_pct": row.get("team_appearance_pct", ""),
            "wins": row.get("wins", ""),
            "losses": row.get("losses", ""),
            "draws": row.get("draws", ""),
            "matches_played": row.get("matches_played", ""),
            "win_rate": row.get("win_rate", ""),
            "avg_team_rank": row.get("avg_team_rank", ""),
            "best_team_rank": row.get("best_team_rank", ""),
            "unique_authors": row.get("unique_authors", ""),
            "unique_events": row.get("unique_events", ""),
            "top_teammates": ":".join(name for name, _pair_row in top_teammates),
            "top_teammate_appearances": ":".join(pair_row.get("team_appearances", "") for _name, pair_row in top_teammates),
            "top_teammate_win_rates": ":".join(pair_row.get("win_rate", "") for _name, pair_row in top_teammates),
        })

    result.sort(
        key=lambda row: (
            row.get("snapshot_date", ""),
            row.get("source", ""),
            row.get("format_slug", ""),
            safe_int(row.get("usage_rank"), default=999999),
            -safe_int(row.get("team_appearances")),
            row.get("pokemon_name", ""),
        )
    )
    return result


def collect_datasets(args: argparse.Namespace) -> dict[str, list[dict]]:
    if args.source.lower() != "pikalytics":
        raise ValueError(
            "Only the pikalytics source is implemented right now; "
            "pokemon-zone is blocked by Cloudflare from this environment."
        )

    html = fetch_html(args.url)
    soup = BeautifulSoup(html, "html.parser")
    snapshot = build_snapshot_context(args, soup)
    top_teams_html = fetch_html(TOP_TEAMS_URL)
    top_teams_soup = BeautifulSoup(top_teams_html, "html.parser")

    pokemon_usage_rows = parse_pokemon_usage(soup, snapshot)
    team_core_rows = parse_team_cores(soup, snapshot)
    top_team_rows = parse_recent_top_teams(top_teams_soup, snapshot, limit=TOP_TEAMS_LIMIT)
    top_team_rows, team_pokemon_detail_rows = enrich_top_teams_with_source_details(top_team_rows)
    combination_rows = build_team_combination_summaries(top_team_rows, snapshot)
    pokemon_team_rows = build_pokemon_team_metrics(combination_rows, pokemon_usage_rows, snapshot)
    team_combination_rows = [
        row
        for row in combination_rows
        if safe_int(row.get("combination_size")) >= 2
    ]

    return {
        "pokemon_usage": pokemon_usage_rows,
        "pokemon_team_metrics": pokemon_team_rows,
        "team_cores": team_core_rows,
        "top_teams": top_team_rows,
        "team_pokemon_details": team_pokemon_detail_rows,
        "team_combinations": team_combination_rows,
    }


def resolve_output_paths(output_arg: str) -> dict[str, Path]:
    output_path = Path(output_arg).expanduser()
    if output_path.exists() and output_path.is_dir():
        base_dir = output_path.resolve()
        base_name = DEFAULT_OUTPUT.stem
    else:
        output_path = output_path.resolve()
        base_dir = output_path.parent
        base_name = output_path.stem if output_path.suffix.lower() == ".csv" else output_path.name

    return {
        dataset_name: base_dir / f"{base_name}_{dataset_name}.csv"
        for dataset_name in DATASET_ORDER
    }


def load_existing_rows(csv_path: Path, fieldnames: list[str]) -> list[dict]:
    if not csv_path.exists():
        return []

    with open(csv_path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return [
            {field: row.get(field, "") for field in fieldnames}
            for row in reader
        ]


def event_identity_value(row: dict) -> str:
    return (
        row.get("event_standings_url", "")
        or row.get("team_source_url", "")
        or "|".join(
            [
                row.get("event_date", ""),
                row.get("event_name", ""),
                row.get("author", ""),
                row.get("pokemon_names", "") or row.get("team_pokemon_names", ""),
            ]
        )
    )


def dataset_row_key(dataset_name: str, row: dict) -> tuple[str, ...]:
    if dataset_name == "pokemon_usage":
        return (row.get("snapshot_date", ""), row.get("source", ""), row.get("format_slug", ""), row.get("pokemon_name", ""))
    if dataset_name == "pokemon_team_metrics":
        return (row.get("snapshot_date", ""), row.get("source", ""), row.get("format_slug", ""), row.get("pokemon_name", ""))
    if dataset_name == "team_cores":
        return (row.get("snapshot_date", ""), row.get("source", ""), row.get("format_slug", ""), row.get("core_size", ""), row.get("pokemon_names", ""))
    if dataset_name == "top_teams":
        return (row.get("source", ""), row.get("format_slug", ""), event_identity_value(row), row.get("author", ""), row.get("pokemon_names", ""))
    if dataset_name == "team_pokemon_details":
        return (
            row.get("source", ""),
            row.get("format_slug", ""),
            event_identity_value(row),
            row.get("author", ""),
            row.get("team_pokemon_names", ""),
            row.get("slot_index", ""),
            row.get("pokemon_name", ""),
        )
    if dataset_name == "team_combinations":
        return (row.get("snapshot_date", ""), row.get("source", ""), row.get("format_slug", ""), row.get("combination_size", ""), row.get("pokemon_names", ""))
    raise ValueError(f"Unknown dataset: {dataset_name}")


def row_preference_key(row: dict, fieldnames: list[str]) -> tuple[int, str]:
    populated_fields = sum(1 for field in fieldnames if normalize_whitespace(row.get(field, "")))
    return (populated_fields, row.get("snapshot_at_utc", ""))


def dataset_sort_key(dataset_name: str, row: dict) -> tuple:
    prefix = (row.get("snapshot_date", ""), row.get("source", ""), row.get("format_slug", ""))

    if dataset_name == "pokemon_usage":
        return prefix + (safe_int(row.get("pokemon_rank"), default=999999), row.get("pokemon_name", ""))
    if dataset_name == "pokemon_team_metrics":
        return prefix + (
            safe_int(row.get("usage_rank"), default=999999),
            -safe_int(row.get("team_appearances")),
            row.get("pokemon_name", ""),
        )
    if dataset_name == "team_cores":
        return prefix + (
            safe_int(row.get("core_size")),
            safe_int(row.get("core_rank"), default=999999),
            row.get("core_label", ""),
        )
    if dataset_name == "top_teams":
        return (row.get("event_date", ""), row.get("source", ""), row.get("format_slug", "")) + (
            safe_int(row.get("team_rank"), default=999999),
            row.get("author", ""),
            row.get("pokemon_names", ""),
        )
    if dataset_name == "team_pokemon_details":
        return (row.get("event_date", ""), row.get("source", ""), row.get("format_slug", "")) + (
            safe_int(row.get("team_rank"), default=999999),
            safe_int(row.get("slot_index"), default=999999),
            row.get("pokemon_name", ""),
        )
    if dataset_name == "team_combinations":
        return prefix + (
            safe_int(row.get("combination_size")),
            safe_int(row.get("combination_rank"), default=999999),
            row.get("combination_label", ""),
        )

    raise ValueError(f"Unknown dataset: {dataset_name}")


def merge_dataset_rows(
    dataset_name: str,
    existing: list[dict],
    new_rows: list[dict],
) -> tuple[list[dict], int, int]:
    fieldnames = DATASET_FIELDS[dataset_name]

    result = []
    index = {}

    for row in existing:
        normalized = {field: row.get(field, "") for field in fieldnames}
        key = dataset_row_key(dataset_name, normalized)
        if key in index:
            current_index = index[key]
            current_row = result[current_index]
            if row_preference_key(normalized, fieldnames) >= row_preference_key(current_row, fieldnames):
                result[current_index] = normalized
            continue

        index[key] = len(result)
        result.append(normalized)

    inserted = 0
    updated = 0

    for row in new_rows:
        normalized = {field: row.get(field, "") for field in fieldnames}
        key = dataset_row_key(dataset_name, normalized)
        if key in index:
            result[index[key]] = normalized
            updated += 1
        else:
            index[key] = len(result)
            result.append(normalized)
            inserted += 1

    result.sort(key=lambda row: dataset_sort_key(dataset_name, row))
    return result, inserted, updated


def save_rows(csv_path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    args = parse_args()
    setup_logging(args.verbose)
    logger = logging.getLogger(__name__)

    try:
        datasets = collect_datasets(args)
    except requests.RequestException as exc:
        logger.error(f"Failed to fetch source page: {exc}")
        sys.exit(1)
    except ValueError as exc:
        logger.error(str(exc))
        sys.exit(1)

    if not any(datasets.values()):
        logger.error("No metrics were parsed from the source page")
        sys.exit(1)

    output_paths = resolve_output_paths(args.output)
    logger.info(f"Source   : {args.url}")

    for dataset_name in DATASET_ORDER:
        rows = datasets.get(dataset_name, [])
        fieldnames = DATASET_FIELDS[dataset_name]
        output_path = output_paths[dataset_name]
        existing_rows = load_existing_rows(output_path, fieldnames)
        merged_rows, inserted, updated = merge_dataset_rows(dataset_name, existing_rows, rows)
        save_rows(output_path, fieldnames, merged_rows)
        logger.info(
            f"{dataset_name:20} {output_path.name} parsed={len(rows)} inserted={inserted} updated={updated}"
        )


if __name__ == "__main__":
    main()