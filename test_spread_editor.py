#!/usr/bin/env python3
"""Focused regression tests for stat_spread_editor.py."""

import tempfile
from pathlib import Path

from extractor import load_csv
from stat_spread_editor import (
    StatSpreadEditorApp,
    analyze_spread_rows,
    build_spread_csv_values,
    build_editor_entries,
    build_value_csv_values,
    collect_stat_image_candidates,
    discover_available_dates,
    infer_primary_max_usage,
    normalize_date,
    parse_spread_rows_from_csv,
    parse_value_rows_from_csv,
    save_spreads_to_csv,
    save_entry_values_to_csv,
)


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
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print(f"{'─' * 60}")


section("normalize_date")
check("YYYYMMDD input", normalize_date("20260518"), "2026-05-18")
check("YYYY-MM-DD input", normalize_date("2026-05-18"), "2026-05-18")


section("discover_available_dates")
with tempfile.TemporaryDirectory() as temp_dir:
    root = Path(temp_dir)
    results_dir = root / "results"
    results_dir.mkdir()
    (results_dir / "20260518singles_extracted.json").write_text("{}", encoding="utf-8")
    (results_dir / "20260510doubles_extracted.json").write_text("{}", encoding="utf-8")

    singles_csv = root / "singles.csv"
    doubles_csv = root / "doubles.csv"
    singles_csv.write_text(
        "date,pokemon,usage,moves,move usage,ability,ability usage,sp,sp usage,nature,nature usage,item,item usage\n"
        "2026-05-03,garchomp,1,,,,,,,,,,\n",
        encoding="utf-8",
    )
    doubles_csv.write_text(
        "date,pokemon,usage,moves,move usage,ability,ability usage,sp,sp usage,nature,nature usage,item,item usage\n"
        "2026-04-27,farigiraf,8,,,,,,,,,,\n",
        encoding="utf-8",
    )

    dates = discover_available_dates(
        results_dir=results_dir,
        csv_paths={"singles": singles_csv, "doubles": doubles_csv},
    )
    check("singles dates include JSON and CSV", dates["singles"], ["2026-05-18", "2026-05-03"])
    check("doubles dates include JSON and CSV", dates["doubles"], ["2026-05-10", "2026-04-27"])


section("spread row parse/build round trip")
spreads_text = "2 HP / 32 Atk / 32 Spe:32 Atk / 2 Def / 32 Spe"
usage_text = "55.6:7.4"
rows = parse_spread_rows_from_csv(spreads_text, usage_text)
check("row 1 HP parsed", rows[0]["HP"], "2")
check("row 1 Atk parsed", rows[0]["Atk"], "32")
check("row 1 Def defaults to blank", rows[0]["Def"], "")
check("row 2 usage parsed", rows[1]["usage"], "7.4")
roundtrip_spreads, roundtrip_usage = build_spread_csv_values(rows)
check("spread text round trip", roundtrip_spreads, spreads_text)
check("usage text round trip", roundtrip_usage, usage_text)


section("analyze_spread_rows")
errors, warnings = analyze_spread_rows([
    {"usage": "55.6", "HP": "2", "Atk": "32", "Def": "", "SpA": "", "SpD": "", "Spe": "32"},
    {"usage": "7.4", "HP": "", "Atk": "32", "Def": "2", "SpA": "", "SpD": "", "Spe": "31"},
])
check("valid rows produce no blocking errors", errors, [])
check("non-66 totals produce warnings only", warnings, ["Row 2: stat total is 65 instead of 66"])


section("collect_stat_image_candidates")
with tempfile.TemporaryDirectory() as temp_dir:
    folder = Path(temp_dir)
    (folder / "image2.jpeg").write_bytes(b"not-a-real-image")
    (folder / "image7.jpeg").write_bytes(b"not-a-real-image")
    results = {
        "folder": str(folder),
        "autocorrected_extracted": [
            {
                "image": "image7.jpeg",
                "data": {
                    "screen_type": "held_item",
                    "pokemon": "farigiraf",
                    "usage": 8,
                    "spread_values": [[1, 2, 32, 0, 0, 0, 32]],
                },
            },
            {
                "image": "image2.jpeg",
                "data": {
                    "screen_type": "Stat Points",
                    "pokemon": "farigiraf",
                    "usage": 8,
                    "spread_values": [[1, 2, 32, 0, 0, 0, 32]],
                },
            },
            {
                "image": "image9.jpeg",
                "data": {
                    "screen_type": "ability",
                    "pokemon": "farigiraf",
                    "usage": 8,
                    "spread_values": [],
                },
            },
        ],
    }
    candidates = collect_stat_image_candidates(results)
    check("one usage key detected", sorted(candidates.keys()), ["8"])
    check("stat-points candidate sorted first", candidates["8"][0].image_name, "image2.jpeg")
    check("spread fallback candidate kept", candidates["8"][1].image_name, "image7.jpeg")


section("infer_primary_max_usage")
results = {
    "autocorrected_extracted": [
        {"image": "image0.jpeg", "data": {"usage": 1}},
        {"image": "image1.jpeg", "data": {"usage": 1}},
        {"image": "image2.jpeg", "data": {"usage": 2}},
        {"image": "image3.jpeg", "data": {"usage": 2}},
        {"image": "image4.jpeg", "data": {"usage": 3}},
        {"image": "image5.jpeg", "data": {"usage": 3}},
        {"image": "image6.jpeg", "data": {"usage": 437}},
    ]
}
check("primary max usage ignores one-off outlier ranks", infer_primary_max_usage(results), 3)


section("build_editor_entries loads all CSV rows")
with tempfile.TemporaryDirectory() as temp_dir:
    csv_path = Path(temp_dir) / "champions_singles.csv"
    csv_path.write_text(
        "date,pokemon,usage,moves,move usage,ability,ability usage,sp,sp usage,nature,nature usage,item,item usage\n"
        + "\n".join(
            f"2026-05-18,pokemon-{index},{index},,,,,,,,,,"
            for index in range(1, 13)
        )
        + "\n",
        encoding="utf-8",
    )

    entries, conflicts, _, _ = build_editor_entries(
        "2026-05-18",
        "singles",
        csv_path,
        {},
        {},
    )
    check("all 12 CSV rows loaded", len(entries), 12)
    check("no conflicts for simple CSV", conflicts, [])


section("save_spreads_to_csv")
with tempfile.TemporaryDirectory() as temp_dir:
    csv_path = Path(temp_dir) / "manual_spreads.csv"
    save_spreads_to_csv(
        "2026-05-18",
        "singles",
        "farigiraf",
        "8",
        "2 HP / 32 Atk / 32 Spe:32 Atk / 2 Def / 32 Spe",
        "55.6:7.4",
        csv_path,
    )
    saved_rows, _ = load_csv(csv_path)
    check("one row written", len(saved_rows), 1)
    check("saved pokemon name", saved_rows[0]["pokemon"], "farigiraf")
    check("saved spread column", saved_rows[0]["sp"], "2 HP / 32 Atk / 32 Spe:32 Atk / 2 Def / 32 Spe")
    check("saved spread usage column", saved_rows[0]["sp usage"], "55.6:7.4")


section("named entry round trip")
named_rows = parse_value_rows_from_csv("fake move:close combat", "61.5:59.2")
check("named row 1 value parsed", named_rows[0]["value"], "fake move")
check("named row 2 usage parsed", named_rows[1]["usage"], "59.2")
roundtrip_values, roundtrip_named_usage = build_value_csv_values(named_rows, "moves")
check("named values round trip preserves freeform text", roundtrip_values, "fake move:close combat")
check("named usage round trip", roundtrip_named_usage, "61.5:59.2")

with tempfile.TemporaryDirectory() as temp_dir:
    csv_path = Path(temp_dir) / "manual_named.csv"
    save_entry_values_to_csv(
        "2026-05-18",
        "singles",
        "lopunny",
        "10",
        "moves",
        "fake move:close combat",
        "61.5:59.2",
        csv_path,
    )
    saved_rows, _ = load_csv(csv_path)
    check("named row written", len(saved_rows), 1)
    check("named field saved", saved_rows[0]["moves"], "fake move:close combat")
    check("named usage saved", saved_rows[0]["move usage"], "61.5:59.2")


section("editor unzip prep and cleanup")
with tempfile.TemporaryDirectory() as temp_dir:
    root = Path(temp_dir)
    folder = root / "data" / "doubles" / "20260713doubles"
    folder.mkdir(parents=True)
    (folder / "doubles_slot_1_slide_3.png").write_bytes(b"img")
    zip_path = folder.parent / f"{folder.name}.zip"

    from extractor import archive_folder_to_zip

    archive_folder_to_zip(folder)

    app = StatSpreadEditorApp.__new__(StatSpreadEditorApp)
    app.active_results_folder = None
    app.active_results_folder_was_unzipped = False

    payload = {"folder": str(folder), "format": "doubles"}
    prepared = StatSpreadEditorApp._prepare_results_folder(app, payload)
    check("prepare restores archived folder", folder.exists(), True)
    check("prepare marks folder as unzipped", app.active_results_folder_was_unzipped, True)
    check("prepared payload keeps folder path", prepared["folder"], str(folder.resolve()))

    class _MessageBoxStub:
        @staticmethod
        def showwarning(_title, _message):
            raise AssertionError("cleanup should not warn on successful archive")

    import stat_spread_editor as spread_editor_module

    original_messagebox = spread_editor_module.messagebox
    try:
        spread_editor_module.messagebox = _MessageBoxStub
        StatSpreadEditorApp._cleanup_unzipped_results_folder(app)
    finally:
        spread_editor_module.messagebox = original_messagebox

    check("cleanup re-archives folder", zip_path.exists(), True)
    check("cleanup removes restored folder", folder.exists(), False)
    check("cleanup clears active folder", app.active_results_folder, None)
    check("cleanup clears unzip flag", app.active_results_folder_was_unzipped, False)


passed = sum(1 for result in _results if result)
total = len(_results)
print(f"\n{'═' * 60}")
if passed == total:
    print(f"  Results: {passed}/{total} passed  ✓ all clear")
else:
    print(f"  Results: {passed}/{total} passed  ✗ failures present")
print(f"{'═' * 60}")

raise SystemExit(0 if passed == total else 1)