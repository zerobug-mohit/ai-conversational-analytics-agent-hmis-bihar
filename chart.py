"""
chart.py
--------
McKinsey-style, slide-ready charts from BigQuery result sets.
- Font: Tahoma
- 200 DPI output
- Thin figure border, action title with rule, source footer
- Data labels inside bars (white) when bar is wide/tall enough
- No gridlines on bar charts; very faint gridlines on line/area charts

Chart types
-----------
gauge                  — single metric as a horizontal progress bar
line                   — time-series; supports series_column for multi-series long format
bar / column           — vertical bar chart
horizontal_bar         — horizontal bar chart
grouped_bar            — multiple metrics side-by-side
lollipop               — horizontal lollipop (dot + stem) for ranked lists
pie                    — pie chart for part-to-whole (≤8 segments)
donut                  — donut chart with aggregate total in center
funnel                 — ordered dropout / funnel analysis with retention %
stacked_bar            — vertical stacked bars for composition
stacked_column         — alias for stacked_bar
stacked_horizontal_bar — horizontal stacked bars
area                   — area chart (single or multi-series / long format)
stacked_area           — stacked area chart
heatmap                — matrix heatmap; x_column = column dim, series_column = row dim
dumbbell               — two-value comparison per entity (exactly 2 y_columns)
slope                  — slopegraph before/after (exactly 2 y_columns)
scatter                — scatter plot; label_column for point labels
bubble                 — bubble chart; size_column for bubble size
waterfall              — sequential positive/negative contribution chart
treemap                — area-proportional rectangles (requires squarify package)
radar                  — radar / spider multi-indicator chart
box_plot               — box-and-whisker distribution chart
dot_plot               — multi-metric proportional dot grid per entity
"""

import io
import logging
import threading

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

logger = logging.getLogger(__name__)

# ── Global style ──────────────────────────────────────────────────────────────
matplotlib.rcParams.update({
    "font.family":          "sans-serif",
    "font.sans-serif":      ["Tahoma", "Verdana", "DejaVu Sans"],
    "figure.facecolor":     "#FFFFFF",
    "axes.facecolor":       "#FFFFFF",
    "axes.edgecolor":       "#DDDDDD",
    "axes.linewidth":       0.8,
    "axes.spines.top":      False,
    "axes.spines.right":    False,
    "axes.spines.left":     False,
    "axes.spines.bottom":   False,
    "axes.grid":            False,
    "xtick.bottom":         False,
    "xtick.color":          "#555555",
    "ytick.left":           False,
    "ytick.color":          "#555555",
    "xtick.labelsize":      10,
    "ytick.labelsize":      10,
    "xtick.major.pad":      5,
    "ytick.major.pad":      5,
    "axes.labelcolor":      "#333333",
    "axes.labelsize":       11,
    "legend.frameon":       False,
    "legend.fontsize":      10,
    "legend.borderpad":     0,
    "legend.labelspacing":  0.4,
    "text.color":           "#1A1A1A",
})

# ── McKinsey colour palette ───────────────────────────────────────────────────
_NAVY    = "#003D6B"
_BLUE    = "#1565A8"
_STEEL   = "#2E86C1"
_TEAL    = "#148A72"
_GRAY_L  = "#E4E4E4"   # gauge track / bar backgrounds

_PALETTE = [_NAVY, _STEEL, _TEAL, "#D4860B", "#7B3FA0", _BLUE]

_DPI     = 200
_FOOTER  = "Source: Bihar HMIS Data  |  HMIS Analytics Bot"
_LABEL_INSIDE_THRESH = 0.15   # fraction of max where label flips inside/outside

# Matplotlib's global state is NOT thread-safe. asyncio.to_thread() runs chart
# generation in a thread pool, so concurrent users would corrupt each other's
# figure handles. Serialise all rendering through this lock so only one chart
# renders at a time. Charts are fast (<1s), so the wait is negligible.
_chart_lock = threading.Lock()


# ── Public entry point ────────────────────────────────────────────────────────

def generate_chart(spec: dict, rows: list[dict]) -> io.BytesIO | None:
    if not spec.get("should_visualize") or not rows:
        return None
    chart_type = spec.get("chart_type", "none")
    if chart_type == "none":
        return None
    with _chart_lock:
        try:
            if chart_type == "gauge":
                return _gauge(spec, rows)
            elif chart_type == "line":
                return _line(spec, rows)
            elif chart_type in ("bar", "column"):
                return _bar(spec, rows, horizontal=False)
            elif chart_type == "horizontal_bar":
                return _bar(spec, rows, horizontal=True)
            elif chart_type in ("grouped_bar", "grouped_column"):
                return _grouped_bar(spec, rows)
            elif chart_type == "lollipop":
                return _lollipop(spec, rows)
            elif chart_type == "pie":
                return _pie(spec, rows, donut=False)
            elif chart_type == "donut":
                return _pie(spec, rows, donut=True)
            elif chart_type == "funnel":
                return _funnel(spec, rows)
            elif chart_type in ("stacked_bar", "stacked_column"):
                return _stacked_bar(spec, rows, horizontal=False)
            elif chart_type == "stacked_horizontal_bar":
                return _stacked_bar(spec, rows, horizontal=True)
            elif chart_type == "area":
                return _area(spec, rows, stacked=False)
            elif chart_type == "stacked_area":
                return _area(spec, rows, stacked=True)
            elif chart_type == "heatmap":
                return _heatmap(spec, rows)
            elif chart_type == "dumbbell":
                return _dumbbell(spec, rows)
            elif chart_type == "slope":
                return _slope(spec, rows)
            elif chart_type in ("scatter", "connected_scatter"):
                return _scatter(spec, rows, connected=(chart_type == "connected_scatter"))
            elif chart_type == "bubble":
                return _scatter(spec, rows, bubble=True)
            elif chart_type == "waterfall":
                return _waterfall(spec, rows)
            elif chart_type == "treemap":
                return _treemap(spec, rows)
            elif chart_type == "radar":
                return _radar(spec, rows)
            elif chart_type == "box_plot":
                return _box_plot(spec, rows)
            elif chart_type == "dot_plot":
                return _dot_plot(spec, rows)
        except Exception as exc:
            logger.warning(f"Chart generation failed ({chart_type}): {exc}", exc_info=True)
        finally:
            plt.close("all")  # release figures even when an exception skips _save()
    return None


# ── Shared helpers ────────────────────────────────────────────────────────────

def _save(fig: plt.Figure) -> io.BytesIO:
    """Save to PNG with slide-ready margins, border, and footer."""
    fig.text(
        0.015, 0.012, _FOOTER,
        fontsize=8, color="#AAAAAA", va="bottom", ha="left",
        transform=fig.transFigure,
    )
    # linewidth kwarg in savefig() was removed in matplotlib ≥3.9 for the Agg backend.
    # Set the figure border on the patch directly before saving.
    fig.patch.set_edgecolor("#CCCCCC")
    fig.patch.set_linewidth(1.2)
    buf = io.BytesIO()
    fig.savefig(
        buf, format="png", dpi=_DPI,
        bbox_inches="tight", pad_inches=0.30,
        facecolor="white",
    )
    buf.seek(0)
    plt.close(fig)
    return buf


def _title(ax: plt.Axes, text: str) -> None:
    """Bold left-aligned action title + thin rule beneath it."""
    ax.set_title(
        text, fontsize=14, fontweight="bold",
        color="#1A1A1A", loc="left", pad=16,
    )
    # ax.axhline rejects 'transform' in matplotlib ≥3.9; ax.plot accepts it fine
    ax.plot(
        [0, 1], [1, 1],
        transform=ax.transAxes,
        color="#DDDDDD", linewidth=0.8, clip_on=False,
        solid_capstyle="butt",
    )


def _bar_label(ax: plt.Axes, bar, val: float, max_val: float,
               horizontal: bool) -> None:
    """
    Place label inside the bar (white bold) when it fits,
    outside (dark) when the bar is too narrow/short.
    """
    threshold = max_val * _LABEL_INSIDE_THRESH
    fmt = f"{val:.1f}"

    if horizontal:
        w = bar.get_width()
        cy = bar.get_y() + bar.get_height() / 2
        if w >= threshold:
            ax.text(w / 2, cy, fmt,
                    va="center", ha="center",
                    fontsize=10, fontweight="bold", color="white")
        else:
            ax.text(w + max_val * 0.015, cy, fmt,
                    va="center", ha="left",
                    fontsize=10, fontweight="bold", color="#1A1A1A")
    else:
        h = bar.get_height()
        cx = bar.get_x() + bar.get_width() / 2
        if h >= threshold:
            ax.text(cx, h / 2, fmt,
                    ha="center", va="center",
                    fontsize=10, fontweight="bold", color="white")
        else:
            ax.text(cx, h + max_val * 0.015, fmt,
                    ha="center", va="bottom",
                    fontsize=10, fontweight="bold", color="#1A1A1A")


def _annotate_line_points(
    ax: plt.Axes,
    series_data: list[tuple[list, list, str]],
    fontsize: int = 9,
) -> None:
    """
    Place value labels on line chart points without collisions.

    At each x position, labels are sorted descending by y-value. The highest label
    goes above its marker (+9 pts). Any subsequent label whose y-value is within
    8% of the y-axis range of an already-placed "above" label is flipped below the
    marker (-9 pts) instead, preventing text overlap on crowded charts.
    """
    by_x: dict[str, list[tuple[float, str]]] = {}
    for x_vals, y_vals, color in series_data:
        for xv, yv in zip(x_vals, y_vals):
            by_x.setdefault(str(xv), []).append((yv, color))

    y_lo, y_hi = ax.get_ylim()
    collision_thresh = ((y_hi - y_lo) or 1.0) * 0.08

    for xv, group in by_x.items():
        group_sorted = sorted(group, key=lambda t: t[0], reverse=True)
        placed_above: list[float] = []

        offsets: list[tuple[int, str]] = []
        for yv, _ in group_sorted:
            too_close = any(abs(yv - py) < collision_thresh for py in placed_above)
            if too_close:
                offsets.append((-9, "top"))
            else:
                offsets.append((9, "bottom"))
                placed_above.append(yv)

        for (yv, color), (ypt, va) in zip(group_sorted, offsets):
            ax.annotate(
                f"{yv:.1f}", xy=(xv, yv),
                xytext=(0, ypt), textcoords="offset points",
                ha="center", va=va,
                fontsize=fontsize, color=color, fontweight="bold",
            )


# ── Chart implementations ─────────────────────────────────────────────────────

def _gauge(spec: dict, rows: list[dict]) -> io.BytesIO | None:
    """Single metric as a McKinsey-style horizontal progress bar."""
    y_col = (spec.get("y_columns") or [None])[0]
    if not y_col:
        return None

    value = float(rows[0].get(y_col) or 0)
    title = spec.get("title", "")

    fig, ax = plt.subplots(figsize=(11, 3.2))

    # Background track
    ax.barh(0, 100, color=_GRAY_L, height=0.44, zorder=1)
    # Value bar
    ax.barh(0, value, color=_NAVY, height=0.44, zorder=2)

    # Value label — white inside if wide, navy outside if narrow
    if value > 12:
        ax.text(value / 2, 0, f"{value:.1f}%",
                va="center", ha="center",
                fontsize=22, fontweight="bold", color="white", zorder=3)
    else:
        ax.text(value + 1.8, 0, f"{value:.1f}%",
                va="center", ha="left",
                fontsize=20, fontweight="bold", color=_NAVY, zorder=3)

    ax.set_xlim(0, 112)
    ax.set_ylim(-0.7, 0.7)
    ax.set_yticks([])
    ax.set_xlabel("(%)", fontsize=10, color="#888888", labelpad=6)
    ax.tick_params(axis="x", labelsize=10, colors="#888888", bottom=True)

    # Bottom spine only
    ax.spines["bottom"].set_visible(True)
    ax.spines["bottom"].set_color("#DDDDDD")

    _title(ax, title)
    fig.tight_layout(pad=2.0, rect=[0, 0.06, 1, 1])
    return _save(fig)


def _line(spec: dict, rows: list[dict]) -> io.BytesIO | None:
    """Time-series line chart — handles both wide-format (multiple y columns)
    and long-format (series_column groups rows into separate lines)."""
    x_col      = spec.get("x_column")
    y_cols     = spec.get("y_columns") or []
    y_labels   = spec.get("y_labels") or y_cols
    series_col = spec.get("series_column")

    if not x_col or not y_cols:
        return None

    # ── Long-format branch: one row per (date × series) ──────────────────────
    if series_col:
        y_col = y_cols[0]

        # Stable series order (first-seen)
        series_order = list(dict.fromkeys(str(r.get(series_col, "")) for r in rows))
        all_x_sorted = sorted(set(str(r.get(x_col, ""))[:7] for r in rows))
        n = len(all_x_sorted)

        fig, ax = plt.subplots(figsize=(max(11, n * 0.9), 6.5))

        series_data: list[tuple[list, list, str]] = []
        for i, sval in enumerate(series_order):
            s_rows = sorted(
                [r for r in rows if str(r.get(series_col, "")) == sval],
                key=lambda r: str(r.get(x_col, "")),
            )
            x_vals = [str(r.get(x_col, ""))[:7] for r in s_rows]
            y_vals = [float(r.get(y_col) or 0) for r in s_rows]
            color  = _PALETTE[i % len(_PALETTE)]

            ax.plot(x_vals, y_vals,
                    marker="o", markersize=7, linewidth=2.5,
                    color=color, label=sval, zorder=3,
                    markerfacecolor="white", markeredgewidth=2.2,
                    markeredgecolor=color)
            series_data.append((x_vals, y_vals, color))

        y_lo, y_hi = ax.get_ylim()
        ax.set_ylim(y_lo, y_hi + (y_hi - y_lo) * 0.14)
        _annotate_line_points(ax, series_data)

        ax.legend(loc="best", fontsize=10, ncol=min(len(series_order), 4))
        ax.yaxis.grid(True, color="#F0F0F0", linewidth=0.9, zorder=0)
        ax.set_axisbelow(True)
        ax.set_xlabel(spec.get("x_label") or "", fontsize=11, labelpad=8)
        ax.set_ylabel(spec.get("y_label") or "", fontsize=11, labelpad=8)
        plt.xticks(rotation=40, ha="right", fontsize=10)
        plt.yticks(fontsize=10)
        ax.spines["bottom"].set_visible(True)
        ax.spines["bottom"].set_color("#DDDDDD")
        _title(ax, spec.get("title") or "")
        fig.tight_layout(pad=2.0, rect=[0, 0.06, 1, 1])
        return _save(fig)

    # ── Wide-format branch: multiple y columns, each is a line ───────────────
    rows   = sorted(rows, key=lambda r: str(r.get(x_col, "")))
    x_vals = [str(r.get(x_col, ""))[:7] for r in rows]
    n      = len(x_vals)

    fig, ax = plt.subplots(figsize=(max(11, n * 0.9), 6.5))

    series_data: list[tuple[list, list, str]] = []
    for i, (col, label) in enumerate(zip(y_cols, y_labels)):
        y_vals = [float(r.get(col) or 0) for r in rows]
        color  = _PALETTE[i % len(_PALETTE)]
        ax.plot(x_vals, y_vals,
                marker="o", markersize=7, linewidth=2.5,
                color=color, label=label, zorder=3,
                markerfacecolor="white", markeredgewidth=2.2,
                markeredgecolor=color)
        series_data.append((x_vals, y_vals, color))

    y_lo, y_hi = ax.get_ylim()
    ax.set_ylim(y_lo, y_hi + (y_hi - y_lo) * 0.14)
    _annotate_line_points(ax, series_data)

    ax.yaxis.grid(True, color="#F0F0F0", linewidth=0.9, zorder=0)
    ax.set_axisbelow(True)
    ax.set_xlabel(spec.get("x_label") or "", fontsize=11, labelpad=8)
    ax.set_ylabel(spec.get("y_label") or "", fontsize=11, labelpad=8)
    if len(y_cols) > 1:
        ax.legend(loc="best", fontsize=10, ncol=min(len(y_cols), 4))
    plt.xticks(rotation=40, ha="right", fontsize=10)
    plt.yticks(fontsize=10)
    ax.spines["bottom"].set_visible(True)
    ax.spines["bottom"].set_color("#DDDDDD")
    _title(ax, spec.get("title") or "")
    fig.tight_layout(pad=2.0, rect=[0, 0.06, 1, 1])
    return _save(fig)


def _bar(spec: dict, rows: list[dict], horizontal: bool = False) -> io.BytesIO | None:
    """Vertical or horizontal bar chart with inside/outside labels."""
    x_col    = spec.get("x_column")
    y_cols   = spec.get("y_columns") or []
    y_labels = spec.get("y_labels") or y_cols
    if not y_cols:
        return None

    # Single-row multi-metric case (e.g. completeness or PRE breakdown for one
    # period: one row × pct_fully_complete + section_a_pct_fully + ... ).
    # No x_column to use, so synthesize categories from the y_labels and plot
    # each y_column as a bar of the single row's value. Always horizontal —
    # the labels are long English names ("ANC, Deliveries & Live Births") and
    # would overlap on a vertical axis.
    if (not x_col) and len(rows) == 1 and len(y_cols) >= 2:
        single = rows[0]
        cats = [str(lbl) for lbl in y_labels]
        vals = [float(single.get(c) or 0) for c in y_cols]
        pairs = sorted(zip(cats, vals), key=lambda t: t[1])
        cats, vals = [p[0] for p in pairs], [p[1] for p in pairs]
        max_val = max(vals) if vals else 1

        fig, ax = plt.subplots(figsize=(12, max(5, len(cats) * 0.60)))
        bars = ax.barh(cats, vals, color=_NAVY, height=0.60, zorder=2)
        for bar, val in zip(bars, vals):
            _bar_label(ax, bar, val, max_val, horizontal=True)

        # Caller may pass an x_label (e.g. "% Facilities Fully Complete");
        # otherwise fall back to a generic "Value".
        ax.set_xlabel(spec.get("x_label") or spec.get("y_label") or "Value",
                      fontsize=11, labelpad=8)
        ax.set_xlim(0, max_val * 1.25)
        plt.yticks(fontsize=10)
        ax.xaxis.grid(True, color="#F0F0F0", linewidth=0.8, zorder=0)
        ax.set_axisbelow(True)
        ax.spines["bottom"].set_visible(True)
        ax.spines["bottom"].set_color("#DDDDDD")

        _title(ax, spec.get("title") or "")
        fig.tight_layout(pad=2.0, rect=[0, 0.06, 1, 1])
        return _save(fig)

    if not x_col:
        return None
    if len(y_cols) > 1:
        return _grouped_bar(spec, rows)

    y_col   = y_cols[0]
    y_label = y_labels[0]

    rows    = rows[:20]
    cats    = [str(r.get(x_col, "")) for r in rows]
    vals    = [float(r.get(y_col) or 0) for r in rows]
    max_val = max(vals) if vals else 1

    if horizontal:
        pairs = sorted(zip(cats, vals), key=lambda t: t[1])
        cats, vals = [p[0] for p in pairs], [p[1] for p in pairs]
        max_val = max(vals) if vals else 1

        fig, ax = plt.subplots(figsize=(12, max(5, len(cats) * 0.60)))
        bars = ax.barh(cats, vals, color=_NAVY, height=0.60, zorder=2)
        for bar, val in zip(bars, vals):
            _bar_label(ax, bar, val, max_val, horizontal=True)

        ax.set_xlabel(y_label, fontsize=11, labelpad=8)
        ax.set_xlim(0, max_val * 1.25)
        plt.yticks(fontsize=10)
        ax.xaxis.grid(True, color="#F0F0F0", linewidth=0.8, zorder=0)
        ax.set_axisbelow(True)
        ax.spines["bottom"].set_visible(True)
        ax.spines["bottom"].set_color("#DDDDDD")

    else:
        fig, ax = plt.subplots(figsize=(max(10, len(cats) * 1.1), 6.5))
        bars = ax.bar(cats, vals, color=_NAVY, width=0.58, zorder=2)
        for bar, val in zip(bars, vals):
            _bar_label(ax, bar, val, max_val, horizontal=False)

        ax.set_ylabel(y_label, fontsize=11, labelpad=8)
        ax.set_ylim(0, max_val * 1.22)
        plt.xticks(rotation=40, ha="right", fontsize=10)
        plt.yticks(fontsize=10)
        ax.yaxis.grid(True, color="#F0F0F0", linewidth=0.8, zorder=0)
        ax.set_axisbelow(True)
        ax.spines["bottom"].set_visible(True)
        ax.spines["bottom"].set_color("#DDDDDD")

    _title(ax, spec.get("title") or "")
    fig.tight_layout(pad=2.0, rect=[0, 0.06, 1, 1])
    return _save(fig)


def _lollipop(spec: dict, rows: list[dict]) -> io.BytesIO | None:
    """Horizontal lollipop — stem from zero + dot at value. Better than bar for ranked lists."""
    x_col    = spec.get("x_column")
    y_cols   = spec.get("y_columns") or []
    y_labels = spec.get("y_labels") or y_cols
    if not x_col or not y_cols:
        return None
    y_col  = y_cols[0]
    y_label = y_labels[0]

    rows    = sorted(rows[:20], key=lambda r: float(r.get(y_col) or 0))
    cats    = [str(r.get(x_col, "")) for r in rows]
    vals    = [float(r.get(y_col) or 0) for r in rows]
    max_val = max(vals) if vals else 1

    fig, ax = plt.subplots(figsize=(12, max(5, len(cats) * 0.55)))
    ax.hlines(cats, 0, vals, color=_GRAY_L, linewidth=2.5, zorder=1)
    ax.scatter(vals, cats, color=_NAVY, s=120, zorder=3)

    for cat, val in zip(cats, vals):
        ax.text(val + max_val * 0.015, cat, f"{val:.1f}",
                va="center", ha="left", fontsize=10, fontweight="bold", color=_NAVY)

    ax.set_xlabel(y_label, fontsize=11, labelpad=8)
    ax.set_xlim(0, max_val * 1.25)
    ax.xaxis.grid(True, color="#F0F0F0", linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    ax.spines["bottom"].set_visible(True)
    ax.spines["bottom"].set_color("#DDDDDD")
    plt.yticks(fontsize=10)

    _title(ax, spec.get("title") or "")
    fig.tight_layout(pad=2.0, rect=[0, 0.06, 1, 1])
    return _save(fig)


def _pie(spec: dict, rows: list[dict], donut: bool = False) -> io.BytesIO | None:
    """Pie or donut chart for part-to-whole (≤8 segments)."""
    x_col  = spec.get("x_column")
    y_cols = spec.get("y_columns") or []
    if not x_col or not y_cols:
        return None
    y_col = y_cols[0]

    rows = [r for r in rows if float(r.get(y_col) or 0) > 0][:8]
    if not rows:
        return None

    labels = [str(r.get(x_col, "")) for r in rows]
    vals   = [float(r.get(y_col, 0)) for r in rows]
    colors = [_PALETTE[i % len(_PALETTE)] for i in range(len(vals))]

    fig, ax = plt.subplots(figsize=(10, 7))
    wedge_props = dict(edgecolor="white", linewidth=2.5)
    if donut:
        wedge_props["width"] = 0.5

    wedges, texts, autotexts = ax.pie(
        vals, labels=labels, autopct="%1.1f%%",
        colors=colors, startangle=90,
        pctdistance=0.78 if donut else 0.65,
        wedgeprops=wedge_props,
    )
    for t in texts:
        t.set_fontsize(10)
    for at in autotexts:
        at.set_fontsize(9)
        at.set_fontweight("bold")
        at.set_color("white")

    if donut:
        ax.text(0, 0, f"{sum(vals):,.0f}",
                ha="center", va="center",
                fontsize=20, fontweight="bold", color=_NAVY)

    _title(ax, spec.get("title") or "")
    fig.tight_layout(pad=2.0, rect=[0, 0.06, 1, 1])
    return _save(fig)


def _funnel(spec: dict, rows: list[dict]) -> io.BytesIO | None:
    """Funnel chart — ordered stages with retention % between steps."""
    x_col    = spec.get("x_column")
    y_cols   = spec.get("y_columns") or []
    y_labels = spec.get("y_labels") or y_cols
    if not x_col or not y_cols:
        return None
    y_col   = y_cols[0]
    y_label = y_labels[0]

    cats    = [str(r.get(x_col, "")) for r in rows]
    vals    = [float(r.get(y_col) or 0) for r in rows]
    max_val = max(vals) if vals else 1

    fig, ax = plt.subplots(figsize=(10, max(4, len(cats) * 0.8)))

    for i, (cat, val) in enumerate(zip(cats, vals)):
        color = _PALETTE[i % len(_PALETTE)]
        ax.barh(i, val, height=0.6, color=color, zorder=2)
        ax.text(-max_val * 0.02, i, cat,
                va="center", ha="right", fontsize=10, color="#333333")
        if val >= max_val * 0.15:
            ax.text(val / 2, i, f"{val:,.0f}",
                    va="center", ha="center", fontsize=10, fontweight="bold", color="white")
        else:
            ax.text(val + max_val * 0.015, i, f"{val:,.0f}",
                    va="center", ha="left", fontsize=10, fontweight="bold", color="#1A1A1A")
        if i > 0 and vals[i - 1] > 0:
            pct = (val / vals[i - 1]) * 100
            ax.text(max_val * 1.05, i, f"{pct:.1f}%",
                    va="center", ha="left", fontsize=9, color="#888888")

    if len(cats) > 1:
        ax.text(max_val * 1.05, len(cats) - 0.55, "Retention",
                va="bottom", ha="left", fontsize=9, color="#888888", style="italic")

    ax.set_xlim(-max_val * 0.3, max_val * 1.25)
    ax.set_ylim(-0.7, len(cats) - 0.3)
    ax.set_yticks([])
    ax.set_xlabel(y_label, fontsize=11, labelpad=8)
    ax.xaxis.grid(True, color="#F0F0F0", linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    ax.spines["bottom"].set_visible(True)
    ax.spines["bottom"].set_color("#DDDDDD")

    _title(ax, spec.get("title") or "")
    fig.tight_layout(pad=2.0, rect=[0, 0.06, 1, 1])
    return _save(fig)


def _stacked_bar(spec: dict, rows: list[dict], horizontal: bool = False) -> io.BytesIO | None:
    """Stacked bar/column chart for composition."""
    x_col    = spec.get("x_column")
    y_cols   = spec.get("y_columns") or []
    y_labels = spec.get("y_labels") or y_cols
    if not x_col or not y_cols:
        return None

    rows     = rows[:15]
    cats     = [str(r.get(x_col, "")) for r in rows]
    all_vals = [[float(r.get(col) or 0) for r in rows] for col in y_cols]
    totals   = [sum(all_vals[j][i] for j in range(len(y_cols))) for i in range(len(rows))]
    max_tot  = max(totals) if totals else 1

    if horizontal:
        fig, ax = plt.subplots(figsize=(12, max(5, len(cats) * 0.6)))
        lefts = [0.0] * len(cats)
        for i, (label, vals) in enumerate(zip(y_labels, all_vals)):
            color = _PALETTE[i % len(_PALETTE)]
            bars = ax.barh(cats, vals, left=lefts, color=color,
                           height=0.6, label=label, zorder=2)
            for bar, val in zip(bars, vals):
                if val >= max_tot * 0.08:
                    cx = bar.get_x() + bar.get_width() / 2
                    cy = bar.get_y() + bar.get_height() / 2
                    ax.text(cx, cy, f"{val:.0f}",
                            va="center", ha="center", fontsize=9, fontweight="bold", color="white")
            lefts = [l + v for l, v in zip(lefts, vals)]
        ax.set_xlabel(spec.get("y_label") or "", fontsize=11, labelpad=8)
        ax.set_xlim(0, max_tot * 1.1)
        ax.xaxis.grid(True, color="#F0F0F0", linewidth=0.8, zorder=0)
        ax.spines["bottom"].set_visible(True)
        ax.spines["bottom"].set_color("#DDDDDD")
        plt.yticks(fontsize=10)
    else:
        x = np.arange(len(cats))
        fig, ax = plt.subplots(figsize=(max(10, len(cats) * 1.1), 6.5))
        bottoms = [0.0] * len(cats)
        for i, (label, vals) in enumerate(zip(y_labels, all_vals)):
            color = _PALETTE[i % len(_PALETTE)]
            bars = ax.bar(x, vals, bottom=bottoms, color=color,
                          width=0.6, label=label, zorder=2)
            for bar, val in zip(bars, vals):
                if val >= max_tot * 0.08:
                    cx = bar.get_x() + bar.get_width() / 2
                    cy = bar.get_y() + bar.get_height() / 2
                    ax.text(cx, cy, f"{val:.0f}",
                            va="center", ha="center", fontsize=9, fontweight="bold", color="white")
            bottoms = [b + v for b, v in zip(bottoms, vals)]
        ax.set_xticks(x)
        ax.set_xticklabels(cats, rotation=40, ha="right", fontsize=10)
        ax.set_ylabel(spec.get("y_label") or "", fontsize=11, labelpad=8)
        ax.set_ylim(0, max_tot * 1.1)
        ax.yaxis.grid(True, color="#F0F0F0", linewidth=0.8, zorder=0)
        ax.spines["bottom"].set_visible(True)
        ax.spines["bottom"].set_color("#DDDDDD")
        plt.yticks(fontsize=10)

    ax.set_axisbelow(True)
    ax.legend(loc="upper right", fontsize=10, ncol=min(len(y_cols), 4))

    _title(ax, spec.get("title") or "")
    fig.tight_layout(pad=2.0, rect=[0, 0.06, 1, 1])
    return _save(fig)


def _area(spec: dict, rows: list[dict], stacked: bool = False) -> io.BytesIO | None:
    """Area or stacked area chart — wide-format or long-format via series_column."""
    x_col      = spec.get("x_column")
    y_cols     = spec.get("y_columns") or []
    y_labels   = spec.get("y_labels") or y_cols
    series_col = spec.get("series_column")
    if not x_col or not y_cols:
        return None

    if series_col:
        y_col        = y_cols[0]
        series_order = list(dict.fromkeys(str(r.get(series_col, "")) for r in rows))
        x_sorted     = sorted(set(str(r.get(x_col, ""))[:7] for r in rows))
        n            = len(x_sorted)
        fig, ax = plt.subplots(figsize=(max(11, n * 0.9), 6.5))

        if stacked:
            ys = []
            for sv in series_order:
                lookup = {str(r.get(x_col, ""))[:7]: float(r.get(y_col) or 0)
                          for r in rows if str(r.get(series_col, "")) == sv}
                ys.append([lookup.get(x, 0) for x in x_sorted])
            colors = [_PALETTE[i % len(_PALETTE)] for i in range(len(series_order))]
            ax.stackplot(x_sorted, ys, labels=series_order, colors=colors, alpha=0.93)
        else:
            for i, sv in enumerate(series_order):
                s_rows = sorted([r for r in rows if str(r.get(series_col, "")) == sv],
                                key=lambda r: str(r.get(x_col, "")))
                xv = [str(r.get(x_col, ""))[:7] for r in s_rows]
                yv = [float(r.get(y_col) or 0) for r in s_rows]
                color = _PALETTE[i % len(_PALETTE)]
                ax.fill_between(xv, yv, alpha=0.80, color=color)
                ax.plot(xv, yv, color=color, linewidth=2.5, label=sv,
                        marker="o", markersize=5, markerfacecolor="white",
                        markeredgewidth=2, markeredgecolor=color)
    else:
        rows   = sorted(rows, key=lambda r: str(r.get(x_col, "")))
        x_vals = [str(r.get(x_col, ""))[:7] for r in rows]
        n      = len(x_vals)
        fig, ax = plt.subplots(figsize=(max(11, n * 0.9), 6.5))

        if stacked:
            ys = [[float(r.get(col) or 0) for r in rows] for col in y_cols]
            colors = [_PALETTE[i % len(_PALETTE)] for i in range(len(y_cols))]
            ax.stackplot(x_vals, ys, labels=y_labels, colors=colors, alpha=0.93)
        else:
            for i, (col, label) in enumerate(zip(y_cols, y_labels)):
                y_vals = [float(r.get(col) or 0) for r in rows]
                color  = _PALETTE[i % len(_PALETTE)]
                ax.fill_between(x_vals, y_vals, alpha=0.20, color=color)
                ax.plot(x_vals, y_vals, color=color, linewidth=2.5, label=label,
                        marker="o", markersize=5, markerfacecolor="white",
                        markeredgewidth=2, markeredgecolor=color)

    ax.yaxis.grid(True, color="#F0F0F0", linewidth=0.9, zorder=0)
    ax.set_axisbelow(True)
    ax.set_xlabel(spec.get("x_label") or "", fontsize=11, labelpad=8)
    ax.set_ylabel(spec.get("y_label") or "", fontsize=11, labelpad=8)
    if len(y_cols) > 1 or series_col:
        ax.legend(loc="best", fontsize=10, ncol=min(len(y_cols), 4))
    plt.xticks(rotation=40, ha="right", fontsize=10)
    plt.yticks(fontsize=10)
    ax.spines["bottom"].set_visible(True)
    ax.spines["bottom"].set_color("#DDDDDD")

    _title(ax, spec.get("title") or "")
    fig.tight_layout(pad=2.0, rect=[0, 0.06, 1, 1])
    return _save(fig)


def _heatmap(spec: dict, rows: list[dict]) -> io.BytesIO | None:
    """Matrix heatmap — x_column = column dim, series_column = row dim, y_columns[0] = value."""
    x_col   = spec.get("x_column")
    row_col = spec.get("series_column")
    y_cols  = spec.get("y_columns") or []
    if not x_col or not row_col or not y_cols:
        return None
    val_col = y_cols[0]

    col_labels = list(dict.fromkeys(str(r.get(x_col, "")) for r in rows))
    row_labels = list(dict.fromkeys(str(r.get(row_col, "")) for r in rows))

    matrix = np.full((len(row_labels), len(col_labels)), np.nan)
    for r in rows:
        ri = row_labels.index(str(r.get(row_col, "")))
        ci = col_labels.index(str(r.get(x_col, "")))
        v  = r.get(val_col)
        if v is not None:
            matrix[ri][ci] = float(v)

    fig, ax = plt.subplots(
        figsize=(max(10, len(col_labels) * 1.4), max(5, len(row_labels) * 0.65))
    )
    masked = np.ma.masked_invalid(matrix)
    im = ax.imshow(masked, cmap="Blues", aspect="auto", vmin=0)

    ax.set_xticks(range(len(col_labels)))
    ax.set_xticklabels(col_labels, rotation=40, ha="right", fontsize=10)
    ax.set_yticks(range(len(row_labels)))
    ax.set_yticklabels(row_labels, fontsize=10)

    max_v = np.nanmax(matrix) if not np.all(np.isnan(matrix)) else 1
    for i in range(len(row_labels)):
        for j in range(len(col_labels)):
            v = matrix[i][j]
            if not np.isnan(v):
                color = "white" if v > max_v * 0.55 else "#1A1A1A"
                ax.text(j, i, f"{v:.1f}", ha="center", va="center",
                        fontsize=9, color=color, fontweight="bold")

    plt.colorbar(im, ax=ax, fraction=0.025, pad=0.04)
    _title(ax, spec.get("title") or "")
    fig.tight_layout(pad=2.0, rect=[0, 0.06, 1, 1])
    return _save(fig)


def _dumbbell(spec: dict, rows: list[dict]) -> io.BytesIO | None:
    """Dumbbell chart — two values per entity connected by a line (requires exactly 2 y_columns)."""
    x_col    = spec.get("x_column")
    y_cols   = spec.get("y_columns") or []
    y_labels = spec.get("y_labels") or y_cols
    if not x_col or len(y_cols) < 2:
        return None
    col_a, col_b   = y_cols[0], y_cols[1]
    label_a = y_labels[0]
    label_b = y_labels[1] if len(y_labels) > 1 else col_b

    rows    = sorted(rows[:20], key=lambda r: float(r.get(col_b) or 0))
    cats    = [str(r.get(x_col, "")) for r in rows]
    vals_a  = [float(r.get(col_a) or 0) for r in rows]
    vals_b  = [float(r.get(col_b) or 0) for r in rows]
    all_v   = vals_a + vals_b
    max_val = max(all_v) if all_v else 1

    fig, ax = plt.subplots(figsize=(12, max(5, len(cats) * 0.55)))
    for cat, va, vb in zip(cats, vals_a, vals_b):
        ax.plot([va, vb], [cat, cat], color=_GRAY_L, linewidth=2.5, zorder=1)
    ax.scatter(vals_a, cats, color=_NAVY,  s=110, zorder=3, label=label_a)
    ax.scatter(vals_b, cats, color=_STEEL, s=110, zorder=3, label=label_b, marker="D")

    for cat, va, vb in zip(cats, vals_a, vals_b):
        ax.text(va - max_val * 0.015, cat, f"{va:.1f}",
                va="center", ha="right", fontsize=9, color=_NAVY, fontweight="bold")
        ax.text(vb + max_val * 0.015, cat, f"{vb:.1f}",
                va="center", ha="left", fontsize=9, color=_STEEL, fontweight="bold")

    ax.set_xlim(min(all_v) * 0.85, max_val * 1.2)
    ax.xaxis.grid(True, color="#F0F0F0", linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    ax.legend(loc="lower right", fontsize=10)
    ax.spines["bottom"].set_visible(True)
    ax.spines["bottom"].set_color("#DDDDDD")
    plt.yticks(fontsize=10)

    _title(ax, spec.get("title") or "")
    fig.tight_layout(pad=2.0, rect=[0, 0.06, 1, 1])
    return _save(fig)


def _slope(spec: dict, rows: list[dict]) -> io.BytesIO | None:
    """Slopegraph — one entity per row, exactly 2 y_columns as the two time points."""
    x_col    = spec.get("x_column")
    y_cols   = spec.get("y_columns") or []
    y_labels = spec.get("y_labels") or y_cols
    if not x_col or len(y_cols) < 2:
        return None
    col_a, col_b   = y_cols[0], y_cols[1]
    label_a = y_labels[0]
    label_b = y_labels[1] if len(y_labels) > 1 else col_b

    rows    = rows[:15]
    cats    = [str(r.get(x_col, "")) for r in rows]
    vals_a  = [float(r.get(col_a) or 0) for r in rows]
    vals_b  = [float(r.get(col_b) or 0) for r in rows]
    all_v   = vals_a + vals_b

    fig, ax = plt.subplots(figsize=(9, max(5, len(cats) * 0.45)))
    for cat, va, vb in zip(cats, vals_a, vals_b):
        color = _TEAL if vb >= va else _NAVY
        ax.plot([0, 1], [va, vb], color=color, linewidth=1.8, alpha=0.8, zorder=2)
        ax.scatter([0, 1], [va, vb], color=color, s=60, zorder=3)
        ax.text(-0.06, va, f"{cat}  {va:.1f}", va="center", ha="right",
                fontsize=9, color=color)
        ax.text(1.06, vb, f"{vb:.1f}  {cat}", va="center", ha="left",
                fontsize=9, color=color)

    ax.set_xticks([0, 1])
    ax.set_xticklabels([label_a, label_b], fontsize=12, fontweight="bold")
    ax.set_xlim(-0.65, 1.65)
    margin = (max(all_v) - min(all_v)) * 0.12 if all_v else 1
    ax.set_ylim(min(all_v) - margin, max(all_v) + margin)
    ax.yaxis.grid(True, color="#F0F0F0", linewidth=0.9, zorder=0)
    ax.set_axisbelow(True)
    ax.spines["bottom"].set_visible(True)
    ax.spines["bottom"].set_color("#DDDDDD")

    _title(ax, spec.get("title") or "")
    fig.tight_layout(pad=2.0, rect=[0, 0.06, 1, 1])
    return _save(fig)


def _scatter(spec: dict, rows: list[dict],
             bubble: bool = False, connected: bool = False) -> io.BytesIO | None:
    """Scatter, bubble, or connected scatter plot."""
    x_col      = spec.get("x_column")
    y_cols     = spec.get("y_columns") or []
    y_labels   = spec.get("y_labels") or y_cols
    size_col   = spec.get("size_column")
    label_col  = spec.get("label_column") or spec.get("series_column")
    if not x_col or not y_cols:
        return None
    y_col = y_cols[0]

    x_vals = [float(r.get(x_col) or 0) for r in rows]
    y_vals = [float(r.get(y_col) or 0) for r in rows]
    sizes  = None
    if bubble and size_col:
        raw    = [float(r.get(size_col) or 0) for r in rows]
        max_s  = max(raw) if any(v > 0 for v in raw) else 1
        sizes  = [max(30, (v / max_s) * 900) for v in raw]

    fig, ax = plt.subplots(figsize=(10, 7))
    scatter_kw = dict(color=_NAVY, alpha=0.75, edgecolors="white",
                      linewidth=0.8, zorder=3)
    ax.scatter(x_vals, y_vals, s=sizes if sizes else 80, **scatter_kw)

    if connected:
        ax.plot(x_vals, y_vals, color=_NAVY, linewidth=1.2, alpha=0.4, zorder=2)

    if label_col:
        for xv, yv, r in zip(x_vals, y_vals, rows):
            lbl = str(r.get(label_col, ""))
            ax.annotate(lbl, (xv, yv), xytext=(5, 5),
                        textcoords="offset points", fontsize=8, color="#555555")

    ax.set_xlabel(x_col.replace("_", " ").title(), fontsize=11, labelpad=8)
    ax.set_ylabel(y_labels[0], fontsize=11, labelpad=8)
    ax.xaxis.grid(True, color="#F0F0F0", linewidth=0.8, zorder=0)
    ax.yaxis.grid(True, color="#F0F0F0", linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    ax.spines["bottom"].set_visible(True)
    ax.spines["bottom"].set_color("#DDDDDD")

    _title(ax, spec.get("title") or "")
    fig.tight_layout(pad=2.0, rect=[0, 0.06, 1, 1])
    return _save(fig)


def _waterfall(spec: dict, rows: list[dict]) -> io.BytesIO | None:
    """Waterfall chart — sequential positive/negative contributions to a running total."""
    x_col    = spec.get("x_column")
    y_cols   = spec.get("y_columns") or []
    y_labels = spec.get("y_labels") or y_cols
    if not x_col or not y_cols:
        return None
    y_col   = y_cols[0]
    y_label = y_labels[0]

    cats = [str(r.get(x_col, "")) for r in rows]
    vals = [float(r.get(y_col) or 0) for r in rows]

    running = 0.0
    bottoms, heights, colors_list = [], [], []
    for val in vals:
        if val >= 0:
            bottoms.append(running)
            heights.append(val)
            colors_list.append(_TEAL)
        else:
            bottoms.append(running + val)
            heights.append(abs(val))
            colors_list.append("#C0392B")
        running += val

    max_top = max(b + h for b, h in zip(bottoms, heights))
    min_bot = min(bottoms)

    fig, ax = plt.subplots(figsize=(max(10, len(cats) * 1.2), 6.5))
    ax.bar(cats, heights, bottom=bottoms, color=colors_list, alpha=0.9, width=0.6, zorder=2)

    for cat, val, bot, h in zip(cats, vals, bottoms, heights):
        ax.text(cat, bot + h / 2, f"{val:+.1f}",
                ha="center", va="center", fontsize=10, fontweight="bold", color="white")

    ax.axhline(0, color="#AAAAAA", linewidth=0.8, zorder=1)
    ax.set_ylabel(y_label, fontsize=11, labelpad=8)
    margin = (max_top - min_bot) * 0.1 if max_top != min_bot else 1
    ax.set_ylim(min_bot - margin, max_top + margin)
    plt.xticks(rotation=40, ha="right", fontsize=10)
    ax.yaxis.grid(True, color="#F0F0F0", linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    ax.spines["bottom"].set_visible(True)
    ax.spines["bottom"].set_color("#DDDDDD")

    _title(ax, spec.get("title") or "")
    fig.tight_layout(pad=2.0, rect=[0, 0.06, 1, 1])
    return _save(fig)


def _treemap(spec: dict, rows: list[dict]) -> io.BytesIO | None:
    """Treemap — area-proportional rectangles. Falls back to horizontal_bar if squarify missing."""
    try:
        import squarify
    except ImportError:
        logger.warning("squarify not installed — falling back to horizontal_bar for treemap")
        return _bar(spec, rows, horizontal=True)

    x_col  = spec.get("x_column")
    y_cols = spec.get("y_columns") or []
    if not x_col or not y_cols:
        return None
    y_col = y_cols[0]

    pairs = sorted(
        [(str(r.get(x_col, "")), float(r.get(y_col) or 0)) for r in rows
         if float(r.get(y_col) or 0) > 0],
        key=lambda t: t[1], reverse=True,
    )[:20]
    if not pairs:
        return None

    labels_list, sizes = zip(*pairs)
    colors = [_PALETTE[i % len(_PALETTE)] for i in range(len(sizes))]

    fig, ax = plt.subplots(figsize=(14, 8))
    squarify.plot(
        sizes=sizes,
        label=[f"{l}\n{v:,.1f}" for l, v in zip(labels_list, sizes)],
        color=colors, alpha=0.9, ax=ax,
        text_kwargs={"fontsize": 10, "color": "white", "fontweight": "bold"},
    )
    ax.axis("off")
    _title(ax, spec.get("title") or "")
    fig.tight_layout(pad=2.0)
    return _save(fig)


def _radar(spec: dict, rows: list[dict]) -> io.BytesIO | None:
    """Radar / spider chart — multi-indicator profile. Each row is one series (up to 4)."""
    x_col    = spec.get("x_column")
    y_cols   = spec.get("y_columns") or []
    y_labels = spec.get("y_labels") or y_cols
    if len(y_cols) < 3:
        return None

    if x_col:
        series_data = [(str(r.get(x_col, "")),
                        [float(r.get(c) or 0) for c in y_cols])
                       for r in rows[:4]]
    else:
        series_data = [("", [float(rows[0].get(c) or 0) for c in y_cols])]

    N      = len(y_cols)
    angles = np.linspace(0, 2 * np.pi, N, endpoint=False).tolist()
    angles += angles[:1]
    spokes = [l.replace("_", " ")[:18] for l in y_labels]

    fig, ax = plt.subplots(figsize=(9, 9), subplot_kw=dict(polar=True))
    for i, (name, vals) in enumerate(series_data):
        closed = vals + vals[:1]
        color  = _PALETTE[i % len(_PALETTE)]
        ax.plot(angles, closed, color=color, linewidth=2.5, label=name, zorder=3)
        ax.fill(angles, closed, color=color, alpha=0.15)

    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(spokes, fontsize=10)
    ax.set_yticklabels([])
    ax.spines["polar"].set_color("#DDDDDD")
    ax.grid(color="#F0F0F0", linewidth=0.9)
    if len(series_data) > 1:
        ax.legend(loc="upper right", bbox_to_anchor=(1.35, 1.15), fontsize=10)

    _title(ax, spec.get("title") or "")
    fig.tight_layout(pad=2.0)
    return _save(fig)


def _box_plot(spec: dict, rows: list[dict]) -> io.BytesIO | None:
    """Box-and-whisker distribution chart — one box per group (x_column) or per y_column."""
    x_col    = spec.get("x_column")
    y_cols   = spec.get("y_columns") or []
    y_labels = spec.get("y_labels") or y_cols
    if not y_cols:
        return None
    y_col = y_cols[0]

    if x_col:
        groups      = list(dict.fromkeys(str(r.get(x_col, "")) for r in rows))
        group_data  = {g: [] for g in groups}
        for r in rows:
            v = r.get(y_col)
            if v is not None:
                group_data[str(r.get(x_col, ""))].append(float(v))
        group_labels = groups
        group_vals   = [group_data[g] for g in groups]
    else:
        group_labels = [y_labels[0]]
        group_vals   = [[float(r.get(y_col) or 0) for r in rows]]

    fig, ax = plt.subplots(figsize=(max(8, len(group_labels) * 1.2), 6.5))
    ax.boxplot(
        group_vals, labels=group_labels, patch_artist=True,
        medianprops=dict(color="white", linewidth=2.5),
        boxprops=dict(facecolor=_NAVY, alpha=0.75),
        whiskerprops=dict(color=_NAVY),
        capprops=dict(color=_NAVY),
        flierprops=dict(marker="o", color=_STEEL, markersize=5, alpha=0.6),
    )
    ax.set_ylabel(y_labels[0], fontsize=11, labelpad=8)
    plt.xticks(rotation=40 if len(group_labels) > 4 else 0, ha="right", fontsize=10)
    ax.yaxis.grid(True, color="#F0F0F0", linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    ax.spines["bottom"].set_visible(True)
    ax.spines["bottom"].set_color("#DDDDDD")

    _title(ax, spec.get("title") or "")
    fig.tight_layout(pad=2.0, rect=[0, 0.06, 1, 1])
    return _save(fig)


def _dot_plot(spec: dict, rows: list[dict]) -> io.BytesIO | None:
    """Dot plot — proportional dots for multiple metrics across entities."""
    x_col    = spec.get("x_column")
    y_cols   = spec.get("y_columns") or []
    y_labels = spec.get("y_labels") or y_cols
    if not x_col or len(y_cols) < 2:
        return None

    rows  = rows[:15]
    cats  = [str(r.get(x_col, "")) for r in rows]
    max_v = max(
        (float(r.get(c) or 0) for r in rows for c in y_cols), default=1
    )

    fig, ax = plt.subplots(
        figsize=(max(10, len(y_cols) * 1.8), max(5, len(cats) * 0.55))
    )
    for j, (col, label) in enumerate(zip(y_cols, y_labels)):
        vals = [float(r.get(col) or 0) for r in rows]
        sizes = [max(40, (v / max_v) * 900) for v in vals]
        ax.scatter([j] * len(cats), cats, s=sizes,
                   color=_PALETTE[j % len(_PALETTE)], alpha=0.80, zorder=3)
        for cat, val in zip(cats, vals):
            ax.text(j, cat, f"{val:.1f}", ha="center", va="center",
                    fontsize=8, color="white", fontweight="bold")

    ax.set_xticks(range(len(y_cols)))
    ax.set_xticklabels([l.replace("_", " ")[:15] for l in y_labels],
                       rotation=30, ha="right", fontsize=10)
    ax.xaxis.grid(True, color="#F0F0F0", linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    ax.spines["bottom"].set_visible(True)
    ax.spines["bottom"].set_color("#DDDDDD")
    plt.yticks(fontsize=10)

    _title(ax, spec.get("title") or "")
    fig.tight_layout(pad=2.0, rect=[0, 0.06, 1, 1])
    return _save(fig)


def _grouped_bar(spec: dict, rows: list[dict]) -> io.BytesIO | None:
    """Multiple metrics side-by-side across categories."""
    x_col    = spec.get("x_column")
    y_cols   = spec.get("y_columns") or []
    y_labels = spec.get("y_labels") or y_cols
    if not x_col or not y_cols:
        return None

    rows     = rows[:15]
    cats     = [str(r.get(x_col, "")) for r in rows]
    n_groups = len(cats)
    n_bars   = len(y_cols)
    x        = np.arange(n_groups)
    width    = min(0.75 / n_bars, 0.22)

    all_vals = [float(r.get(c) or 0) for r in rows for c in y_cols]
    max_val  = max(all_vals) if all_vals else 1

    fig, ax = plt.subplots(figsize=(max(11, n_groups * 1.4), 6.5))

    for i, (col, label) in enumerate(zip(y_cols, y_labels)):
        y_vals = [float(r.get(col) or 0) for r in rows]
        offset = (i - n_bars / 2 + 0.5) * width
        bars = ax.bar(x + offset, y_vals, width,
                      label=label, color=_PALETTE[i % len(_PALETTE)],
                      zorder=2)
        for bar, val in zip(bars, y_vals):
            _bar_label(ax, bar, val, max_val, horizontal=False)

    ax.set_xticks(x)
    ax.set_xticklabels(cats, rotation=40, ha="right", fontsize=10)
    ax.set_ylabel(spec.get("y_label") or "", fontsize=11, labelpad=8)
    ax.set_ylim(0, max_val * 1.22)
    ax.legend(loc="upper right", fontsize=10, ncol=min(n_bars, 4))
    plt.yticks(fontsize=10)
    ax.yaxis.grid(True, color="#F0F0F0", linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    ax.spines["bottom"].set_visible(True)
    ax.spines["bottom"].set_color("#DDDDDD")

    _title(ax, spec.get("title") or "")
    fig.tight_layout(pad=2.0, rect=[0, 0.06, 1, 1])
    return _save(fig)
