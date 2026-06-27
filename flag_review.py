#!/usr/bin/env python3
"""
Pokemon Extraction Flag Reviewer

Post-processes the results JSON from extractor.py and validates each per-image
extracted value against known Pokemon game data (fetched from PokeAPI and cached).

Validation rules applied to every image:
  - Pokemon name   → fuzzy match vs. PokeAPI Pokedex     (flag if score < threshold)
  - Move names     → fuzzy match vs. PokeAPI moves        (flag if score < threshold)
  - Ability names  → fuzzy match vs. PokeAPI abilities    (flag if score < threshold)
  - Item names     → fuzzy match vs. PokeAPI items        (flag if score < threshold)
  - Nature names   → exact match vs. the 25 standard natures
  - Stat spreads   → each spread's stat values must sum to 66
  - Stat abbrevs   → only HP / Atk / Def / SpA / SpD / Spe allowed

Anything that fails is written to results/flagged_<foldername>.json
for manual review and correction.

Usage:
    python flag_review.py results/20260503singles_extracted.json
    python flag_review.py results/20260503doubles_extracted.json --threshold 85
    python flag_review.py results/20260503singles_extracted.json --refresh-cache
    python flag_review.py results/20260503singles_extracted.json --out my_flags.json
"""

import json
import re
import sys
import argparse
import logging
from pathlib import Path
from datetime import datetime, timedelta

import requests

try:
    from rapidfuzz import process as fz_process, fuzz as fz_fuzz
    HAS_RAPIDFUZZ = True
except ImportError:
    HAS_RAPIDFUZZ = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SCRIPT_DIR   = Path(__file__).parent
CACHE_FILE   = SCRIPT_DIR / "data" / "pokeapi_cache.json"
RESULTS_DIR  = SCRIPT_DIR / "results"
POKEAPI_BASE = "https://pokeapi.co/api/v2"
POKEAPI_LIMIT = 10000
CACHE_MAX_AGE_DAYS = 30

EXPECTED_SP_SUM = 66  # Total stat points in Pokemon HOME's "Stat Points" screen

VALID_STATS = {"HP", "Atk", "Def", "SpA", "SpD", "Spe"}

# All 25 natures — these never change, no API needed
VALID_NATURES = {
    "hardy", "lonely", "brave", "adamant", "naughty",
    "bold", "docile", "relaxed", "impish", "lax",
    "timid", "hasty", "serious", "jolly", "naive",
    "modest", "mild", "quiet", "bashful", "rash",
    "calm", "gentle", "sassy", "careful", "quirky",
}


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        level=logging.DEBUG if verbose else logging.INFO,
    )


# ---------------------------------------------------------------------------
# Name normalisation
# ---------------------------------------------------------------------------
def normalize(name: str) -> str:
    """Lowercase, drop apostrophes, replace hyphens/underscores with spaces."""
    name = name.lower()
    name = name.replace("'", "").replace("\u2019", "")   # straight + curly apostrophe
    name = re.sub(r"[-_]", " ", name)
    name = re.sub(r"\s+", " ", name)
    return name.strip()


# ---------------------------------------------------------------------------
# PokeAPI fetch + cache
# ---------------------------------------------------------------------------
def _fetch_resource_names(endpoint: str) -> list:
    logger = logging.getLogger(__name__)
    url = f"{POKEAPI_BASE}/{endpoint}?limit={POKEAPI_LIMIT}&offset=0"
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        names = [r["name"] for r in resp.json().get("results", [])]
        logger.info(f"  Fetched {len(names):>6} {endpoint} names from PokeAPI")
        return names
    except requests.exceptions.RequestException as e:
        logger.warning(f"  Failed to fetch '{endpoint}' from PokeAPI: {e}")
        return []


def load_or_fetch_cache(refresh: bool = False) -> dict:
    """Load PokeAPI name lists from local cache; fetch if missing, stale, or refresh=True."""
    logger = logging.getLogger(__name__)

    if not refresh and CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, encoding="utf-8") as f:
                cache = json.load(f)
            fetched_at = datetime.fromisoformat(cache.get("fetched_at", "2000-01-01"))
            age = datetime.now() - fetched_at
            if age < timedelta(days=CACHE_MAX_AGE_DAYS):
                logger.info(f"Using cached PokeAPI lookup snapshot ({age.days}d old)")
                logger.debug(f"  Cache snapshot fetched on {fetched_at.date()}")
                return cache
            logger.info(f"Cache is {age.days} days old — refreshing")
        except (json.JSONDecodeError, KeyError, ValueError):
            logger.warning("Cache file is corrupt — re-fetching")

    logger.info("Fetching name lists from PokeAPI (may take a moment)…")
    cache = {
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "pokemon":    _fetch_resource_names("pokemon"),
        "moves":      _fetch_resource_names("move"),
        "abilities":  _fetch_resource_names("ability"),
        "items":      _fetch_resource_names("item"),
    }

    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)
    logger.info(f"Cache saved → {CACHE_FILE}")
    return cache


def build_lookup(raw_names: list) -> dict:
    """Map normalized-name → original PokeAPI name for O(1) lookup."""
    return {normalize(n): n for n in raw_names}


# ---------------------------------------------------------------------------
# Fuzzy matching
# ---------------------------------------------------------------------------
def fuzzy_check(extracted: str, lookup: dict, threshold: float) -> tuple:
    """
    Check extracted string against a lookup dict (normalized→original).
    Returns (best_original_match, score, is_valid).
    is_valid = True if normalized exact hit OR fuzzy score >= threshold.
    """
    if not extracted:
        return ("", 0.0, False)

    norm = normalize(extracted)

    # Exact hit after normalization
    if norm in lookup:
        return (lookup[norm], 100.0, True)

    if not HAS_RAPIDFUZZ:
        return ("(install rapidfuzz for fuzzy matching)", 0.0, False)

    result = fz_process.extractOne(
        norm, list(lookup.keys()), scorer=fz_fuzz.WRatio, score_cutoff=0
    )
    if result is None:
        return ("", 0.0, False)

    best_norm, score, _ = result
    best_original = lookup.get(best_norm, best_norm)
    return (best_original, round(float(score), 1), float(score) >= threshold)


def fuzzy_check_nature(extracted: str) -> tuple:
    """Check a nature name against the fixed VALID_NATURES set."""
    norm = normalize(extracted)
    if norm in VALID_NATURES:
        return (norm, 100.0, True)
    if not HAS_RAPIDFUZZ:
        return ("(install rapidfuzz)", 0.0, False)
    result = fz_process.extractOne(
        norm, list(VALID_NATURES), scorer=fz_fuzz.WRatio, score_cutoff=0
    )
    if result is None:
        return ("", 0.0, False)
    best, score, _ = result
    return (best, round(float(score), 1), False)   # always flag unknown natures


# ---------------------------------------------------------------------------
# Stat spread validation
# ---------------------------------------------------------------------------
STAT_CASE_MAP = {
    "hp": "HP", "atk": "Atk", "def": "Def",
    "spa": "SpA", "spd": "SpD", "spe": "Spe",
}


def parse_spread(spread: str) -> dict:
    """'2 HP / 32 Atk / 32 Spe' → {'HP': 2, 'Atk': 32, 'Spe': 32}.
    Also normalises stat abbreviation case so 'hp', 'spa' etc. are accepted."""
    result = {}
    for part in spread.split("/"):
        part = part.strip()
        m = re.match(r"^(\d+)\s+([A-Za-z]+)$", part)
        if m:
            stat = STAT_CASE_MAP.get(m.group(2).lower(), m.group(2))
            result[stat] = int(m.group(1))
    return result


def check_spread(spread: str) -> list:
    """
    Validate one spread string.  Returns list of (issue, note) tuples.
    """
    issues = []
    parts = parse_spread(spread)

    if not parts:
        issues.append(("unparseable_spread", f"Could not parse spread: {spread!r}"))
        return issues

    # Unknown stat abbreviations
    for stat in parts:
        if stat not in VALID_STATS:
            issues.append((
                "unknown_stat_abbreviation",
                f"'{stat}' is not a valid stat (HP/Atk/Def/SpA/SpD/Spe) in spread {spread!r}"
            ))

    # Sum check — only run if all stats are known
    unknown_stats = [s for s in parts if s not in VALID_STATS]
    if not unknown_stats:
        total = sum(parts.values())
        if total != EXPECTED_SP_SUM:
            issues.append((
                "spread_sum_mismatch",
                f"Values {dict(parts)} sum to {total}, expected {EXPECTED_SP_SUM}"
            ))

    return issues


# ---------------------------------------------------------------------------
# Per-image validation
# ---------------------------------------------------------------------------
def validate_entry(entry: dict, lookups: dict, threshold: float) -> list:
    """
    Validate a single per-image extracted entry.
    Validates ALL non-empty fields regardless of screen_type, so the results
    are robust even when screen_type is misidentified.
    Returns list of flag dicts (empty = no issues).
    """
    flags = []
    data    = entry.get("data", {})
    image   = entry.get("image", "?")
    screen  = data.get("screen_type", "unknown")
    pokemon = data.get("pokemon", "")
    usage   = data.get("usage", "?")

    def _flag(field, extracted, issue, note, best_match="", score=0.0):
        flags.append({
            "image":       image,
            "screen_type": screen,
            "pokemon":     pokemon,
            "usage":       usage,
            "field":       field,
            "extracted":   extracted,
            "issue":       issue,
            "best_match":  best_match,
            "score":       score,
            "note":        note,
        })

    # --- Pokemon name ---
    if pokemon:
        best, score, valid = fuzzy_check(pokemon, lookups["pokemon"], threshold)
        if not valid:
            _flag(
                field="pokemon", extracted=pokemon,
                issue="low_confidence_name",
                best_match=best, score=score,
                note=f"Score {score:.0f} < {threshold}; suggested: '{best}'"
            )

    # --- Moves ---
    for move in data.get("moves", []):
        best, score, valid = fuzzy_check(move, lookups["moves"], threshold)
        if not valid:
            _flag(
                field=f"moves['{move}']", extracted=move,
                issue="unknown_move",
                best_match=best, score=score,
                note=f"Score {score:.0f}; suggested: '{best}'"
            )

    # --- Abilities ---
    for ability in data.get("abilities", []):
        best, score, valid = fuzzy_check(ability, lookups["abilities"], threshold)
        if not valid:
            _flag(
                field=f"abilities['{ability}']", extracted=ability,
                issue="unknown_ability",
                best_match=best, score=score,
                note=f"Score {score:.0f}; suggested: '{best}'"
            )

    # --- Items ---
    for item in data.get("items", []):
        best, score, valid = fuzzy_check(item, lookups["items"], threshold)
        if not valid:
            _flag(
                field=f"items['{item}']", extracted=item,
                issue="unknown_item",
                best_match=best, score=score,
                note=f"Score {score:.0f}; suggested: '{best}'"
            )

    # --- Natures ---
    for nature in data.get("natures", []):
        best, score, valid = fuzzy_check_nature(nature)
        if not valid:
            _flag(
                field=f"natures['{nature}']", extracted=nature,
                issue="unknown_nature",
                best_match=best, score=score,
                note=f"'{nature}' not in standard 25 natures; suggested: '{best}'"
            )

    # --- Stat spreads (legacy named strings) ---
    for i, spread in enumerate(data.get("spreads", [])):
        for issue_type, note in check_spread(spread):
            _flag(
                field=f"spreads[{i}]", extracted=spread,
                issue=issue_type, note=note,
            )

    # --- Stat spread values (raw numeric lists, new 7-element format) ---
    for i, row in enumerate(data.get("spread_values", [])):
        expected_rank = i + 1

        if not isinstance(row, list) or len(row) not in (6, 7):
            _flag(
                field=f"spread_values[{i}]", extracted=str(row),
                issue="spread_wrong_length",
                note=f"Expected 6 or 7 integers, got {len(row) if isinstance(row, list) else type(row).__name__}: {row}",
            )
            continue

        if len(row) == 7:
            row_rank = row[0]
            stat_vals = row[1:]
            if row_rank != expected_rank:
                _flag(
                    field=f"spread_values[{i}][rank]", extracted=str(row_rank),
                    issue="spread_rank_mismatch",
                    note=f"Row {expected_rank}: rank element is {row_rank}, expected {expected_rank}. "
                         "Row rank may have been included as a stat value.",
                )
        else:
            # 6-element legacy; no rank to check
            stat_vals = row

        try:
            total = sum(int(v) for v in stat_vals)
        except (TypeError, ValueError):
            _flag(
                field=f"spread_values[{i}]", extracted=str(row),
                issue="spread_non_integer",
                note=f"Could not convert all values to int: {stat_vals}",
            )
            continue

        if total != EXPECTED_SP_SUM:
            _flag(
                field=f"spread_values[{i}]", extracted=str(stat_vals),
                issue="spread_sum_mismatch",
                note=f"Stat values {list(stat_vals)} sum to {total}, expected {EXPECTED_SP_SUM}",
            )

    return flags


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def print_report(flags: list, folder: str, date_str: str, fmt_type: str,
                 total_images: int, screen_counts: dict, out_path: Path) -> None:
    SEP = "═" * 64
    sub = "─" * 64

    print(f"\n{SEP}")
    print(f"  FLAG REVIEW  —  {folder}  ({fmt_type.upper()}, {date_str})")
    print(SEP)
    print(f"  Images validated : {total_images}")
    print(f"  Issues flagged   : {len(flags)}")
    print(f"\n  Screen types seen:")
    for screen, count in sorted(screen_counts.items()):
        print(f"    {screen:<25} {count:>3} images")
    print(sub)

    if not flags:
        print("  ✓ All extracted data looks valid — nothing to review!")
    else:
        # Summary by issue type
        by_issue: dict = {}
        for f in flags:
            by_issue.setdefault(f["issue"], []).append(f)
        print(f"\n  Issue breakdown:")
        for issue, fs in sorted(by_issue.items()):
            print(f"    {issue:<35} {len(fs):>3}x")

        # Per-image detail
        by_image: dict = {}
        for f in flags:
            by_image.setdefault(f["image"], []).append(f)

        print(f"\n  Detail by image:")
        print(sub)
        for img in sorted(by_image, key=lambda n: int(re.search(r"\d+", n).group()
                                                       if re.search(r"\d+", n) else 0)):
            img_flags = by_image[img]
            # Use first flag for Pokemon/screen context
            ctx = img_flags[0]
            print(f"\n  {img}  [{ctx['screen_type']}]  Pokemon: {ctx['pokemon']}  rank={ctx['usage']}")
            for f in img_flags:
                match_hint = (
                    f"  →  '{f['best_match']}' ({f['score']:.0f}%)"
                    if f.get("best_match") else ""
                )
                print(f"    ⚠  [{f['issue']}]  {f['field']} = {f['extracted']!r}{match_hint}")
                print(f"       {f['note']}")

    print(f"\n{sub}")
    print(f"  Flagged JSON → {out_path}")
    print(f"{SEP}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate extracted Pokemon stats; flag unknown names / bad spreads",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "results_json",
        help="Path to extracted JSON produced by extractor.py",
    )
    parser.add_argument(
        "--threshold", type=float, default=80.0, metavar="N",
        help="Fuzzy match score 0-100; values below this are flagged (default: 80)",
    )
    parser.add_argument(
        "--refresh-cache", action="store_true",
        help="Force re-fetch of PokeAPI data even if local cache is fresh",
    )
    parser.add_argument(
        "--out", metavar="PATH",
        help="Override output path (default: results/flagged_<foldername>.json)",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    setup_logging(args.verbose)
    logger = logging.getLogger(__name__)

    if not HAS_RAPIDFUZZ:
        logger.warning(
            "rapidfuzz not installed — only exact-match and nature/spread checks will run. "
            "Install with:  pip install rapidfuzz"
        )

    # ---- Load results JSON ----
    results_path = Path(args.results_json)
    if not results_path.exists():
        logger.error(f"Results file not found: {results_path}")
        sys.exit(1)

    with open(results_path, encoding="utf-8") as f:
        results = json.load(f)

    folder    = Path(results.get("folder", "")).name or results_path.stem.replace("_extracted", "")
    date_str  = results.get("date", "?")
    fmt_type  = results.get("format", "?")

    # Prefer autocorrected per-image results when available, while keeping the
    # original raw payload for audit/debugging.
    per_image_list = (
        results.get("autocorrected_extracted")
        or results.get("raw_extracted")
        or results.get("extracted", [])
    )
    logger.info(
        f"Validating {len(per_image_list)} per-image entries from '{folder}' "
        f"({date_str}, {fmt_type})"
    )
    logger.info(f"Fuzzy threshold: {args.threshold}")

    # ---- Load PokeAPI name data ----
    cache = load_or_fetch_cache(refresh=args.refresh_cache)
    lookups = {
        "pokemon":    build_lookup(cache.get("pokemon",   [])),
        "moves":      build_lookup(cache.get("moves",     [])),
        "abilities":  build_lookup(cache.get("abilities", [])),
        "items":      build_lookup(cache.get("items",     [])),
    }
    logger.info(
        f"Lookups: {len(lookups['pokemon'])} Pokemon  |  {len(lookups['moves'])} moves  |"
        f"  {len(lookups['abilities'])} abilities  |  {len(lookups['items'])} items"
    )

    # ---- Validate each image entry ----
    all_flags: list = []
    screen_counts: dict = {}

    for entry in per_image_list:
        screen = entry.get("data", {}).get("screen_type", "unknown")
        screen_counts[screen] = screen_counts.get(screen, 0) + 1
        flags = validate_entry(entry, lookups, args.threshold)
        all_flags.extend(flags)

    # ---- Output path ----
    if args.out:
        out_path = Path(args.out)
    else:
        RESULTS_DIR.mkdir(exist_ok=True)
        out_path = RESULTS_DIR / f"flagged_{folder}.json"

    flagged_output = {
        "source_results":    str(results_path),
        "date":              date_str,
        "format":            fmt_type,
        "threshold":         args.threshold,
        "validated_at":      datetime.now().isoformat(timespec="seconds"),
        "total_images":      len(per_image_list),
        "screen_type_counts": screen_counts,
        "flagged_count":     len(all_flags),
        "flags":             all_flags,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(flagged_output, f, indent=2, ensure_ascii=False)

    # ---- Print report ----
    print_report(
        all_flags, folder, date_str, fmt_type,
        total_images=len(per_image_list),
        screen_counts=screen_counts,
        out_path=out_path,
    )


if __name__ == "__main__":
    main()
