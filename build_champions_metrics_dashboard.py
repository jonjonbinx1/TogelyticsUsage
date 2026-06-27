#!/usr/bin/env python3
"""Build a self-contained HTML dashboard for Champions web metrics datasets."""

from __future__ import annotations

import argparse
import csv
import json
import webbrowser
from collections import Counter
from collections import defaultdict
from pathlib import Path


SCRIPT_DIR = Path(__file__).parent
DEFAULT_INPUT_BASE = SCRIPT_DIR / "champions_web_metrics.csv"
DEFAULT_OUTPUT = SCRIPT_DIR / "results" / "champions_web_metrics_dashboard.html"

DATASET_NAMES = (
    "pokemon_usage",
    "pokemon_team_metrics",
    "team_cores",
    "top_teams",
    "team_pokemon_details",
    "team_combinations",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build an HTML dashboard from Champions web metrics CSV datasets.",
    )
    parser.add_argument(
        "--input-base",
        default=str(DEFAULT_INPUT_BASE),
        help=(
            "Base dataset path. If this is 'champions_web_metrics.csv', the script reads "
            "champions_web_metrics_pokemon_usage.csv, champions_web_metrics_top_teams.csv, etc."
        ),
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=f"HTML output path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--snapshot-date",
        help="Preselect a snapshot date in the dashboard. Defaults to the latest available snapshot.",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open the generated dashboard in the default web browser.",
    )
    return parser.parse_args()


def resolve_dataset_paths(input_base: str) -> dict[str, Path]:
    base_path = Path(input_base)
    base_dir = base_path.parent if base_path.parent != Path("") else SCRIPT_DIR
    base_name = base_path.stem if base_path.suffix.lower() == ".csv" else base_path.name
    return {
        dataset_name: base_dir / f"{base_name}_{dataset_name}.csv"
        for dataset_name in DATASET_NAMES
    }


def load_rows(csv_path: Path) -> list[dict[str, str]]:
    if not csv_path.exists():
        return []

    with open(csv_path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def safe_int(value: str, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def safe_float(value: str, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def split_names(value: str) -> list[str]:
    return [name for name in str(value or "").split(":") if name]


def build_count_rows(counter: Counter[str], total: int, limit: int = 10) -> list[dict[str, object]]:
    rows = []
    for name, count in counter.most_common(limit):
        rows.append({
            "label": name,
            "count": count,
            "pct": round((count / total) * 100.0, 2) if total else 0.0,
        })
    return rows


def latest_snapshot_date(rows: list[dict[str, str]]) -> str:
    dates = sorted({row.get("snapshot_date", "") for row in rows if row.get("snapshot_date")})
    if not dates:
        raise ValueError("No snapshot_date values were found in the dataset files.")
    return dates[-1]


def group_rows_by_snapshot(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        snapshot_date = row.get("snapshot_date", "")
        if snapshot_date:
            grouped[snapshot_date].append(row)
    return grouped


def build_snapshot_summary(
    usage_rows: list[dict[str, str]],
    team_rows: list[dict[str, str]],
    combination_rows: list[dict[str, str]],
) -> dict[str, object]:
    usage_leader = ""
    usage_leader_pct = 0.0
    if usage_rows:
        leader = min(usage_rows, key=lambda row: safe_int(row.get("pokemon_rank"), default=999999))
        usage_leader = leader.get("pokemon_name", "")
        usage_leader_pct = safe_float(leader.get("usage_pct"))

    sample_teams = 0
    if team_rows:
        sample_teams = max(safe_int(row.get("sample_teams"), default=0) for row in team_rows)

    anchor = ""
    anchor_presence = 0.0
    anchor_win_rate = 0.0
    if team_rows:
        best_anchor = max(
            team_rows,
            key=lambda row: (
                safe_float(row.get("team_appearance_pct")),
                safe_float(row.get("win_rate")),
                -safe_int(row.get("usage_rank"), default=999999),
            ),
        )
        anchor = best_anchor.get("pokemon_name", "")
        anchor_presence = safe_float(best_anchor.get("team_appearance_pct"))
        anchor_win_rate = safe_float(best_anchor.get("win_rate"))

    best_pairing = ""
    best_pairing_win_rate = 0.0
    pairings = [row for row in combination_rows if row.get("combination_type") == "pairing"]
    if pairings:
        strongest_pairing = max(
            pairings,
            key=lambda row: (
                safe_float(row.get("team_appearance_pct")),
                safe_float(row.get("win_rate")),
            ),
        )
        best_pairing = strongest_pairing.get("combination_label", "")
        best_pairing_win_rate = safe_float(strongest_pairing.get("win_rate"))

    return {
        "format_name": usage_rows[0].get("format_name", "") if usage_rows else "Pokemon Champions",
        "source": usage_rows[0].get("source", "") if usage_rows else "",
        "usage_leader": usage_leader,
        "usage_leader_pct": round(usage_leader_pct, 2),
        "sample_teams": sample_teams,
        "anchor": anchor,
        "anchor_presence": round(anchor_presence, 2),
        "anchor_win_rate": round(anchor_win_rate, 2),
        "best_pairing": best_pairing,
        "best_pairing_win_rate": round(best_pairing_win_rate, 2),
    }


def build_dashboard_payload(dataset_rows: dict[str, list[dict[str, str]]], preferred_snapshot: str | None) -> dict[str, object]:
    usage_rows = dataset_rows["pokemon_usage"]
    if not usage_rows:
        raise ValueError(
            "Missing pokemon usage data. Run collect_champions_web_metrics.py before building the dashboard."
        )

    snapshots = sorted({row.get("snapshot_date", "") for row in usage_rows if row.get("snapshot_date")})
    if not snapshots:
        raise ValueError("No snapshots were found in champions_web_metrics_pokemon_usage.csv.")

    selected_snapshot = preferred_snapshot or snapshots[-1]
    if selected_snapshot not in snapshots:
        raise ValueError(f"Snapshot date '{selected_snapshot}' was not found in the available datasets.")

    usage_by_snapshot = group_rows_by_snapshot(dataset_rows["pokemon_usage"])
    performance_by_snapshot = group_rows_by_snapshot(dataset_rows["pokemon_team_metrics"])
    source_cores_by_snapshot = group_rows_by_snapshot(dataset_rows["team_cores"])
    top_teams_by_snapshot = group_rows_by_snapshot(dataset_rows["top_teams"])
    team_details_by_snapshot = group_rows_by_snapshot(dataset_rows["team_pokemon_details"])
    combinations_by_snapshot = group_rows_by_snapshot(dataset_rows["team_combinations"])

    snapshot_payload: dict[str, dict[str, object]] = {}
    usage_index_by_snapshot: dict[str, dict[str, float]] = {}

    for snapshot_date in snapshots:
        usage_snapshot_rows = sorted(
            usage_by_snapshot.get(snapshot_date, []),
            key=lambda row: safe_int(row.get("pokemon_rank"), default=999999),
        )
        team_metric_rows = sorted(
            performance_by_snapshot.get(snapshot_date, []),
            key=lambda row: (
                -safe_float(row.get("team_appearance_pct")),
                -safe_float(row.get("win_rate")),
                safe_int(row.get("usage_rank"), default=999999),
                row.get("pokemon_name", ""),
            ),
        )
        team_core_rows = sorted(
            source_cores_by_snapshot.get(snapshot_date, []),
            key=lambda row: (
                safe_int(row.get("core_size"), default=999999),
                safe_int(row.get("core_rank"), default=999999),
            ),
        )
        top_team_rows = sorted(
            top_teams_by_snapshot.get(snapshot_date, []),
            key=lambda row: safe_int(row.get("team_rank"), default=999999),
        )
        combination_rows = sorted(
            combinations_by_snapshot.get(snapshot_date, []),
            key=lambda row: (
                safe_int(row.get("combination_size"), default=999999),
                safe_int(row.get("combination_rank"), default=999999),
            ),
        )
        detail_rows = team_details_by_snapshot.get(snapshot_date, [])

        usage_index_by_snapshot[snapshot_date] = {
            row.get("pokemon_name", ""): round(safe_float(row.get("usage_pct")), 2)
            for row in usage_snapshot_rows
        }

        usage_chart_rows = [
            {
                "pokemon": row.get("pokemon_name", ""),
                "rank": safe_int(row.get("pokemon_rank"), default=0),
                "usage_pct": round(safe_float(row.get("usage_pct")), 2),
            }
            for row in usage_snapshot_rows[:15]
        ]

        performance_chart_rows = [
            {
                "pokemon": row.get("pokemon_name", ""),
                "usage_rank": safe_int(row.get("usage_rank"), default=0) or None,
                "usage_pct": round(safe_float(row.get("usage_pct")), 2),
                "team_appearances": safe_int(row.get("team_appearances"), default=0),
                "team_appearance_pct": round(safe_float(row.get("team_appearance_pct")), 2),
                "win_rate": round(safe_float(row.get("win_rate")), 2),
                "avg_team_rank": round(safe_float(row.get("avg_team_rank")), 2),
                "top_teammates": split_names(row.get("top_teammates", "")),
            }
            for row in team_metric_rows[:18]
        ]

        core_chart_rows: dict[str, list[dict[str, object]]] = {"2": [], "3": [], "4": []}
        for row in team_core_rows:
            core_size = str(safe_int(row.get("core_size"), default=0))
            if core_size not in core_chart_rows:
                continue
            core_chart_rows[core_size].append(
                {
                    "label": row.get("core_label", "") or row.get("pokemon_names", "").replace(":", " / "),
                    "usage_pct": round(safe_float(row.get("usage_pct")), 2),
                    "team_count": safe_int(row.get("team_count"), default=0),
                }
            )
        for core_size in core_chart_rows:
            core_chart_rows[core_size] = core_chart_rows[core_size][:10]

        combo_chart_rows: dict[str, list[dict[str, object]]] = {
            "pairing": [],
            "trio": [],
            "full_team": [],
        }
        for row in combination_rows:
            combo_type = row.get("combination_type", "")
            if combo_type not in combo_chart_rows:
                continue
            combo_chart_rows[combo_type].append(
                {
                    "label": row.get("combination_label", "") or row.get("pokemon_names", "").replace(":", " / "),
                    "team_appearances": safe_int(row.get("team_appearances"), default=0),
                    "team_appearance_pct": round(safe_float(row.get("team_appearance_pct")), 2),
                    "win_rate": round(safe_float(row.get("win_rate")), 2),
                }
            )
        for combo_type in combo_chart_rows:
            combo_chart_rows[combo_type] = combo_chart_rows[combo_type][:12]

        set_breakdown: dict[str, dict[str, list[dict[str, object]]]] = {}
        pokemon_detail_groups: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in detail_rows:
            pokemon_name = row.get("pokemon_name", "")
            if pokemon_name:
                pokemon_detail_groups[pokemon_name].append(row)

        for pokemon_name, pokemon_rows in sorted(pokemon_detail_groups.items()):
            item_counter: Counter[str] = Counter()
            move_counter: Counter[str] = Counter()
            ability_counter: Counter[str] = Counter()
            teammate_counter: Counter[str] = Counter()
            for row in pokemon_rows:
                item_name = row.get("item", "") or "Unknown"
                item_counter[item_name] += 1

                ability_name = row.get("ability", "") or "Unknown"
                ability_counter[ability_name] += 1

                for move_name in split_names(row.get("moves", "")):
                    move_counter[move_name] += 1

                for teammate in split_names(row.get("teammates", "")):
                    teammate_counter[teammate] += 1

            sample_count = len(pokemon_rows)
            set_breakdown[pokemon_name] = {
                "items": build_count_rows(item_counter, sample_count),
                "moves": build_count_rows(move_counter, sample_count),
                "abilities": build_count_rows(ability_counter, sample_count),
                "teammates": build_count_rows(teammate_counter, sample_count),
            }

        top_team_cards = [
            {
                "team_rank": safe_int(row.get("team_rank"), default=0),
                "author": row.get("author", ""),
                "record": row.get("record", ""),
                "event_name": row.get("event_name", ""),
                "event_rank": safe_int(row.get("event_rank"), default=0),
                "win_rate": round(safe_float(row.get("win_rate")), 2),
                "pokemon": split_names(row.get("pokemon_names", "")),
                "team_source_url": row.get("team_source_url", ""),
            }
            for row in top_team_rows[:12]
        ]

        snapshot_payload[snapshot_date] = {
            "summary": build_snapshot_summary(usage_snapshot_rows, team_metric_rows, combination_rows),
            "usage": usage_chart_rows,
            "teamPerformance": performance_chart_rows,
            "sourceCores": core_chart_rows,
            "observedCombinations": combo_chart_rows,
            "setBreakdown": set_breakdown,
            "topTeams": top_team_cards,
        }

    return {
        "title": usage_rows[0].get("format_name", "Pokemon Champions Dashboard"),
        "snapshots": snapshots,
        "selectedSnapshot": selected_snapshot,
        "snapshotData": snapshot_payload,
        "usageIndexBySnapshot": usage_index_by_snapshot,
    }


def build_html(payload: dict[str, object]) -> str:
    title = str(payload.get("title", "Champions Metrics Dashboard"))
    data_json = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    return (
        DASHBOARD_TEMPLATE
        .replace("__DASHBOARD_TITLE__", html_escape(title))
        .replace("__DASHBOARD_DATA__", data_json)
    )


def html_escape(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def main() -> None:
    args = parse_args()
    dataset_paths = resolve_dataset_paths(args.input_base)
    dataset_rows = {name: load_rows(path) for name, path in dataset_paths.items()}
    payload = build_dashboard_payload(dataset_rows, args.snapshot_date)
    html = build_html(payload)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")

    print(f"Wrote dashboard: {output_path}")
    print(f"Snapshots     : {len(payload['snapshots'])}")
    print(f"Selected      : {payload['selectedSnapshot']}")

    if args.open:
        webbrowser.open(output_path.resolve().as_uri())


DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>__DASHBOARD_TITLE__</title>
  <style>
    :root {
      --bg: #f5efe2;
      --panel: rgba(255, 251, 244, 0.9);
      --panel-strong: rgba(255, 249, 239, 0.98);
      --ink: #21180f;
      --muted: #715c49;
      --line: rgba(74, 53, 32, 0.12);
      --accent: #d65a31;
      --accent-2: #1f7a8c;
      --accent-3: #7d8f2a;
      --accent-soft: rgba(214, 90, 49, 0.12);
      --shadow: 0 18px 44px rgba(41, 25, 8, 0.12);
      --radius: 20px;
    }

    * {
      box-sizing: border-box;
    }

    body {
      margin: 0;
      min-height: 100vh;
      font-family: "Aptos", "Segoe UI Variable", "Trebuchet MS", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(214, 90, 49, 0.22), transparent 28%),
        radial-gradient(circle at top right, rgba(31, 122, 140, 0.16), transparent 24%),
        linear-gradient(180deg, #f8f2e7 0%, #efe2ce 100%);
    }

    .shell {
      width: min(1400px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 24px 0 40px;
    }

    .hero {
      position: relative;
      overflow: hidden;
      padding: 28px;
      border-radius: 28px;
      background:
        linear-gradient(135deg, rgba(255, 251, 246, 0.95), rgba(247, 236, 218, 0.92)),
        linear-gradient(135deg, rgba(214, 90, 49, 0.08), rgba(31, 122, 140, 0.04));
      box-shadow: var(--shadow);
      border: 1px solid rgba(74, 53, 32, 0.08);
    }

    .hero::after {
      content: "";
      position: absolute;
      inset: auto -80px -120px auto;
      width: 280px;
      height: 280px;
      border-radius: 50%;
      background: radial-gradient(circle, rgba(214, 90, 49, 0.18), transparent 72%);
      pointer-events: none;
    }

    .eyebrow {
      margin: 0 0 8px;
      text-transform: uppercase;
      letter-spacing: 0.18em;
      font-size: 12px;
      color: var(--muted);
    }

    h1 {
      margin: 0;
      max-width: 760px;
      font-size: clamp(30px, 5vw, 54px);
      line-height: 0.98;
      letter-spacing: -0.04em;
    }

    .hero-copy {
      margin: 14px 0 0;
      max-width: 800px;
      color: var(--muted);
      font-size: 15px;
      line-height: 1.6;
    }

    .toolbar {
      display: flex;
      flex-wrap: wrap;
      gap: 12px 20px;
      align-items: end;
      margin-top: 22px;
    }

    .control {
      min-width: 220px;
    }

    .control-label {
      display: block;
      margin-bottom: 8px;
      font-size: 12px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted);
    }

    select {
      width: 100%;
      padding: 12px 14px;
      border-radius: 14px;
      border: 1px solid rgba(74, 53, 32, 0.14);
      background: rgba(255, 252, 247, 0.95);
      color: var(--ink);
      font: inherit;
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.65);
    }

    .grid {
      display: grid;
      grid-template-columns: repeat(12, minmax(0, 1fr));
      gap: 18px;
      margin-top: 18px;
    }

    .panel {
      position: relative;
      padding: 20px;
      border-radius: var(--radius);
      background: var(--panel);
      border: 1px solid rgba(74, 53, 32, 0.08);
      box-shadow: var(--shadow);
      backdrop-filter: blur(10px);
    }

    .panel-title {
      margin: 0;
      font-size: 20px;
      letter-spacing: -0.03em;
    }

    .panel-copy {
      margin: 6px 0 0;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.5;
    }

    .summary-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-top: 16px;
    }

    .summary-card {
      padding: 16px;
      border-radius: 18px;
      background: linear-gradient(180deg, rgba(255, 251, 244, 0.96), rgba(244, 233, 214, 0.85));
      border: 1px solid rgba(74, 53, 32, 0.08);
      min-height: 116px;
    }

    .summary-label {
      margin: 0 0 10px;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      color: var(--muted);
    }

    .summary-value {
      margin: 0;
      font-size: clamp(22px, 3vw, 32px);
      line-height: 1;
      letter-spacing: -0.04em;
    }

    .summary-subvalue {
      margin: 8px 0 0;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.4;
    }

    .span-12 { grid-column: span 12; }
    .span-8 { grid-column: span 8; }
    .span-7 { grid-column: span 7; }
    .span-6 { grid-column: span 6; }
    .span-5 { grid-column: span 5; }
    .span-4 { grid-column: span 4; }

    .chart-block {
      margin-top: 18px;
      min-height: 320px;
    }

    .bar-chart {
      display: grid;
      gap: 10px;
    }

    .bar-row {
      display: grid;
      grid-template-columns: minmax(0, 188px) minmax(0, 1fr) auto;
      align-items: center;
      gap: 12px;
    }

    .bar-label {
      font-size: 13px;
      line-height: 1.3;
      color: var(--ink);
    }

    .bar-track {
      position: relative;
      overflow: hidden;
      height: 14px;
      border-radius: 999px;
      background: rgba(74, 53, 32, 0.08);
    }

    .bar-fill {
      height: 100%;
      border-radius: inherit;
      background: linear-gradient(90deg, var(--accent), #ee964b);
    }

    .bar-fill.secondary {
      background: linear-gradient(90deg, var(--accent-2), #44b3c2);
    }

    .bar-fill.olive {
      background: linear-gradient(90deg, var(--accent-3), #b7c55c);
    }

    .bar-value {
      font-size: 12px;
      color: var(--muted);
      white-space: nowrap;
    }

    .pill-row {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 16px;
    }

    .pill {
      padding: 8px 12px;
      border-radius: 999px;
      border: 1px solid rgba(74, 53, 32, 0.12);
      background: rgba(255, 251, 246, 0.9);
      color: var(--muted);
      font: inherit;
      cursor: pointer;
    }

    .pill.active {
      border-color: rgba(214, 90, 49, 0.35);
      color: var(--ink);
      background: rgba(214, 90, 49, 0.12);
    }

    .empty-state {
      display: grid;
      place-items: center;
      min-height: 280px;
      border: 1px dashed rgba(74, 53, 32, 0.14);
      border-radius: 16px;
      color: var(--muted);
      text-align: center;
      padding: 20px;
    }

    .chart-svg {
      width: 100%;
      height: 340px;
      overflow: visible;
    }

    .chart-legend {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin-top: 14px;
      font-size: 12px;
      color: var(--muted);
    }

    .legend-chip {
      display: inline-flex;
      align-items: center;
      gap: 8px;
    }

    .legend-dot {
      width: 12px;
      height: 12px;
      border-radius: 50%;
      flex: 0 0 12px;
    }

    .two-column {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 16px;
      margin-top: 16px;
    }

    .team-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
      margin-top: 18px;
    }

    .team-card {
      padding: 16px;
      border-radius: 16px;
      background: var(--panel-strong);
      border: 1px solid rgba(74, 53, 32, 0.08);
    }

    .team-head {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: start;
    }

    .team-rank {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 40px;
      height: 40px;
      border-radius: 50%;
      background: rgba(214, 90, 49, 0.12);
      color: var(--accent);
      font-weight: 700;
    }

    .team-meta {
      margin: 10px 0 0;
      font-size: 13px;
      color: var(--muted);
      line-height: 1.5;
    }

    .team-members {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 14px;
    }

    .member-chip {
      padding: 8px 10px;
      border-radius: 999px;
      background: rgba(31, 122, 140, 0.08);
      color: var(--ink);
      font-size: 12px;
    }

    a.team-link {
      display: inline-block;
      margin-top: 12px;
      color: var(--accent-2);
      text-decoration: none;
      font-size: 13px;
      font-weight: 700;
    }

    a.team-link:hover {
      text-decoration: underline;
    }

    @media (max-width: 1120px) {
      .summary-grid,
      .two-column,
      .team-grid {
        grid-template-columns: 1fr 1fr;
      }

      .span-8,
      .span-7,
      .span-6,
      .span-5,
      .span-4 {
        grid-column: span 12;
      }
    }

    @media (max-width: 760px) {
      .shell {
        width: min(100vw - 18px, 100%);
        padding: 12px 0 24px;
      }

      .hero,
      .panel {
        padding: 16px;
        border-radius: 18px;
      }

      .summary-grid,
      .two-column,
      .team-grid {
        grid-template-columns: 1fr;
      }

      .bar-row {
        grid-template-columns: 1fr;
        gap: 6px;
      }
    }
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <p class="eyebrow">Pokemon Champions Web Metrics</p>
      <h1 id="dashboard-title">__DASHBOARD_TITLE__</h1>
      <p class="hero-copy">
        A local viewer for usage, top-team performance, source cores, observed combinations, and per-Pokemon set details.
      </p>
      <div class="toolbar">
        <div class="control">
          <label class="control-label" for="snapshot-select">Snapshot Date</label>
          <select id="snapshot-select"></select>
        </div>
        <div class="control">
          <label class="control-label" for="pokemon-select">Set Explorer Pokemon</label>
          <select id="pokemon-select"></select>
        </div>
      </div>
      <div class="summary-grid" id="summary-grid"></div>
    </section>

    <section class="grid">
      <article class="panel span-6">
        <h2 class="panel-title">Usage Ladder</h2>
        <p class="panel-copy">Top Pokemon by source usage percentage for the selected snapshot.</p>
        <div class="chart-block" id="usage-chart"></div>
      </article>

      <article class="panel span-6">
        <h2 class="panel-title">Top Team Anchors</h2>
        <p class="panel-copy">Bubble view of top-team presence against win rate. Bubble size tracks team appearances.</p>
        <div class="chart-block" id="performance-chart"></div>
      </article>

      <article class="panel span-6">
        <h2 class="panel-title">Source Common Cores</h2>
        <p class="panel-copy">Pikalytics core frequency pulled directly from the format page.</p>
        <div class="pill-row" id="core-filter"></div>
        <div class="chart-block" id="core-chart"></div>
      </article>

      <article class="panel span-6">
        <h2 class="panel-title">Observed Team Combinations</h2>
        <p class="panel-copy">Pairings, trios, and full teams aggregated from the scraped top-team sample.</p>
        <div class="pill-row" id="combo-filter"></div>
        <div class="chart-block" id="combo-chart"></div>
      </article>

      <article class="panel span-7">
        <h2 class="panel-title">Usage Timeline</h2>
        <p class="panel-copy">Tracks the selected snapshot's key Pokemon across all collected snapshot dates.</p>
        <div class="chart-block" id="timeline-chart"></div>
      </article>

      <article class="panel span-5">
        <h2 class="panel-title">Set Explorer</h2>
        <p class="panel-copy">Item, move, and teammate frequencies from the normalized team_pokemon_details dataset.</p>
        <div class="two-column">
          <div>
            <h3 class="panel-copy">Items</h3>
            <div class="chart-block" id="item-chart"></div>
          </div>
          <div>
            <h3 class="panel-copy">Moves</h3>
            <div class="chart-block" id="move-chart"></div>
          </div>
        </div>
        <div class="two-column">
          <div>
            <h3 class="panel-copy">Abilities</h3>
            <div class="chart-block" id="ability-chart"></div>
          </div>
          <div>
            <h3 class="panel-copy">Top Teammates</h3>
            <div class="chart-block" id="teammate-chart"></div>
          </div>
        </div>
      </article>

      <article class="panel span-12">
        <h2 class="panel-title">Top Team Viewer</h2>
        <p class="panel-copy">Quick scan of the best-performing sampled teams with source teamlist links when available.</p>
        <div class="team-grid" id="team-grid"></div>
      </article>
    </section>
  </div>

  <script id="dashboard-data" type="application/json">__DASHBOARD_DATA__</script>
  <script>
    const dashboardData = JSON.parse(document.getElementById('dashboard-data').textContent);

    const state = {
      snapshot: dashboardData.selectedSnapshot,
      coreSize: '2',
      comboType: 'pairing',
      pokemon: '',
    };

    const colorScale = ['#d65a31', '#1f7a8c', '#7d8f2a', '#bc6c25', '#2d6a4f', '#7b2cbf'];

    function snapshotData() {
      return dashboardData.snapshotData[state.snapshot] || {};
    }

    function formatPct(value) {
      return `${Number(value || 0).toFixed(2)}%`;
    }

    function escapeHtml(value) {
      return String(value || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    }

    function showEmpty(containerId, message) {
      document.getElementById(containerId).innerHTML = `<div class="empty-state">${escapeHtml(message)}</div>`;
    }

    function renderSummary() {
      const summary = snapshotData().summary || {};
      const cards = [
        {
          label: 'Usage Leader',
          value: summary.usage_leader || 'Unavailable',
          subvalue: summary.usage_leader ? `${formatPct(summary.usage_leader_pct)} source usage` : 'No usage rows in this snapshot',
        },
        {
          label: 'Sampled Teams',
          value: String(summary.sample_teams || 0),
          subvalue: `${summary.source || 'Source'} top-team sample`,
        },
        {
          label: 'Most Common Anchor',
          value: summary.anchor || 'Unavailable',
          subvalue: summary.anchor ? `${formatPct(summary.anchor_presence)} presence, ${formatPct(summary.anchor_win_rate)} win rate` : 'No top-team aggregate rows',
        },
        {
          label: 'Best Pairing Signal',
          value: summary.best_pairing || 'Unavailable',
          subvalue: summary.best_pairing ? `${formatPct(summary.best_pairing_win_rate)} combined win rate` : 'No observed pairings',
        },
      ];

      document.getElementById('summary-grid').innerHTML = cards.map((card) => `
        <div class="summary-card">
          <p class="summary-label">${escapeHtml(card.label)}</p>
          <p class="summary-value">${escapeHtml(card.value)}</p>
          <p class="summary-subvalue">${escapeHtml(card.subvalue)}</p>
        </div>
      `).join('');
    }

    function renderBarChart(containerId, rows, options = {}) {
      if (!rows || !rows.length) {
        showEmpty(containerId, options.emptyMessage || 'No chart data available.');
        return;
      }

      const maxValue = Math.max(...rows.map((row) => Number(row[options.valueKey] || 0)), 1);
      const fillClass = options.fillClass || '';
      document.getElementById(containerId).innerHTML = `
        <div class="bar-chart">
          ${rows.map((row) => {
            const value = Number(row[options.valueKey] || 0);
            const width = Math.max((value / maxValue) * 100, value > 0 ? 4 : 0);
            const label = options.labelRenderer ? options.labelRenderer(row) : row.label;
            const valueText = options.valueRenderer ? options.valueRenderer(row) : String(value);
            return `
              <div class="bar-row">
                <div class="bar-label">${escapeHtml(label)}</div>
                <div class="bar-track"><div class="bar-fill ${fillClass}" style="width:${width}%"></div></div>
                <div class="bar-value">${escapeHtml(valueText)}</div>
              </div>
            `;
          }).join('')}
        </div>
      `;
    }

    function renderScatterPlot(containerId, rows) {
      if (!rows || !rows.length) {
        showEmpty(containerId, 'No team performance data available for this snapshot.');
        return;
      }

      const width = 640;
      const height = 320;
      const padding = { top: 18, right: 18, bottom: 42, left: 52 };
      const plotWidth = width - padding.left - padding.right;
      const plotHeight = height - padding.top - padding.bottom;
      const xMax = Math.max(...rows.map((row) => Number(row.team_appearance_pct || 0)), 1);
      const yMin = Math.min(...rows.map((row) => Number(row.win_rate || 0)), 0);
      const yMax = Math.max(...rows.map((row) => Number(row.win_rate || 0)), 100);
      const sizeMax = Math.max(...rows.map((row) => Number(row.team_appearances || 0)), 1);

      const gridLines = [0.25, 0.5, 0.75, 1].map((ratio) => {
        const y = padding.top + plotHeight * ratio;
        return `<line x1="${padding.left}" y1="${y}" x2="${width - padding.right}" y2="${y}" stroke="rgba(74,53,32,0.12)" stroke-width="1" />`;
      }).join('');

      const circles = rows.map((row, index) => {
        const x = padding.left + (Number(row.team_appearance_pct || 0) / xMax) * plotWidth;
        const y = padding.top + (1 - ((Number(row.win_rate || 0) - yMin) / Math.max(yMax - yMin, 1))) * plotHeight;
        const radius = 8 + (Number(row.team_appearances || 0) / sizeMax) * 18;
        const color = colorScale[index % colorScale.length];
        const tooltip = `${row.pokemon}: ${formatPct(row.team_appearance_pct)} presence, ${formatPct(row.win_rate)} win rate, ${row.team_appearances} teams`;
        return `
          <g>
            <circle cx="${x}" cy="${y}" r="${radius}" fill="${color}" fill-opacity="0.72"></circle>
            <title>${escapeHtml(tooltip)}</title>
            <text x="${x}" y="${y + 4}" text-anchor="middle" font-size="10" fill="#21180f">${escapeHtml(row.pokemon)}</text>
          </g>
        `;
      }).join('');

      document.getElementById(containerId).innerHTML = `
        <svg class="chart-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="Top team performance scatter plot">
          ${gridLines}
          <line x1="${padding.left}" y1="${height - padding.bottom}" x2="${width - padding.right}" y2="${height - padding.bottom}" stroke="#715c49" stroke-width="1.5"></line>
          <line x1="${padding.left}" y1="${padding.top}" x2="${padding.left}" y2="${height - padding.bottom}" stroke="#715c49" stroke-width="1.5"></line>
          ${circles}
          <text x="${width / 2}" y="${height - 10}" text-anchor="middle" font-size="12" fill="#715c49">Team appearance percentage</text>
          <text x="18" y="${height / 2}" text-anchor="middle" font-size="12" fill="#715c49" transform="rotate(-90 18 ${height / 2})">Win rate</text>
        </svg>
      `;
    }

    function renderTimeline(containerId) {
      const usageRows = snapshotData().usage || [];
      if (!usageRows.length || dashboardData.snapshots.length === 0) {
        showEmpty(containerId, 'No usage timeline data available.');
        return;
      }

      const focusPokemon = usageRows.slice(0, Math.min(5, usageRows.length)).map((row) => row.pokemon);
      const width = 720;
      const height = 320;
      const padding = { top: 18, right: 18, bottom: 42, left: 42 };
      const plotWidth = width - padding.left - padding.right;
      const plotHeight = height - padding.top - padding.bottom;
      const allValues = [];
      focusPokemon.forEach((pokemon) => {
        dashboardData.snapshots.forEach((snapshot) => {
          allValues.push(Number((dashboardData.usageIndexBySnapshot[snapshot] || {})[pokemon] || 0));
        });
      });
      const maxValue = Math.max(...allValues, 1);
      const xStep = dashboardData.snapshots.length > 1 ? plotWidth / (dashboardData.snapshots.length - 1) : 0;

      const lines = focusPokemon.map((pokemon, index) => {
        const points = dashboardData.snapshots.map((snapshot, snapshotIndex) => {
          const value = Number((dashboardData.usageIndexBySnapshot[snapshot] || {})[pokemon] || 0);
          const x = padding.left + xStep * snapshotIndex;
          const y = padding.top + (1 - value / maxValue) * plotHeight;
          return { x, y, value };
        });
        const path = points.map((point, pointIndex) => `${pointIndex === 0 ? 'M' : 'L'} ${point.x} ${point.y}`).join(' ');
        const color = colorScale[index % colorScale.length];
        const dots = points.map((point) => `
          <g>
            <circle cx="${point.x}" cy="${point.y}" r="4" fill="${color}"></circle>
            <title>${escapeHtml(`${pokemon}: ${formatPct(point.value)}`)}</title>
          </g>
        `).join('');
        return `<path d="${path}" fill="none" stroke="${color}" stroke-width="3"></path>${dots}`;
      }).join('');

      const labels = dashboardData.snapshots.map((snapshot, index) => {
        const x = padding.left + xStep * index;
        return `<text x="${x}" y="${height - 18}" text-anchor="middle" font-size="11" fill="#715c49">${escapeHtml(snapshot)}</text>`;
      }).join('');

      document.getElementById(containerId).innerHTML = `
        <svg class="chart-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="Usage trend lines">
          <line x1="${padding.left}" y1="${height - padding.bottom}" x2="${width - padding.right}" y2="${height - padding.bottom}" stroke="#715c49" stroke-width="1.5"></line>
          <line x1="${padding.left}" y1="${padding.top}" x2="${padding.left}" y2="${height - padding.bottom}" stroke="#715c49" stroke-width="1.5"></line>
          ${lines}
          ${labels}
        </svg>
        <div class="chart-legend">
          ${focusPokemon.map((pokemon, index) => `
            <span class="legend-chip">
              <span class="legend-dot" style="background:${colorScale[index % colorScale.length]}"></span>
              ${escapeHtml(pokemon)}
            </span>
          `).join('')}
        </div>
      `;
    }

    function renderTeamCards() {
      const teams = snapshotData().topTeams || [];
      if (!teams.length) {
        showEmpty('team-grid', 'No top team rows are available for this snapshot.');
        return;
      }

      document.getElementById('team-grid').innerHTML = teams.map((team) => `
        <article class="team-card">
          <div class="team-head">
            <div>
              <div class="team-rank">#${team.team_rank}</div>
            </div>
            <div style="flex:1">
              <strong>${escapeHtml(team.author)}</strong>
              <div class="team-meta">${escapeHtml(team.record)} · ${formatPct(team.win_rate)} win rate</div>
              <div class="team-meta">${escapeHtml(team.event_name)}${team.event_rank ? ` · Event rank #${team.event_rank}` : ''}</div>
            </div>
          </div>
          <div class="team-members">
            ${team.pokemon.map((pokemon) => `<span class="member-chip">${escapeHtml(pokemon)}</span>`).join('')}
          </div>
          ${team.team_source_url ? `<a class="team-link" href="${escapeHtml(team.team_source_url)}" target="_blank" rel="noreferrer">Open source teamlist</a>` : ''}
        </article>
      `).join('');
    }

    function renderSourceCores() {
      const rows = (snapshotData().sourceCores || {})[state.coreSize] || [];
      renderBarChart('core-chart', rows, {
        valueKey: 'usage_pct',
        fillClass: 'secondary',
        emptyMessage: 'No source core rows are available for this core size.',
        valueRenderer: (row) => `${formatPct(row.usage_pct)} · ${row.team_count} teams`,
      });
    }

    function renderObservedCombinations() {
      const rows = (snapshotData().observedCombinations || {})[state.comboType] || [];
      renderBarChart('combo-chart', rows, {
        valueKey: 'team_appearance_pct',
        fillClass: 'olive',
        emptyMessage: 'No observed combination rows are available for this view.',
        valueRenderer: (row) => `${formatPct(row.team_appearance_pct)} · ${row.team_appearances} teams · ${formatPct(row.win_rate)} WR`,
      });
    }

    function renderSetExplorer() {
      const setBreakdown = snapshotData().setBreakdown || {};
      const pokemon = state.pokemon;
      const details = setBreakdown[pokemon] || {};
      renderBarChart('item-chart', details.items || [], {
        valueKey: 'count',
        emptyMessage: 'No item data is available for this Pokemon.',
        valueRenderer: (row) => `${row.count} teams · ${formatPct(row.pct)}`,
      });
      renderBarChart('move-chart', details.moves || [], {
        valueKey: 'count',
        fillClass: 'secondary',
        emptyMessage: 'No move data is available for this Pokemon.',
        valueRenderer: (row) => `${row.count} uses · ${formatPct(row.pct)}`,
      });
      renderBarChart('ability-chart', details.abilities || [], {
        valueKey: 'count',
        fillClass: 'olive',
        emptyMessage: 'No ability data is available for this Pokemon.',
        valueRenderer: (row) => `${row.count} teams · ${formatPct(row.pct)}`,
      });
      renderBarChart('teammate-chart', details.teammates || [], {
        valueKey: 'count',
        fillClass: 'secondary',
        emptyMessage: 'No teammate data is available for this Pokemon.',
        valueRenderer: (row) => `${row.count} shared teams · ${formatPct(row.pct)}`,
      });
    }

    function syncPokemonSelect() {
      const setBreakdown = snapshotData().setBreakdown || {};
      const pokemonNames = Object.keys(setBreakdown).sort((left, right) => left.localeCompare(right));
      const select = document.getElementById('pokemon-select');
      const nextPokemon = pokemonNames.includes(state.pokemon) ? state.pokemon : (pokemonNames[0] || '');
      state.pokemon = nextPokemon;
      select.innerHTML = pokemonNames.map((pokemon) => `
        <option value="${escapeHtml(pokemon)}" ${pokemon === nextPokemon ? 'selected' : ''}>${escapeHtml(pokemon)}</option>
      `).join('');
      select.disabled = pokemonNames.length === 0;
    }

    function renderUsageChart() {
      renderBarChart('usage-chart', snapshotData().usage || [], {
        valueKey: 'usage_pct',
        emptyMessage: 'No usage rows are available for this snapshot.',
        labelRenderer: (row) => `#${row.rank} ${row.pokemon}`,
        valueRenderer: (row) => formatPct(row.usage_pct),
      });
    }

    function renderAll() {
      renderSummary();
      renderUsageChart();
      renderScatterPlot('performance-chart', snapshotData().teamPerformance || []);
      renderSourceCores();
      renderObservedCombinations();
      renderTimeline('timeline-chart');
      syncPokemonSelect();
      renderSetExplorer();
      renderTeamCards();
    }

    function renderPills(containerId, items, selectedValue, onClick) {
      document.getElementById(containerId).innerHTML = items.map((item) => `
        <button class="pill ${item.value === selectedValue ? 'active' : ''}" type="button" data-value="${item.value}">${escapeHtml(item.label)}</button>
      `).join('');
      document.querySelectorAll(`#${containerId} .pill`).forEach((button) => {
        button.addEventListener('click', () => onClick(button.dataset.value));
      });
    }

    function initControls() {
      const snapshotSelect = document.getElementById('snapshot-select');
      snapshotSelect.innerHTML = dashboardData.snapshots.map((snapshot) => `
        <option value="${snapshot}" ${snapshot === state.snapshot ? 'selected' : ''}>${snapshot}</option>
      `).join('');
      snapshotSelect.addEventListener('change', (event) => {
        state.snapshot = event.target.value;
        renderAll();
      });

      document.getElementById('pokemon-select').addEventListener('change', (event) => {
        state.pokemon = event.target.value;
        renderSetExplorer();
      });

      renderPills('core-filter', [
        { value: '2', label: '2-Pokemon Cores' },
        { value: '3', label: '3-Pokemon Cores' },
        { value: '4', label: '4-Pokemon Cores' },
      ], state.coreSize, (value) => {
        state.coreSize = value;
        renderPills('core-filter', [
          { value: '2', label: '2-Pokemon Cores' },
          { value: '3', label: '3-Pokemon Cores' },
          { value: '4', label: '4-Pokemon Cores' },
        ], state.coreSize, arguments.callee);
        renderSourceCores();
      });

      renderPills('combo-filter', [
        { value: 'pairing', label: 'Pairings' },
        { value: 'trio', label: 'Trios' },
        { value: 'full_team', label: 'Full Teams' },
      ], state.comboType, (value) => {
        state.comboType = value;
        renderPills('combo-filter', [
          { value: 'pairing', label: 'Pairings' },
          { value: 'trio', label: 'Trios' },
          { value: 'full_team', label: 'Full Teams' },
        ], state.comboType, arguments.callee);
        renderObservedCombinations();
      });
    }

    initControls();
    renderAll();
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    main()