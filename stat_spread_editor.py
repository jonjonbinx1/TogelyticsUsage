#!/usr/bin/env python3
"""Manual stat spread editor for extracted Pokemon HOME screenshots."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    from PIL import Image, ImageOps, ImageTk
except ImportError:
    Image = None
    ImageOps = None
    ImageTk = None

from flag_review import VALID_NATURES, load_or_fetch_cache, normalize as normalize_lookup_name

from extractor import (
    CORE_FIELDS,
    DEFAULT_CSV,
    RESULTS_DIR,
    TRAILING_COLS,
    _load_pokemon_name_lookup,
    _values_to_spread,
    archive_folder_to_zip,
    build_known_pokemon_names,
    build_prefilled_name_index,
    ensure_folder_unzipped,
    format_for_csv,
    folder_zip_path,
    load_csv,
    merge_duplicate_csv_rows,
    rectify_existing_rows_from_prefill,
    save_csv,
)

STAT_NAMES = ["HP", "Atk", "Def", "SpA", "SpD", "Spe"]
STAT_CASE_MAP = {
    "hp": "HP",
    "atk": "Atk",
    "def": "Def",
    "spa": "SpA",
    "spd": "SpD",
    "spe": "Spe",
}
MAX_SPREAD_ROWS = 5
MAX_ENTRY_ROWS = 5
PREVIEW_SIZE = (920, 700)
MAX_SUGGESTION_OPTIONS = 50

ENTRY_MODE_LABELS = {
    "stat_points": "Stat Points",
    "moves": "Moves",
    "ability": "Ability",
    "held_item": "Held Item",
    "stat_alignment": "Stat Alignment",
}

ENTRY_MODE_FIELDS = {
    "stat_points": {"value_key": "sp", "usage_key": "sp usage", "kind": "spread"},
    "moves": {"value_key": "moves", "usage_key": "move usage", "kind": "named", "lookup_key": "moves"},
    "ability": {"value_key": "ability", "usage_key": "ability usage", "kind": "named", "lookup_key": "abilities"},
    "held_item": {"value_key": "item", "usage_key": "item usage", "kind": "named", "lookup_key": "items"},
    "stat_alignment": {"value_key": "nature", "usage_key": "nature usage", "kind": "named", "lookup_key": "natures"},
}

SCREEN_TYPE_TO_MODE = {
    "stat_points": "stat_points",
    "stat_alignment": "stat_alignment",
    "held_item": "held_item",
    "moves": "moves",
    "ability": "ability",
}

_POKEMON_LOOKUP_CACHE: dict | None = None
_ENTRY_LOOKUP_CACHE: dict[str, dict] | None = None

# Add this near the top of stat_spread_editor.py
DEFAULT_ENTRY_KIND = "stat_points"  # Replace with the actual intended value


@dataclass
class CandidateImage:
    path: Path
    image_name: str
    score: int
    screen_type: str
    pokemon: str
    usage: str
    exists: bool


@dataclass
class RosterEntry:
    date: str
    format: str
    pokemon: str
    usage: str
    row: dict[str, str]
    note: str = ""
    image_candidates_by_kind: dict[str, list[CandidateImage]] = field(default_factory=dict)
    selected_images: dict[str, Path] = field(default_factory=dict)


def normalize_date(value: str) -> str:
    text = value.strip()
    match = re.match(r"^(\d{4})-?(\d{2})-?(\d{2})$", text)
    if not match:
        raise ValueError("Date must be YYYY-MM-DD or YYYYMMDD")
    year, month, day = match.groups()
    return f"{year}-{month}-{day}"


def usage_sort_key(value: str) -> tuple:
    try:
        return (0, int(str(value).strip()))
    except ValueError:
        return (1, str(value).strip())


def image_sort_key(value: str) -> tuple:
    match = re.search(r"(\d+)", value)
    if match:
        return (0, int(match.group(1)), value)
    return (1, 0, value)


def normalize_screen_type(value: str) -> str:
    return re.sub(r"\s+", "_", str(value or "").strip().lower())


def entry_mode_label(mode: str) -> str:
    return ENTRY_MODE_LABELS.get(mode, mode.replace("_", " ").title())


def entry_mode_from_screen_type(value: str) -> str | None:
    return SCREEN_TYPE_TO_MODE.get(normalize_screen_type(value))


def entry_mode_fields(mode: str) -> dict:
    return ENTRY_MODE_FIELDS[mode]


def entry_image_key(usage: str, mode: str) -> str:
    return f"{str(usage).strip()}|{mode}"


def entry_rows_max(mode: str) -> int:
    return MAX_SPREAD_ROWS if mode == "stat_points" else MAX_ENTRY_ROWS


def load_entry_lookup_data() -> dict[str, dict[str, list[str] | dict[str, str]]]:
    global _ENTRY_LOOKUP_CACHE
    if _ENTRY_LOOKUP_CACHE is not None:
        return _ENTRY_LOOKUP_CACHE

    options: dict[str, list[str]] = {}
    lookups: dict[str, dict[str, str]] = {}

    try:
        cache = load_or_fetch_cache(refresh=False)
    except Exception:
        cache = {}

    for mode, cache_key in (("moves", "moves"), ("ability", "abilities"), ("held_item", "items")):
        raw_values = [str(value).strip().lower() for value in cache.get(cache_key, []) if str(value).strip()]
        normalized_values = sorted({normalize_lookup_name(value) for value in raw_values if normalize_lookup_name(value)})
        options[mode] = normalized_values
        lookups[mode] = {normalize_lookup_name(value): value for value in normalized_values}

    nature_values = sorted(VALID_NATURES)
    options["stat_alignment"] = nature_values
    lookups["stat_alignment"] = {name: name for name in nature_values}

    _ENTRY_LOOKUP_CACHE = {"options": options, "lookups": lookups}
    return _ENTRY_LOOKUP_CACHE


def filter_lookup_options(mode: str, query: str, limit: int = 50) -> list[str]:
    options = load_entry_lookup_data()["options"].get(mode, [])
    if not query:
        return list(options)[:limit]

    query_norm = normalize_lookup_name(query)
    matches = [value for value in options if query_norm in normalize_lookup_name(value)]
    return matches[:limit]


def canonicalize_named_entry(mode: str, value: str) -> str:
    text = str(value).strip()
    if not text:
        return ""

    normalized = normalize_lookup_name(text)
    lookup = load_entry_lookup_data()["lookups"].get(mode, {})
    if normalized in lookup:
        return normalized
    return text.lower()


class SearchableCombobox(ttk.Combobox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._all_values: list[str] = []
        self.bind("<KeyRelease>", self._on_keyrelease)

    def set_options(self, values: list[str]) -> None:
        self._all_values = list(dict.fromkeys(values))
        self["values"] = self._all_values[:MAX_SUGGESTION_OPTIONS]

    def _on_keyrelease(self, _event=None) -> None:
        query = self.get().strip()
        if not self._all_values:
            return
        filtered = filter_lookup_options_for_values(self._all_values, query)
        self["values"] = filtered[:MAX_SUGGESTION_OPTIONS]


def filter_lookup_options_for_values(values: list[str], query: str, limit: int = 50) -> list[str]:
    if not query:
        return list(values)[:limit]
    query_norm = normalize_lookup_name(query)
    matches = [value for value in values if query_norm in normalize_lookup_name(value)]
    return matches[:limit]


def results_json_path_for(date_str: str, fmt_type: str) -> Path:
    return RESULTS_DIR / f"{date_str.replace('-', '')}{fmt_type}_extracted.json"


def editor_state_path_for(date_str: str, fmt_type: str) -> Path:
    return RESULTS_DIR / f"{date_str.replace('-', '')}{fmt_type}_spread_editor_state.json"


def load_results_payload(results_path: Path | None) -> dict:
    if not results_path or not results_path.exists():
        return {}
    with open(results_path, encoding="utf-8") as handle:
        return json.load(handle)


def discover_available_dates(
    results_dir: Path = RESULTS_DIR,
    csv_paths: dict[str, Path] | None = None,
) -> dict[str, list[str]]:
    available = {fmt: set() for fmt in DEFAULT_CSV}
    pattern = re.compile(r"^(\d{8})(singles|doubles)_extracted\.json$")

    if results_dir.exists():
        for path in results_dir.iterdir():
            match = pattern.match(path.name)
            if not match:
                continue
            date_digits, fmt_type = match.groups()
            available[fmt_type].add(normalize_date(date_digits))

    for fmt_type, csv_path in (csv_paths or DEFAULT_CSV).items():
        rows, _ = load_csv(csv_path)
        for row in rows:
            date_text = str(row.get("date", "")).strip()
            if not date_text:
                continue
            try:
                available[fmt_type].add(normalize_date(date_text))
            except ValueError:
                continue

    return {
        fmt_type: sorted(date_values, reverse=True)
        for fmt_type, date_values in available.items()
    }


def iter_result_entries(results: dict) -> list:
    return (
        results.get("autocorrected_extracted")
        or results.get("raw_extracted")
        or results.get("extracted")
        or []
    )


def load_pokemon_lookup() -> dict:
    global _POKEMON_LOOKUP_CACHE
    if _POKEMON_LOOKUP_CACHE is None:
        _POKEMON_LOOKUP_CACHE = _load_pokemon_name_lookup()
    return _POKEMON_LOOKUP_CACHE


def collect_entry_image_candidates(results: dict) -> dict[str, dict[str, list[CandidateImage]]]:
    if not results:
        return {}

    folder_text = str(results.get("folder", "")).strip()
    folder = Path(folder_text) if folder_text else None
    candidates: dict[str, dict[str, list[CandidateImage]]] = {mode: {} for mode in ENTRY_MODE_FIELDS}

    for entry in iter_result_entries(results):
        data = entry.get("data", entry)
        usage = str(data.get("usage", "")).strip()
        image_name = str(entry.get("image", "")).strip()
        if not usage or not image_name:
            continue

        screen_type = normalize_screen_type(data.get("screen_type", ""))
        mode = entry_mode_from_screen_type(screen_type)
        path = folder / image_name if folder else Path(image_name)
        stat_score = 0
        if screen_type == "stat_points":
            stat_score += 2
        if data.get("spread_values"):
            stat_score += 1
        if stat_score > 0:
            stat_candidate = CandidateImage(
                path=path,
                image_name=image_name,
                score=stat_score,
                screen_type=screen_type or "unknown",
                pokemon=str(data.get("pokemon", "")).strip(),
                usage=usage,
                exists=path.exists(),
            )
            candidates.setdefault("stat_points", {}).setdefault(usage, []).append(stat_candidate)

        if mode and mode != "stat_points":
            score = 0
            field_config = entry_mode_fields(mode)
            if data.get(field_config["value_key"] + "s", data.get(field_config["value_key"], [])):
                score += 2
            if data.get(field_config["usage_key"].replace(" ", "_"), data.get(field_config["usage_key"], [])):
                score += 1
            if score == 0:
                continue

            candidate = CandidateImage(
                path=path,
                image_name=image_name,
                score=score,
                screen_type=mode,
                pokemon=str(data.get("pokemon", "")).strip(),
                usage=usage,
                exists=path.exists(),
            )
            candidates.setdefault(mode, {}).setdefault(usage, []).append(candidate)

    for mode, usage_candidates in candidates.items():
        for usage, usage_list in usage_candidates.items():
            usage_candidates[usage] = sorted(
                usage_list,
                key=lambda candidate: (
                    -candidate.score,
                    image_sort_key(candidate.image_name),
                ),
            )

    return candidates


def collect_stat_image_candidates(results: dict) -> dict[str, list[CandidateImage]]:
    return collect_entry_image_candidates(results).get("stat_points", {})


def parse_value_rows_from_csv(values_text: str, usage_text: str) -> list[dict[str, str]]:
    value_parts = [part.strip() for part in values_text.split(":")] if values_text else []
    usage_parts = [part.strip() for part in usage_text.split(":")] if usage_text else []
    row_count = max(MAX_ENTRY_ROWS, len(value_parts), len(usage_parts))
    rows: list[dict[str, str]] = []

    for index in range(min(row_count, MAX_ENTRY_ROWS)):
        row = {"value": "", "usage": ""}
        row["usage"] = usage_parts[index] if index < len(usage_parts) else ""
        row["value"] = value_parts[index] if index < len(value_parts) else ""
        rows.append(row)

    while len(rows) < MAX_ENTRY_ROWS:
        rows.append({"value": "", "usage": ""})

    return rows


def build_value_csv_values(rows: list[dict[str, str]], mode: str) -> tuple[str, str]:
    value_parts = []
    usage_parts = []

    for row in rows:
        value_text = canonicalize_named_entry(mode, row.get("value", ""))
        usage_text = normalize_usage_text(row.get("usage", ""))
        if not value_text and not usage_text:
            continue
        value_parts.append(value_text)
        usage_parts.append(format_percentage(float(usage_text)) if usage_text else "")

    return ":".join(value_parts), ":".join(usage_parts)


def row_text_for_mode(row: dict[str, str], mode: str) -> tuple[str, str]:
    field_config = entry_mode_fields(mode)
    return str(row.get(field_config["value_key"], "")).strip(), str(row.get(field_config["usage_key"], "")).strip()


def set_row_text_for_mode(row: dict[str, str], mode: str, values_text: str, usage_text: str) -> None:
    field_config = entry_mode_fields(mode)
    row[field_config["value_key"]] = values_text
    row[field_config["usage_key"]] = usage_text


def row_has_mode_values(row: dict[str, str], mode: str) -> bool:
    value_text, usage_text = row_text_for_mode(row, mode)
    return bool(value_text.strip() or usage_text.strip())


def display_mode_for_row(row: dict[str, str]) -> str:
    for mode in ENTRY_MODE_FIELDS:
        if row_has_mode_values(row, mode):
            return mode
    return DEFAULT_ENTRY_KIND


def infer_primary_max_usage(results: dict) -> int | None:
    usage_counts = {}
    for entry in iter_result_entries(results):
        data = entry.get("data", entry)
        usage_text = str(data.get("usage", "")).strip()
        try:
            usage = int(usage_text)
        except ValueError:
            continue
        usage_counts[usage] = usage_counts.get(usage, 0) + 1

    repeated_usages = [usage for usage, count in usage_counts.items() if count >= 2]
    if len(repeated_usages) < 3:
        return None
    return max(repeated_usages)


def load_editor_state(date_str: str, fmt_type: str) -> dict:
    state_path = editor_state_path_for(date_str, fmt_type)
    if not state_path.exists():
        return {"selected_images": {}}
    try:
        with open(state_path, encoding="utf-8") as handle:
            state = json.load(handle)
    except (json.JSONDecodeError, OSError):
        return {"selected_images": {}}
    selected_images = state.get("selected_images")
    if not isinstance(selected_images, dict):
        selected_images = {}
    return {"selected_images": selected_images}


def save_editor_state(date_str: str, fmt_type: str, selected_images: dict[str, str]) -> None:
    state_path = editor_state_path_for(date_str, fmt_type)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"selected_images": selected_images}
    with open(state_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)


def parse_named_spread(spread: str) -> dict[str, int]:
    values = {stat: 0 for stat in STAT_NAMES}
    for part in spread.split("/"):
        part = part.strip()
        if not part:
            continue
        match = re.match(r"^(\d+)\s+([A-Za-z]+)$", part)
        if not match:
            continue
        stat = STAT_CASE_MAP.get(match.group(2).lower())
        if stat:
            values[stat] = int(match.group(1))
    return values


def parse_spread_rows_from_csv(spreads_text: str, usage_text: str) -> list[dict[str, str]]:
    spread_parts = [part.strip() for part in spreads_text.split(":")] if spreads_text else []
    usage_parts = [part.strip() for part in usage_text.split(":")] if usage_text else []
    row_count = max(MAX_SPREAD_ROWS, len(spread_parts), len(usage_parts))
    rows: list[dict[str, str]] = []

    for index in range(min(row_count, MAX_SPREAD_ROWS)):
        row = {stat: "" for stat in STAT_NAMES}
        row["usage"] = usage_parts[index] if index < len(usage_parts) else ""
        if index < len(spread_parts) and spread_parts[index]:
            parsed = parse_named_spread(spread_parts[index])
            for stat in STAT_NAMES:
                value = parsed.get(stat, 0)
                row[stat] = str(value) if value else ""
        rows.append(row)

    while len(rows) < MAX_SPREAD_ROWS:
        row = {stat: "" for stat in STAT_NAMES}
        row["usage"] = ""
        rows.append(row)

    return rows


def merge_row_group(rows: list[dict]) -> dict:
    merged: dict[str, str] = {field: "" for field in CORE_FIELDS}
    pokemon_counts: dict[str, int] = {}

    for row in rows:
        pokemon = str(row.get("pokemon", "")).strip().lower()
        if pokemon:
            pokemon_counts[pokemon] = pokemon_counts.get(pokemon, 0) + 1
        for field in CORE_FIELDS:
            value = str(row.get(field, "")).strip()
            if value and not merged.get(field):
                merged[field] = value

    if pokemon_counts:
        merged["pokemon"] = max(pokemon_counts.items(), key=lambda item: (item[1], item[0]))[0]

    return merged


def normalize_usage_text(text: str) -> str:
    return text.strip().rstrip("%")


def format_percentage(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def row_has_any_values(row: dict[str, str]) -> bool:
    if normalize_usage_text(row.get("usage", "")):
        return True
    return any(str(row.get(stat, "")).strip() for stat in STAT_NAMES)


def analyze_spread_rows(rows: list[dict[str, str]]) -> tuple[list[str], list[str]]:
    errors = []
    warnings = []

    for index, row in enumerate(rows, start=1):
        usage_text = normalize_usage_text(row.get("usage", ""))
        stat_values = []
        has_stat_input = False

        for stat in STAT_NAMES:
            raw = str(row.get(stat, "")).strip()
            if not raw:
                stat_values.append(0)
                continue

            has_stat_input = True
            try:
                value = int(raw)
            except ValueError:
                errors.append(f"Row {index}: {stat} must be an integer")
                value = 0
            if value < 0 or value > 32:
                errors.append(f"Row {index}: {stat} must be between 0 and 32")
            stat_values.append(value)

        if not usage_text and not has_stat_input:
            continue

        if not usage_text:
            errors.append(f"Row {index}: usage is required when a spread is entered")
        else:
            try:
                usage_value = float(usage_text)
            except ValueError:
                errors.append(f"Row {index}: usage must be a number")
                usage_value = 0.0
            if usage_value < 0 or usage_value > 100:
                errors.append(f"Row {index}: usage must be between 0 and 100")

        if not has_stat_input:
            errors.append(f"Row {index}: at least one stat value is required")
            continue

        total = sum(stat_values)
        if total != 66:
            warnings.append(f"Row {index}: stat total is {total} instead of 66")

    return errors, warnings


def build_spread_csv_values(rows: list[dict[str, str]]) -> tuple[str, str]:
    spread_parts = []
    usage_parts = []

    for row in rows:
        usage_text = normalize_usage_text(row.get("usage", ""))
        values = [int(str(row.get(stat, "")).strip() or 0) for stat in STAT_NAMES]
        if not usage_text and sum(values) == 0:
            continue

        spread_parts.append(_values_to_spread([1, *values]))
        usage_parts.append(format_percentage(float(usage_text)) if usage_text else "")

    return ":".join(spread_parts), ":".join(usage_parts)


def build_field_csv_values(rows: list[dict[str, str]], mode: str) -> tuple[str, str]:
    if mode == "stat_points":
        return build_spread_csv_values(rows)
    return build_value_csv_values(rows, mode)


def filled_field_count(row: dict) -> int:
    count = 0
    for field in CORE_FIELDS:
        if field in {"date", "pokemon", "usage"}:
            continue
        if str(row.get(field, "")).strip():
            count += 1
    return count


def sort_csv_rows(rows: list[dict]) -> list[dict]:
    return sorted(
        rows,
        key=lambda row: (
            row.get("date", ""),
            usage_sort_key(row.get("usage", "")),
            row.get("pokemon", "").lower(),
        ),
    )


def clean_csv_rows(rows: list[dict], date_str: str) -> tuple[list[dict], dict, list[dict]]:
    pokemon_lookup = load_pokemon_lookup()
    known_names = build_known_pokemon_names(rows, exclude_date=date_str)
    authoritative_names, conflicts = build_prefilled_name_index(
        rows,
        date_str,
        known_names=known_names,
        pokemon_lookup=pokemon_lookup,
    )
    corrected_rows, _ = rectify_existing_rows_from_prefill(rows, date_str, authoritative_names)
    merged_rows, _ = merge_duplicate_csv_rows(corrected_rows, target_date=date_str)
    return merged_rows, authoritative_names, conflicts


def build_rows_from_results(results: dict, date_str: str) -> list[dict]:
    rows_by_usage: dict[str, list[dict]] = {}
    extracted_rows = iter_result_entries(results)

    for entry in extracted_rows:
        data = entry.get("data", entry)
        usage = str(data.get("usage", "")).strip()
        pokemon = str(data.get("pokemon", "")).strip().lower()
        if not usage or not pokemon:
            continue
        row = {"date": date_str}
        row.update(format_for_csv(data))
        rows_by_usage.setdefault(usage, []).append(row)

    rows = []
    for usage in sorted(rows_by_usage, key=usage_sort_key):
        group = rows_by_usage[usage]
        merged = merge_row_group(group)
        merged["date"] = date_str
        rows.append(merged)

    return rows


def save_entry_values_to_csv(
    date_str: str,
    fmt_type: str,
    pokemon: str,
    usage: str,
    mode: str,
    values_text: str,
    usage_text: str,
    csv_path: Path,
) -> None:
    rows, trailing = load_csv(csv_path)
    trailing_cols = trailing if csv_path.exists() else TRAILING_COLS[fmt_type]
    cleaned_rows, _, _ = clean_csv_rows(rows, date_str)

    target_key = (date_str, pokemon.lower(), str(usage).strip())
    target_row = None
    for row in cleaned_rows:
        key = (row.get("date", ""), row.get("pokemon", "").lower(), str(row.get("usage", "")).strip())
        if key == target_key:
            target_row = row
            break

    if target_row is None:
        target_row = {field: "" for field in CORE_FIELDS}
        target_row["date"] = date_str
        target_row["pokemon"] = pokemon.lower()
        target_row["usage"] = str(usage).strip()
        cleaned_rows.append(target_row)

    set_row_text_for_mode(target_row, mode, values_text, usage_text)
    save_csv(csv_path, sort_csv_rows(cleaned_rows), trailing_cols)


def build_editor_entries(
    date_str: str,
    fmt_type: str,
    csv_path: Path,
    results: dict,
    selected_images: dict[str, str],
) -> tuple[list[RosterEntry], list[dict], Path, int]:
    all_rows, trailing = load_csv(csv_path)
    cleaned_rows, _, conflicts = clean_csv_rows(all_rows, date_str)
    date_rows = [row for row in cleaned_rows if row.get("date", "") == date_str]

    if not date_rows and results:
        date_rows = build_rows_from_results(results, date_str)

    grouped_rows: dict[str, list[dict]] = {}
    for row in date_rows:
        usage = str(row.get("usage", "")).strip()
        if usage:
            grouped_rows.setdefault(usage, []).append(row)

    candidates_by_kind = collect_entry_image_candidates(results)
    entries: list[RosterEntry] = []

    for usage in sorted(grouped_rows, key=usage_sort_key):
        group = grouped_rows[usage]
        chosen_row = merge_row_group(group)

        note = ""
        distinct_names = sorted({row.get("pokemon", "") for row in group if row.get("pokemon", "")})
        if len(distinct_names) > 1:
            note = f"Multiple CSV names for this rank: {', '.join(distinct_names)}"

        row_candidates_by_kind: dict[str, list[CandidateImage]] = {}
        row_selected_images: dict[str, Path] = {}

        for mode in ENTRY_MODE_FIELDS:
            image_candidates = list(candidates_by_kind.get(mode, {}).get(usage, []))
            selected_path_text = selected_images.get(entry_image_key(usage, mode), "")
            if not selected_path_text and mode == "stat_points":
                selected_path_text = selected_images.get(usage, "")
            selected_path = Path(selected_path_text) if selected_path_text else None
            if selected_path and all(candidate.path != selected_path for candidate in image_candidates):
                image_candidates.insert(
                    0,
                    CandidateImage(
                        path=selected_path,
                        image_name=selected_path.name,
                        score=99,
                        screen_type=mode,
                        pokemon=chosen_row.get("pokemon", ""),
                        usage=usage,
                        exists=selected_path.exists(),
                    ),
                )

            if selected_path is None and image_candidates:
                selected_path = image_candidates[0].path

            row_candidates_by_kind[mode] = image_candidates
            if selected_path is not None:
                row_selected_images[mode] = selected_path

        entries.append(
            RosterEntry(
                date=date_str,
                format=fmt_type,
                pokemon=str(chosen_row.get("pokemon", "")).strip(),
                usage=usage,
                row=chosen_row,
                note=note,
                image_candidates_by_kind=row_candidates_by_kind,
                selected_images=row_selected_images,
            )
        )

    trailing_cols = trailing if csv_path.exists() else TRAILING_COLS[fmt_type]
    return entries, conflicts, csv_path if csv_path else DEFAULT_CSV[fmt_type], trailing_cols


def save_spreads_to_csv(
    date_str: str,
    fmt_type: str,
    pokemon: str,
    usage: str,
    spreads_text: str,
    spread_usage_text: str,
    csv_path: Path,
) -> None:
    save_entry_values_to_csv(
        date_str,
        fmt_type,
        pokemon,
        usage,
        "stat_points",
        spreads_text,
        spread_usage_text,
        csv_path,
    )


class StatSpreadEditorApp:
    def __init__(self, root: tk.Tk, date_str: str = "", fmt_type: str = "singles") -> None:
        self.root = root
        self.root.title("Stat Spread Editor")
        self.root.geometry("1500x900")

        self.date_var = tk.StringVar(value=date_str)
        self.format_var = tk.StringVar(value=fmt_type)
        self.results_path_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Load a date and format to begin.")
        self.current_label_var = tk.StringVar(value="")
        self.note_var = tk.StringVar(value="")
        self.image_info_var = tk.StringVar(value="")
        self.image_choice_var = tk.StringVar(value="")
        self.available_dates_by_format = discover_available_dates()

        self.entries: list[RosterEntry] = []
        self.current_index: int | None = None
        self.results_payload: dict = {}
        self.results_path: Path | None = None
        self.csv_path: Path | None = None
        self.trailing_cols = 0
        self.selected_images: dict[str, str] = {}
        self.current_image_options: list[tuple[str, Path]] = []
        self.active_results_folder: Path | None = None
        self.active_results_folder_was_unzipped = False
        self.entry_mode_var = tk.StringVar(value=entry_mode_label(DEFAULT_ENTRY_KIND))
        self.photo_image = None
        self.suspend_events = False
        self.suspend_tree_event = False
        self.dirty = False

        self.spread_row_vars: list[dict[str, tk.StringVar]] = []
        self.spread_sum_labels: list[ttk.Label] = []
        self.named_row_vars: list[dict[str, tk.StringVar]] = []
        self.named_value_boxes: list[SearchableCombobox] = []

        self._build_ui()
        self.refresh_date_choices(preserve_current=bool(date_str))
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        if date_str:
            self.load_run()

    def _cleanup_unzipped_results_folder(self) -> None:
        folder = self.active_results_folder
        was_unzipped = self.active_results_folder_was_unzipped
        self.active_results_folder = None
        self.active_results_folder_was_unzipped = False

        if not was_unzipped or folder is None:
            return

        try:
            archive_folder_to_zip(folder)
        except FileNotFoundError:
            return
        except Exception as exc:
            messagebox.showwarning(
                "Archive warning",
                f"Failed to re-archive image folder:\n{folder}\n\n{exc}",
            )

    def _prepare_results_folder(self, results_payload: dict) -> dict:
        folder_text = str(results_payload.get("folder", "")).strip()
        if not folder_text:
            self.active_results_folder = None
            self.active_results_folder_was_unzipped = False
            return results_payload

        folder = Path(folder_text)
        try:
            resolved_folder, was_unzipped = ensure_folder_unzipped(folder, folder_zip_path(folder))
        except FileNotFoundError:
            self.active_results_folder = None
            self.active_results_folder_was_unzipped = False
            return results_payload

        updated_payload = dict(results_payload)
        updated_payload["folder"] = str(resolved_folder)
        self.active_results_folder = resolved_folder
        self.active_results_folder_was_unzipped = was_unzipped
        return updated_payload

    def on_close(self) -> None:
        if not self.confirm_discard_changes():
            return
        self._cleanup_unzipped_results_folder()
        self.root.destroy()

    def _build_ui(self) -> None:
        root_frame = ttk.Frame(self.root, padding=10)
        root_frame.pack(fill="both", expand=True)

        controls = ttk.Frame(root_frame)
        controls.pack(fill="x", pady=(0, 10))

        ttk.Label(controls, text="Date").grid(row=0, column=0, padx=(0, 6), sticky="w")
        self.date_choice = ttk.Combobox(controls, textvariable=self.date_var, width=16)
        self.date_choice.grid(row=0, column=1, padx=(0, 12), sticky="w")
        ttk.Label(controls, text="Format").grid(row=0, column=2, padx=(0, 6), sticky="w")
        self.format_choice = ttk.Combobox(
            controls,
            textvariable=self.format_var,
            values=["singles", "doubles"],
            width=12,
            state="readonly",
        )
        self.format_choice.grid(row=0, column=3, padx=(0, 12), sticky="w")
        self.format_choice.bind("<<ComboboxSelected>>", self.on_format_changed)
        ttk.Button(controls, text="Load Run", command=self.load_run).grid(row=0, column=4, padx=(0, 8))
        ttk.Button(controls, text="Browse Results JSON", command=self.browse_results_json).grid(row=0, column=5, padx=(0, 8))
        ttk.Label(controls, textvariable=self.results_path_var).grid(row=1, column=0, columnspan=6, sticky="w", pady=(6, 0))

        main_pane = ttk.Panedwindow(root_frame, orient="horizontal")
        main_pane.pack(fill="both", expand=True)

        left_frame = ttk.Frame(main_pane, padding=(0, 0, 10, 0))
        right_frame = ttk.Frame(main_pane)
        main_pane.add(left_frame, weight=1)
        main_pane.add(right_frame, weight=4)

        self.tree = ttk.Treeview(left_frame, columns=("usage", "pokemon", "saved", "image"), show="headings", height=25)
        self.tree.heading("usage", text="Rank")
        self.tree.heading("pokemon", text="Pokemon")
        self.tree.heading("saved", text="Spreads")
        self.tree.heading("image", text="Image")
        self.tree.column("usage", width=70, anchor="center")
        self.tree.column("pokemon", width=180, anchor="w")
        self.tree.column("saved", width=80, anchor="center")
        self.tree.column("image", width=70, anchor="center")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)

        detail_header = ttk.Frame(right_frame)
        detail_header.pack(fill="x")
        ttk.Label(detail_header, textvariable=self.current_label_var, font=("Segoe UI", 15, "bold")).pack(anchor="w")
        ttk.Label(detail_header, textvariable=self.note_var, foreground="#7a4d00").pack(anchor="w", pady=(4, 0))

        image_controls = ttk.Frame(right_frame)
        image_controls.pack(fill="x", pady=(10, 8))
        ttk.Button(image_controls, text="Prev", command=self.select_previous).pack(side="left")
        ttk.Button(image_controls, text="Next", command=self.select_next).pack(side="left", padx=(8, 16))
        ttk.Label(image_controls, text="Image").pack(side="left", padx=(0, 6))
        self.image_choice = ttk.Combobox(image_controls, textvariable=self.image_choice_var, state="readonly", width=55)
        self.image_choice.pack(side="left", padx=(0, 8))
        self.image_choice.bind("<<ComboboxSelected>>", self.on_image_choice_changed)
        ttk.Button(image_controls, text="Choose Image...", command=self.choose_image).pack(side="left", padx=(0, 8))
        ttk.Button(image_controls, text="Reload From CSV", command=self.reload_current_row).pack(side="left")

        content_pane = ttk.Panedwindow(right_frame, orient="horizontal")
        content_pane.pack(fill="both", expand=True)

        image_frame = ttk.Frame(content_pane, padding=(0, 0, 10, 0))
        editor_column = ttk.Frame(content_pane)
        content_pane.add(image_frame, weight=3)
        content_pane.add(editor_column, weight=2)

        self.image_label = ttk.Label(
            image_frame,
            text="No image loaded",
            relief="solid",
            anchor="center",
            width=120,
        )
        self.image_label.pack(fill="both", expand=True)
        ttk.Label(image_frame, textvariable=self.image_info_var).pack(anchor="w", pady=(6, 0))

        mode_controls = ttk.Frame(editor_column)
        mode_controls.pack(fill="x", pady=(0, 8))
        ttk.Label(mode_controls, text="Edit Type").pack(side="left", padx=(0, 6))
        self.mode_choice = ttk.Combobox(
            mode_controls,
            textvariable=self.entry_mode_var,
            values=[entry_mode_label(mode) for mode in ENTRY_MODE_FIELDS],
            state="readonly",
            width=18,
        )
        self.mode_choice.pack(side="left")
        self.mode_choice.bind("<<ComboboxSelected>>", self.on_mode_changed)

        self.editor_stack = ttk.Frame(editor_column)
        self.editor_stack.pack(fill="both", expand=True)

        self.spread_editor_frame = ttk.LabelFrame(self.editor_stack, text="Stat Spreads", padding=10)
        self.named_editor_frame = ttk.LabelFrame(self.editor_stack, text="Named Entries", padding=10)
        self._build_spread_editor_frame(self.spread_editor_frame)
        self._build_named_editor_frame(self.named_editor_frame)

        self._show_editor_frame(DEFAULT_ENTRY_KIND)

        buttons = ttk.Frame(editor_column)
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text="Clear", command=self.clear_editor).pack(side="left")
        ttk.Button(buttons, text="Save", command=self.save_current_row).pack(side="right")
        ttk.Button(buttons, text="Save + Next", command=lambda: self.save_current_row(move_next=True)).pack(side="right", padx=(0, 8))

        status = ttk.Label(root_frame, textvariable=self.status_var)
        status.pack(fill="x", pady=(10, 0))

        for row_vars in self.spread_row_vars + self.named_row_vars:
            for variable in row_vars.values():
                variable.trace_add("write", self.on_editor_changed)

    def _build_spread_editor_frame(self, editor_frame: ttk.LabelFrame) -> None:
        headings = ["#", "Usage %", *STAT_NAMES, "Sum"]
        for column, heading in enumerate(headings):
            ttk.Label(editor_frame, text=heading).grid(row=0, column=column, padx=4, pady=4, sticky="w")

        for row_index in range(MAX_SPREAD_ROWS):
            ttk.Label(editor_frame, text=str(row_index + 1)).grid(row=row_index + 1, column=0, padx=4, pady=3, sticky="w")
            row_vars = {"usage": tk.StringVar()}
            usage_entry = ttk.Entry(editor_frame, textvariable=row_vars["usage"], width=10)
            usage_entry.grid(row=row_index + 1, column=1, padx=4, pady=3)

            for column, stat in enumerate(STAT_NAMES, start=2):
                row_vars[stat] = tk.StringVar()
                stat_entry = ttk.Entry(editor_frame, textvariable=row_vars[stat], width=6)
                stat_entry.grid(row=row_index + 1, column=column, padx=4, pady=3)

            sum_label = ttk.Label(editor_frame, text="")
            sum_label.grid(row=row_index + 1, column=len(headings) - 1, padx=4, pady=3, sticky="w")
            self.spread_sum_labels.append(sum_label)
            self.spread_row_vars.append(row_vars)

    def _build_named_editor_frame(self, editor_frame: ttk.LabelFrame) -> None:
        headings = ["#", "Usage %", "Value"]
        for column, heading in enumerate(headings):
            ttk.Label(editor_frame, text=heading).grid(row=0, column=column, padx=4, pady=4, sticky="w")

        for row_index in range(MAX_ENTRY_ROWS):
            ttk.Label(editor_frame, text=str(row_index + 1)).grid(row=row_index + 1, column=0, padx=4, pady=3, sticky="w")
            row_vars = {"usage": tk.StringVar(), "value": tk.StringVar()}
            usage_entry = ttk.Entry(editor_frame, textvariable=row_vars["usage"], width=10)
            usage_entry.grid(row=row_index + 1, column=1, padx=4, pady=3)

            value_box = SearchableCombobox(editor_frame, textvariable=row_vars["value"], width=32)
            value_box.grid(row=row_index + 1, column=2, padx=4, pady=3, sticky="we")
            self.named_value_boxes.append(value_box)
            self.named_row_vars.append(row_vars)

    def _show_editor_frame(self, mode: str) -> None:
        for child in self.editor_stack.winfo_children():
            child.pack_forget()
        frame = self.spread_editor_frame if mode == "stat_points" else self.named_editor_frame
        frame.pack(fill="both", expand=True)
        self._refresh_named_options(mode)

    def _refresh_named_options(self, mode: str) -> None:
        if mode == "stat_points":
            return
        options = filter_lookup_options(mode, "")
        for box in self.named_value_boxes:
            box.set_options(options)

    def current_mode(self) -> str:
        label = self.entry_mode_var.get().strip()
        for mode, mode_label in ENTRY_MODE_LABELS.items():
            if label == mode or label == mode_label:
                return mode
        for mode, mode_label in ENTRY_MODE_LABELS.items():
            if label.casefold() == mode_label.casefold():
                return mode
        return DEFAULT_ENTRY_KIND

    def on_mode_changed(self, _event=None) -> None:
        mode = self.current_mode()
        self.entry_mode_var.set(entry_mode_label(mode))
        self._show_editor_frame(mode)
        for index in range(len(self.entries)):
            self.refresh_tree_row(index)
        if self.current_index is not None:
            self.populate_editor(self.entries[self.current_index])
            self.refresh_image_options(self.entries[self.current_index])
        self.update_sum_labels()

    def entry_selected_image_key(self, usage: str, mode: str) -> str:
        return entry_image_key(usage, mode)

    def selected_image_for_entry(self, entry: RosterEntry, mode: str) -> Path | None:
        selected = entry.selected_images.get(mode)
        if selected is not None:
            return selected
        if mode == "stat_points":
            legacy = self.selected_images.get(entry.usage, "")
            return Path(legacy) if legacy else None
        return None

    def browse_results_json(self) -> None:
        path_text = filedialog.askopenfilename(
            title="Choose results JSON",
            initialdir=str(RESULTS_DIR),
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path_text:
            return
        self.results_path = Path(path_text)
        self.results_path_var.set(str(self.results_path))
        self.load_run()

    def refresh_date_choices(self, preserve_current: bool = True) -> None:
        self.available_dates_by_format = discover_available_dates()
        fmt_type = self.format_var.get().strip().lower() or "singles"
        available_dates = self.available_dates_by_format.get(fmt_type, [])
        current_date = self.date_var.get().strip()
        self.date_choice["values"] = available_dates

        if preserve_current and current_date:
            return
        if current_date in available_dates:
            return
        if not current_date and available_dates:
            self.date_var.set(available_dates[0])

    def on_format_changed(self, _event=None) -> None:
        current_date = self.date_var.get().strip()
        self.refresh_date_choices(preserve_current=bool(current_date))

    def load_run(self) -> None:
        if not self.confirm_discard_changes():
            return

        self._cleanup_unzipped_results_folder()

        try:
            date_str = normalize_date(self.date_var.get())
        except ValueError as exc:
            messagebox.showerror("Invalid date", str(exc))
            return

        fmt_type = self.format_var.get().strip().lower()
        if fmt_type not in DEFAULT_CSV:
            messagebox.showerror("Invalid format", "Format must be 'singles' or 'doubles'")
            return

        auto_results_path = results_json_path_for(date_str, fmt_type)
        if not self.results_path or normalize_date_from_path(self.results_path) != (date_str, fmt_type):
            self.results_path = auto_results_path if auto_results_path.exists() else None

        self.results_payload = self._prepare_results_folder(load_results_payload(self.results_path))
        self.results_path_var.set(str(self.results_path) if self.results_path else "No results JSON loaded")
        self.csv_path = DEFAULT_CSV[fmt_type]
        state = load_editor_state(date_str, fmt_type)
        self.selected_images = {key: str(value) for key, value in state.get("selected_images", {}).items()}

        if self.current_mode() not in ENTRY_MODE_FIELDS:
            self.entry_mode_var.set(entry_mode_label(DEFAULT_ENTRY_KIND))
        self._show_editor_frame(self.current_mode())

        entries, conflicts, _, trailing_cols = build_editor_entries(
            date_str,
            fmt_type,
            self.csv_path,
            self.results_payload,
            self.selected_images,
        )
        self.trailing_cols = trailing_cols
        self.entries = entries
        self.current_index = None
        self.dirty = False

        self.suspend_tree_event = True
        for item in self.tree.get_children():
            self.tree.delete(item)
        for index, entry in enumerate(self.entries):
            active_mode = self.current_mode()
            saved_text = "yes" if row_has_mode_values(entry.row, active_mode) else ""
            image_text = "yes" if self.selected_image_for_entry(entry, active_mode) else ""
            self.tree.insert("", "end", iid=str(index), values=(entry.usage, entry.pokemon, saved_text, image_text))
        self.suspend_tree_event = False

        if conflicts:
            self.status_var.set(
                f"Loaded {len(self.entries)} entries. Prefill conflicts detected for {len(conflicts)} ranks."
            )
        elif self.results_payload:
            self.status_var.set(f"Loaded {len(self.entries)} entries and results JSON candidates.")
        else:
            self.status_var.set(f"Loaded {len(self.entries)} entries. No results JSON found; use the image picker.")

        if self.entries:
            self.select_index(0, force=True)
        else:
            self.clear_view()

    def select_index(self, index: int, force: bool = False) -> None:
        if index < 0 or index >= len(self.entries):
            return
        if not force and not self.confirm_discard_changes():
            self.restore_tree_selection()
            return

        self.current_index = index
        entry = self.entries[index]

        self.suspend_tree_event = True
        self.tree.selection_set(str(index))
        self.tree.focus(str(index))
        self.tree.see(str(index))
        self.suspend_tree_event = False

        self.current_label_var.set(f"Rank {entry.usage} - {entry.pokemon} ({entry_mode_label(self.current_mode())})")
        self.note_var.set(entry.note)
        self.populate_editor(entry)
        self.refresh_image_options(entry)
        self.dirty = False

    def restore_tree_selection(self) -> None:
        if self.current_index is None:
            return
        self.suspend_tree_event = True
        self.tree.selection_set(str(self.current_index))
        self.tree.focus(str(self.current_index))
        self.suspend_tree_event = False

    def on_tree_select(self, _event=None) -> None:
        if self.suspend_tree_event:
            return
        selection = self.tree.selection()
        if not selection:
            return
        index = int(selection[0])
        if index == self.current_index:
            return
        self.select_index(index)

    def select_previous(self) -> None:
        if self.current_index is None:
            return
        self.select_index(self.current_index - 1)

    def select_next(self) -> None:
        if self.current_index is None:
            return
        self.select_index(self.current_index + 1)

    def refresh_image_options(self, entry: RosterEntry) -> None:
        mode = self.current_mode()
        options = []
        seen = set()
        for candidate in entry.image_candidates_by_kind.get(mode, []):
            path_key = str(candidate.path)
            if path_key in seen:
                continue
            seen.add(path_key)
            exists_text = "" if candidate.exists else " (missing)"
            label = f"{candidate.image_name} [{candidate.screen_type}]{exists_text}"
            options.append((label, candidate.path))

        selected_path = self.selected_image_for_entry(entry, mode)
        if selected_path is not None and str(selected_path) not in seen:
            options.insert(0, (f"{selected_path.name} [manual]", selected_path))

        self.current_image_options = options
        self.image_choice["values"] = [label for label, _ in options]

        if selected_path is not None:
            for label, path in options:
                if path == selected_path:
                    self.image_choice_var.set(label)
                    break
            else:
                self.image_choice_var.set("")
        else:
            self.image_choice_var.set("")

        self.show_image(selected_path)

    def show_image(self, image_path: Path | None) -> None:
        self.photo_image = None
        if image_path is None:
            self.image_label.configure(image="", text="No image selected")
            self.image_info_var.set("Choose an image for this rank.")
            return

        path = Path(image_path)
        if not path.exists():
            self.image_label.configure(image="", text="Image file not found")
            self.image_info_var.set(str(path))
            return

        if Image is None or ImageOps is None or ImageTk is None:
            self.image_label.configure(image="", text="Pillow is required to preview JPEG images")
            self.image_info_var.set("Install dependencies with: pip install -r requirements.txt")
            return

        try:
            image = Image.open(path)
            image = ImageOps.exif_transpose(image)
            image.thumbnail(PREVIEW_SIZE)
            self.photo_image = ImageTk.PhotoImage(image)
        except OSError as exc:
            self.image_label.configure(image="", text="Failed to open image")
            self.image_info_var.set(f"{path} ({exc})")
            return

        self.image_label.configure(image=self.photo_image, text="")
        self.image_info_var.set(str(path))

    def on_image_choice_changed(self, _event=None) -> None:
        if self.current_index is None:
            return
        selected_label = self.image_choice_var.get()
        mode = self.current_mode()
        for label, path in self.current_image_options:
            if label == selected_label:
                entry = self.entries[self.current_index]
                entry.selected_images[mode] = path
                self.selected_images[self.entry_selected_image_key(entry.usage, mode)] = str(path)
                if mode == "stat_points":
                    self.selected_images[entry.usage] = str(path)
                save_editor_state(entry.date, entry.format, self.selected_images)
                self.show_image(path)
                self.refresh_tree_row(self.current_index)
                return

    def choose_image(self) -> None:
        if self.current_index is None:
            return
        entry = self.entries[self.current_index]
        mode = self.current_mode()
        initial_dir = None
        selected_path = self.selected_image_for_entry(entry, mode)
        if selected_path is not None:
            initial_dir = str(selected_path.parent)
        elif self.results_payload.get("folder"):
            initial_dir = self.results_payload.get("folder")

        path_text = filedialog.askopenfilename(
            title=f"Choose {entry_mode_label(mode).lower()} image for rank {entry.usage}",
            initialdir=initial_dir,
            filetypes=[("Image files", "*.jpeg *.jpg *.png"), ("All files", "*.*")],
        )
        if not path_text:
            return

        selected_path = Path(path_text)
        entry.selected_images[mode] = selected_path
        self.selected_images[self.entry_selected_image_key(entry.usage, mode)] = str(selected_path)
        if mode == "stat_points":
            self.selected_images[entry.usage] = str(selected_path)
        save_editor_state(entry.date, entry.format, self.selected_images)
        self.refresh_image_options(entry)
        self.refresh_tree_row(self.current_index)

    def populate_editor(self, entry: RosterEntry) -> None:
        mode = self.current_mode()
        field_config = entry_mode_fields(mode)
        values_text, usage_text = row_text_for_mode(entry.row, mode)

        if field_config["kind"] == "spread":
            rows = parse_spread_rows_from_csv(values_text, usage_text)
            self.suspend_events = True
            for row_vars, row_values in zip(self.spread_row_vars, rows):
                row_vars["usage"].set(row_values["usage"])
                for stat in STAT_NAMES:
                    row_vars[stat].set(row_values[stat])
            for row_vars in self.named_row_vars:
                row_vars["usage"].set("")
                row_vars["value"].set("")
        else:
            rows = parse_value_rows_from_csv(values_text, usage_text)
            self.suspend_events = True
            for row_vars, row_values in zip(self.named_row_vars, rows):
                row_vars["usage"].set(row_values["usage"])
                row_vars["value"].set(row_values["value"])
            for row_vars in self.spread_row_vars:
                row_vars["usage"].set("")
                for stat in STAT_NAMES:
                    row_vars[stat].set("")
        self.suspend_events = False
        self.update_sum_labels()

    def reload_current_row(self) -> None:
        if self.current_index is None:
            return
        if not self.confirm_discard_changes():
            return
        entry = self.entries[self.current_index]
        self.populate_editor(entry)
        self.dirty = False
        self.status_var.set(f"Reloaded rank {entry.usage} from CSV")

    def clear_editor(self) -> None:
        self.suspend_events = True
        for row_vars in self.spread_row_vars:
            row_vars["usage"].set("")
            for stat in STAT_NAMES:
                row_vars[stat].set("")
        for row_vars in self.named_row_vars:
            row_vars["usage"].set("")
            row_vars["value"].set("")
        self.suspend_events = False
        self.update_sum_labels()
        self.dirty = True

    def on_editor_changed(self, *_args) -> None:
        if self.suspend_events:
            return
        self.dirty = True
        self.update_sum_labels()

    def update_sum_labels(self) -> None:
        for row_vars, label in zip(self.spread_row_vars, self.spread_sum_labels):
            values = []
            has_any = False
            for stat in STAT_NAMES:
                raw = row_vars[stat].get().strip()
                if raw:
                    has_any = True
                    try:
                        values.append(int(raw))
                    except ValueError:
                        label.configure(text="?")
                        break
                else:
                    values.append(0)
            else:
                if not has_any and not normalize_usage_text(row_vars["usage"].get()):
                    label.configure(text="")
                else:
                    total = sum(values)
                    suffix = "" if total == 66 else " !"
                    label.configure(text=f"{total}{suffix}")

    def current_editor_rows(self) -> list[dict[str, str]]:
        mode = self.current_mode()
        if mode == "stat_points":
            rows = []
            for row_vars in self.spread_row_vars:
                row = {"usage": row_vars["usage"].get()}
                for stat in STAT_NAMES:
                    row[stat] = row_vars[stat].get()
                rows.append(row)
            return rows

        rows = []
        for row_vars in self.named_row_vars:
            rows.append({"usage": row_vars["usage"].get(), "value": row_vars["value"].get()})
        return rows

    def save_current_row(self, move_next: bool = False) -> None:
        if self.current_index is None or self.csv_path is None:
            return

        entry = self.entries[self.current_index]
        mode = self.current_mode()
        rows = self.current_editor_rows()

        if mode == "stat_points":
            errors, warnings = analyze_spread_rows(rows)
            if errors:
                messagebox.showerror("Invalid stat spreads", "\n".join(errors))
                return

            if warnings:
                should_continue = messagebox.askyesno(
                    "Confirm stat totals",
                    "The following rows do not total 66:\n\n"
                    + "\n".join(warnings)
                    + "\n\nDo you want to save anyway?",
                )
                if not should_continue:
                    return

            values_text, usage_text = build_field_csv_values(rows, mode)
        else:
            values_text, usage_text = build_field_csv_values(rows, mode)

        save_entry_values_to_csv(
            entry.date,
            entry.format,
            entry.pokemon,
            entry.usage,
            mode,
            values_text,
            usage_text,
            self.csv_path,
        )

        set_row_text_for_mode(entry.row, mode, values_text, usage_text)
        self.dirty = False
        self.refresh_tree_row(self.current_index)
        self.status_var.set(f"Saved {entry_mode_label(mode).lower()} for rank {entry.usage} ({entry.pokemon})")

        if move_next:
            self.select_index(self.current_index + 1)

    def refresh_tree_row(self, index: int) -> None:
        entry = self.entries[index]
        mode = self.current_mode()
        saved_text = "yes" if row_has_mode_values(entry.row, mode) else ""
        image_text = "yes" if self.selected_image_for_entry(entry, mode) else ""
        self.tree.item(str(index), values=(entry.usage, entry.pokemon, saved_text, image_text))

    def confirm_discard_changes(self) -> bool:
        if not self.dirty:
            return True
        result = messagebox.askyesnocancel(
            "Unsaved changes",
            f"Save the current {entry_mode_label(self.current_mode()).lower()} changes before switching?",
        )
        if result is None:
            return False
        if result:
            self.save_current_row()
            return not self.dirty
        return True

    def clear_view(self) -> None:
        self.current_label_var.set("")
        self.note_var.set("")
        self.image_choice_var.set("")
        self.image_choice["values"] = []
        self.current_image_options = []
        self.show_image(None)
        self.suspend_events = True
        for row_vars in self.spread_row_vars:
            row_vars["usage"].set("")
            for stat in STAT_NAMES:
                row_vars[stat].set("")
        for row_vars in self.named_row_vars:
            row_vars["usage"].set("")
            row_vars["value"].set("")
        self.suspend_events = False
        self.update_sum_labels()


def normalize_date_from_path(path: Path | None) -> tuple[str, str] | None:
    if path is None:
        return None
    match = re.match(r"^(\d{8})(singles|doubles)_extracted\.json$", path.name)
    if not match:
        return None
    date_digits, fmt_type = match.groups()
    date_str = normalize_date(date_digits)
    return date_str, fmt_type


def main() -> None:
    parser = argparse.ArgumentParser(description="Manual stat spread editor")
    parser.add_argument("--date", help="Date to load (YYYY-MM-DD or YYYYMMDD)")
    parser.add_argument("--format", choices=["singles", "doubles"], default="singles")
    args = parser.parse_args()

    date_str = normalize_date(args.date) if args.date else ""
    root = tk.Tk()
    app = StatSpreadEditorApp(root, date_str=date_str, fmt_type=args.format)

    if Image is None or ImageTk is None:
        app.status_var.set("Install dependencies with: pip install -r requirements.txt")

    root.mainloop()


if __name__ == "__main__":
    main()