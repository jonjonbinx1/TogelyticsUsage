#!/usr/bin/env python3
"""
Pokemon Usage Stats Validator

Compares extracted data (from extractor.py's results JSON) against the
ground truth data in the CSV files, and reports per-field and overall accuracy.

Usage:
    # Validate a specific results file:
    python validate.py results/20260427doubles_extracted.json

    # Validate with an explicit CSV path:
    python validate.py results/20260427singles_extracted.json --csv champions_singles.csv

    # Show summary only (no per-Pokemon detail):
    python validate.py results/20260427doubles_extracted.json --summary

    # Output a machine-readable accuracy JSON:
    python validate.py results/20260427doubles_extracted.json --json-out results/accuracy_20260427doubles.json
"""

import csv
import json
import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent

DEFAULT_CSV = {
    "doubles": SCRIPT_DIR / "champions_doubles.csv",
    "singles": SCRIPT_DIR / "champions_singles.csv",
}

# Fields to evaluate and their display names
EVAL_FIELDS = [
    ("pokemon",        "Pokemon Name",      "name"),
    ("usage",          "Usage Rank",        "exact"),
    ("moves",          "Moves",             "set"),
    ("move_usage",     "Move Usage %",      "numeric"),
    ("abilities",      "Abilities",         "set"),
    ("ability_usage",  "Ability Usage %",   "numeric"),
    ("spreads",        "EV Spreads",        "set"),
    ("spread_usage",   "Spread Usage %",    "numeric"),
    ("natures",        "Natures",           "set"),
    ("nature_usage",   "Nature Usage %",    "numeric"),
    ("items",          "Items",             "set"),
    ("item_usage",     "Item Usage %",      "numeric"),
]

# Tolerance for numeric comparisons (percentage points)
NUMERIC_TOLERANCE = 2.0


# ---------------------------------------------------------------------------
# CSV loading
# ---------------------------------------------------------------------------
def load_ground_truth(csv_path: Path, date_str: str) -> dict:
    """
    Load CSV rows for a given date, keyed by lowercase pokemon name.
    Returns {} if date not found or CSV does not exist.
    """
    if not csv_path.exists():
        return {}

    result = {}
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("date", "").strip() == date_str:
                key = row.get("pokemon", "").lower().strip()
                if key:
                    result[key] = row
    return result


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------
def parse_str_list(value: str) -> list:
    """Split colon-separated string into a list of lowercase strings."""
    if not value or not value.strip():
        return []
    return [v.strip().lower() for v in value.split(":") if v.strip()]


def parse_float_list(value: str) -> list:
    """Split colon-separated string into a list of floats."""
    if not value or not value.strip():
        return []
    result = []
    for v in value.split(":"):
        v = v.strip()
        try:
            result.append(float(v))
        except ValueError:
            pass
    return result


# ---------------------------------------------------------------------------
# Similarity metrics
# ---------------------------------------------------------------------------
def jaccard(a: list, b: list) -> float:
    """Set overlap (Jaccard similarity): |A∩B| / |A∪B|."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    sa = {x.lower() for x in a}
    sb = {x.lower() for x in b}
    inter = sa & sb
    union = sa | sb
    return len(inter) / len(union) if union else 1.0


def numeric_agreement(a: list, b: list, tol: float = NUMERIC_TOLERANCE) -> float:
    """
    Fraction of paired values (by index) that agree within tolerance.
    If lists differ in length, unmatched positions count as misses.
    """
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    total = max(len(a), len(b))
    matches = sum(
        1 for i in range(min(len(a), len(b))) if abs(a[i] - b[i]) <= tol
    )
    return matches / total


def exact_match(a: str, b: str) -> float:
    return 1.0 if a.lower().strip() == b.lower().strip() else 0.0


# ---------------------------------------------------------------------------
# Per-entry comparison
# ---------------------------------------------------------------------------
def compare(extracted: dict, ground_truth: dict) -> dict:
    """
    Compare extracted data against one ground-truth CSV row.
    Returns dict of field → score (0.0 – 1.0).
    """
    scores = {}

    # Map extracted field names → CSV field names
    field_map = {
        "pokemon":       ("pokemon",        "name"),
        "usage":         ("usage",          "exact"),
        "moves":         ("moves",          "set"),
        "move_usage":    ("move usage",     "numeric"),
        "abilities":     ("ability",        "set"),
        "ability_usage": ("ability usage",  "numeric"),
        "spreads":       ("sp",             "set"),
        "spread_usage":  ("sp usage",       "numeric"),
        "natures":       ("nature",         "set"),
        "nature_usage":  ("nature usage",   "numeric"),
        "items":         ("item",           "set"),
        "item_usage":    ("item usage",     "numeric"),
    }

    for ext_key, (csv_key, cmp_type) in field_map.items():
        ext_val = extracted.get(ext_key)
        gt_val  = ground_truth.get(csv_key, "")

        if cmp_type == "name":
            scores[ext_key] = exact_match(str(ext_val or ""), str(gt_val))

        elif cmp_type == "exact":
            scores[ext_key] = exact_match(str(ext_val or ""), str(gt_val))

        elif cmp_type == "set":
            ext_list = [x.lower() for x in (ext_val or [])]
            gt_list  = parse_str_list(gt_val)
            scores[ext_key] = jaccard(ext_list, gt_list)

        elif cmp_type == "numeric":
            ext_list = [float(x) for x in (ext_val or [])]
            gt_list  = parse_float_list(gt_val)
            scores[ext_key] = numeric_agreement(ext_list, gt_list)

    return scores


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------
BAR_WIDTH = 12


def score_bar(score: float) -> str:
    filled = round(score * BAR_WIDTH)
    return "█" * filled + "░" * (BAR_WIDTH - filled)


def pct(score: float) -> str:
    return f"{score * 100:5.1f}%"


def _diff_line(label: str, extracted, gt_raw: str, join_char: str = ", ") -> str:
    if isinstance(extracted, list):
        ext_str = join_char.join(str(x) for x in extracted)
    else:
        ext_str = str(extracted or "")
    gt_str = gt_raw.replace(":", join_char) if gt_raw else ""
    return f"    EXTRACTED : {ext_str}\n    EXPECTED  : {gt_str}"


def print_pokemon_report(pokemon: str, scores: dict, extracted: dict, gt: dict) -> None:
    overall = sum(scores.values()) / len(scores) if scores else 0.0
    status = "✓" if overall >= 0.9 else ("~" if overall >= 0.6 else "✗")
    print(f"\n  {status} {pokemon.upper():<22} overall {pct(overall)}")
    print(f"  {'─' * 58}")

    show_diffs = overall < 1.0

    detail_map = {
        "pokemon":       ("pokemon",        extracted.get("pokemon"),     gt.get("pokemon", "")),
        "usage":         ("usage",          extracted.get("usage"),       gt.get("usage", "")),
        "moves":         ("moves",          extracted.get("moves"),       gt.get("moves", "")),
        "move_usage":    ("move usage %",   extracted.get("move_usage"),  gt.get("move usage", "")),
        "abilities":     ("abilities",      extracted.get("abilities"),   gt.get("ability", "")),
        "ability_usage": ("ability usage %",extracted.get("ability_usage"),gt.get("ability usage","")),
        "spreads":       ("spreads",        extracted.get("spreads"),     gt.get("sp", "")),
        "spread_usage":  ("spread usage %", extracted.get("spread_usage"),gt.get("sp usage", "")),
        "natures":       ("natures",        extracted.get("natures"),     gt.get("nature", "")),
        "nature_usage":  ("nature usage %", extracted.get("nature_usage"),gt.get("nature usage","")),
        "items":         ("items",          extracted.get("items"),       gt.get("item", "")),
        "item_usage":    ("item usage %",   extracted.get("item_usage"),  gt.get("item usage", "")),
    }

    for ext_key, (label, ext_val, gt_raw) in detail_map.items():
        score = scores.get(ext_key, 0.0)
        bar = score_bar(score)
        print(f"    {label:<22} [{bar}] {pct(score)}")
        if show_diffs and score < 0.95:
            print(_diff_line(label, ext_val, gt_raw))


def print_summary_table(field_averages: dict, matched_count: int, total_extracted: int,
                        not_in_gt: list, not_extracted: list) -> None:
    print("\n" + "=" * 64)
    print("  SUMMARY")
    print("=" * 64)
    print(f"  Pokemon matched to ground truth : {matched_count} / {total_extracted}")
    if not_in_gt:
        print(f"  Not in ground truth (new?)      : {', '.join(not_in_gt)}")
    if not_extracted:
        print(f"  In ground truth but not found   : {', '.join(not_extracted)}")

    print(f"\n  {'Field':<24} {'Score':>10}  Bar")
    print(f"  {'─'*24} {'─'*10}  {'─'*BAR_WIDTH}")

    overall_sum = 0.0
    for ext_key, label, _ in EVAL_FIELDS:
        avg = field_averages.get(ext_key, 0.0)
        overall_sum += avg
        print(f"  {label:<24} {pct(avg):>10}  {score_bar(avg)}")

    grand = overall_sum / len(EVAL_FIELDS) if EVAL_FIELDS else 0.0
    print(f"  {'─'*24} {'─'*10}  {'─'*BAR_WIDTH}")
    print(f"  {'OVERALL':<24} {pct(grand):>10}  {score_bar(grand)}")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare extracted Pokemon stats against CSV ground truth",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "results_json",
        help="Path to extracted results JSON produced by extractor.py",
    )
    parser.add_argument(
        "--csv",
        help="Path to ground-truth CSV (auto-detected from format in JSON if omitted)",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print summary table only, skip per-Pokemon detail",
    )
    parser.add_argument(
        "--json-out",
        metavar="PATH",
        help="Write accuracy results to this JSON file",
    )
    args = parser.parse_args()

    results_path = Path(args.results_json)
    if not results_path.exists():
        print(f"Error: Results file not found: {results_path}", file=sys.stderr)
        sys.exit(1)

    with open(results_path, encoding="utf-8") as f:
        results = json.load(f)

    date_str     = results.get("date", "")
    fmt_type     = results.get("format", "")
    extracted_list = results.get("extracted", [])

    if not date_str or not fmt_type:
        print("Error: Results JSON is missing 'date' or 'format' keys.", file=sys.stderr)
        sys.exit(1)

    # Resolve CSV
    csv_path = Path(args.csv).resolve() if args.csv else DEFAULT_CSV.get(fmt_type)
    if not csv_path:
        print(f"Error: Cannot determine CSV for format '{fmt_type}'.", file=sys.stderr)
        sys.exit(1)

    # Load ground truth
    ground_truth = load_ground_truth(csv_path, date_str)

    print(f"\n{'═'*64}")
    print(f"  VALIDATION REPORT")
    print(f"  Format    : {fmt_type.upper()}")
    print(f"  Date      : {date_str}")
    print(f"  CSV       : {csv_path.name}")
    print(f"  Extracted : {len(extracted_list)} images processed")
    print(f"  GT rows   : {len(ground_truth)} Pokemon in CSV for this date")
    print(f"{'═'*64}")

    if not ground_truth:
        print(f"\n  No ground truth data found for {date_str} in {csv_path.name}")
        print("  Cannot validate — run extractor.py --apply to populate the CSV first,")
        print("  or provide a CSV that already has data for this date.")
        sys.exit(0)

    # Check for any empty ground truth rows (placeholder rows with no stats)
    empty_gt = [p for p, row in ground_truth.items()
                if not row.get("moves", "").strip()]
    if empty_gt:
        print(f"\n  NOTE: {len(empty_gt)} ground truth rows have no stats (placeholder only):")
        print(f"  {', '.join(empty_gt)}")
        print("  These will be skipped in validation.\n")
        ground_truth = {p: r for p, r in ground_truth.items() if p not in empty_gt}

    if not ground_truth:
        print("  All ground truth rows are empty placeholders — nothing to validate.")
        sys.exit(0)

    # Match extracted entries to ground truth
    all_scores = {}
    not_in_gt = []

    # Build set of extracted Pokemon names for "not extracted" check
    extracted_pokemon = {}
    for entry in extracted_list:
        data = entry.get("data", {})
        name = data.get("pokemon", "").lower().strip()
        if name:
            extracted_pokemon[name] = data

    for name, data in extracted_pokemon.items():
        if name in ground_truth:
            scores = compare(data, ground_truth[name])
            all_scores[name] = scores
            if not args.summary:
                print_pokemon_report(name, scores, data, ground_truth[name])
        else:
            not_in_gt.append(name)

    not_extracted = [p for p in ground_truth if p not in extracted_pokemon]

    # Compute per-field averages
    field_averages = {}
    if all_scores:
        for ext_key, _, _ in EVAL_FIELDS:
            vals = [s.get(ext_key, 0.0) for s in all_scores.values()]
            field_averages[ext_key] = sum(vals) / len(vals) if vals else 0.0

    print_summary_table(
        field_averages,
        matched_count=len(all_scores),
        total_extracted=len(extracted_pokemon),
        not_in_gt=not_in_gt,
        not_extracted=not_extracted,
    )

    # Optional JSON output
    if args.json_out:
        out_data = {
            "date":          date_str,
            "format":        fmt_type,
            "matched":       len(all_scores),
            "total_in_gt":   len(ground_truth),
            "field_averages": field_averages,
            "grand_average": sum(field_averages.values()) / len(field_averages) if field_averages else 0.0,
            "per_pokemon":   {
                pokemon: {
                    "scores": scores,
                    "overall": sum(scores.values()) / len(scores) if scores else 0.0,
                }
                for pokemon, scores in all_scores.items()
            },
            "not_in_gt":       not_in_gt,
            "not_extracted":   not_extracted,
        }
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(out_data, f, indent=2, ensure_ascii=False)
        print(f"  Accuracy JSON saved → {out_path}")


if __name__ == "__main__":
    main()
