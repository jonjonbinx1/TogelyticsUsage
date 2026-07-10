#!/usr/bin/env python3
"""
Unit tests for stat spread extraction logic.

Tests the Python-side parsing of spread_values from the vision model response,
covering:
  - _values_to_spread  (7-element new format, 6-element legacy, edge cases)
  - format_for_csv     (spread_values → CSV 'sp' column)
    - autocorrect_pokemon_names (repeated low-confidence OCR name fixes)
  - flag_review spread_values validation  (rank check, sum check)

Ground truth for the assertions is read directly from the stat-points images
stored in data/singles/20260503singles and manually verified against the screen.

Run with:
    python test_stat_spreads.py
"""

import sys
import traceback
from copy import deepcopy
from pathlib import Path

# ---------------------------------------------------------------------------
# Make the project root importable
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from extractor import (
    CORE_FIELDS,
    _values_to_spread,
    _coerce_usage_rank,
    autocorrect_pokemon_names,
    build_known_pokemon_names,
    build_historical_spread_reference,
    build_prefilled_name_index,
    format_for_csv,
    merge_duplicate_csv_rows,
    merge_ranked_extractions,
    rectify_existing_rows_from_prefill,
    rectify_pokemon_names_from_prefill,
    sanitize_extracted_entry,
)
from flag_review import validate_entry, EXPECTED_SP_SUM

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"

_results = []


def check(name: str, got, expected):
    ok = got == expected
    status = PASS if ok else FAIL
    print(f"  [{status}] {name}")
    if not ok:
        print(f"         got      : {got!r}")
        print(f"         expected : {expected!r}")
    _results.append(ok)


def section(title: str):
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")


def csv_row(date: str, pokemon: str, usage, **fields):
    row = {field: "" for field in CORE_FIELDS}
    row["date"] = date
    row["pokemon"] = pokemon
    row["usage"] = str(usage)
    row.update(fields)
    return row


# ===========================================================================
# 1. _values_to_spread
# ===========================================================================
section("_values_to_spread — 7-element new format (row rank stripped)")

# Prompt examples
check("example row 1",  _values_to_spread([1,  2, 32,  0,  0,  0, 32]), "2 HP / 32 Atk / 32 Spe")
check("example row 2",  _values_to_spread([2,  0, 32,  2,  0,  0, 32]), "32 Atk / 2 Def / 32 Spe")
check("example row 3",  _values_to_spread([3,  0, 32,  1,  0,  1, 32]), "32 Atk / 1 Def / 1 SpD / 32 Spe")
check("example row 4",  _values_to_spread([4,  0, 32,  0,  0,  2, 32]), "32 Atk / 2 SpD / 32 Spe")
check("example row 5",  _values_to_spread([5, 32,  0, 11,  0, 12, 11]), "32 HP / 11 Def / 12 SpD / 11 Spe")

# Ground truth from image5.jpeg (Aegislash #9, 2026-05-03)
section("_values_to_spread — Aegislash image5.jpeg ground truth")
check("aegislash row 1 (15.6%)", _values_to_spread([1, 32, 32,  1,  0,  1,  0]), "32 HP / 32 Atk / 1 Def / 1 SpD")
check("aegislash row 2  (9.7%)", _values_to_spread([2, 32, 32,  2,  0,  0,  0]), "32 HP / 32 Atk / 2 Def")
check("aegislash row 3  (7.2%)", _values_to_spread([3, 32, 32,  0,  0,  0,  2]), "32 HP / 32 Atk / 2 Spe")
check("aegislash row 4  (5.5%)", _values_to_spread([4, 32, 32,  0,  0,  2,  0]), "32 HP / 32 Atk / 2 SpD")
check("aegislash row 5  (4.9%)", _values_to_spread([5,  2, 32,  0,  0,  0, 32]), "2 HP / 32 Atk / 32 Spe")

# Ground truth from image12.jpeg (Corviknight #5, 2026-05-03)
section("_values_to_spread — Corviknight image12.jpeg ground truth")
check("corviknight row 1 (45.5%)", _values_to_spread([1, 32,  0, 32,  0,  2,  0]), "32 HP / 32 Def / 2 SpD")
check("corviknight row 2 (11.2%)", _values_to_spread([2, 32,  0, 32,  0,  0,  2]), "32 HP / 32 Def / 2 Spe")
check("corviknight row 3  (4.3%)", _values_to_spread([3, 32,  0,  2,  0, 32,  0]), "32 HP / 2 Def / 32 SpD")
check("corviknight row 4  (2.8%)", _values_to_spread([4, 32,  2, 32,  0,  0,  0]), "32 HP / 2 Atk / 32 Def")
check("corviknight row 5  (2.6%)", _values_to_spread([5, 32,  0, 25,  0,  9,  0]), "32 HP / 25 Def / 9 SpD")

# Ground truth from image21.jpeg (Garchomp #1, 2026-05-03)
section("_values_to_spread — Garchomp image21.jpeg ground truth")
check("garchomp row 1 (55.6%)", _values_to_spread([1,  2, 32,  0,  0,  0, 32]), "2 HP / 32 Atk / 32 Spe")
check("garchomp row 2  (7.4%)", _values_to_spread([2,  0, 32,  2,  0,  0, 32]), "32 Atk / 2 Def / 32 Spe")
check("garchomp row 3  (5.4%)", _values_to_spread([3,  0, 32,  1,  0,  1, 32]), "32 Atk / 1 Def / 1 SpD / 32 Spe")
check("garchomp row 4  (4.2%)", _values_to_spread([4,  0, 32,  0,  0,  2, 32]), "32 Atk / 2 SpD / 32 Spe")
check("garchomp row 5  (2.0%)", _values_to_spread([5, 32,  0, 11,  0, 12, 11]), "32 HP / 11 Def / 12 SpD / 11 Spe")

# Spread with a zero-only result (all zeros)
section("_values_to_spread — edge cases")
check("all-zero 7-elem",  _values_to_spread([1, 0, 0, 0, 0, 0, 0]), "")
check("single stat",      _values_to_spread([3, 0, 0, 0, 32, 0, 0]), "32 SpA")

section("_values_to_spread — 6-element legacy format (no row rank)")
check("legacy [2,0,0,32,0,32]",  _values_to_spread([2,  0,  0, 32,  0, 32]), "2 HP / 32 SpA / 32 Spe")
check("legacy [32,0,32,0,2,0]",  _values_to_spread([32, 0, 32,  0,  2,  0]), "32 HP / 32 Def / 2 SpD")

section("_values_to_spread — wrong lengths return empty string")
check("5-element",  _values_to_spread([0, 0, 32, 0, 32]), "")
check("8-element",  _values_to_spread([1, 0, 0, 32, 0, 32, 0, 0]), "")
check("empty list", _values_to_spread([]), "")

section("_coerce_usage_rank — reject malformed OCR ranks")
check("integer preserved", _coerce_usage_rank(7), 7)
check("digit string parsed", _coerce_usage_rank("10"), 10)
check("float-like integer parsed", _coerce_usage_rank("4.0"), 4)
check("non-integer float rejected", _coerce_usage_rank("67.9"), None)
check("blank rejected", _coerce_usage_rank(""), None)


# ===========================================================================
# 2. format_for_csv
# ===========================================================================
section("format_for_csv — spread_values → 'sp' column")

# Garchomp full slide (7-element new format)
garchomp_data = {
    "pokemon": "garchomp",
    "usage": 1,
    "spread_values": [
        [1,  2, 32,  0,  0,  0, 32],
        [2,  0, 32,  2,  0,  0, 32],
        [3,  0, 32,  1,  0,  1, 32],
        [4,  0, 32,  0,  0,  2, 32],
        [5, 32,  0, 11,  0, 12, 11],
    ],
    "spread_usage": [55.6, 7.4, 5.4, 4.2, 2.0],
}
row = format_for_csv(garchomp_data)
check(
    "garchomp sp column",
    row["sp"],
    "2 HP / 32 Atk / 32 Spe:32 Atk / 2 Def / 32 Spe:32 Atk / 1 Def / 1 SpD / 32 Spe:32 Atk / 2 SpD / 32 Spe:32 HP / 11 Def / 12 SpD / 11 Spe",
)
check("garchomp sp usage", row["sp usage"], "55.6:7.4:5.4:4.2:2.0")

# Corviknight full slide
corviknight_data = {
    "pokemon": "corviknight",
    "usage": 5,
    "spread_values": [
        [1, 32,  0, 32,  0,  2,  0],
        [2, 32,  0, 32,  0,  0,  2],
        [3, 32,  0,  2,  0, 32,  0],
        [4, 32,  2, 32,  0,  0,  0],
        [5, 32,  0, 25,  0,  9,  0],
    ],
    "spread_usage": [45.5, 11.2, 4.3, 2.8, 2.6],
}
row = format_for_csv(corviknight_data)
check(
    "corviknight sp column",
    row["sp"],
    "32 HP / 32 Def / 2 SpD:32 HP / 32 Def / 2 Spe:32 HP / 2 Def / 32 SpD:32 HP / 2 Atk / 32 Def:32 HP / 25 Def / 9 SpD",
)

# Legacy 6-element format still works
legacy_data = {
    "pokemon": "meowscarada",
    "usage": 10,
    "spread_values": [
        [2,  0,  0, 32,  0, 32],
        [0,  0,  2, 32,  0, 32],
    ],
    "spread_usage": [61.3, 9.5],
}
row = format_for_csv(legacy_data)
check(
    "legacy 6-element sp column",
    row["sp"],
    "2 HP / 32 SpA / 32 Spe:2 Def / 32 SpA / 32 Spe",
)


# ===========================================================================
# 3. autocorrect_pokemon_names
# ===========================================================================
section("autocorrect_pokemon_names — repeated OCR misses")

pokemon_lookup = {
    "farigiraf": "farigiraf",
    "charizard": "charizard",
    "basculin red striped": "basculin-red-striped",
}

ocr_entries = [
    {"image": "image3.jpeg", "data": {"pokemon": "fangirl", "usage": 8}},
    {"image": "image4.jpeg", "data": {"pokemon": "fangirl", "usage": 8}},
    {"image": "image5.jpeg", "data": {"pokemon": "Charizard", "usage": 4}},
    {"image": "image6.jpeg", "data": {"pokemon": "basculin", "usage": 1}},
    {"image": "image7.jpeg", "data": {"pokemon": "basculin", "usage": 1}},
]

corrected_entries, correction_log = autocorrect_pokemon_names(ocr_entries, pokemon_lookup)

check(
    "repeated low-confidence name corrected",
    [corrected_entries[0]["data"]["pokemon"], corrected_entries[1]["data"]["pokemon"]],
    ["farigiraf", "farigiraf"],
)
check(
    "exact match normalizes case only",
    corrected_entries[2]["data"]["pokemon"],
    "charizard",
)
check(
    "valid base species name is not rewritten to form id",
    [corrected_entries[3]["data"]["pokemon"], corrected_entries[4]["data"]["pokemon"]],
    ["basculin", "basculin"],
)
check("only expected corrections were applied", len(correction_log), 3)


# ===========================================================================
# 4. prefilled name authority
# ===========================================================================
section("prefilled name authority — same-date rank rectification")

existing_rows = [
    csv_row("2026-05-10", "basculegion", 10, moves="last respects"),
    csv_row("2026-05-10", "gyarados", 11, moves="waterfall"),
    csv_row("2026-05-18", "basculegion", 7),
    csv_row("2026-05-18", "basculin", 7, moves="last respects", move_usage="99.9"),
    csv_row("2026-05-18", "gyrados", 10),
    csv_row("2026-05-18", "hippowdon", 8, moves="earthquake"),
]

known_pokemon_names = build_known_pokemon_names(existing_rows, exclude_date="2026-05-18")
prefilled_name_index, prefilled_conflicts = build_prefilled_name_index(
    existing_rows,
    "2026-05-18",
    known_names=known_pokemon_names,
)
check("only blank same-date rows become prefill authority", prefilled_name_index, {"7": "basculegion"})
check("no prefill conflicts detected", prefilled_conflicts, [])

prefilled_entries, prefilled_entry_corrections = rectify_pokemon_names_from_prefill(
    [
        {"image": "image7.jpeg", "data": {"pokemon": "basculin", "usage": 7}},
        {"image": "image8.jpeg", "data": {"pokemon": "hippowdon", "usage": 8}},
    ],
    prefilled_name_index,
)
check("prefilled rank overrides exact but wrong OCR name", prefilled_entries[0]["data"]["pokemon"], "basculegion")
check("ranks without prefill authority are unchanged", prefilled_entries[1]["data"]["pokemon"], "hippowdon")
check("one extracted entry corrected from prefill authority", len(prefilled_entry_corrections), 1)

prefilled_existing_rows, prefilled_existing_corrections = rectify_existing_rows_from_prefill(
    existing_rows, "2026-05-18", prefilled_name_index
)
merged_existing_rows, merged_existing_count = merge_duplicate_csv_rows(
    prefilled_existing_rows, target_date="2026-05-18"
)
rank7_rows = [r for r in merged_existing_rows if r["date"] == "2026-05-18" and r["usage"] == "7"]
check("existing conflicting same-rank rows are collapsed", len(rank7_rows), 1)
check("merged row keeps authoritative prefilled name", rank7_rows[0]["pokemon"], "basculegion")
check("merged row keeps previously extracted data", rank7_rows[0]["moves"], "last respects")
check("one existing row rewritten to authoritative name", len(prefilled_existing_corrections), 1)
check("one duplicate existing row merged away", merged_existing_count, 1)


# ===========================================================================
# 4b. per-rank extraction merging
# ===========================================================================
section("ranked extraction merge — majority pokemon name")

ranked_entries = [
    {"image": "a.png", "data": {"pokemon": "farigiraf", "usage": 4, "moves": ["trick room"]}},
    {"image": "b.png", "data": {"pokemon": "farigiraf", "usage": 4, "abilities": ["armor tail"]}},
    {"image": "c.png", "data": {"pokemon": "girafarig", "usage": 4, "items": ["sitrus berry"]}},
]
merged_ranked_entries = merge_ranked_extractions(ranked_entries)
check("one entry produced per usage rank", len(merged_ranked_entries), 1)
check("majority name wins for rank merge", merged_ranked_entries[0]["data"]["pokemon"], "farigiraf")
check("rank merge keeps data from all entries", merged_ranked_entries[0]["data"]["items"], ["sitrus berry"])


# ===========================================================================
# 5. flag_review validate_entry — spread_values checks
# ===========================================================================
section("flag_review — spread_values rank mismatch detection")

# Row rank included correctly → no flags for spread_values
good_entry = {
    "image": "test.jpeg",
    "data": {
        "screen_type": "stat_points",
        "pokemon": "garchomp",
        "usage": 1,
        "spread_values": [
            [1, 2, 32, 0, 0, 0, 32],
            [2, 0, 32, 2, 0, 0, 32],
        ],
        "spread_usage": [55.6, 7.4],
        "moves": [], "move_usage": [], "abilities": [], "ability_usage": [],
        "natures": [], "nature_usage": [], "items": [], "item_usage": [],
    },
}
flags_good = validate_entry(good_entry, {"pokemon": {}, "moves": {}, "abilities": {}, "items": {}}, threshold=85)
spread_flags_good = [f for f in flags_good if "spread_values" in f["field"]]
check("no spread flags for correct 7-elem data", len(spread_flags_good), 0)

# Model accidentally uses row rank as first stat (old bug) → rank mismatch flags
bad_entry_rank = {
    "image": "test.jpeg",
    "data": {
        "screen_type": "stat_points",
        "pokemon": "garchomp",
        "usage": 1,
        # Row 1 is lucky (rank=1 may equal HP=1 accidentally), but rows 2-5 will mismatch
        "spread_values": [
            [1,  2, 32,  0,  0,  0, 32],  # rank=1 correct
            [2,  0,  0, 32,  0, 32,  0],  # rank=2 correct but wrong stat values is fine here
        ],
        "spread_usage": [55.6, 7.4],
        "moves": [], "move_usage": [], "abilities": [], "ability_usage": [],
        "natures": [], "nature_usage": [], "items": [], "item_usage": [],
    },
}
flags_rank = validate_entry(bad_entry_rank, {"pokemon": {}, "moves": {}, "abilities": {}, "items": {}}, threshold=85)
rank_flags = [f for f in flags_rank if f["issue"] == "spread_rank_mismatch"]
check("no rank mismatch when ranks are correct", len(rank_flags), 0)

# Ranks are wrong (classic bug: rank 2 listed as HP=2)
bad_entry_shifted = {
    "image": "test.jpeg",
    "data": {
        "screen_type": "stat_points",
        "pokemon": "corviknight",
        "usage": 5,
        # Model included rank 1 correctly but row 2 is: [row2rank=2, HP=32, ...missing Spe]
        # This simulates the old 6-element-with-rank-as-HP bug
        "spread_values": [
            [1, 32,  0, 32,  0,  2,  0],  # correct (rank=1)
            [2, 32,  0, 32,  0,  0,  0],  # rank=2 correct but only 5 real stats (bad)
        ],
        "spread_usage": [45.5, 11.2],
        "moves": [], "move_usage": [], "abilities": [], "ability_usage": [],
        "natures": [], "nature_usage": [], "items": [], "item_usage": [],
    },
}
flags_shifted = validate_entry(bad_entry_shifted, {"pokemon": {}, "moves": {}, "abilities": {}, "items": {}}, threshold=85)
sum_flags = [f for f in flags_shifted if f["issue"] == "spread_sum_mismatch"]
# Row 1: sum = 32+0+32+0+2+0 = 66 ✓   Row 2: sum = 32+0+32+0+0+0 = 64 ✗
check("sum mismatch flagged for row with wrong total", len(sum_flags), 1)

section("flag_review — spread_values wrong length detection")
bad_len_entry = {
    "image": "test.jpeg",
    "data": {
        "screen_type": "stat_points",
        "pokemon": "test",
        "usage": 1,
        "spread_values": [[1, 2, 32, 0]],  # only 4 elements
        "spread_usage": [50.0],
        "moves": [], "move_usage": [], "abilities": [], "ability_usage": [],
        "natures": [], "nature_usage": [], "items": [], "item_usage": [],
    },
}
flags_len = validate_entry(bad_len_entry, {"pokemon": {}, "moves": {}, "abilities": {}, "items": {}}, threshold=85)
len_flags = [f for f in flags_len if f["issue"] == "spread_wrong_length"]
check("wrong-length spread flagged", len(len_flags), 1)


# ===========================================================================
# 6. sanitize_extracted_entry
# ===========================================================================
section("sanitize_extracted_entry — clears irrelevant fields and repairs spreads")

noisy_moves_entry = {
    "pokemon": "charizard",
    "usage": 7,
    "screen_type": "moves",
    "moves": ["heat wave", "protect", "solar beam", "weather ball", "air slash"],
    "move_usage": [95.3, 95.1, 92.9, 72.6, 11.0],
    "abilities": ["blaze", "solar power"],
    "ability_usage": [74.4, 25.6],
    "spread_values": [
        [1, 1, 2, 0, 0, 32, 0, 32],
        [2, 2, 0, 0, 2, 32, 0, 32],
    ],
    "spread_usage": [34.3, 5.1],
    "natures": [],
    "nature_usage": [],
    "items": [],
    "item_usage": [],
}
clean_moves = sanitize_extracted_entry(noisy_moves_entry)
check("moves screen keeps moves", clean_moves["moves"], noisy_moves_entry["moves"])
check("moves screen drops leaked spread rows", clean_moves["spread_values"], [])
check("moves screen drops leaked abilities", clean_moves["abilities"], [])

historical_rows = [
    csv_row("2026-06-28", "sinistcha", 4, sp="32 HP / 14 Def / 20 SpD:32 HP / 4 Def / 30 SpD:32 HP / 2 Def / 32 SpD:31 HP / 14 Def / 21 SpD:32 HP / 24 Def / 10 SpD"),
]
historical_ref = build_historical_spread_reference(historical_rows, before_date="2026-07-06")
bad_stat_points = {
    "pokemon": "sinistcha",
    "usage": 2,
    "screen_type": "stat_points",
    "moves": ["matcha gotcha"],
    "move_usage": [98.9],
    "abilities": [],
    "ability_usage": [],
    "spread_values": [
        [1, 1, 32, 0, 14, 0, 20, 0],
        [2, 2, 32, 0, 4, 0, 30, 0],
        [3, 3, 32, 0, 2, 0, 32, 0],
    ],
    "spread_usage": [17.7, 12.7, 5.2],
    "natures": ["bold"],
    "nature_usage": [43.9],
    "items": [],
    "item_usage": [],
}
clean_stats = sanitize_extracted_entry(bad_stat_points, historical_spreads=historical_ref["sinistcha"])
check(
    "stat_points repairs duplicated-rank rows from history",
    clean_stats["spread_values"],
    [
        [1, 32, 0, 14, 0, 20, 0],
        [2, 32, 0, 4, 0, 30, 0],
        [3, 32, 0, 2, 0, 32, 0],
    ],
)
check("stat_points drops leaked moves", clean_stats["moves"], [])
check("stat_points drops leaked natures", clean_stats["natures"], [])

garchomp_stats = {
    "pokemon": "garchomp",
    "usage": 1,
    "screen_type": "stat_points",
    "spread_values": [
        [1, 48, 5, 32, 0, 0, 32],
        [2, 0, 32, 2, 0, 0, 32],
        [3, 0, 32, 0, 0, 2, 32],
        [4, 0, 30, 4, 0, 0, 32],
        [5, 31, 14, 4, 0, 3, 14],
    ],
    "spread_usage": [48.2, 5.0, 4.9, 4.5, 1.6],
    "moves": [], "move_usage": [], "abilities": [], "ability_usage": [],
    "natures": [], "nature_usage": [], "items": [], "item_usage": [],
}
garchomp_history_rows = [
    csv_row("2026-06-28", "garchomp", 1, sp="2 HP / 32 Atk / 32 Spe:32 Atk / 2 Def / 32 Spe:32 Atk / 2 SpD / 32 Spe:30 Atk / 4 Def / 32 Spe:22 HP / 10 Atk / 2 SpD / 32 Spe"),
]
garchomp_ref = build_historical_spread_reference(garchomp_history_rows, before_date="2026-07-06")
clean_garchomp = sanitize_extracted_entry(garchomp_stats, historical_spreads=garchomp_ref["garchomp"])
check(
    "valid spread rows are preserved even if unusual",
    clean_garchomp["spread_values"],
    [
        [1, 2, 32, 0, 0, 0, 32],
        [2, 0, 32, 2, 0, 0, 32],
        [3, 0, 32, 0, 0, 2, 32],
        [4, 0, 30, 4, 0, 0, 32],
        [5, 31, 14, 4, 0, 3, 14],
    ],
)

garchomp_bad_row = deepcopy(garchomp_stats)
garchomp_bad_row["spread_values"][-1] = [5, 48, 14, 4, 0, 3, 14]
clean_garchomp_bad = sanitize_extracted_entry(garchomp_bad_row, historical_spreads=garchomp_ref["garchomp"])
check(
    "historical fallback repairs impossible spread rows",
    clean_garchomp_bad["spread_values"][-1],
    [5, 22, 10, 0, 0, 2, 32],
)


# ===========================================================================
# 4. Sum invariant — all real spreads must sum to EXPECTED_SP_SUM (66)
# ===========================================================================
section(f"Sum invariant — all ground-truth spreads sum to {EXPECTED_SP_SUM}")

KNOWN_GOOD_SPREADS = [
    # Garchomp image21.jpeg
    [2, 32,  0,  0,  0, 32],
    [0, 32,  2,  0,  0, 32],
    [0, 32,  1,  0,  1, 32],
    [0, 32,  0,  0,  2, 32],
    [32,  0, 11,  0, 12, 11],
    # Aegislash image5.jpeg
    [32, 32,  1,  0,  1,  0],
    [32, 32,  2,  0,  0,  0],
    [32, 32,  0,  0,  0,  2],
    [32, 32,  0,  0,  2,  0],
    [ 2, 32,  0,  0,  0, 32],
    # Corviknight image12.jpeg
    [32,  0, 32,  0,  2,  0],
    [32,  0, 32,  0,  0,  2],
    [32,  0,  2,  0, 32,  0],
    [32,  2, 32,  0,  0,  0],
    [32,  0, 25,  0,  9,  0],
]

for i, vals in enumerate(KNOWN_GOOD_SPREADS):
    total = sum(vals)
    check(f"spread {i+1} sum={total}", total, EXPECTED_SP_SUM)


# ===========================================================================
# Summary
# ===========================================================================
total   = len(_results)
passed  = sum(_results)
failed  = total - passed
print(f"\n{'═'*60}")
print(f"  Results: {passed}/{total} passed", end="")
if failed:
    print(f"  ({failed} FAILED)")
else:
    print("  ✓ all clear")
print(f"{'═'*60}\n")

sys.exit(0 if failed == 0 else 1)
