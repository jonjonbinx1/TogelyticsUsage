#!/usr/bin/env python3
"""
Pokemon Usage Stats Image Extractor

Uses the Ollama vision model (qwen3-vl:30b-a3b) to read Pokemon competitive
usage statistic screenshots and upsert the extracted data into CSV files.

Folder naming convention: YYYYMMDDsingles / YYYYMMDDdoubles
  - Date parsed from folder name maps to the 'date' column in CSV.

Usage:
    # Let the script choose the newest data folder (prompt for singles/doubles if needed):
    python extractor.py

    # Dry run (preview what would change, no CSV write):
    python extractor.py data/doubles/20260427doubles

    # Apply changes to CSV:
    python extractor.py data/doubles/20260427doubles --apply

    # Process a singles folder with verbose output:
    python extractor.py data/singles/20260503singles --apply --verbose

    # Use a custom CSV path:
    python extractor.py data/doubles/20260427doubles --apply --csv my_doubles.csv

    # Resume from a specific image index (e.g. after a crash):
    python extractor.py data/doubles/20260427doubles --apply --start 20
"""

import os
import re
import csv
import json
import base64
import argparse
import logging
import time
import sys
import subprocess
from copy import deepcopy
from pathlib import Path

import requests

# Load local .env files without requiring an extra dependency.
def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return

    try:
        with open(path, encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:].lstrip()
                if "=" not in line:
                    continue

                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if not key:
                    continue

                if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                    value = value[1:-1]

                os.environ.setdefault(key, value)
    except OSError:
        return


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).parent
for _env_path in (Path.cwd() / ".env", SCRIPT_DIR / ".env"):
    _load_env_file(_env_path)

AI_PROVIDER = os.getenv("AI_PROVIDER", "ollama").strip().lower()
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").strip()
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3-vl:30b-a3b").strip()
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").strip()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini").strip()
OPENROUTER_HTTP_REFERER = os.getenv("OPENROUTER_HTTP_REFERER", "").strip()
OPENROUTER_X_TITLE = os.getenv("OPENROUTER_X_TITLE", "").strip()
RESULTS_DIR = SCRIPT_DIR / "results"

DEFAULT_CSV = {
    "doubles": SCRIPT_DIR / "champions_doubles.csv",
    "singles": SCRIPT_DIR / "champions_singles.csv",
}

# These are the real data columns in the CSV; trailing empty columns are
# handled separately to preserve the original file format.
CORE_FIELDS = [
    "date", "pokemon", "usage",
    "moves", "move usage",
    "ability", "ability usage",
    "sp", "sp usage",
    "nature", "nature usage",
    "item", "item usage",
]

# Number of trailing empty columns per format (to keep original CSV shape)
TRAILING_COLS = {
    "doubles": 2,
    "singles": 4,
}

AUTO_POKEMON_CONSENSUS_MIN_COUNT = 2
AUTO_POKEMON_CONSENSUS_MIN_SCORE = 60.0
AUTO_POKEMON_REVIEW_THRESHOLD = 80.0

DEFAULT_AI_MODEL = os.getenv("AI_MODEL", "").strip() or (
    OLLAMA_MODEL if AI_PROVIDER == "ollama" else (
        OPENROUTER_MODEL if AI_PROVIDER == "openrouter" else OPENAI_MODEL
    )
)

# ---------------------------------------------------------------------------
# Extraction prompt
# ---------------------------------------------------------------------------
EXTRACTION_PROMPT = """/no_think
You are reading a Pokemon competitive usage statistics screenshot from Pokemon HOME.
The screen shows ONE of five possible stat categories. Read the title to determine which.

Always return ONLY this JSON — no markdown fences, no explanation, no extra text:
{
  "screen_type": "moves|ability|held_item|stat_points|stat_alignment",
  "pokemon": "pokemon-name",
  "usage": 1,
  "moves": [],
  "move_usage": [],
  "abilities": [],
  "ability_usage": [],
  "spread_values": [],
  "spread_usage": [],
  "natures": [],
  "nature_usage": [],
  "items": [],
  "item_usage": []
}

NOTE: "spread_values" is a list of lists — each inner list has exactly 6 integers [HP, Atk, Def, SpA, SpD, Spe].
Do NOT use a "spreads" key for named strings. Output the raw numbers only.

======================================================================
SCREEN TYPE IDENTIFICATION (read the large white title text):
======================================================================

--- SCREEN: "Moves" ---
Lists move names with a rank number and usage percentage.
→ Fill "moves" and "move_usage". All other lists stay empty.
Example row: "1 / 96.3% / Flower Trick"  →  moves=["flower trick"], move_usage=[96.3]
CRITICAL: "moves" must be a list of plain strings — NOT sub-lists. Each entry is just the move name.

--- SCREEN: "Ability" ---
Lists ability names with rank and usage percentage.
→ Fill "abilities" and "ability_usage". All other lists stay empty.
CRITICAL: "abilities" must be a list of plain strings — NOT sub-lists. Each entry is just the ability name.

--- SCREEN: "Held Item" ---
Lists item names (with coloured item icons) with rank and usage percentage.
→ Fill "items" and "item_usage". All other lists stay empty.
CRITICAL: "items" must be a list of plain strings — NOT sub-lists. Each entry is just the item name.
DO NOT include rank numbers or percentages in the items list. Example:
  CORRECT:   items=["sitrus berry", "kasib berry", "leftovers"]
  WRONG:     items=[[1, "Sitrus Berry", 31.8], [2, "Kasib Berry", 23.5]]

--- SCREEN: "Stat Alignment" (= Nature) ---
Lists nature names (Jolly, Adamant, Timid, etc.) with rank and usage %.
Each row also shows which stat is boosted (red up-arrow ∧) and which is lowered (blue down-arrow ∨) — ignore those arrows, only extract the nature name.
→ Fill "natures" and "nature_usage". All other lists stay empty.
CRITICAL: "natures" must be a list of plain strings — NOT sub-lists. Each entry is just the nature name.

--- SCREEN: "Stat Points" (= EV Spreads) ---
Each visible row has exactly 8 values left to right:
  col 1 = ROW RANK   (an integer: 1, 2, 3, 4, 5) This is stacked above the usage %
  col 2 = USAGE %    → store in spread_usage
  col 3 to col 8     = 6 stat numbers, read strictly left to right

INCLUDE the row rank in the output. For EACH row output a 7-integer list:
  [ROW_RANK, stat1, stat2, stat3, stat4, stat5, stat6]

DO NOT name or label the stats. DO NOT skip any number. Read every number you
see in the row from left to right, keeping the row rank as the first element.
IMPORTANT: the row rank is only a label. Do NOT treat it as HP or merge it into
the first stat. The 6 stat values start after the usage percentage.

Example: row reads "1  28.3%  2  0  0  32  0  32"
  USAGE=28.3 → spread_usage
  spread_values entry: [1, 2, 0, 0, 32, 0, 32]   ← rank=1, then 6 stat numbers

More examples (full row shown → 7-element output):
  "1  55.6%  2  32   0   0   0  32"  →  [1,  2, 32,  0,  0,  0, 32]
  "2   7.4%  0  32   2   0   0  32"  →  [2,  0, 32,  2,  0,  0, 32]
  "3   5.4%  0  32   1   0   1  32"  →  [3,  0, 32,  1,  0,  1, 32]
  "4   4.2%  0  32   0   0   2  32"  →  [4,  0, 32,  0,  0,  2, 32]
  "5   2.0% 32   0  11   0  12  11"  →  [5, 32,  0, 11,  0, 12, 11]

CRITICAL: each inner list MUST have exactly 7 integers; the first is always the
row rank (1, 2, 3, …). The 6 numbers after the rank are read left to right.

→ Fill "spread_values" (list of 7-integer lists) and "spread_usage". All other lists stay empty.

======================================================================
GLOBAL RULES (apply to all screen types):
======================================================================
- "pokemon": name shown in top header bar, lowercase, hyphens for compound names
    e.g. "rotom-wash", "tapu-koko", "mr-mime", "iron-hands", "meowscarada"
- "usage": the rank INTEGER of this POKEMON (the #N shown top-left of the screen, NOT the row rank)
- All name fields: lowercase with spaces  e.g. "close combat", "rough skin", "choice scarf", "jolly"
- Percentages: decimal number only, no percent sign  e.g. 99.5 not "99.5%"
- Include ALL visible rows in the image
- Fields for other screen types: use empty list []
- CRITICAL: moves, abilities, items, and natures MUST be lists of plain strings.
  DO NOT nest sub-lists like [1, "name", 31.8] or [1, "name"].
  Each entry is just the name string: ["sitrus berry", "leftovers"]

Return ONLY the JSON object. Nothing before or after it.
"""


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        level=level,
    )


# ---------------------------------------------------------------------------
# Folder / date parsing
# ---------------------------------------------------------------------------
def parse_folder_name(folder_name: str) -> tuple:
    """Parse '20260427doubles' → ('2026-04-27', 'doubles')."""
    m = re.match(r"^(\d{4})(\d{2})(\d{2})(singles|doubles)$", folder_name, re.IGNORECASE)
    if not m:
        raise ValueError(
            f"Folder '{folder_name}' does not match expected pattern "
            "YYYYMMDDsingles or YYYYMMDDdoubles"
        )
    year, month, day, fmt = m.groups()
    return f"{year}-{month}-{day}", fmt.lower()


def discover_input_folders(data_root: Path | None = None) -> list:
    """Return known extraction folders sorted newest-first."""
    data_root = data_root or (SCRIPT_DIR / "data")
    candidates = []
    pattern = re.compile(r"^(\d{8})(singles|doubles)$", re.IGNORECASE)

    for fmt_type in ("singles", "doubles"):
        fmt_dir = data_root / fmt_type
        if not fmt_dir.is_dir():
            continue

        for child in fmt_dir.iterdir():
            if not child.is_dir():
                continue
            match = pattern.match(child.name)
            if not match:
                continue
            date_digits, parsed_fmt = match.groups()
            candidates.append((date_digits, parsed_fmt.lower(), child.resolve()))

    candidates.sort(
        key=lambda item: (item[0], item[1] == "singles", item[2].stat().st_mtime),
        reverse=True,
    )
    return [item[2] for item in candidates]


def discover_recent_results_folder(results_dir: Path | None = None) -> Path | None:
    """Return the newest extracted folder inferred from results JSON files."""
    results_dir = results_dir or RESULTS_DIR
    if not results_dir.is_dir():
        return None

    pattern = re.compile(r"^(\d{8})(singles|doubles)_extracted\.json$")
    candidates = []

    for path in results_dir.iterdir():
        match = pattern.match(path.name)
        if not match:
            continue

        date_digits, fmt_type = match.groups()
        folder = SCRIPT_DIR / "data" / fmt_type / f"{date_digits}{fmt_type}"
        if folder.is_dir():
            candidates.append((date_digits, path.stat().st_mtime, folder.resolve()))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return candidates[0][2]


def prompt_for_format_choice(candidates: list) -> Path:
    latest_date, _ = parse_folder_name(candidates[0].name)
    format_map = {parse_folder_name(folder.name)[1]: folder for folder in candidates}
    default_folder = format_map.get("singles", candidates[0])

    print(f"No folder argument was provided. Latest date {latest_date} has multiple formats.")
    print("Choose format: singles or doubles [s/d] (default s)")

    while True:
        choice = input("Format [s/d]: ").strip().lower()
        if not choice:
            return default_folder
        if choice in {"s", "singles"} and "singles" in format_map:
            return format_map["singles"]
        if choice in {"d", "doubles"} and "doubles" in format_map:
            return format_map["doubles"]
        print("Invalid selection. Enter 's' for singles, 'd' for doubles, or press Enter for the default.")


def resolve_input_folder(folder_arg: str | None, prompt_user: bool | None = None) -> tuple:
    """Resolve the folder argument or infer one from known data folders.

    Returns (folder_path, resolution_note).
    """
    if folder_arg:
        return Path(folder_arg).resolve(), ""

    # Prefer data folders (newest available) when they exist. Fall back to
    # the most-recent results JSON only if no data folders are present.
    candidates = discover_input_folders()
    if candidates:
        latest_date, _ = parse_folder_name(candidates[0].name)
        latest_candidates = [folder for folder in candidates if parse_folder_name(folder.name)[0] == latest_date]

        if len(latest_candidates) == 1:
            folder = latest_candidates[0]
            return folder, f"No folder provided; using newest folder {folder.name}"

        if prompt_user is None:
            prompt_user = sys.stdin.isatty()

        if prompt_user:
            folder = prompt_for_format_choice(latest_candidates)
            return folder, f"No folder provided; selected {folder.name}"

        folder = candidates[0]
        return folder, f"No folder provided; using newest folder {folder.name}"

    # No data folders found — try to infer from the results JSON files.
    recent_results_folder = discover_recent_results_folder()
    if recent_results_folder is not None:
        return (
            recent_results_folder,
            f"No folder provided; using most recent results folder {recent_results_folder.name}",
        )

    raise ValueError("No extraction folders were found under data/singles or data/doubles")


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------
def get_images(folder: Path) -> list:
    """Return JPEG/PNG files sorted by their trailing integer (image0, image1, …)."""
    exts = {".jpeg", ".jpg", ".png"}
    images = [p for p in folder.iterdir() if p.suffix.lower() in exts]

    def _key(p: Path) -> int:
        m = re.search(r"(\d+)", p.stem)
        return int(m.group(1)) if m else 0

    return sorted(images, key=_key)


def encode_image(path: Path) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


# ---------------------------------------------------------------------------
# Ollama vision inference
# ---------------------------------------------------------------------------
def _image_media_type(image_path: Path) -> str:
    suffix = image_path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    return "image/jpeg"


def _call_ollama(image_path: Path, model: str, prompt: str, attempt: int) -> str:
    """Raw Ollama call; returns the response text (may be empty)."""
    logger = logging.getLogger(__name__)
    payload = {
        "model": model,
        "prompt": prompt,
        "images": [encode_image(image_path)],
        "stream": False,
        "options": {
            "temperature": 0.1,
            "num_predict": 2048,
            "think": False,
        },
    }
    start = time.time()
    resp = requests.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=300)
    resp.raise_for_status()
    elapsed = time.time() - start
    logger.debug(f"  attempt={attempt} HTTP {resp.status_code} in {elapsed:.2f}s")
    try:
        j = resp.json()
        return (j.get("response", "") if isinstance(j, dict) else resp.text) or ""
    except ValueError:
        return resp.text or ""


def _call_openai_compatible(
    image_path: Path,
    model: str,
    prompt: str,
    attempt: int,
    base_url: str,
    headers: dict | None = None,
) -> str:
    """Raw OpenAI-compatible vision call; returns the response text (may be empty)."""
    logger = logging.getLogger(__name__)
    image_b64 = encode_image(image_path)
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{_image_media_type(image_path)};base64,{image_b64}"
                        },
                    },
                ],
            }
        ],
        "temperature": 0.1,
        "max_tokens": 2048,
    }
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)

    if not base_url.endswith("/v1"):
        base_url = f"{base_url}/v1"

    start = time.time()
    resp = requests.post(f"{base_url}/chat/completions", json=payload, headers=request_headers, timeout=300)
    resp.raise_for_status()
    elapsed = time.time() - start
    logger.debug(f"  attempt={attempt} HTTP {resp.status_code} in {elapsed:.2f}s")
    try:
        j = resp.json()
        choices = j.get("choices", []) if isinstance(j, dict) else []
        if choices:
            message = choices[0].get("message", {}) or {}
            content = message.get("content", "")
            if isinstance(content, list):
                return "".join(block.get("text", "") for block in content if isinstance(block, dict))
            return content or ""
        return resp.text or ""
    except ValueError:
        return resp.text or ""


# Retry prompt used when the first attempt returns an empty response.
# More forceful, focuses the model on the screen title to get it started.
RETRY_PROMPT = """/no_think
The screenshot shows one of these five Pokemon HOME stat screens:
  "Moves", "Ability", "Held Item", "Stat Alignment", or "Stat Points".

Read the white title text to determine which screen this is, then extract ALL
rows visible and return ONLY a valid JSON object with these keys:
  screen_type, pokemon, usage, moves, move_usage, abilities, ability_usage,
  spread_values, spread_usage, natures, nature_usage, items, item_usage

For the "Stat Points" screen, each visible row has 8 values left to right:
  col 1 = ROW RANK (1, 2, 3, …)  col 2 = USAGE %  col 3–8 = 6 stat numbers
INCLUDE the row rank in the output. For each row output a 7-integer list:
  [ROW_RANK, stat1, stat2, stat3, stat4, stat5, stat6]
DO NOT name the stats — just include the rank then read the 6 numbers left to right.
IMPORTANT: the row rank is not HP and must never be copied into the first stat.
Example row "1  55.6%  2  32  0  0  0  32" → spread_values entry [1, 2, 32, 0, 0, 0, 32].

"usage" is the Pokemon rank shown as #N at the top-left of the screen (an integer).
All list fields not relevant to the detected screen_type must be [].
CRITICAL: moves, abilities, items, and natures MUST be lists of plain strings.
  DO NOT nest sub-lists. Each entry is just the name: ["sitrus berry", "leftovers"]
Return ONLY the JSON — no markdown, no explanation.
"""


def call_vision_model(image_path: Path, provider: str, model: str, max_retries: int = 2) -> dict | None:
    """Send image to Ollama; retry on empty response; return parsed JSON or None."""
    logger = logging.getLogger(__name__)

    provider = provider.strip().lower()
    if provider not in {"ollama", "openai", "openrouter"}:
        raise ValueError(f"Unsupported AI provider: {provider}")

    for attempt in range(1, max_retries + 1):
        prompt = EXTRACTION_PROMPT if attempt == 1 else RETRY_PROMPT
        try:
            if provider == "ollama":
                raw = _call_ollama(image_path, model, prompt, attempt).strip()
            elif provider == "openrouter":
                headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}"}
                if OPENROUTER_HTTP_REFERER:
                    headers["HTTP-Referer"] = OPENROUTER_HTTP_REFERER
                if OPENROUTER_X_TITLE:
                    headers["X-Title"] = OPENROUTER_X_TITLE
                raw = _call_openai_compatible(
                    image_path,
                    model,
                    prompt,
                    attempt,
                    OPENROUTER_BASE_URL,
                    headers=headers,
                ).strip()
            else:
                headers = {}
                if OPENAI_API_KEY:
                    headers["Authorization"] = f"Bearer {OPENAI_API_KEY}"
                raw = _call_openai_compatible(
                    image_path,
                    model,
                    prompt,
                    attempt,
                    OPENAI_BASE_URL,
                    headers=headers,
                ).strip()
        except requests.exceptions.RequestException as e:
            logger.error(f"  HTTP error (attempt {attempt}) for {image_path.name}: {e}")
            if attempt == max_retries:
                return None
            continue

        # Persist raw response for debugging (overwrite per attempt)
        try:
            RESULTS_DIR.mkdir(parents=True, exist_ok=True)
            raw_path = RESULTS_DIR / f"{image_path.stem}_raw.txt"
            with open(raw_path, "w", encoding="utf-8") as rf:
                rf.write(raw)
            logger.debug(f"  Raw response saved → {raw_path}")
        except Exception:
            logger.debug("  Failed to save raw response file", exc_info=True)

        logger.debug(f"  Raw length={len(raw)} snippet={raw[:800]!r}")

        if not raw:
            logger.warning(f"  Empty response on attempt {attempt} for {image_path.name} — retrying")
            continue

        # Strip markdown code fences if the model added them
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw)
        cleaned = re.sub(r"\s*```\s*$", "", cleaned).strip()

        # Pull out the first JSON object if there's surrounding text
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if m:
            cleaned = m.group(0)

        try:
            return _normalize_name_lists(json.loads(cleaned))
        except json.JSONDecodeError as e:
            logger.error(f"  JSON parse error (attempt {attempt}) for {image_path.name}: {e}")
            logger.debug(f"  Cleaned snippet: {cleaned[:1000]}")
            if attempt == max_retries:
                return None

    return None


def _normalize_name_lists(data: dict) -> dict:
    """Fix model output where name lists contain sub-lists instead of plain strings.

    The model sometimes returns items/moves/abilities/natures as nested lists:
      [[1, "Sitrus Berry", 31.8], [2, "Kasib Berry", 23.5]]
    instead of:
      ["sitrus berry", "kasib berry"]

    This extracts just the name string from each sub-list entry.
    Also strips percent signs from usage values that were returned as strings.
    """
    _NAME_FIELDS = ("moves", "abilities", "items", "natures")
    _USAGE_FIELDS = ("move_usage", "ability_usage", "item_usage", "nature_usage", "spread_usage")

    for field in _NAME_FIELDS:
        raw = data.get(field)
        if not isinstance(raw, list):
            continue
        cleaned = []
        for entry in raw:
            if isinstance(entry, str):
                cleaned.append(entry)
            elif isinstance(entry, list):
                # Sub-list: pick the longest string element as the name
                names = [x for x in entry if isinstance(x, str)]
                if names:
                    cleaned.append(max(names, key=len))
                # else: skip entries with no string
            # else: skip non-string, non-list entries
        data[field] = cleaned

    for field in _USAGE_FIELDS:
        raw = data.get(field)
        if not isinstance(raw, list):
            continue
        cleaned = []
        for entry in raw:
            if isinstance(entry, (int, float)):
                cleaned.append(entry)
            elif isinstance(entry, str):
                # Strip percent sign and convert
                cleaned.append(entry.rstrip("%"))
            elif isinstance(entry, list):
                # Sub-list: pick the last numeric element as the percentage
                nums = [x for x in entry if isinstance(x, (int, float))]
                if nums:
                    cleaned.append(nums[-1])
                # else: skip
            # else: skip
        data[field] = cleaned

    return data


_SCREEN_ALLOWED_FIELDS = {
    "moves": {"moves", "move_usage"},
    "ability": {"abilities", "ability_usage"},
    "held_item": {"items", "item_usage"},
    "stat_alignment": {"natures", "nature_usage"},
    "stat_points": {"spread_values", "spread_usage"},
}


def _parse_spread_text_values(spread: str) -> list[int] | None:
    """Parse a CSV spread string into [HP, Atk, Def, SpA, SpD, Spe]."""
    values = [0] * 6
    index_by_stat = {name.lower(): idx for idx, name in enumerate(_STAT_NAMES)}

    for part in str(spread or "").split("/"):
        token = part.strip()
        if not token:
            continue
        m = re.match(r"^(\d+)\s+([A-Za-z]+)$", token)
        if not m:
            return None
        val = int(m.group(1))
        stat_idx = index_by_stat.get(m.group(2).lower())
        if stat_idx is None:
            return None
        values[stat_idx] = val

    return values


def build_historical_spread_reference(rows: list, before_date: str) -> dict:
    """Map pokemon name -> most recent prior list of 6-stat spread rows."""
    latest_by_pokemon = {}

    for row in rows:
        row_date = str(row.get("date", "")).strip()
        if not row_date or row_date >= before_date:
            continue

        pokemon = _normalize_pokemon_name(row.get("pokemon", ""))
        spread_text = str(row.get("sp", "")).strip()
        if not pokemon or not spread_text:
            continue

        parsed_spreads = []
        parse_failed = False
        for spread in spread_text.split(":"):
            parsed = _parse_spread_text_values(spread)
            if parsed is None:
                parse_failed = True
                break
            parsed_spreads.append(parsed)

        if parse_failed or not parsed_spreads:
            continue

        current = latest_by_pokemon.get(pokemon)
        if current is None or row_date > current["date"]:
            latest_by_pokemon[pokemon] = {"date": row_date, "spreads": parsed_spreads}

    return {pokemon: info["spreads"] for pokemon, info in latest_by_pokemon.items()}


def _looks_like_valid_spread_stats(values: list) -> bool:
    if len(values) != 6:
        return False
    try:
        ints = [int(v) for v in values]
    except (TypeError, ValueError):
        return False
    return all(0 <= v <= 32 for v in ints) and sum(ints) == 66


def _normalize_spread_row(row, expected_rank: int, historical_rows: list | None = None) -> list[int] | None:
    """Repair common OCR spread row mistakes into [rank, HP, Atk, Def, SpA, SpD, Spe]."""
    if not isinstance(row, list):
        return None

    try:
        raw = [int(v) for v in row]
    except (TypeError, ValueError):
        raw = []

    candidates = []

    if len(raw) == 6:
        candidates.append([expected_rank] + raw)
    elif len(raw) == 7:
        candidates.append(raw)
        if raw and raw[0] == expected_rank:
            # OCR duplicated the row rank as the first stat and dropped the trailing zero.
            candidates.append([expected_rank] + raw[2:] + [0])
            # OCR captured an extra trailing stat-like number; drop it and pad the tail.
            candidates.append([expected_rank] + raw[1:-1] + [0])
    elif len(raw) == 8 and raw and raw[0] == expected_rank:
        # Common pattern: [rank, rank, hp, atk, def, spa, spd, spe]
        candidates.append([expected_rank] + raw[2:])
    elif len(raw) == 9 and raw and raw[0] == expected_rank:
        # Common pattern: [rank, usage_tens, usage_ones, hp, atk, def, spa, spd, spe]
        candidates.append([expected_rank] + raw[3:])

    for candidate in candidates:
        if len(candidate) != 7:
            continue
        if candidate[0] != expected_rank:
            continue
        if _looks_like_valid_spread_stats(candidate[1:]):
            return candidate

    if historical_rows and 0 <= expected_rank - 1 < len(historical_rows):
        historical = historical_rows[expected_rank - 1]
        if _looks_like_valid_spread_stats(historical):
            return [expected_rank] + [int(v) for v in historical]

    return raw if len(raw) in (6, 7) else None


def _infer_screen_type(data: dict) -> str:
    screen_type = str(data.get("screen_type", "")).strip().lower()
    if screen_type in _SCREEN_ALLOWED_FIELDS:
        return screen_type

    populated = []
    if data.get("moves"):
        populated.append("moves")
    if data.get("abilities"):
        populated.append("ability")
    if data.get("items"):
        populated.append("held_item")
    if data.get("natures"):
        populated.append("stat_alignment")
    if data.get("spread_values"):
        populated.append("stat_points")

    if len(populated) == 1:
        return populated[0]

    # When the model overfills several sections at once, prefer the simpler
    # title-driven screens over noisy stat spreads.
    for candidate in ("moves", "ability", "held_item", "stat_alignment", "stat_points"):
        if candidate in populated:
            return candidate

    return screen_type


def sanitize_extracted_entry(data: dict, historical_spreads: list | None = None) -> dict:
    """Keep only fields relevant to the detected screen and repair spread rows."""
    cleaned = deepcopy(data)

    screen_type = _infer_screen_type(cleaned)
    if screen_type:
        cleaned["screen_type"] = screen_type

    if screen_type in _SCREEN_ALLOWED_FIELDS:
        allowed = _SCREEN_ALLOWED_FIELDS[screen_type]
        for fields in _SCREEN_ALLOWED_FIELDS.values():
            for field in fields:
                if field not in allowed:
                    cleaned[field] = []

    if cleaned.get("screen_type") == "stat_points":
        rows = cleaned.get("spread_values", [])
        repaired_rows = []
        for idx, row in enumerate(rows):
            repaired = _normalize_spread_row(row, idx + 1, historical_rows=historical_spreads)
            if repaired is not None:
                repaired_rows.append(repaired)
        cleaned["spread_values"] = repaired_rows

        spread_usage = cleaned.get("spread_usage", [])
        if isinstance(spread_usage, list):
            cleaned["spread_usage"] = spread_usage[:len(repaired_rows)]
    else:
        cleaned["spread_values"] = []
        cleaned["spread_usage"] = []

    return cleaned


# ---------------------------------------------------------------------------
# Data formatting
# ---------------------------------------------------------------------------
_STAT_NAMES = ["HP", "Atk", "Def", "SpA", "SpD", "Spe"]
_STAT_CASE = {
    "hp": "HP", "atk": "Atk", "def": "Def",
    "spa": "SpA", "spd": "SpD", "spe": "Spe",
}


def _values_to_spread(values: list) -> str:
    """Convert raw stat values list to a named spread string.

    Accepts:
      7-element [ROW_RANK, HP, Atk, Def, SpA, SpD, Spe] – new format; index 0 is stripped.
      6-element [HP, Atk, Def, SpA, SpD, Spe]           – legacy format; used as-is.
    Returns '' for any other length.
    """
    if len(values) == 7:
        values = values[1:]   # strip the row rank that is now included in output
    elif len(values) != 6:
        return ""
    parts = []
    for val, name in zip(values, _STAT_NAMES):
        try:
            v = int(val)
        except (TypeError, ValueError):
            continue
        if v != 0:
            parts.append(f"{v} {name}")
    return " / ".join(parts)


def _normalize_spread(spread: str) -> str:
    """Normalise stat abbreviation case: '2 hp / 32 spa' → '2 HP / 32 SpA'."""
    parts = []
    for part in spread.split("/"):
        part = part.strip()
        m = re.match(r"^(\d+)\s+([A-Za-z]+)$", part)
        if m:
            val, stat = m.group(1), m.group(2)
            stat = _STAT_CASE.get(stat.lower(), stat)
            parts.append(f"{val} {stat}")
        else:
            parts.append(part)
    return " / ".join(parts)


def _join(lst: list) -> str:
    return ":".join(str(x) for x in lst) if lst else ""


def _join_lower(lst: list) -> str:
    """Join list items as lowercase colon-separated string."""
    return ":".join(str(x).strip().lower() for x in lst) if lst else ""


def _normalize_pokemon_name(value) -> str:
    """Coerce a Pokemon name-like value to a safe lowercase string."""
    return str(value or "").strip().lower()


def _coerce_usage_rank(value) -> int | None:
    """Return an integer rank when the value cleanly represents one."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None

    text = str(value or "").strip()
    if not text:
        return None

    if re.fullmatch(r"\d+", text):
        return int(text)
    if re.fullmatch(r"\d+\.0+", text):
        return int(float(text))
    return None


def format_for_csv(data: dict) -> dict:
    """Convert extracted dict to a flat CSV-row dict using CORE_FIELDS keys."""
    usage_rank = _coerce_usage_rank(data.get("usage", ""))
    return {
        "pokemon":        _normalize_pokemon_name(data.get("pokemon", "")),
        "usage":          str(usage_rank) if usage_rank is not None else "",
        "moves":          _join_lower(data.get("moves", [])),
        "move usage":     _join(data.get("move_usage", [])),
        "ability":        _join_lower(data.get("abilities", [])),
        "ability usage":  _join(data.get("ability_usage", [])),
        "sp":             _join(
            # Prefer raw spread_values (model outputs 6 numbers; Python names the stats)
            # Fall back to named spread strings with case normalization for older responses
            [_values_to_spread(v) for v in data.get("spread_values", [])]
            if data.get("spread_values")
            else [_normalize_spread(s) for s in data.get("spreads", [])]
        ),
        "sp usage":       _join(data.get("spread_usage", [])),
        "nature":         _join_lower(data.get("natures", [])),
        "nature usage":   _join(data.get("nature_usage", [])),
        "item":           _join_lower(data.get("items", [])),
        "item usage":     _join(data.get("item_usage", [])),
    }


def _load_pokemon_name_lookup() -> dict:
    logger = logging.getLogger(__name__)
    try:
        from flag_review import build_lookup, load_or_fetch_cache
    except Exception as e:
        logger.warning(f"Pokemon autocorrection unavailable; could not import review helpers: {e}")
        return {}

    try:
        cache = load_or_fetch_cache(refresh=False)
    except Exception as e:
        logger.warning(f"Pokemon autocorrection unavailable; could not load PokeAPI cache: {e}")
        return {}

    return build_lookup(cache.get("pokemon", []))


def _match_pokemon_name(name: str, pokemon_lookup: dict) -> tuple:
    try:
        from flag_review import HAS_RAPIDFUZZ, fuzzy_check, normalize
    except Exception:
        return "", 0.0, False

    norm = normalize(name)
    if norm in pokemon_lookup:
        return pokemon_lookup[norm], 100.0, True

    if not HAS_RAPIDFUZZ:
        return "", 0.0, False

    best_match, score, _ = fuzzy_check(name, pokemon_lookup, threshold=0.0)
    if best_match.startswith("("):
        return "", 0.0, False
    return best_match, score, False


def autocorrect_pokemon_names(extracted_list: list, pokemon_lookup: dict | None = None) -> tuple:
    """Apply safe Pokemon-name fixes before dedup/upsert.

    Uses exact normalized matches first. For OCR misses, only applies a fuzzy
    suggestion when the same low-confidence OCR name repeats across multiple
    images. This mirrors the flag-review pass without rewriting already-valid
    base species names into PokeAPI form identifiers.
    """
    corrected = deepcopy(extracted_list)
    if not corrected:
        return corrected, []

    if pokemon_lookup is None:
        pokemon_lookup = _load_pokemon_name_lookup()
    if not pokemon_lookup:
        return corrected, []

    by_name = {}
    for idx, entry in enumerate(corrected):
        data = entry.get("data", {})
        name = str(data.get("pokemon", "")).strip()
        if name:
            by_name.setdefault(name.casefold(), []).append((idx, name))

    corrections = []
    for group in by_name.values():
        first_name = group[0][1]
        candidate, score, is_exact = _match_pokemon_name(first_name, pokemon_lookup)
        if not candidate:
            continue

        target = ""
        reason = ""
        if is_exact:
            target = candidate
            reason = "exact_lookup"
        elif (
            len(group) >= AUTO_POKEMON_CONSENSUS_MIN_COUNT
            and score >= AUTO_POKEMON_CONSENSUS_MIN_SCORE
            and score < AUTO_POKEMON_REVIEW_THRESHOLD
        ):
            target = candidate
            reason = f"fuzzy_repeat score={score:.1f} count={len(group)}"

        if not target:
            continue

        for idx, _ in group:
            current = corrected[idx].get("data", {}).get("pokemon", "")
            if current == target:
                continue

            corrected[idx]["data"]["pokemon"] = target
            corrections.append({
                "image": corrected[idx].get("image", "?"),
                "usage": str(corrected[idx].get("data", {}).get("usage", "")).strip(),
                "field": "pokemon",
                "from": current,
                "to": target,
                "score": score,
                "reason": reason,
            })

    return corrected, corrections


def _row_has_non_key_data(row: dict) -> bool:
    for field in CORE_FIELDS:
        if field in {"date", "pokemon", "usage"}:
            continue
        if str(row.get(field, "")).strip():
            return True
    return False


def build_known_pokemon_names(rows: list, exclude_date: str | None = None) -> set:
    """Collect repo-known Pokemon names from existing CSV rows.

    Sparse prefill rows for the current date are excluded so a typo in a blank
    seed row does not become self-authorizing.
    """
    known_names = set()
    for row in rows:
        pokemon = str(row.get("pokemon", "")).strip().lower()
        if not pokemon:
            continue

        if exclude_date is not None and row.get("date", "") == exclude_date and not _row_has_non_key_data(row):
            continue

        known_names.add(pokemon)

    return known_names


def _is_trusted_prefill_name(name: str, known_names: set, pokemon_lookup: dict | None = None) -> bool:
    if name in known_names:
        return True

    if not pokemon_lookup:
        return False

    _candidate, _score, is_exact = _match_pokemon_name(name, pokemon_lookup)
    return is_exact


def build_prefilled_name_index(
    rows: list,
    date_str: str,
    known_names: set | None = None,
    pokemon_lookup: dict | None = None,
) -> tuple:
    """Return authoritative usage->pokemon names from manual prefill rows.

    A row counts as a prefill authority only when it has date/pokemon/usage set
    and all other data fields are blank. This lets a manually seeded roster for
    the target date override OCR mistakes without trusting previously extracted
    partial data.
    """
    candidates = {}
    for row in rows:
        if row.get("date", "") != date_str:
            continue

        usage = str(row.get("usage", "")).strip()
        pokemon = str(row.get("pokemon", "")).strip().lower()
        if not usage or not pokemon or _row_has_non_key_data(row):
            continue

        if known_names is not None and not _is_trusted_prefill_name(pokemon, known_names, pokemon_lookup):
            continue

        candidates.setdefault(usage, set()).add(pokemon)

    authoritative = {}
    conflicts = []
    for usage, names in candidates.items():
        if len(names) == 1:
            authoritative[usage] = next(iter(names))
        else:
            conflicts.append({
                "usage": usage,
                "pokemon_names": sorted(names),
            })

    return authoritative, conflicts


def rectify_pokemon_names_from_prefill(extracted_list: list, authoritative_names: dict) -> tuple:
    """Overwrite extracted Pokemon names when a same-date prefill is authoritative."""
    corrected = deepcopy(extracted_list)
    corrections = []

    if not authoritative_names:
        return corrected, corrections

    for entry in corrected:
        data = entry.get("data", {})
        usage = str(data.get("usage", "")).strip()
        target = authoritative_names.get(usage)
        if not target:
            continue

        current = str(data.get("pokemon", "")).strip()
        if current == target:
            continue

        data["pokemon"] = target
        corrections.append({
            "image": entry.get("image", "?"),
            "usage": usage,
            "field": "pokemon",
            "from": current,
            "to": target,
            "score": 100.0,
            "reason": "prefilled_name_rank",
        })

    return corrected, corrections


def rectify_existing_rows_from_prefill(rows: list, date_str: str, authoritative_names: dict) -> tuple:
    """Canonicalise existing CSV rows to authoritative prefilled names for the date."""
    corrected = [dict(row) for row in rows]
    corrections = []

    if not authoritative_names:
        return corrected, corrections

    for row in corrected:
        if row.get("date", "") != date_str:
            continue

        usage = str(row.get("usage", "")).strip()
        target = authoritative_names.get(usage)
        if not target:
            continue

        current = str(row.get("pokemon", "")).strip()
        if current == target:
            continue

        row["pokemon"] = target
        corrections.append({
            "date": date_str,
            "usage": usage,
            "field": "pokemon",
            "from": current,
            "to": target,
            "reason": "prefilled_name_rank_existing",
        })

    return corrected, corrections


def merge_duplicate_csv_rows(rows: list, target_date: str | None = None) -> tuple:
    """Merge duplicate CSV rows with the same (date, pokemon, usage) key."""
    merged = []
    index = {}
    merged_count = 0

    for row in rows:
        row_copy = dict(row)
        row_date = row_copy.get("date", "")
        key = (row_date, row_copy.get("pokemon", "").lower(), str(row_copy.get("usage", "")))

        if target_date is not None and row_date != target_date:
            merged.append(row_copy)
            continue

        if key in index:
            existing = merged[index[key]]
            for field, value in row_copy.items():
                if value and not existing.get(field):
                    existing[field] = value
            merged_count += 1
        else:
            index[key] = len(merged)
            merged.append(row_copy)

    return merged, merged_count


# ---------------------------------------------------------------------------
# CSV I/O
# ---------------------------------------------------------------------------
def load_csv(csv_path: Path) -> tuple:
    """
    Load CSV preserving trailing empty columns.
    Returns (rows: list[dict], trailing_count: int).
    Rows use only CORE_FIELDS keys.
    """
    if not csv_path.exists():
        return [], 0

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        all_rows = list(reader)

    if not all_rows:
        return [], 0

    headers = all_rows[0]
    trailing = sum(1 for h in reversed(headers) if not h.strip())

    rows = []
    for raw in all_rows[1:]:
        row = {}
        for i, field in enumerate(CORE_FIELDS):
            row[field] = raw[i].strip() if i < len(raw) else ""
        rows.append(row)

    return rows, trailing


def save_csv(csv_path: Path, rows: list, trailing_cols: int) -> None:
    headers = CORE_FIELDS + [""] * trailing_cols
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for row in rows:
            writer.writerow([row.get(f, "") for f in CORE_FIELDS] + [""] * trailing_cols)


# ---------------------------------------------------------------------------
# Upsert logic
# ---------------------------------------------------------------------------
def upsert(existing: list, new_rows: list, date_str: str) -> tuple:
    """
    Upsert new_rows into existing list.
    Key: (date, pokemon, usage).  Non-empty fields from new_rows overwrite existing.
    Returns (updated_list, inserted_count, updated_count).
    """
    inserted = 0
    updated = 0

    # Build an index: key → list index
    index = {}
    result = list(existing)
    for i, row in enumerate(result):
        key = (row.get("date", ""), row.get("pokemon", "").lower(), str(row.get("usage", "")))
        index[key] = i

    for nr in new_rows:
        nr["date"] = date_str
        key = (date_str, nr.get("pokemon", "").lower(), str(nr.get("usage", "")))

        if key in index:
            idx = index[key]
            for field, value in nr.items():
                if value:  # only overwrite with non-empty values
                    result[idx][field] = value
            updated += 1
        else:
            result.append(nr)
            index[key] = len(result) - 1
            inserted += 1

    # Keep rows sorted: date ASC, usage rank ASC (numeric)
    def _sort_key(r):
        try:
            rank = int(r.get("usage") or 0)
        except ValueError:
            rank = 0
        return (r.get("date", ""), rank)

    result.sort(key=_sort_key)
    return result, inserted, updated


# ---------------------------------------------------------------------------
# Deduplication across multiple images for the same Pokemon
# ---------------------------------------------------------------------------
def deduplicate(extracted_list: list) -> list:
    """
    If multiple images produced data for the same (pokemon, usage) pair,
    merge them — preferring the entry with more non-empty fields.
    """
    seen = {}  # (pokemon, usage) → index in result
    result = []

    for entry in extracted_list:
        data = entry["data"]
        key = (_normalize_pokemon_name(data.get("pokemon", "")), str(data.get("usage", "")))

        if key in seen:
            idx = seen[key]
            existing_data = result[idx]["data"]
            # Merge: keep non-empty values from both, prefer existing if both present
            for field, value in data.items():
                if value and not existing_data.get(field):
                    existing_data[field] = value
        else:
            seen[key] = len(result)
            result.append(entry)

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract Pokemon usage stats from screenshots into CSV",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "folder",
        nargs="?",
        help="Image folder path, e.g. data/doubles/20260427doubles (optional: newest folder will be used; interactive mode prompts for s/d when needed)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes to CSV (default is dry-run: only saves JSON results)",
    )
    parser.add_argument(
        "--csv",
        help="Override the target CSV file path",
    )
    parser.add_argument(
        "--provider",
        default=AI_PROVIDER,
        choices=["ollama", "openai", "openrouter"],
        help=f"AI provider to use (default: {AI_PROVIDER})",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_AI_MODEL,
        help=f"AI model to use (default: {DEFAULT_AI_MODEL})",
    )
    parser.add_argument(
        "--start",
        type=int,
        default=0,
        help="Skip images before this index, useful for resuming (default: 0)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit the number of images processed (useful for testing)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--skip-flag",
        action="store_true",
        help="Skip running flag_review.py after extraction (default: run flag_review)",
    )
    args = parser.parse_args()

    setup_logging(args.verbose)
    logger = logging.getLogger(__name__)

    # ---- Resolve folder ----
    try:
        folder, folder_resolution_note = resolve_input_folder(args.folder)
    except ValueError as e:
        logger.error(str(e))
        sys.exit(1)

    if folder_resolution_note:
        logger.info(folder_resolution_note)

    if not folder.is_dir():
        logger.error(f"Folder not found: {folder}")
        sys.exit(1)

    # ---- Parse date + format from folder name ----
    try:
        date_str, fmt_type = parse_folder_name(folder.name)
    except ValueError as e:
        logger.error(str(e))
        sys.exit(1)

    logger.info(f"Folder   : {folder.name}")
    logger.info(f"Date     : {date_str}")
    logger.info(f"Format   : {fmt_type}")

    # ---- CSV path ----
    csv_path = Path(args.csv).resolve() if args.csv else DEFAULT_CSV[fmt_type]
    trailing = TRAILING_COLS[fmt_type]
    logger.info(f"CSV      : {csv_path}")
    logger.info(f"AI       : {args.provider} / {args.model}")
    logger.info(f"Mode     : {'APPLY' if args.apply else 'DRY RUN (use --apply to write CSV)'}")

    existing, loaded_trailing = load_csv(csv_path)
    if csv_path.exists():
        trailing = loaded_trailing

    pokemon_lookup = _load_pokemon_name_lookup()
    known_pokemon_names = build_known_pokemon_names(existing, exclude_date=date_str)
    historical_spread_reference = build_historical_spread_reference(existing, before_date=date_str)
    prefilled_name_index, prefilled_name_conflicts = build_prefilled_name_index(
        existing,
        date_str,
        known_names=known_pokemon_names,
        pokemon_lookup=pokemon_lookup,
    )
    if prefilled_name_index:
        logger.info(
            f"Using prefilled Pokemon names for {len(prefilled_name_index)} ranks on {date_str}"
        )
    for conflict in prefilled_name_conflicts:
        logger.warning(
            "Conflicting prefilled Pokemon names for rank "
            f"{conflict['usage']}: {', '.join(conflict['pokemon_names'])}; skipping authority"
        )

    # ---- Discover images ----
    images = get_images(folder)
    if not images:
        logger.error(f"No images found in {folder}")
        sys.exit(1)
    logger.info(f"Images   : {len(images)} found")

    # ---- Ensure results directory ----
    RESULTS_DIR.mkdir(exist_ok=True)
    results_file = RESULTS_DIR / f"{folder.name}_extracted.json"

    # ---- Process each image ----
    extracted_all = []
    errors = []
    processed = 0

    for img_path in images:
        # Determine numeric index from filename
        m = re.search(r"(\d+)", img_path.stem)
        img_idx = int(m.group(1)) if m else 0

        if img_idx < args.start:
            continue

        if args.limit is not None and processed >= args.limit:
            logger.info(f"Reached limit of {args.limit} images — stopping")
            break

        logger.info(f"[{img_idx:>3}] {img_path.name} …")
        data = call_vision_model(img_path, args.provider, args.model)

        if data is None:
            logger.warning(f"      ✗ Failed to extract data")
            errors.append(img_path.name)
            processed += 1
            continue

        data = sanitize_extracted_entry(
            data,
            historical_spreads=historical_spread_reference.get(
                _normalize_pokemon_name(data.get("pokemon", ""))
            ),
        )

        pokemon = data.get("pokemon", "?")
        usage = data.get("usage", "?")
        screen = data.get("screen_type", "?")
        moves_count = len(data.get("moves", []))
        spreads_count = len(data.get("spread_values", data.get("spreads", [])))
        natures_count = len(data.get("natures", []))
        abilities_count = len(data.get("abilities", []))
        items_count = len(data.get("items", []))
        logger.info(
            f"      ✓ {pokemon} rank={usage} screen={screen} "
            f"moves={moves_count} spreads={spreads_count} "
            f"natures={natures_count} abilities={abilities_count} items={items_count}"
        )

        extracted_all.append({"image": img_path.name, "data": data})
        processed += 1

    # ---- Rectify obvious OCR misses before deduping / CSV upsert ----
    raw_per_image = deepcopy(extracted_all)
    corrected_per_image, prefill_name_corrections = rectify_pokemon_names_from_prefill(
        extracted_all, prefilled_name_index
    )
    if prefill_name_corrections:
        corrected_ranks = len({c["usage"] for c in prefill_name_corrections})
        logger.info(
            f"Applied {len(prefill_name_corrections)} prefilled-name corrections across {corrected_ranks} ranks"
        )

    corrected_per_image, fuzzy_name_corrections = autocorrect_pokemon_names(
        corrected_per_image, pokemon_lookup=pokemon_lookup
    )
    autocorrections = prefill_name_corrections + fuzzy_name_corrections
    if fuzzy_name_corrections:
        corrected_ranks = len({c["usage"] for c in fuzzy_name_corrections})
        logger.info(
            f"Applied {len(fuzzy_name_corrections)} Pokemon autocorrections across {corrected_ranks} ranks"
        )

    # ---- Deduplicate (same Pokemon across multiple images) ----
    extracted_all = deduplicate(corrected_per_image)

    # ---- Save raw results JSON ----
    results_payload = {
        "folder": str(folder),
        "date": date_str,
        "format": fmt_type,
        "provider": args.provider,
        "model": args.model,
        "prefilled_name_authority": prefilled_name_index,
        "prefilled_name_conflicts": prefilled_name_conflicts,
        "raw_extracted": raw_per_image,   # per-image, one screen type each
        "autocorrected_extracted": corrected_per_image,
        "autocorrections": autocorrections,
        "extracted": extracted_all,        # deduplicated + merged
        "errors": errors,
    }
    with open(results_file, "w", encoding="utf-8") as f:
        json.dump(results_payload, f, indent=2, ensure_ascii=False)
    logger.info(f"\nResults JSON saved → {results_file}")

    if errors:
        logger.warning(f"Failed images ({len(errors)}): {', '.join(errors)}")

    # ---- Build CSV rows ----
    csv_rows = []
    for entry in extracted_all:
        row = format_for_csv(entry["data"])
        if row["pokemon"] and row["usage"]:
            csv_rows.append(row)

    if not csv_rows:
        logger.warning("No valid data extracted — nothing to write.")
        return

    logger.info(f"\nExtracted {len(csv_rows)} Pokemon entries:")
    for row in sorted(csv_rows, key=lambda r: _coerce_usage_rank(r.get("usage")) or 0):
        moves_preview = row["moves"].split(":")[0] if row["moves"] else "—"
        rank = _coerce_usage_rank(row.get("usage")) or 0
        logger.info(f"  Rank {rank:>2}: {row['pokemon']:<20}  first move: {moves_preview}")

    # ---- Upsert into CSV ----
    existing_for_upsert, existing_name_corrections = rectify_existing_rows_from_prefill(
        existing, date_str, prefilled_name_index
    )
    if existing_name_corrections:
        corrected_ranks = len({c["usage"] for c in existing_name_corrections})
        logger.info(
            f"Reconciled {len(existing_name_corrections)} existing CSV name conflicts across {corrected_ranks} ranks"
        )

    existing_for_upsert, merged_existing_rows = merge_duplicate_csv_rows(
        existing_for_upsert, target_date=date_str
    )
    if merged_existing_rows:
        logger.info(f"Merged {merged_existing_rows} duplicate CSV rows for {date_str}")

    updated_rows, inserted, updated_count = upsert(existing_for_upsert, csv_rows, date_str)

    logger.info(f"\nCSV changes: {inserted} rows to insert, {updated_count} rows to update")

    if args.apply:
        save_csv(csv_path, updated_rows, trailing)
        logger.info(f"CSV written → {csv_path}")
    else:
        logger.info("Dry run — CSV not modified. Run with --apply to save.")

    logger.info("\nDone!")
    logger.info(
        f"Run 'python validate.py {results_file}' to compare extraction against CSV ground truth."
    )
    logger.info(
        f"Run 'python flag_review.py {results_file}' to check for unknown names / bad spreads."
    )
    # Automatically run the flagging/validation step unless the user opted out.
    if not args.skip_flag:
        flag_script = SCRIPT_DIR / "flag_review.py"
        if flag_script.exists():
            cmd = [sys.executable, str(flag_script), str(results_file)]
            if args.verbose:
                cmd.append("--verbose")
            logger.info(f"Running flag_review: {' '.join(cmd)}")
            try:
                subprocess.run(cmd, check=False)
            except Exception as e:
                logger.error(f"Failed to run flag_review.py: {e}")
        else:
            logger.warning("flag_review.py not found; skipping validation step")


if __name__ == "__main__":
    main()
