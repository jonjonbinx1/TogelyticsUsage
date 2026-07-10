#!/usr/bin/env python3
"""Regression tests for CSV review row repair flow."""

import tempfile
from pathlib import Path

import extractor


PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
_results = []


class DummyLogger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


logger = DummyLogger()


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


section("missing csv field detection includes paired usage columns")
row = {
    "moves": "earthquake",
    "move usage": "",
    "ability": "rough skin",
    "ability usage": "99.0",
    "sp": "2 HP / 32 Atk / 32 Spe",
    "sp usage": "",
    "nature": "jolly",
    "nature usage": "73",
    "item": "focus sash",
    "item usage": "",
}
check(
    "missing paired fields are detected",
    extractor._missing_csv_fields_for_row(row),
    ["move usage", "sp usage", "item usage"],
)
check(
    "slides inferred from missing paired fields",
    extractor._slides_for_missing_csv_fields(["move usage", "sp usage", "item usage"]),
    [1, 3, 5],
)
check(
    "slide-specific fields isolated",
    extractor._missing_csv_fields_for_slide(["move usage", "sp usage", "item usage"], 3),
    ["sp usage"],
)


section("review mode infers csv from review folder")
with tempfile.TemporaryDirectory() as temp_dir:
    root = Path(temp_dir)
    original_defaults = extractor.DEFAULT_CSV
    original_script_dir = extractor.SCRIPT_DIR
    try:
        extractor.SCRIPT_DIR = root
        extractor.DEFAULT_CSV = {
            "singles": root / "champions_singles.csv",
            "doubles": root / "champions_doubles.csv",
        }
        paths, review_results, results_path = extractor._resolve_review_csv_paths(
            folder_arg=str(root / "data" / "singles" / "20260503singles"),
            csv_arg=None,
            review_csv_arg=None,
            review_results_arg=None,
            logger=logger,
        )
    finally:
        extractor.DEFAULT_CSV = original_defaults
        extractor.SCRIPT_DIR = original_script_dir

    check("folder review resolves singles csv", paths, [root / "champions_singles.csv"])
    check("folder review has no results payload", review_results, None)
    check("folder review has no results path", results_path, None)


section("blank image detection")
with tempfile.TemporaryDirectory() as temp_dir:
    root = Path(temp_dir)
    blank_path = root / "blank.png"
    nonblank_path = root / "nonblank.png"

    from PIL import Image

    Image.new("RGB", (16, 16), color=(0, 0, 0)).save(blank_path)
    patterned = Image.new("RGB", (16, 16), color=(0, 0, 0))
    patterned.putpixel((8, 8), (255, 255, 255))
    patterned.save(nonblank_path)

    check("solid image is treated as blank", extractor._is_effectively_blank_image(blank_path), True)
    check("patterned image is not treated as blank", extractor._is_effectively_blank_image(nonblank_path), False)


section("csv review repairs missing fields from exact slide image")
with tempfile.TemporaryDirectory() as temp_dir:
    root = Path(temp_dir)
    data_folder = root / "data" / "singles" / "20260503singles"
    data_folder.mkdir(parents=True)
    csv_path = root / "champions_singles.csv"
    csv_path.write_text(
        "date,pokemon,usage,moves,move usage,ability,ability usage,sp,sp usage,nature,nature usage,item,item usage,,,\n"
        "2026-05-03,garchomp,1,,99,rough skin,99,2 HP / 32 Atk / 32 Spe,55.6,jolly,69.4,focus sash,39.4,,,\n",
        encoding="utf-8",
    )
    image_path = data_folder / "singles_slot_1_slide_1.png"
    image_path.write_bytes(b"fake")

    original_script_dir = extractor.SCRIPT_DIR
    original_results_dir = extractor.RESULTS_DIR
    original_call_vision_model = extractor.call_vision_model
    try:
        extractor.SCRIPT_DIR = root
        extractor.RESULTS_DIR = root / "results"

        def fake_call_vision_model(image_path, provider, model, image_meta=None, max_retries=2, prompt_override=None):
            return {
                "screen_type": "moves",
                "pokemon": "garchomp",
                "usage": 1,
                "moves": ["earthquake", "outrage"],
                "move_usage": [99.0, 65.9],
                "abilities": [],
                "ability_usage": [],
                "spread_values": [],
                "spread_usage": [],
                "natures": [],
                "nature_usage": [],
                "items": [],
                "item_usage": [],
            }

        extractor.call_vision_model = fake_call_vision_model
        exit_code = extractor._review_csv_rows(
            csv_path=csv_path,
            review_slot=1,
            review_image=str(image_path),
            review_slide=1,
            provider="ollama",
            model="fake",
            logger=logger,
            apply_changes=True,
        )
        rows, _ = extractor.load_csv(csv_path)
    finally:
        extractor.call_vision_model = original_call_vision_model
        extractor.RESULTS_DIR = original_results_dir
        extractor.SCRIPT_DIR = original_script_dir

    check("csv review exits cleanly", exit_code, 0)
    check("missing moves were filled", rows[0]["moves"], "earthquake:outrage")
    check("existing move usage preserved when already present", rows[0]["move usage"], "99")


section("csv review rejects mismatched hallucinated pokemon result")
with tempfile.TemporaryDirectory() as temp_dir:
    root = Path(temp_dir)
    data_folder = root / "data" / "doubles" / "20260710doubles"
    data_folder.mkdir(parents=True)
    csv_path = root / "champions_doubles.csv"
    csv_path.write_text(
        "date,pokemon,usage,moves,move usage,ability,ability usage,sp,sp usage,nature,nature usage,item,item usage,,\n"
        "2026-07-10,archaludon,13,,,stamina,96.5,32 HP / 2 SpA / 32 SpD,8.4,modest,71.6,leftovers,89.7,,\n",
        encoding="utf-8",
    )
    image_path = data_folder / "doubles_slot_13_slide_1.png"
    image_path.write_bytes(b"fake")

    original_script_dir = extractor.SCRIPT_DIR
    original_results_dir = extractor.RESULTS_DIR
    original_call_vision_model = extractor.call_vision_model
    try:
        extractor.SCRIPT_DIR = root
        extractor.RESULTS_DIR = root / "results"

        def fake_bad_review(image_path, provider, model, image_meta=None, max_retries=2, prompt_override=None):
            return {
                "screen_type": "moves",
                "pokemon": "chess-openings",
                "usage": 13,
                "moves": ["e4", "c5"],
                "move_usage": [99.0, 50.0],
                "abilities": [],
                "ability_usage": [],
                "spread_values": [],
                "spread_usage": [],
                "natures": [],
                "nature_usage": [],
                "items": [],
                "item_usage": [],
            }

        extractor.call_vision_model = fake_bad_review
        exit_code = extractor._review_csv_rows(
            csv_path=csv_path,
            review_slot=13,
            review_image=str(image_path),
            review_slide=1,
            provider="ollama",
            model="fake",
            logger=logger,
            apply_changes=True,
        )
        rows, _ = extractor.load_csv(csv_path)
    finally:
        extractor.call_vision_model = original_call_vision_model
        extractor.RESULTS_DIR = original_results_dir
        extractor.SCRIPT_DIR = original_script_dir

    check("mismatched review still exits cleanly", exit_code, 0)
    check("mismatched review does not overwrite moves", rows[0]["moves"], "")
    check("mismatched review does not overwrite move usage", rows[0]["move usage"], "")


passed = sum(1 for result in _results if result)
total = len(_results)
print(f"\n{'═' * 60}")
if passed == total:
    print(f"  Results: {passed}/{total} passed  ✓ all clear")
else:
    print(f"  Results: {passed}/{total} passed  ✗ failures present")
print(f"{'═' * 60}")

raise SystemExit(0 if passed == total else 1)
