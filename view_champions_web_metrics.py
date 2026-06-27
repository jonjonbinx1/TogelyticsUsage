#!/usr/bin/env python3
"""Interactive chart viewer for Pokemon Champions web metrics datasets."""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


SCRIPT_DIR = Path(__file__).parent
WEB_METRICS_DIR = SCRIPT_DIR / "results" / "web_metrics"
DEFAULT_OUTPUT = WEB_METRICS_DIR / "champions_web_metrics.csv"
DATASET_ORDER = [
    "pokemon_usage",
    "pokemon_team_metrics",
    "team_cores",
    "top_teams",
    "team_pokemon_details",
    "team_combinations",
]
CORE_SIZE_OPTIONS = ["2", "3", "4"]
COMBO_TYPE_OPTIONS = ["pairing", "trio", "quartet", "quintet", "full_team"]

Figure = None
FigureCanvasTkAgg = None
NavigationToolbar2Tk = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="View or export charts from the generated Champions web metrics CSV datasets.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=(
            "Base collector output path, or any one of the generated dataset CSVs. "
            "Defaults to results/web_metrics/champions_web_metrics.csv."
        ),
    )
    parser.add_argument(
        "--source",
        help="Optional source filter to preselect in the viewer or export mode.",
    )
    parser.add_argument(
        "--format-name",
        help="Optional format-name filter to preselect in the viewer or export mode.",
    )
    parser.add_argument(
        "--snapshot-date",
        help="Optional snapshot date (YYYY-MM-DD) to preselect in the viewer or export mode.",
    )
    parser.add_argument(
        "--pokemon",
        help="Optional Pokemon name to preselect on the set explorer tab or in export mode.",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=15,
        help="Default number of ranked rows to show per chart.",
    )
    parser.add_argument(
        "--export-dir",
        help="If set, write PNG charts to this directory instead of launching the interactive viewer.",
    )
    parser.add_argument(
        "--list-snapshots",
        action="store_true",
        help="Print the discovered source/format/snapshot combinations and exit.",
    )
    return parser.parse_args()


def load_matplotlib(gui: bool) -> None:
    global Figure, FigureCanvasTkAgg, NavigationToolbar2Tk

    try:
        import matplotlib

        matplotlib.use("TkAgg" if gui else "Agg")
        from matplotlib.figure import Figure as MatplotlibFigure

        Figure = MatplotlibFigure
        if gui:
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg as TkFigureCanvas
            from matplotlib.backends.backend_tkagg import NavigationToolbar2Tk as TkNavigationToolbar

            FigureCanvasTkAgg = TkFigureCanvas
            NavigationToolbar2Tk = TkNavigationToolbar
    except ImportError as exc:
        raise SystemExit(
            "matplotlib is required for graph viewing. Install dependencies with: pip install -r requirements.txt"
        ) from exc


def normalize_whitespace(value: str) -> str:
    return " ".join(str(value or "").split()).strip()


def safe_int(value, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def safe_float(value, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def make_output_base(path_value: str | Path) -> Path:
    path = Path(path_value)
    if path.suffix.lower() != ".csv":
        return path

    for dataset_name in DATASET_ORDER:
        suffix = f"_{dataset_name}.csv"
        if path.name.endswith(suffix):
            return path.with_name(path.name[: -len(suffix)] + ".csv")

    return path


def resolve_output_paths(path_value: str | Path) -> dict[str, Path]:
    output_path = make_output_base(path_value)
    if output_path.suffix.lower() == ".csv":
        base_dir = output_path.parent
        base_name = output_path.stem
    else:
        base_dir = output_path.parent
        base_name = output_path.name

    return {
        dataset_name: base_dir / f"{base_name}_{dataset_name}.csv"
        for dataset_name in DATASET_ORDER
    }


def read_csv_rows(csv_path: Path) -> list[dict]:
    if not csv_path.exists():
        return []

    with open(csv_path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def load_datasets(path_value: str | Path) -> tuple[dict[str, list[dict]], dict[str, Path]]:
    dataset_paths = resolve_output_paths(path_value)
    return {name: read_csv_rows(path) for name, path in dataset_paths.items()}, dataset_paths


def sanitize_filename(value: str) -> str:
    cleaned = []
    for char in normalize_whitespace(value):
        if char.isalnum() or char in {"-", "_"}:
            cleaned.append(char)
        elif char in {" ", "/", ":", "|"}:
            cleaned.append("_")
    text = "".join(cleaned).strip("_")
    return text or "chart"


def unique_values(rows: list[dict], field_name: str) -> list[str]:
    return sorted({normalize_whitespace(row.get(field_name, "")) for row in rows if normalize_whitespace(row.get(field_name, ""))})


def all_rows(datasets: dict[str, list[dict]]) -> list[dict]:
    combined = []
    for rows in datasets.values():
        combined.extend(rows)
    return combined


def filter_rows(
    rows: list[dict],
    source: str = "",
    format_name: str = "",
    snapshot_date: str = "",
) -> list[dict]:
    filtered = []
    for row in rows:
        if source and row.get("source", "") != source:
            continue
        if format_name and row.get("format_name", "") != format_name:
            continue
        if snapshot_date and row.get("snapshot_date", "") != snapshot_date:
            continue
        filtered.append(row)
    return filtered


def select_default_source(datasets: dict[str, list[dict]], requested: str = "") -> str:
    sources = unique_values(all_rows(datasets), "source")
    if requested in sources:
        return requested
    return sources[0] if sources else ""


def select_default_format(datasets: dict[str, list[dict]], source: str, requested: str = "") -> str:
    rows = filter_rows(all_rows(datasets), source=source)
    formats = unique_values(rows, "format_name")
    if requested in formats:
        return requested
    return formats[0] if formats else ""


def select_default_snapshot(
    datasets: dict[str, list[dict]],
    source: str,
    format_name: str,
    requested: str = "",
) -> str:
    rows = filter_rows(all_rows(datasets), source=source, format_name=format_name)
    snapshots = sorted(unique_values(rows, "snapshot_date"), reverse=True)
    if requested in snapshots:
        return requested
    return snapshots[0] if snapshots else ""


def select_default_pokemon(rows: list[dict], requested: str = "") -> str:
    counts = Counter(row.get("pokemon_name", "") for row in rows if row.get("pokemon_name", ""))
    if requested in counts:
        return requested
    if not counts:
        return ""
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def create_figure(width: float = 10.0, height: float = 6.0):
    return Figure(figsize=(width, height), constrained_layout=True)


def draw_empty_state(ax, title: str, message: str) -> None:
    ax.set_title(title)
    ax.axis("off")
    ax.text(0.5, 0.5, message, ha="center", va="center", fontsize=12)


def format_combo_label(value: str) -> str:
    return value.replace(":", " + ")


def annotate_horizontal_bars(ax, values: list[float], suffix: str = "") -> None:
    for index, value in enumerate(values):
        ax.text(value, index, f" {value:.1f}{suffix}" if isinstance(value, float) else f" {value}{suffix}", va="center")


def plot_counter(ax, counter: Counter, title: str, top_n: int, color: str, suffix: str = "") -> None:
    items = sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:max(top_n, 1)]
    if not items:
        draw_empty_state(ax, title, "No data for this view.")
        return

    labels = [item[0] for item in reversed(items)]
    values = [item[1] for item in reversed(items)]
    ax.barh(labels, values, color=color)
    ax.set_title(title)
    annotate_horizontal_bars(ax, values, suffix=suffix)


def create_usage_figure(rows: list[dict], top_n: int):
    figure = create_figure(10.0, 6.5)
    ax = figure.add_subplot(111)
    ranked_rows = sorted(rows, key=lambda row: safe_int(row.get("pokemon_rank"), default=999999))[:max(top_n, 1)]
    if not ranked_rows:
        draw_empty_state(ax, "Pokemon Usage", "No usage rows are available for this selection.")
        return figure

    labels = [row.get("pokemon_name", "") for row in reversed(ranked_rows)]
    values = [safe_float(row.get("usage_pct")) for row in reversed(ranked_rows)]
    ax.barh(labels, values, color="#4c78a8")
    ax.set_title("Pokemon Usage")
    ax.set_xlabel("Usage %")
    annotate_horizontal_bars(ax, values, suffix="%")
    return figure


def create_pokemon_performance_figure(rows: list[dict], top_n: int):
    figure = create_figure(13.0, 6.5)
    ax_bar = figure.add_subplot(121)
    ax_scatter = figure.add_subplot(122)

    if not rows:
        draw_empty_state(ax_bar, "Pokemon Team Metrics", "No team-metric rows are available for this selection.")
        draw_empty_state(ax_scatter, "Usage vs Win Rate", "No team-metric rows are available for this selection.")
        return figure

    ranked_rows = sorted(
        rows,
        key=lambda row: (
            -safe_int(row.get("team_appearances")),
            -safe_float(row.get("win_rate")),
            row.get("pokemon_name", ""),
        ),
    )
    bar_rows = ranked_rows[:max(top_n, 1)]
    labels = [row.get("pokemon_name", "") for row in reversed(bar_rows)]
    appearances = [safe_int(row.get("team_appearances")) for row in reversed(bar_rows)]
    ax_bar.barh(labels, appearances, color="#72b7b2")
    ax_bar.set_title("Most Common Top-Team Pokemon")
    ax_bar.set_xlabel("Team Appearances")
    annotate_horizontal_bars(ax_bar, appearances)

    scatter_rows = [row for row in rows if safe_int(row.get("team_appearances")) > 0]
    if scatter_rows:
        usage_values = [safe_float(row.get("usage_pct")) for row in scatter_rows]
        win_rates = [safe_float(row.get("win_rate")) for row in scatter_rows]
        sizes = [safe_int(row.get("team_appearances")) * 28 + 48 for row in scatter_rows]
        ax_scatter.scatter(usage_values, win_rates, s=sizes, color="#f58518", alpha=0.7, edgecolors="#7f4f10")
        ax_scatter.set_title("Usage vs Top-Team Win Rate")
        ax_scatter.set_xlabel("Format Usage %")
        ax_scatter.set_ylabel("Top-Team Win Rate %")
        highlight_rows = ranked_rows[: min(max(top_n, 1), 12)]
        for row in highlight_rows:
            ax_scatter.annotate(
                row.get("pokemon_name", ""),
                (safe_float(row.get("usage_pct")), safe_float(row.get("win_rate"))),
                fontsize=8,
                xytext=(4, 4),
                textcoords="offset points",
            )
    else:
        draw_empty_state(ax_scatter, "Usage vs Win Rate", "No scatter data is available for this selection.")

    return figure


def create_team_structure_figure(
    core_rows: list[dict],
    combo_rows: list[dict],
    top_n: int,
    core_size: str,
    combo_type: str,
):
    figure = create_figure(13.0, 6.5)
    ax_core = figure.add_subplot(121)
    ax_combo = figure.add_subplot(122)

    filtered_core_rows = [row for row in core_rows if row.get("core_size", "") == core_size]
    filtered_core_rows = sorted(filtered_core_rows, key=lambda row: safe_int(row.get("core_rank"), default=999999))[:max(top_n, 1)]
    if filtered_core_rows:
        core_labels = [format_combo_label(row.get("pokemon_names", "")) for row in reversed(filtered_core_rows)]
        core_values = [safe_float(row.get("usage_pct")) for row in reversed(filtered_core_rows)]
        ax_core.barh(core_labels, core_values, color="#54a24b")
        ax_core.set_title(f"Top Source {core_size}-Pokemon Cores")
        ax_core.set_xlabel("Usage %")
        annotate_horizontal_bars(ax_core, core_values, suffix="%")
    else:
        draw_empty_state(ax_core, "Source Cores", "No source core rows match this selection.")

    filtered_combo_rows = [row for row in combo_rows if row.get("combination_type", "") == combo_type]
    filtered_combo_rows = sorted(
        filtered_combo_rows,
        key=lambda row: (
            -safe_int(row.get("team_appearances")),
            -safe_float(row.get("win_rate")),
            row.get("pokemon_names", ""),
        ),
    )[:max(top_n, 1)]
    if filtered_combo_rows:
        combo_labels = [format_combo_label(row.get("pokemon_names", "")) for row in reversed(filtered_combo_rows)]
        combo_values = [safe_int(row.get("team_appearances")) for row in reversed(filtered_combo_rows)]
        ax_combo.barh(combo_labels, combo_values, color="#e45756")
        ax_combo.set_title(f"Most Common Derived {combo_type.title()}s")
        ax_combo.set_xlabel("Team Appearances")
        annotate_horizontal_bars(ax_combo, combo_values)
    else:
        draw_empty_state(ax_combo, "Derived Combinations", "No derived combination rows match this selection.")

    return figure


def create_set_explorer_figure(rows: list[dict], pokemon_name: str, top_n: int):
    figure = create_figure(13.0, 8.0)
    ax_item = figure.add_subplot(221)
    ax_ability = figure.add_subplot(222)
    ax_move = figure.add_subplot(223)
    ax_teammate = figure.add_subplot(224)

    selected_rows = [row for row in rows if row.get("pokemon_name", "") == pokemon_name]
    if not selected_rows:
        draw_empty_state(ax_item, "Items", "No set data is available for this Pokemon.")
        draw_empty_state(ax_ability, "Abilities", "No set data is available for this Pokemon.")
        draw_empty_state(ax_move, "Moves", "No set data is available for this Pokemon.")
        draw_empty_state(ax_teammate, "Teammates", "No set data is available for this Pokemon.")
        return figure

    item_counter = Counter(row.get("item", "Unknown") or "Unknown" for row in selected_rows)
    ability_counter = Counter(row.get("ability", "Unknown") or "Unknown" for row in selected_rows)
    move_counter = Counter()
    teammate_counter = Counter()
    for row in selected_rows:
        for field_name in ("move_1", "move_2", "move_3", "move_4"):
            move_name = normalize_whitespace(row.get(field_name, ""))
            if move_name:
                move_counter[move_name] += 1
        for teammate in row.get("teammates", "").split(":"):
            teammate_name = normalize_whitespace(teammate)
            if teammate_name:
                teammate_counter[teammate_name] += 1

    plot_counter(ax_item, item_counter, f"{pokemon_name} Items", top_n, "#4c78a8")
    plot_counter(ax_ability, ability_counter, f"{pokemon_name} Abilities", top_n, "#72b7b2")
    plot_counter(ax_move, move_counter, f"{pokemon_name} Moves", top_n, "#f58518")
    plot_counter(ax_teammate, teammate_counter, f"{pokemon_name} Teammates", top_n, "#54a24b")
    return figure


class FigureHost(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)
        self.canvas = None
        self.toolbar = None
        self.figure = None

    def show_figure(self, figure) -> None:
        self.clear()
        self.figure = figure
        self.canvas = FigureCanvasTkAgg(figure, master=self)
        widget = self.canvas.get_tk_widget()
        widget.pack(fill="both", expand=True)
        self.toolbar = NavigationToolbar2Tk(self.canvas, self, pack_toolbar=False)
        self.toolbar.update()
        self.toolbar.pack(fill="x")
        self.canvas.draw_idle()

    def clear(self) -> None:
        if self.toolbar is not None:
            self.toolbar.destroy()
            self.toolbar = None
        if self.canvas is not None:
            self.canvas.get_tk_widget().destroy()
            self.canvas = None
        self.figure = None


class UsageTab(ttk.Frame):
    def __init__(self, master, app, default_top_n: int):
        super().__init__(master)
        self.app = app
        self.top_n_var = tk.IntVar(value=max(default_top_n, 1))

        controls = ttk.Frame(self)
        controls.pack(fill="x", padx=10, pady=(10, 4))
        ttk.Label(controls, text="Top N").pack(side="left")
        top_n_spin = ttk.Spinbox(controls, from_=5, to=50, increment=1, textvariable=self.top_n_var, width=6, command=self.render)
        top_n_spin.pack(side="left", padx=(6, 12))
        top_n_spin.bind("<Return>", lambda _event: self.render())

        self.figure_host = FigureHost(self)
        self.figure_host.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def render(self) -> None:
        figure = create_usage_figure(self.app.get_rows("pokemon_usage"), self.top_n_var.get())
        self.figure_host.show_figure(figure)


class PerformanceTab(ttk.Frame):
    def __init__(self, master, app, default_top_n: int):
        super().__init__(master)
        self.app = app
        self.top_n_var = tk.IntVar(value=max(default_top_n, 1))

        controls = ttk.Frame(self)
        controls.pack(fill="x", padx=10, pady=(10, 4))
        ttk.Label(controls, text="Top N").pack(side="left")
        top_n_spin = ttk.Spinbox(controls, from_=5, to=50, increment=1, textvariable=self.top_n_var, width=6, command=self.render)
        top_n_spin.pack(side="left", padx=(6, 12))
        top_n_spin.bind("<Return>", lambda _event: self.render())

        self.figure_host = FigureHost(self)
        self.figure_host.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def render(self) -> None:
        figure = create_pokemon_performance_figure(self.app.get_rows("pokemon_team_metrics"), self.top_n_var.get())
        self.figure_host.show_figure(figure)


class TeamStructureTab(ttk.Frame):
    def __init__(self, master, app, default_top_n: int):
        super().__init__(master)
        self.app = app
        self.top_n_var = tk.IntVar(value=max(default_top_n, 1))
        self.core_size_var = tk.StringVar(value=CORE_SIZE_OPTIONS[0])
        self.combo_type_var = tk.StringVar(value=COMBO_TYPE_OPTIONS[0])

        controls = ttk.Frame(self)
        controls.pack(fill="x", padx=10, pady=(10, 4))
        ttk.Label(controls, text="Core Size").pack(side="left")
        core_combo = ttk.Combobox(controls, textvariable=self.core_size_var, values=CORE_SIZE_OPTIONS, state="readonly", width=6)
        core_combo.pack(side="left", padx=(6, 12))
        core_combo.bind("<<ComboboxSelected>>", lambda _event: self.render())

        ttk.Label(controls, text="Derived Combo").pack(side="left")
        combo_combo = ttk.Combobox(controls, textvariable=self.combo_type_var, values=COMBO_TYPE_OPTIONS, state="readonly", width=12)
        combo_combo.pack(side="left", padx=(6, 12))
        combo_combo.bind("<<ComboboxSelected>>", lambda _event: self.render())

        ttk.Label(controls, text="Top N").pack(side="left")
        top_n_spin = ttk.Spinbox(controls, from_=5, to=50, increment=1, textvariable=self.top_n_var, width=6, command=self.render)
        top_n_spin.pack(side="left", padx=(6, 12))
        top_n_spin.bind("<Return>", lambda _event: self.render())

        self.figure_host = FigureHost(self)
        self.figure_host.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def render(self) -> None:
        figure = create_team_structure_figure(
            self.app.get_rows("team_cores"),
            self.app.get_rows("team_combinations"),
            self.top_n_var.get(),
            self.core_size_var.get(),
            self.combo_type_var.get(),
        )
        self.figure_host.show_figure(figure)


class SetExplorerTab(ttk.Frame):
    def __init__(self, master, app, default_top_n: int, initial_pokemon: str = ""):
        super().__init__(master)
        self.app = app
        self.initial_pokemon = initial_pokemon
        self.top_n_var = tk.IntVar(value=max(default_top_n, 1))
        self.pokemon_var = tk.StringVar(value=initial_pokemon)

        controls = ttk.Frame(self)
        controls.pack(fill="x", padx=10, pady=(10, 4))
        ttk.Label(controls, text="Pokemon").pack(side="left")
        self.pokemon_combo = ttk.Combobox(controls, textvariable=self.pokemon_var, state="readonly", width=28)
        self.pokemon_combo.pack(side="left", padx=(6, 12))
        self.pokemon_combo.bind("<<ComboboxSelected>>", lambda _event: self.render())

        ttk.Label(controls, text="Top N").pack(side="left")
        top_n_spin = ttk.Spinbox(controls, from_=5, to=50, increment=1, textvariable=self.top_n_var, width=6, command=self.render)
        top_n_spin.pack(side="left", padx=(6, 12))
        top_n_spin.bind("<Return>", lambda _event: self.render())

        self.figure_host = FigureHost(self)
        self.figure_host.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def refresh_pokemon_options(self) -> None:
        rows = self.app.get_rows("team_pokemon_details")
        pokemon_names = unique_values(rows, "pokemon_name")
        self.pokemon_combo.configure(values=pokemon_names)
        selected_name = select_default_pokemon(rows, self.pokemon_var.get() or self.initial_pokemon)
        self.pokemon_var.set(selected_name)

    def render(self) -> None:
        self.refresh_pokemon_options()
        figure = create_set_explorer_figure(
            self.app.get_rows("team_pokemon_details"),
            self.pokemon_var.get(),
            self.top_n_var.get(),
        )
        self.figure_host.show_figure(figure)


class MetricsViewerApp(tk.Tk):
    def __init__(
        self,
        output_path: str,
        default_top_n: int,
        initial_source: str = "",
        initial_format_name: str = "",
        initial_snapshot_date: str = "",
        initial_pokemon: str = "",
    ):
        super().__init__()
        self.title("Pokemon Champions Web Metrics Viewer")
        self.geometry("1450x940")

        self.output_var = tk.StringVar(value=str(make_output_base(output_path)))
        self.source_var = tk.StringVar(value=initial_source)
        self.format_var = tk.StringVar(value=initial_format_name)
        self.snapshot_var = tk.StringVar(value=initial_snapshot_date)
        self.status_var = tk.StringVar(value="Loading datasets...")

        self.dataset_rows: dict[str, list[dict]] = {name: [] for name in DATASET_ORDER}
        self.dataset_paths: dict[str, Path] = resolve_output_paths(self.output_var.get())

        self._build_ui(default_top_n, initial_pokemon)
        self.reload_data()

    def _build_ui(self, default_top_n: int, initial_pokemon: str) -> None:
        root_frame = ttk.Frame(self)
        root_frame.pack(fill="both", expand=True)

        controls = ttk.Frame(root_frame)
        controls.pack(fill="x", padx=10, pady=10)

        ttk.Label(controls, text="Output Base").grid(row=0, column=0, sticky="w")
        output_entry = ttk.Entry(controls, textvariable=self.output_var, width=70)
        output_entry.grid(row=0, column=1, sticky="ew", padx=(6, 8))
        output_entry.bind("<Return>", lambda _event: self.reload_data())

        ttk.Button(controls, text="Browse", command=self.browse_output).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(controls, text="Reload", command=self.reload_data).grid(row=0, column=3, padx=(0, 12))

        ttk.Label(controls, text="Source").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.source_combo = ttk.Combobox(controls, textvariable=self.source_var, state="readonly", width=18)
        self.source_combo.grid(row=1, column=1, sticky="w", padx=(6, 8), pady=(10, 0))
        self.source_combo.bind("<<ComboboxSelected>>", lambda _event: self.on_source_changed())

        ttk.Label(controls, text="Format").grid(row=1, column=2, sticky="w", pady=(10, 0))
        self.format_combo = ttk.Combobox(controls, textvariable=self.format_var, state="readonly", width=42)
        self.format_combo.grid(row=1, column=3, sticky="w", padx=(6, 8), pady=(10, 0))
        self.format_combo.bind("<<ComboboxSelected>>", lambda _event: self.on_format_changed())

        ttk.Label(controls, text="Snapshot").grid(row=1, column=4, sticky="w", pady=(10, 0))
        self.snapshot_combo = ttk.Combobox(controls, textvariable=self.snapshot_var, state="readonly", width=14)
        self.snapshot_combo.grid(row=1, column=5, sticky="w", padx=(6, 0), pady=(10, 0))
        self.snapshot_combo.bind("<<ComboboxSelected>>", lambda _event: self.render_tabs())

        controls.columnconfigure(1, weight=1)

        notebook = ttk.Notebook(root_frame)
        notebook.pack(fill="both", expand=True, padx=10, pady=(0, 8))

        self.usage_tab = UsageTab(notebook, self, default_top_n)
        self.performance_tab = PerformanceTab(notebook, self, default_top_n)
        self.team_structure_tab = TeamStructureTab(notebook, self, default_top_n)
        self.set_explorer_tab = SetExplorerTab(notebook, self, default_top_n, initial_pokemon=initial_pokemon)

        notebook.add(self.usage_tab, text="Usage")
        notebook.add(self.performance_tab, text="Performance")
        notebook.add(self.team_structure_tab, text="Team Structures")
        notebook.add(self.set_explorer_tab, text="Set Explorer")

        status_bar = ttk.Label(root_frame, textvariable=self.status_var, anchor="w")
        status_bar.pack(fill="x", padx=10, pady=(0, 8))

    def browse_output(self) -> None:
        selected = filedialog.askopenfilename(
            title="Choose the collector base CSV or one of the generated dataset CSVs",
            initialdir=SCRIPT_DIR,
            filetypes=(("CSV files", "*.csv"), ("All files", "*.*")),
        )
        if not selected:
            return
        self.output_var.set(str(make_output_base(selected)))
        self.reload_data()

    def get_rows(self, dataset_name: str) -> list[dict]:
        return filter_rows(
            self.dataset_rows.get(dataset_name, []),
            source=self.source_var.get(),
            format_name=self.format_var.get(),
            snapshot_date=self.snapshot_var.get(),
        )

    def reload_data(self) -> None:
        try:
            self.dataset_rows, self.dataset_paths = load_datasets(self.output_var.get())
        except OSError as exc:
            messagebox.showerror("Load Failed", str(exc))
            return

        source_value = select_default_source(self.dataset_rows, self.source_var.get())
        self.source_var.set(source_value)
        self._refresh_format_options()
        self._refresh_snapshot_options()
        self.render_tabs()

        dataset_summary = ", ".join(
            f"{name}={len(rows)}"
            for name, rows in self.dataset_rows.items()
            if rows
        ) or "no dataset rows found"
        self.status_var.set(f"Loaded {dataset_summary}")

    def _refresh_format_options(self) -> None:
        rows = filter_rows(all_rows(self.dataset_rows), source=self.source_var.get())
        format_names = unique_values(rows, "format_name")
        self.format_combo.configure(values=format_names)
        self.format_var.set(select_default_format(self.dataset_rows, self.source_var.get(), self.format_var.get()))

    def _refresh_snapshot_options(self) -> None:
        rows = filter_rows(all_rows(self.dataset_rows), source=self.source_var.get(), format_name=self.format_var.get())
        snapshot_dates = sorted(unique_values(rows, "snapshot_date"), reverse=True)
        self.snapshot_combo.configure(values=snapshot_dates)
        self.snapshot_var.set(
            select_default_snapshot(
                self.dataset_rows,
                self.source_var.get(),
                self.format_var.get(),
                self.snapshot_var.get(),
            )
        )

    def on_source_changed(self) -> None:
        self._refresh_format_options()
        self._refresh_snapshot_options()
        self.render_tabs()

    def on_format_changed(self) -> None:
        self._refresh_snapshot_options()
        self.render_tabs()

    def render_tabs(self) -> None:
        self.usage_tab.render()
        self.performance_tab.render()
        self.team_structure_tab.render()
        self.set_explorer_tab.render()


def print_snapshot_listing(datasets: dict[str, list[dict]]) -> int:
    rows = all_rows(datasets)
    if not rows:
        print("No dataset rows were found.")
        return 1

    sources = unique_values(rows, "source")
    for source in sources:
        print(f"Source: {source}")
        source_rows = filter_rows(rows, source=source)
        for format_name in unique_values(source_rows, "format_name"):
            print(f"  Format: {format_name}")
            format_rows = filter_rows(source_rows, format_name=format_name)
            for snapshot_date in sorted(unique_values(format_rows, "snapshot_date"), reverse=True):
                print(f"    Snapshot: {snapshot_date}")
    return 0


def export_charts(
    output_path: str,
    export_dir: str,
    source: str,
    format_name: str,
    snapshot_date: str,
    pokemon_name: str,
    top_n: int,
) -> int:
    datasets, _dataset_paths = load_datasets(output_path)
    source = select_default_source(datasets, source)
    format_name = select_default_format(datasets, source, format_name)
    snapshot_date = select_default_snapshot(datasets, source, format_name, snapshot_date)

    scoped_rows = {
        dataset_name: filter_rows(rows, source=source, format_name=format_name, snapshot_date=snapshot_date)
        for dataset_name, rows in datasets.items()
    }
    pokemon_name = select_default_pokemon(scoped_rows.get("team_pokemon_details", []), pokemon_name)

    export_path = Path(export_dir)
    export_path.mkdir(parents=True, exist_ok=True)
    file_prefix = sanitize_filename(f"{source}_{format_name}_{snapshot_date}")

    figure_specs = [
        (
            create_usage_figure(scoped_rows.get("pokemon_usage", []), top_n),
            export_path / f"{file_prefix}_usage.png",
        ),
        (
            create_pokemon_performance_figure(scoped_rows.get("pokemon_team_metrics", []), top_n),
            export_path / f"{file_prefix}_performance.png",
        ),
        (
            create_team_structure_figure(
                scoped_rows.get("team_cores", []),
                scoped_rows.get("team_combinations", []),
                top_n,
                CORE_SIZE_OPTIONS[0],
                COMBO_TYPE_OPTIONS[0],
            ),
            export_path / f"{file_prefix}_team_structures.png",
        ),
        (
            create_set_explorer_figure(scoped_rows.get("team_pokemon_details", []), pokemon_name, top_n),
            export_path / f"{file_prefix}_sets_{sanitize_filename(pokemon_name or 'pokemon')}.png",
        ),
    ]

    for figure, path in figure_specs:
        figure.savefig(path, dpi=160)
        print(path)

    return 0


def main() -> int:
    args = parse_args()

    datasets, _dataset_paths = load_datasets(args.output)
    if args.list_snapshots:
        return print_snapshot_listing(datasets)

    if args.export_dir:
        load_matplotlib(gui=False)
        return export_charts(
            args.output,
            args.export_dir,
            args.source or "",
            args.format_name or "",
            args.snapshot_date or "",
            args.pokemon or "",
            max(args.top_n, 1),
        )

    load_matplotlib(gui=True)
    app = MetricsViewerApp(
        output_path=args.output,
        default_top_n=max(args.top_n, 1),
        initial_source=args.source or "",
        initial_format_name=args.format_name or "",
        initial_snapshot_date=args.snapshot_date or "",
        initial_pokemon=args.pokemon or "",
    )
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())