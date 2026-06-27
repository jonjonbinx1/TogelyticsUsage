# Pokemon Champions Usage Extractor

Extracts competitive Pokemon usage statistics from screenshot images using the
**Ollama** vision model (`qwen3-vl:30b-a3b`) and upserts the data into the
`champions_singles.csv` / `champions_doubles.csv` files.

---

## Requirements

- Python 3.10+
- [Ollama](https://ollama.com) running locally with `qwen3-vl:30b-a3b` pulled
- `requests` library

```bash
pip install -r requirements.txt
```

---

## Folder naming convention

Image folders must be named **`YYYYMMDDsingles`** or **`YYYYMMDDdoubles`**  
e.g. `20260427doubles`, `20260503singles`

The date embedded in the folder name is used as the `date` column in the CSV.  
Each `imageN.jpeg` (N = 0, 1, 2 …) corresponds to one Pokemon ranked N+1 by usage.

---

## Workflow

### 1 — Extract data (dry run first)

```bash
# Use the newest known data folder, or choose from a prompt if there are multiple
python extractor.py

# Preview what would be written — does NOT modify the CSV
python extractor.py data/doubles/20260427doubles

# Apply the changes to champions_doubles.csv
python extractor.py data/doubles/20260427doubles --apply

# Singles
python extractor.py data/singles/20260503singles --apply
```

Raw extraction results are always saved to `results/<foldername>_extracted.json`
regardless of `--apply`.

When you omit the `folder` argument, the extractor discovers known folders under
`data/singles` and `data/doubles`. It first prefers the most recently extracted
folder inferred from `results/*_extracted.json`. If that is unavailable and
there is one clear newest data folder it uses that automatically; otherwise it
shows a numbered terminal prompt.

Before CSV upsert, the extractor now runs a conservative Pokemon-name
rectification pass:

- exact matches are normalized to the repo's canonical lowercase form
- same-date prefill rows with only `date/pokemon/usage` filled are treated as authoritative rank-name mappings when the prefilled name matches a trusted repo/canonical Pokemon name
- when that prefill exists, extracted names are rectified to the prefilled Pokemon for that usage rank
- conflicting existing CSV rows for that date/rank are merged back into the authoritative prefilled name
- repeated low-confidence OCR misses such as `fangirl` → `farigiraf` are fixed
- the original OCR payload is kept in `raw_extracted`
- the corrected per-image payload is written to `autocorrected_extracted`
- every applied fix is logged under `autocorrections`

### 2 — Validate extraction accuracy

Requires the CSV to already contain ground truth for the date being validated.

```bash
python validate.py results/20260427doubles_extracted.json
python validate.py results/20260427singles_extracted.json

# Summary table only (no per-Pokemon detail)
python validate.py results/20260427doubles_extracted.json --summary

# Also write a machine-readable accuracy JSON
python validate.py results/20260427doubles_extracted.json \
    --json-out results/accuracy_20260427doubles.json
```

### 3 — True test (20260503singles)

The singles CSV has only placeholder rows for 2026-05-03.  
Run extraction and apply to fill in the data:

```bash
python extractor.py data/singles/20260503singles --apply
```

When ground truth becomes available, run validate to score it.

`flag_review.py` validates `autocorrected_extracted` when present, so known
autofixes are not re-flagged immediately after extraction.

### 4 — Manual stat spread editor

If the stat-point OCR is wrong, open the manual editor after extraction:

```bash
python stat_spread_editor.py
python stat_spread_editor.py --date 2026-05-18 --format singles
python stat_spread_editor.py --date 2026-05-18 --format doubles
```

Launching without arguments opens the editor with UI selectors for date and format at the top.
The date selector is populated from available extraction JSON files and existing CSV dates.

The editor will:

- load the matching `results/<date><format>_extracted.json` when it exists
- show stat-point image candidates for each usage rank in order
- let you pick a replacement image when the JSON does not have the right one
- switch between stat points, moves, ability, held item, and stat alignment editing modes
- suggest move / ability / item / nature names from the local PokeAPI cache where available, while still allowing freeform overrides when the right value is not in the suggestion list
- write the manual field values back to the correct CSV row

### 5 — Collect web metrics into separate CSV datasets

The repo also includes a standalone web collector for public Pokemon Champions
format data. It currently targets the public Pikalytics format page and writes
snapshots into separate chart-friendly CSVs instead of modifying
`champions_singles.csv` or `champions_doubles.csv`.

```bash
# Fetch the current Pokemon Champions snapshot from Pikalytics
python collect_champions_web_metrics.py

# Write the dataset files into a dedicated metrics folder using this base name
python collect_champions_web_metrics.py --output results/web_metrics/champions_web_metrics.csv

# Use a different Pikalytics format URL if needed later
python collect_champions_web_metrics.py \
  --url https://pikalytics.com/pokedex/gen9championsvgc2026regma
```

By default the collector writes into `results/web_metrics/`.

If the base output path is `results/web_metrics/champions_web_metrics.csv`, each run writes:

- `results/web_metrics/champions_web_metrics_pokemon_usage.csv`
- `results/web_metrics/champions_web_metrics_pokemon_team_metrics.csv`
- `results/web_metrics/champions_web_metrics_team_cores.csv`
- `results/web_metrics/champions_web_metrics_top_teams.csv`
- `results/web_metrics/champions_web_metrics_team_pokemon_details.csv`
- `results/web_metrics/champions_web_metrics_team_combinations.csv`

These datasets are split to make downstream charting easier:

- `pokemon_usage`: source ladder/share usage ranks from the format page
- `pokemon_team_metrics`: per-Pokemon performance aggregated from the scraped top teams, joined with source usage rank when available
- `team_cores`: source-provided common 2-Pokemon, 3-Pokemon, and 4-Pokemon cores
- `top_teams`: one row per scraped tournament team with parsed wins/losses/draws, event date, source teamlist URL, tournament player count from Limitless standings when available, and per-slot item/ability/moves when a linked teamlist is available
- `team_pokemon_details`: one normalized row per Pokemon slot from the scraped top teams, including event date, item, ability, moves, teammates, tournament player count, and source-vs-Pikalytics name mapping
- `team_combinations`: aggregated pairings, trios, quartets, quintets, and full teams with team appearances and combined record stats

`pokemon_team_metrics` and `team_combinations` are derived from the `top_teams`
sample, so you can chart how often a Pokemon, pairing, trio, or full team shows
up and what cumulative record those combinations posted in the scraped teams.

The collector fetches top-team rankings from Pikalytics and then follows each
linked source teamlist on Limitless when available so exact team members,
items, abilities, moves, and tournament player counts can be written into `top_teams` and
`team_pokemon_details`.

When the same tournament still appears on later collector runs, the tournament-team datasets
update the existing row instead of adding another copy for a new run date. They are keyed by
the underlying event identity from Limitless rather than by `snapshot_date`.

Current source support:

- `pikalytics` is implemented
- `pokemon-zone` is not implemented yet because it returns Cloudflare 403s from this environment

On Windows you can register a daily or weekly Scheduled Task with the helper script:

```powershell
# Daily at 08:00
powershell -ExecutionPolicy Bypass -File .\register_champions_metrics_task.ps1 -Frequency Daily -Time 08:00

# Weekly on Sunday at 09:00
powershell -ExecutionPolicy Bypass -File .\register_champions_metrics_task.ps1 -Frequency Weekly -DayOfWeek Sunday -Time 09:00
```

### 6 — View web metrics graphs

The repo also includes an interactive chart viewer for the generated web-metrics CSVs.
It opens a desktop window with tabs for usage, Pokemon performance, team structures,
and per-Pokemon set details from the normalized team rows.

```bash
# Launch the viewer against the default results/web_metrics/champions_web_metrics_*.csv files
python view_champions_web_metrics.py

# You can also point at any one of the generated dataset CSVs and it will infer the base name
python view_champions_web_metrics.py --output results/web_metrics/champions_web_metrics_top_teams.csv

# Print the available source / format / snapshot combinations without opening the GUI
python view_champions_web_metrics.py --list-snapshots

# Export a set of PNG charts instead of opening the GUI
python view_champions_web_metrics.py --export-dir results/graph_viewer_preview
```

The export mode writes four chart images for the selected snapshot:

- usage overview
- Pokemon top-team performance
- team structures (source cores plus derived pairings)
- item / ability / move / teammate trends for one Pokemon

---

## Options

### extractor.py

| Flag | Description |
|---|---|
| `folder` | Path to image folder (required) |
| `--apply` | Write results to CSV (default is dry-run) |
| `--csv PATH` | Override target CSV file path |
| `--model NAME` | Ollama model to use (default: `qwen3-vl:30b-a3b`) |
| `--start N` | Skip images before index N (resume after a crash) |
| `--verbose` | Enable debug logging |

### validate.py

| Flag | Description |
|---|---|
| `results_json` | Path to `results/*.json` from extractor.py (required) |
| `--csv PATH` | Override CSV path for ground truth |
| `--summary` | Print summary table only |
| `--json-out PATH` | Write accuracy results to a JSON file |

### collect_champions_web_metrics.py

| Flag | Description |
|---|---|
| `--url URL` | Source format page to fetch (default: current Pokemon Champions Pikalytics page) |
| `--source NAME` | Source label written to the CSV (default: `pikalytics`) |
| `--output PATH` | Base output path used to generate the separate dataset CSVs |
| `--snapshot-date YYYY-MM-DD` | Override the snapshot date written to the CSV |
| `--verbose` | Enable debug logging |

### view_champions_web_metrics.py

| Flag | Description |
|---|---|
| `--output PATH` | Collector base path or any one of the generated dataset CSVs |
| `--source NAME` | Preselect a source in the viewer or export mode |
| `--format-name TEXT` | Preselect a format name in the viewer or export mode |
| `--snapshot-date YYYY-MM-DD` | Preselect a snapshot date in the viewer or export mode |
| `--pokemon NAME` | Preselect the Pokemon shown on the set-explorer chart |
| `--top-n N` | Default number of ranked rows to show per chart |
| `--export-dir PATH` | Save PNG charts to a directory instead of opening the GUI |
| `--list-snapshots` | Print available source / format / snapshot combinations and exit |

---

## CSV format

Both CSVs share the same column structure:

```
date, pokemon, usage, moves, move usage, ability, ability usage,
sp, sp usage, nature, nature usage, item, item usage
```

- Multiple values within a cell are separated by `:`  
  e.g. `close combat:dire claw:fake out:protect`  
- Corresponding usages are `:`-separated in the same order  
  e.g. `99.5:98.4:89.0:67.5`

**Upsert key**: `(date, pokemon, usage)` — the script inserts a new row if this
combination is not present, or updates the existing row if it is.

---

## Accuracy metrics

| Metric | Method |
|---|---|
| Pokemon name | Exact string match |
| Usage rank | Exact integer match |
| Moves / Abilities / Natures / Items / Spreads | Jaccard set similarity |
| Usage percentages | Fraction within ±2% tolerance |

Overall accuracy is the average of all field scores across all matched Pokemon.
