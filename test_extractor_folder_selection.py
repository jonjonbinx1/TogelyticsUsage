#!/usr/bin/env python3
"""Focused regression tests for extractor folder discovery."""

import builtins
import tempfile
import time
from pathlib import Path

from extractor import (
    discover_input_folders,
    discover_recent_results_folder,
    get_images,
    parse_image_filename,
    resolve_input_folder,
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


section("discover_input_folders")
with tempfile.TemporaryDirectory() as temp_dir:
    root = Path(temp_dir)
    data_root = root / "data"
    (data_root / "singles").mkdir(parents=True)
    (data_root / "doubles").mkdir(parents=True)
    (data_root / "singles" / "20260510singles").mkdir()
    (data_root / "doubles" / "20260518doubles").mkdir()
    (data_root / "singles" / "20260518singles").mkdir()
    (data_root / "singles" / "notes").mkdir()

    folders = [folder.name for folder in discover_input_folders(data_root)]
    check(
        "folders discovered newest-first and invalid names ignored",
        folders,
        ["20260518singles", "20260518doubles", "20260510singles"],
    )


section("resolve_input_folder")
with tempfile.TemporaryDirectory() as temp_dir:
    root = Path(temp_dir)
    data_root = root / "data"
    results_root = root / "results"
    (data_root / "singles").mkdir(parents=True)
    (data_root / "doubles").mkdir(parents=True)
    results_root.mkdir()
    (data_root / "singles" / "20260503singles").mkdir()
    (data_root / "doubles" / "20260518doubles").mkdir()
    (results_root / "20260503singles_extracted.json").write_text("{}", encoding="utf-8")

    explicit_path, explicit_note = resolve_input_folder(
        str(data_root / "singles" / "20260503singles"),
        prompt_user=False,
    )
    check("explicit path is preserved", explicit_path.name, "20260503singles")
    check("explicit path has no note", explicit_note, "")

    import extractor as extractor_module

    original_script_dir = extractor_module.SCRIPT_DIR
    original_results_dir = extractor_module.RESULTS_DIR
    try:
        extractor_module.SCRIPT_DIR = root
        extractor_module.RESULTS_DIR = results_root
        recent_folder = discover_recent_results_folder(results_root)
        inferred_path, inferred_note = resolve_input_folder(None, prompt_user=False)
    finally:
        extractor_module.SCRIPT_DIR = original_script_dir
        extractor_module.RESULTS_DIR = original_results_dir

    check("recent results folder discovered", recent_folder.name, "20260503singles")
    check("no-arg path resolves newest data folder", inferred_path.name, "20260518doubles")
    check(
        "no-arg path explains inference",
        inferred_note,
        "No folder provided; using newest folder 20260518doubles",
    )


section("recent results choose newest dated folder")
with tempfile.TemporaryDirectory() as temp_dir:
    root = Path(temp_dir)
    data_root = root / "data"
    results_root = root / "results"
    (data_root / "singles").mkdir(parents=True)
    (data_root / "doubles").mkdir(parents=True)
    results_root.mkdir()
    (data_root / "singles" / "20260518singles").mkdir()
    (data_root / "doubles" / "20260524doubles").mkdir()

    newer_date_path = results_root / "20260524doubles_extracted.json"
    older_date_path = results_root / "20260518singles_extracted.json"
    newer_date_path.write_text("{}", encoding="utf-8")
    time.sleep(0.01)
    older_date_path.write_text("{}", encoding="utf-8")

    import extractor as extractor_module

    original_script_dir = extractor_module.SCRIPT_DIR
    original_results_dir = extractor_module.RESULTS_DIR
    try:
        extractor_module.SCRIPT_DIR = root
        extractor_module.RESULTS_DIR = results_root
        recent_folder = discover_recent_results_folder(results_root)
    finally:
        extractor_module.SCRIPT_DIR = original_script_dir
        extractor_module.RESULTS_DIR = original_results_dir

    check(
        "recent results prefer newest date over newest mtime",
        recent_folder.name,
        "20260524doubles",
    )


section("interactive format choice")
with tempfile.TemporaryDirectory() as temp_dir:
    root = Path(temp_dir)
    data_root = root / "data"
    (data_root / "singles").mkdir(parents=True)
    (data_root / "doubles").mkdir(parents=True)
    (data_root / "singles" / "20260524singles").mkdir()
    (data_root / "doubles" / "20260524doubles").mkdir()

    import extractor as extractor_module

    original_script_dir = extractor_module.SCRIPT_DIR
    original_input = builtins.input
    try:
        extractor_module.SCRIPT_DIR = root
        builtins.input = lambda _prompt="": "d"
        inferred_path, inferred_note = resolve_input_folder(None, prompt_user=True)
    finally:
        extractor_module.SCRIPT_DIR = original_script_dir
        builtins.input = original_input

    check("interactive prompt accepts doubles shortcut", inferred_path.name, "20260524doubles")
    check(
        "interactive prompt explains selected folder",
        inferred_note,
        "No folder provided; selected 20260524doubles",
    )


section("filename metadata parsing")
with tempfile.TemporaryDirectory() as temp_dir:
    root = Path(temp_dir)
    folder = root / "images"
    folder.mkdir()
    (folder / "doubles_slot_1_slide_3.png").write_bytes(b"x")
    (folder / "image12.png").write_bytes(b"x")
    (folder / "doubles_slot_2_slide_5.jpg").write_bytes(b"x")

    meta = parse_image_filename(folder / "doubles_slot_1_slide_3.png")
    check("slot parsed from filename", meta["slot"], 1)
    check("slide parsed from filename", meta["slide"], 3)
    check("slide mapped to stat_points", meta["screen_type"], "stat_points")

    ordered = [p.name for p in get_images(folder)]
    check(
        "metadata filenames sort by slot first",
        ordered,
        ["doubles_slot_1_slide_3.png", "doubles_slot_2_slide_5.jpg", "image12.png"],
    )


passed = sum(1 for result in _results if result)
total = len(_results)
print(f"\n{'═' * 60}")
if passed == total:
    print(f"  Results: {passed}/{total} passed  ✓ all clear")
else:
    print(f"  Results: {passed}/{total} passed  ✗ failures present")
print(f"{'═' * 60}")

raise SystemExit(0 if passed == total else 1)