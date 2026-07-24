#!/usr/bin/env python3
"""
MANUSCRIPT FIGURES - Dallas County naloxone-access / placement pipeline (rewrite 2026-07).

Produces publication-grade (300 dpi PNG + vector PDF + APA caption .txt) cartography:

  Figure 1  "Measured naloxone access desert"       - tract choropleth of % population
            beyond 1 mile of any naloxone access point (FIXED classes), 38 supply points
            by category, 1-mile catchments, headline stat box.
  Figure 2  "Composite vulnerability index" (HERO)  - composite_score choropleth (viridis),
            Tier-1 heavy outline, suppression hatch, Jenks cutoffs on colour bar.
  Figure 3  "Ranked placement strategy"             - muted tier shading, top-50 ranked
            candidates sized by reach, top-5 labelled, greedy coverage table.
  Figure 4  "Overdose mortality at CDC native reporting units" - 150-unit Jenks choropleth
            of model-based OD death rate, suppression marker.

HARD PRIVACY RULE honoured: every layer is AGGREGATED (census-tract / CDC-unit choropleths,
population counts) or FACILITY / STOREFRONT POINT locations. No individual death records
exist in the repo and none are synthesised. Preprint-server bound.

All geometry work is performed in EPSG:32138 (NAD83 / Texas North Central, metres).
Every input schema was inspected before use.
"""

import json
from pathlib import Path

import geopandas as gpd
import jenkspy
import matplotlib as mpl
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import BoundaryNorm, ListedColormap, Normalize
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle
from shapely.ops import unary_union

# --------------------------------------------------------------------------------------
# Paths & constants
# --------------------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
CLEAN = ROOT / "data" / "clean"
TIGER = ROOT / "data" / "raw" / "tiger"
FIGDIR = ROOT / "figures"
FIGDIR.mkdir(exist_ok=True)

CRS_M = 32138           # NAD83 / Texas North Central (metres)
MI = 1609.344           # metres per statute mile
TODAY = "2026-07-24"

# Per-figure source strings (vintages). Each caption's Note lists exactly the sources used.
SRC = {
    "blocks": "Census 2020 PL 94-171 block populations (P1_001N) + TIGER 2024 tabblocks",
    "nalox": "TX DSHS NarcanSites (Apr 2026), SAMHSA findtreatment.gov & ArcGIS naloxone "
             "inventory (verified 2024-2026)",
    "mort": "CDC NCHS drug-overdose death rates, model-based (4day-mt2f), 12-month period "
            "2025-01/2025-12, data as of 2026-07-22",
    "acs": "ACS 2020-2024 5-year (S1701 poverty; S2701 uninsured), released Jan 2026",
    "svi": "CDC/ATSDR SVI 2022",
    "dart": "DART GTFS (via context module, 2026-07-23)",
    "cand": "Dallas County business licences + TABC off-premise permits + OpenStreetMap POIs "
            "(2026-07)",
    "tiger": "County & tract boundaries: US Census TIGER/Line 2024",
    "proj": "Projection: NAD83 / Texas North Central (EPSG:32138)",
}
SUPPRESS_TRACT = ("CDC suppresses tract-level death counts of 1-9; 592 of 645 tracts (91.8%) "
                  "carry suppressed counts, so model-based rates are smoothed estimates, not "
                  "exact counts.")
SUPPRESS_UNIT = ("Texas mortality suppression forces the CDC tract product to publish "
                 "model-based rates aggregated to native reporting units rather than raw "
                 "tract counts; 138 of 150 units (92%) have suppressed underlying counts. "
                 "A 95% confidence interval is published for every unit.")

# --------------------------------------------------------------------------------------
# Publication style
# --------------------------------------------------------------------------------------
mpl.rcParams.update({
    "figure.dpi": 150,
    "savefig.dpi": 300,
    "font.family": "DejaVu Sans",
    "font.size": 9,
    "axes.titlesize": 15,
    "axes.titleweight": "bold",
    "pdf.fonttype": 42,       # embed TrueType (editable vector text)
    "ps.fonttype": 42,
    "svg.fonttype": "none",
    "hatch.linewidth": 0.4,
})

LAND = "#f4f2ee"
COUNTY_EDGE = "#2b2b2b"
TRACT_EDGE = "#b8b3ab"
WHITE = "#ffffff"

# Okabe-Ito colourblind-safe supply symbology (naloxone_locations.category)
SUPPLY_STYLE = {
    "otp_methadone":        dict(marker="^", color="#D55E00", label="OTP / methadone clinic", size=80),
    "storefront_dispenser": dict(marker="s", color="#5b3a86", label="Storefront dispenser",   size=62),
    "recovery_services":    dict(marker="D", color="#009E73", label="Recovery services",       size=62),
    "harm_reduction":       dict(marker="p", color="#CC79A7", label="Harm-reduction access",   size=78),
    "health_clinic":        dict(marker="P", color="#0072B2", label="Health / county clinic",  size=100),
    "nightlife_venue":      dict(marker="X", color="#111111", label="Nightlife venue",         size=64),
}
SUPPLY_ORDER = ["otp_methadone", "storefront_dispenser", "recovery_services",
                "harm_reduction", "health_clinic", "nightlife_venue"]


# --------------------------------------------------------------------------------------
# Cartographic furniture
# --------------------------------------------------------------------------------------
def add_scale_bar(ax, length_km=10, loc=(0.055, 0.05)):
    """Two-tone scale bar labelled in km with a mile reference below."""
    length_m = length_km * 1000
    xlim, ylim = ax.get_xlim(), ax.get_ylim()
    sx, sy = xlim[1] - xlim[0], ylim[1] - ylim[0]
    x0 = xlim[0] + loc[0] * sx
    y0 = ylim[0] + loc[1] * sy
    h = 0.009 * sy
    seg = length_m / 2.0
    ax.add_patch(Rectangle((x0, y0), seg, h, fc="#222", ec="#222", zorder=20))
    ax.add_patch(Rectangle((x0 + seg, y0), seg, h, fc="white", ec="#222", zorder=20))
    tt = dict(ha="center", va="bottom", fontsize=7.5, zorder=21)
    ax.text(x0, y0 + h * 1.3, "0", **tt)
    ax.text(x0 + seg, y0 + h * 1.3, f"{length_km/2:g}", **tt)
    ax.text(x0 + length_m, y0 + h * 1.3, f"{length_km:g} km", **tt)
    ax.text(x0 + length_m / 2, y0 - h * 1.5, f"({length_m/MI:.1f} mi)", ha="center", va="top",
            fontsize=6.8, color="#555", zorder=21)


def add_north_arrow(ax, loc=(0.955, 0.10)):
    xlim, ylim = ax.get_xlim(), ax.get_ylim()
    x = xlim[0] + loc[0] * (xlim[1] - xlim[0])
    y = ylim[0] + loc[1] * (ylim[1] - ylim[0])
    dy = 0.055 * (ylim[1] - ylim[0])
    ax.annotate("", xy=(x, y + dy), xytext=(x, y),
                arrowprops=dict(arrowstyle="-|>", color="#222", lw=1.8), zorder=21)
    ax.text(x, y + dy * 1.14, "N", ha="center", va="bottom", fontsize=10,
            fontweight="bold", zorder=21)


def add_source_strip(fig, sources, extra=""):
    txt = "Sources: " + "  |  ".join(sources) + "."
    if extra:
        txt += "  " + extra
    fig.text(0.012, 0.011, txt, ha="left", va="bottom", fontsize=6.1, color="#555",
             wrap=True, linespacing=1.35)


def style_axes(ax):
    ax.set_aspect("equal")
    ax.set_axis_off()


def set_extent(ax, county):
    minx, miny, maxx, maxy = county.total_bounds
    px, py = (maxx - minx) * 0.03, (maxy - miny) * 0.03
    ax.set_xlim(minx - px, maxx + px)
    ax.set_ylim(miny - py, maxy + py)


def halo(lw=2.2, fg="white"):
    return [pe.withStroke(linewidth=lw, foreground=fg)]


# --------------------------------------------------------------------------------------
# Load data (project everything to metres)
# --------------------------------------------------------------------------------------
print("Loading inputs ...")
ba = gpd.read_file(CLEAN / "block_access_tracts.geojson").to_crs(CRS_M)
comp = gpd.read_file(CLEAN / "composite_index.geojson").to_crs(CRS_M)
mort = gpd.read_file(CLEAN / "mortality_units.geojson").to_crs(CRS_M)
nal = gpd.read_file(CLEAN / "naloxone_locations.geojson")
nal = nal[~nal.geometry.isna()].to_crs(CRS_M)          # drop 1 null-geom mobile unit
pr = gpd.read_file(CLEAN / "placement_ranked.geojson").to_crs(CRS_M)

ba_sum = json.loads((CLEAN / "block_access_summary.json").read_text())
pr_meta = json.loads((CLEAN / "placement_ranked_meta.json").read_text())
comp_meta = json.loads((CLEAN / "composite_methods.json").read_text())

# County boundary from TIGER county file (Dallas = GEOID 48113)
county_raw = gpd.read_file(TIGER / "tl_2024_us_county.zip")
county = county_raw[county_raw["GEOID"] == "48113"].to_crs(CRS_M)[["geometry"]].reset_index(drop=True)
county_geom = county.geometry.iloc[0]
county_line = county.boundary

# Composite Jenks cutoffs (primary equal-weight index)
CUT = comp_meta["tiers"]["cutoffs"]["primary_equal"]
T3_UP = CUT["tier3_upper_bound"]      # 0.338
T1_LO = CUT["tier1_lower_bound"]      # 0.434
SENS = comp_meta["sensitivity"]["agreement"]


def _save(fig, stem):
    fig.savefig(FIGDIR / f"{stem}.png", dpi=300, bbox_inches="tight", facecolor="white")
    fig.savefig(FIGDIR / f"{stem}.pdf", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  wrote {stem}.png + {stem}.pdf")


def _caption(stem, title, body, sources, suppress):
    note = "Note. Sources for this figure: " + "  |  ".join(sources) + ". " + suppress
    (FIGDIR / f"{stem}_caption.txt").write_text(f"{title}\n\n{body}\n\n{note}\n")
    print(f"  wrote {stem}_caption.txt")


# ======================================================================================
# FIGURE 1 - Measured naloxone access desert (LEAD)
# ======================================================================================
def figure1():
    print("Building Figure 1 (lead) ...")
    fig, ax = plt.subplots(figsize=(11, 12))
    style_axes(ax)
    county.plot(ax=ax, facecolor=LAND, edgecolor="none", zorder=0)

    # FIXED classes for pct_pop_beyond_1mi: <5, 5-50, 50-95, 95-<100, 100 (cool -> hot)
    edges = [0, 5, 50, 95, 100, 100.0001]
    labels = ["< 5% (near-full access)", "5-50%", "50-95%", "95-< 100%",
              "100% (total desert)"]
    colors = ["#2c7bb6", "#abd9e9", "#ffffbf", "#fdae61", "#d7191c"]  # RdYlBu (CB-safe)
    cmap = ListedColormap(colors)
    norm = BoundaryNorm(edges, cmap.N)
    ba.plot(ax=ax, column="pct_pop_beyond_1mi", cmap=cmap, norm=norm,
            edgecolor=TRACT_EDGE, linewidth=0.15, zorder=1)

    # 1-mile catchments around the 38 fixed supply points (union outline + light fill)
    served = unary_union(nal.geometry.buffer(1 * MI).values).intersection(county_geom)
    gpd.GeoSeries([served], crs=CRS_M).plot(
        ax=ax, facecolor="#1a1a1a", alpha=0.06, edgecolor="none", zorder=2)
    gpd.GeoSeries([served], crs=CRS_M).boundary.plot(
        ax=ax, color="#1a3a5c", linewidth=0.7, linestyle="--", zorder=3)

    county_line.plot(ax=ax, color=COUNTY_EDGE, linewidth=1.5, zorder=6)

    # 38 supply points by category, distinct symbols + white halos
    for catv in SUPPLY_ORDER:
        st = SUPPLY_STYLE[catv]
        sub = nal[nal["category"] == catv]
        if len(sub):
            ax.scatter(sub.geometry.x, sub.geometry.y, marker=st["marker"], s=st["size"],
                       c=st["color"], edgecolors="white", linewidths=0.9, zorder=8)

    set_extent(ax, county)
    add_scale_bar(ax)
    add_north_arrow(ax, loc=(0.955, 0.90))

    ax.set_title("Figure 1.  The measured naloxone access desert in Dallas County",
                 loc="left", pad=12)
    ax.text(0.0, 1.006,
            "Share of each tract's 2020 population living more than 1 mile from any naloxone "
            "access point (straight-line, measured)",
            transform=ax.transAxes, fontsize=10, color="#333", va="bottom")

    # Headline annotation box
    hl = ba_sum["headline"]
    box = (f"84% of residents live > 1 mile from any naloxone access point\n"
           f"{hl['residents_beyond_1mi_supply']:,} of "
           f"{ba_sum['population']['county_pop_from_2020_blocks']:,} people "
           f"({hl['pct_beyond_1mi_supply']:.0f}%)\n"
           f"{hl['stranded_population']:,} residents stranded "
           f"(> 1 mi from supply AND > 0.5 mi from transit)\n\n"
           "Measured, not modelled: 2020 census-block populations x straight-line distance "
           "to the\n38 fixed supply points. No interpolation or smoothing.")
    ax.text(0.015, 0.985, box, transform=ax.transAxes, fontsize=8.6, va="top", ha="left",
            zorder=15, linespacing=1.5,
            bbox=dict(boxstyle="round,pad=0.7", fc="white", ec="#d7191c", lw=1.4, alpha=0.96))

    # Legends: classes (colour) + supply markers
    class_handles = [Patch(facecolor=colors[i], edgecolor=TRACT_EDGE, label=labels[i])
                     for i in range(len(labels))]
    supply_handles = [
        Line2D([0], [0], marker=SUPPLY_STYLE[c]["marker"], color="none",
               markerfacecolor=SUPPLY_STYLE[c]["color"], markeredgecolor="white",
               markersize=10, label=f"{SUPPLY_STYLE[c]['label']} "
               f"({int((nal['category']==c).sum())})")
        for c in SUPPLY_ORDER
    ]
    supply_handles.append(Line2D([0], [0], color="#1a3a5c", lw=0.9, ls="--",
                                 label="1-mile supply catchment"))
    leg1 = ax.legend(handles=class_handles, title="% population > 1 mi from naloxone",
                     loc="lower left", bbox_to_anchor=(0.012, 0.10), fontsize=7.8,
                     title_fontsize=8.6, frameon=True, framealpha=0.95, borderpad=0.7)
    leg1._legend_box.align = "left"
    ax.add_artist(leg1)
    leg2 = ax.legend(handles=supply_handles, title="Existing naloxone supply (n = 38)",
                     loc="lower right", bbox_to_anchor=(0.986, 0.04), fontsize=7.7,
                     title_fontsize=8.6, frameon=True, framealpha=0.95, borderpad=0.7)
    leg2._legend_box.align = "left"

    srcs = [SRC["blocks"], SRC["nalox"], SRC["tiger"], SRC["proj"]]
    add_source_strip(fig, srcs)
    fig.subplots_adjust(left=0.02, right=0.98, top=0.94, bottom=0.07)
    _save(fig, "figure1")
    _caption("figure1",
        "Figure 1. The measured naloxone access desert in Dallas County.",
        ("Each of the 645 census tracts is shaded by the share of its 2020 census-block "
         "population living more than one mile (straight-line) from any of the 38 fixed "
         "naloxone access points, using five fixed classes (< 5%, 5-50%, 50-95%, 95-< 100%, "
         "and 100% - a total desert). Values are measured, not modelled: every block's "
         "population (2020 PL 94-171 count) is assigned to the nearest supply point in "
         "EPSG:32138, with no interpolation or smoothing. Point symbols mark the 38 fixed "
         "supply sites by category (OTP/methadone, storefront dispenser, recovery services, "
         "harm-reduction access, health clinic, nightlife venue); one mobile harm-reduction "
         "unit without fixed coordinates is omitted. The dashed outline is the union of "
         "1-mile catchments around those sites, clipped to the county. County-wide, "
         "2,196,211 residents (84.0%) live beyond one mile of supply and 902,611 are "
         "stranded (beyond one mile of supply and beyond a half-mile of transit)."),
        [SRC["blocks"], SRC["nalox"], SRC["tiger"], SRC["proj"]],
        "All layers are aggregated population counts or facility point locations; no "
        "individual-level data are used.")


# ======================================================================================
# FIGURE 2 - Composite vulnerability index (HERO)
# ======================================================================================
def figure2():
    print("Building Figure 2 (hero) ...")
    fig, ax = plt.subplots(figsize=(11, 12))
    style_axes(ax)

    ranked = comp[~comp["excluded"]].copy()
    excluded = comp[comp["excluded"]].copy()

    county.plot(ax=ax, facecolor=LAND, edgecolor="none", zorder=0)

    vmin = float(ranked["composite_score"].min())
    vmax = float(ranked["composite_score"].max())
    ranked.plot(ax=ax, column="composite_score", cmap="viridis", vmin=vmin, vmax=vmax,
                edgecolor=TRACT_EDGE, linewidth=0.12, zorder=1)
    if len(excluded):
        excluded.plot(ax=ax, facecolor="#dcdcdc", edgecolor=TRACT_EDGE, linewidth=0.25,
                      hatch="xx", zorder=1)

    # Suppression hatch on count-suppressed tracts. Split by fill brightness so the dotted
    # texture reads on ALL viridis fills: light dots over dark (low) fills, dark dots over
    # bright (high) fills.
    supp = ranked[ranked["count_suppressed"]].copy()
    supp["_nb"] = (supp["composite_score"] - vmin) / (vmax - vmin)
    light = supp[supp["_nb"] < 0.55]     # dark viridis fill -> light dots
    dark = supp[supp["_nb"] >= 0.55]     # bright viridis fill -> dark dots
    if len(light):
        light.plot(ax=ax, facecolor="none", edgecolor="#f5f5f5", linewidth=0.0,
                   hatch="....", zorder=2, alpha=0.6)
    if len(dark):
        dark.plot(ax=ax, facecolor="none", edgecolor="#2a2a2a", linewidth=0.0,
                  hatch="....", zorder=2, alpha=0.5)

    # Tier-1 heavy outline
    t1 = ranked[ranked["tier"] == 1]
    gpd.GeoSeries([unary_union(t1.geometry.values)], crs=CRS_M).boundary.plot(
        ax=ax, color="#111", linewidth=2.0, zorder=5)
    county_line.plot(ax=ax, color=COUNTY_EDGE, linewidth=1.5, zorder=6)

    set_extent(ax, county)
    add_scale_bar(ax)
    add_north_arrow(ax)

    ax.set_title("Figure 2.  Composite overdose-vulnerability index, Dallas County",
                 loc="left", pad=12)
    ax.text(0.0, 1.006,
            "Equal-weighted index of overdose mortality, poverty, uninsurance, naloxone "
            "distance, and transit access; Jenks natural-break tiers",
            transform=ax.transAxes, fontsize=10, color="#333", va="bottom")

    # Colour bar with Jenks cutoffs marked
    sm = mpl.cm.ScalarMappable(cmap="viridis", norm=Normalize(vmin=vmin, vmax=vmax))
    cbar = fig.colorbar(sm, ax=ax, fraction=0.030, pad=0.028, aspect=32)
    cbar.set_label("Composite vulnerability score (0-1)", fontsize=8.6, labelpad=6)
    ticks = sorted({round(vmin, 3), T3_UP, T1_LO, round(vmax, 3)})
    cbar.set_ticks(ticks)
    cbar.set_ticklabels([f"{t:.3f}" for t in ticks])
    cbar.ax.tick_params(labelsize=7.6)
    # Jenks tier cutoffs marked as heavy lines on the bar (values shown as ticks above)
    for cut in (T3_UP, T1_LO):
        cbar.ax.axhline(cut, color="#111", linewidth=1.4)

    n1 = int((ranked["tier"] == 1).sum())
    n2 = int((ranked["tier"] == 2).sum())
    n3 = int((ranked["tier"] == 3).sum())
    handles = [
        Line2D([0], [0], color="#111", lw=2.6,
               label=f"Tier 1 - highest, >= {T1_LO:.3f}  ({n1} tracts, outlined)"),
        Patch(facecolor=plt.cm.viridis(0.60), edgecolor="none",
              label=f"Tier 2 - moderate, {T3_UP:.3f}-{T1_LO:.3f}  ({n2} tracts)"),
        Patch(facecolor=plt.cm.viridis(0.20), edgecolor="none",
              label=f"Tier 3 - lower, < {T3_UP:.3f}  ({n3} tracts)"),
        Patch(facecolor=plt.cm.viridis(0.75), edgecolor="#2a2a2a", hatch="....",
              label="CDC-suppressed death counts (1-9);\nmodel-based rate shown (591 tracts)"),
        Patch(facecolor="#dcdcdc", edgecolor="#888", hatch="xx",
              label="Excluded - zero population (2 tracts)"),
    ]
    leg = ax.legend(handles=handles, title="Vulnerability tiers (Jenks natural breaks)",
                    loc="upper left", fontsize=7.8, title_fontsize=9, frameon=True,
                    framealpha=0.96, borderpad=0.8, labelspacing=0.9)
    leg._legend_box.align = "left"

    srcs = [SRC["mort"], SRC["acs"], SRC["svi"], SRC["nalox"], SRC["dart"], SRC["tiger"], SRC["proj"]]
    add_source_strip(fig, srcs, SUPPRESS_TRACT)
    fig.subplots_adjust(left=0.02, right=0.98, top=0.94, bottom=0.075)
    _save(fig, "figure2")

    md = SENS["mortality_doubled"]
    dd = SENS["distance_doubled"]
    _caption("figure2",
        "Figure 2. Composite overdose-vulnerability index for Dallas County census tracts.",
        ("Fill colour is a composite vulnerability score, the equal-weighted mean (0.20 each) "
         "of five min-max-normalised layers: overdose mortality rate, poverty rate "
         "(ACS 2020-2024), uninsured rate (ACS 2020-2024), distance to the nearest naloxone "
         "access point, and inverted transit connectivity (DART stops within 0.5 mi). "
         "643 tracts are ranked; two zero-population tracts are excluded (cross-hatched). "
         "Tracts fall into three Jenks natural-break tiers "
         f"(Tier 3|2 cut = {T3_UP:.3f}; Tier 2|1 cut = {T1_LO:.3f}, both marked on the "
         f"colour bar); the boundary of Tier 1 (highest vulnerability, {n1} tracts) is "
         "outlined in heavy black. Fine dotted hatching marks the 591 ranked tracts whose "
         "underlying death counts are CDC-suppressed. Sensitivity analysis re-running the "
         "full pipeline with re-weighted layers shows the tiering is robust: doubling "
         f"mortality weight leaves {md['pct_identical_tier']:.0f}% of tracts in the same tier, "
         f"while doubling distance weight flips {100 - dd['pct_identical_tier']:.1f}% - all of "
         "them between adjacent tiers, with zero Tier 1<->Tier 3 flips under either variant."),
        [SRC["mort"], SRC["acs"], SRC["svi"], SRC["nalox"], SRC["dart"], SRC["tiger"], SRC["proj"]],
        SUPPRESS_TRACT + " All layers are tract aggregates; no individual-level data are used.")


# ======================================================================================
# FIGURE 3 - Ranked placement strategy
# ======================================================================================
def _place_labels_fan(ax, xs, ys, texts, xlim, ylim, side="left"):
    """Stack labels in a vertical fan on one side with leader lines to each anchor."""
    sx, sy = xlim[1] - xlim[0], ylim[1] - ylim[0]
    cy = np.mean(ys)
    n = len(texts)
    span = 0.30 * sy
    y_targets = np.linspace(cy + span / 2, cy - span / 2, n)
    if side == "left":
        x_target = min(xs) - 0.16 * sx
        ha = "right"
    else:
        x_target = max(xs) + 0.16 * sx
        ha = "left"
    order = np.argsort(-np.asarray(ys))       # top-most anchor -> top label
    for slot, idx in enumerate(order):
        ty = y_targets[slot]
        ax.annotate("", xy=(xs[idx], ys[idx]), xytext=(x_target, ty),
                    arrowprops=dict(arrowstyle="-", color="#444", lw=0.7,
                                    connectionstyle="arc3,rad=0.0"), zorder=11)
        ax.text(x_target, ty, texts[idx], fontsize=8.0, fontweight="bold", ha=ha,
                va="center", zorder=13, path_effects=halo(2.4),
                bbox=dict(boxstyle="round,pad=0.28", fc="white", ec="#0072B2", lw=0.8,
                          alpha=0.95))


def figure3():
    print("Building Figure 3 ...")
    fig, ax = plt.subplots(figsize=(11, 12))
    style_axes(ax)
    county.plot(ax=ax, facecolor=LAND, edgecolor="none", zorder=0)

    # Muted tier shading
    tier_fill = {1.0: "#c9b8d6", 2.0: "#e7e0ec", 3.0: "#f6f4f8"}
    for tier, fc in tier_fill.items():
        sub = comp[comp["tier"] == tier]
        if len(sub):
            sub.plot(ax=ax, facecolor=fc, edgecolor=TRACT_EDGE, linewidth=0.12, zorder=1)
    comp[comp["excluded"]].plot(ax=ax, facecolor="#e8e8e8", edgecolor=TRACT_EDGE,
                                linewidth=0.15, zorder=1)
    t1 = comp[comp["tier"] == 1.0]
    gpd.GeoSeries([unary_union(t1.geometry.values)], crs=CRS_M).boundary.plot(
        ax=ax, color="#5b3a86", linewidth=1.6, zorder=4)
    county_line.plot(ax=ax, color=COUNTY_EDGE, linewidth=1.5, zorder=6)

    # Existing supply (distinct hollow squares)
    ax.scatter(nal.geometry.x, nal.geometry.y, marker="s", s=46, facecolors="none",
               edgecolors="#111", linewidths=1.1, zorder=8)

    # Top-50 ranked candidates (by rank_composite), sized by reach_raw
    top50 = pr.sort_values("rank_composite").head(50).copy()
    r = top50["reach_raw"].to_numpy(dtype=float)
    rmin, rmax = r.min(), r.max()
    sizes = 45 + (r - rmin) / (rmax - rmin + 1e-9) * 330
    both = top50["in_top50_both"].to_numpy(dtype=bool)
    ACC = "#0072B2"
    # solid = agreement across both need variants
    ax.scatter(top50.geometry.x[both], top50.geometry.y[both], s=sizes[both], marker="o",
               facecolors=ACC, edgecolors="white", linewidths=0.7, alpha=0.75, zorder=9)
    # hollow = composite-only
    ax.scatter(top50.geometry.x[~both], top50.geometry.y[~both], s=sizes[~both], marker="o",
               facecolors="none", edgecolors=ACC, linewidths=1.4, zorder=9)

    # Cluster-count callouts: the top-50 stack into a few dense clusters, so a viewer
    # counting dots undercounts badly. Single-linkage grouping at 1.5 km; annotate
    # clusters of >=3 with their member count.
    from scipy.spatial import cKDTree as _KD
    from collections import defaultdict as _dd
    _pts = np.column_stack([top50.geometry.x.to_numpy(), top50.geometry.y.to_numpy()])
    _parent = list(range(len(_pts)))
    def _find(a):
        while _parent[a] != a:
            _parent[a] = _parent[_parent[a]]; a = _parent[a]
        return a
    for _a, _b in _KD(_pts).query_pairs(r=1500):
        _ra, _rb = _find(_a), _find(_b)
        if _ra != _rb:
            _parent[_ra] = _rb
    _clusters = _dd(list)
    for _i in range(len(_pts)):
        _clusters[_find(_i)].append(_i)
    _n_annotated = 0
    for _members in _clusters.values():
        if len(_members) < 3:
            continue
        _cx, _cy = _pts[_members, 0].mean(), _pts[_members, 1].mean()
        # flip label below-left for clusters near the top edge (avoids the greedy table box);
        # use county bounds, not ax limits — extent isn't set yet at this point
        _b = county.total_bounds
        _dx, _dy = (3200, 3600)
        if _cy > _b[1] + 0.72 * (_b[3] - _b[1]):
            _dx, _dy = (-8500, -3000)
        ax.annotate(f"x{len(_members)} sites here", xy=(_cx, _cy),
                    xytext=(_cx + _dx, _cy + _dy),
                    fontsize=8.4, fontweight="bold", color=ACC, zorder=12,
                    arrowprops=dict(arrowstyle="-", color=ACC, lw=0.9),
                    bbox=dict(boxstyle="round,pad=0.28", fc="white", ec=ACC, lw=0.9,
                              alpha=0.92))
        _n_annotated += len(_members)
    print(f"  fig3 cluster callouts cover {_n_annotated} of {len(_pts)} top-50 sites")

    set_extent(ax, county)
    xlim, ylim = ax.get_xlim(), ax.get_ylim()

    # Label the top-5 picks by short name with leader lines
    top5 = pr.sort_values("rank_composite").head(5).copy()
    short = {
        "SUPER FUELS LOMBARDY LLC": "1. Super Fuels (liquor)",
        "LA MICHOACANA MEAT MARKET DALLAS #30": "2. La Michoacana (liquor)",
        "LIRAS BARBER SHOP": "3. Liras Barber",
        "FIESTA MART #77": "4. Fiesta Mart #77",
        "Fiesta Mart Fuel Station #177": "5. Fiesta Mart Fuel",
    }
    xs = top5.geometry.x.to_numpy()
    ys = top5.geometry.y.to_numpy()
    texts = [short.get(n, n) for n in top5["name"]]
    _place_labels_fan(ax, xs, ys, texts, xlim, ylim, side="left")

    add_scale_bar(ax, loc=(0.40, 0.045))
    add_north_arrow(ax, loc=(0.955, 0.09))

    ax.set_title("Figure 3.  Ranked naloxone placement strategy", loc="left", pad=12)
    ax.text(0.0, 1.006,
            "Top-50 candidate storefronts by NEED x REACH, sized by unserved residents "
            "reachable; vulnerability tiers shaded",
            transform=ax.transAxes, fontsize=10, color="#333", va="bottom")

    # Greedy coverage annotation table
    g = pr_meta["greedy_coverage_gain"]["cumulative_summary"]
    tbl = (f"Greedy coverage of unserved residents\n"
           f"(rank-agnostic, de-overlapped; {g['total_unserved_residents']:,} unserved total)\n"
           f"----------------------------------------\n"
           f"  10 sites   ->   {g['after_10_sites']:,} newly served\n"
           f"  25 sites   ->   {g['after_25_sites']:,} newly served\n"
           f"  50 sites   ->   {g['after_50_sites']:,} newly served")
    ax.text(0.985, 0.985, tbl, transform=ax.transAxes, fontsize=8.0, va="top", ha="right",
            family="DejaVu Sans Mono", zorder=15, linespacing=1.45,
            bbox=dict(boxstyle="round,pad=0.6", fc="white", ec="#5b3a86", lw=1.2, alpha=0.96))

    # Legends. Reference sizes chosen WITHIN the top-50 reach range so markers render
    # correctly; markersize (points) = sqrt(scatter area s).
    size_ref = [9000, 13000, 17000]
    size_handles = [
        Line2D([0], [0], marker="o", color="none", markerfacecolor=ACC,
               markeredgecolor="white",
               markersize=np.sqrt(45 + (v - rmin) / (rmax - rmin + 1e-9) * 330),
               label=f"{v:,} residents")
        for v in size_ref
    ]
    type_handles = [
        Line2D([0], [0], marker="o", color="none", markerfacecolor=ACC,
               markeredgecolor="white", markersize=10,
               label="Top-50 candidate - agrees in both\nNEED variants (composite & grounded)"),
        Line2D([0], [0], marker="o", color="none", markerfacecolor="none",
               markeredgecolor=ACC, markeredgewidth=1.4, markersize=10,
               label="Top-50 candidate - composite ranking only"),
        Line2D([0], [0], marker="s", color="none", markerfacecolor="none",
               markeredgecolor="#111", markersize=9, label="Existing naloxone supply (38)"),
    ]
    ctx_handles = [
        Patch(facecolor=tier_fill[1.0], edgecolor="#5b3a86", lw=1.2, label="Tier 1 tract"),
        Patch(facecolor=tier_fill[2.0], edgecolor=TRACT_EDGE, label="Tier 2 tract"),
        Patch(facecolor=tier_fill[3.0], edgecolor=TRACT_EDGE, label="Tier 3 tract"),
    ]
    leg1 = ax.legend(handles=type_handles, title="Ranked candidates (NEED x REACH)",
                     loc="upper left", fontsize=7.6, title_fontsize=8.6, frameon=True,
                     framealpha=0.96, borderpad=0.7, labelspacing=1.0)
    leg1._legend_box.align = "left"
    ax.add_artist(leg1)
    leg2 = ax.legend(handles=size_handles, title="Symbol size = REACH\n(unserved reachable)",
                     loc="lower right", bbox_to_anchor=(0.972, 0.20), fontsize=7.4,
                     title_fontsize=8.4, frameon=True, framealpha=0.96, borderpad=0.9,
                     labelspacing=1.8, handletextpad=1.4)
    leg2._legend_box.align = "left"
    ax.add_artist(leg2)
    leg3 = ax.legend(handles=ctx_handles, title="Vulnerability tier",
                     loc="lower left", bbox_to_anchor=(0.012, 0.02), fontsize=7.6,
                     title_fontsize=8.4, frameon=True, framealpha=0.96, borderpad=0.7)
    leg3._legend_box.align = "left"

    srcs = [SRC["cand"], SRC["blocks"], SRC["mort"], SRC["acs"], SRC["svi"], SRC["nalox"],
            SRC["tiger"], SRC["proj"]]
    add_source_strip(fig, srcs)
    fig.subplots_adjust(left=0.02, right=0.98, top=0.94, bottom=0.075)
    _save(fig, "figure3")

    ag = pr_meta["agreement_stats"]
    _caption("figure3",
        "Figure 3. Ranked naloxone placement strategy for Dallas County.",
        ("Candidate storefronts are ranked by NEED x REACH. REACH is the number of currently "
         "unserved residents (2020 blocks beyond one mile of existing supply) within a "
         "half-mile walk of a candidate; NEED is tract vulnerability. Two NEED variants are "
         "computed - the five-layer composite index (Figure 2) and a mortality-free "
         "'grounded' index (poverty, uninsurance, SVI, measured naloxone distance) - and each "
         "is multiplied by normalised REACH. The two rankings agree closely "
         f"(Spearman rho = {ag['spearman_rho_composite_vs_grounded']:.3f}; "
         f"{ag['top50_overlap_count']} of the top 50 shared). The map shows the 50 "
         "top-ranked candidates by composite score, sized by REACH; solid symbols agree in "
         "both variants, hollow symbols rank in the composite variant only. Hollow squares "
         "mark the 38 existing supply sites; muted shading shows the vulnerability tiers. The "
         "five highest-ranked candidates are labelled. The inset reports a separate "
         "rank-agnostic greedy coverage analysis: the best 10, 25, and 50 non-overlapping "
         "sites would newly reach 117,922, 231,806, and 388,927 of the 2,196,211 unserved "
         "residents, respectively. The top 50 concentrate in a small number of geographic "
         "clusters (count callouts on map) because REACH concentrates where large unserved "
         "populations adjoin high-need tracts."),
        [SRC["cand"], SRC["blocks"], SRC["mort"], SRC["acs"], SRC["svi"], SRC["nalox"],
         SRC["tiger"], SRC["proj"]],
        "Candidates are commercial/public storefront point locations; all population and "
        "vulnerability inputs are aggregated. No individual-level data are used.")


# ======================================================================================
# FIGURE 4 - Overdose mortality at CDC native reporting units
# ======================================================================================
def figure4():
    print("Building Figure 4 ...")
    fig, ax = plt.subplots(figsize=(11, 12))
    style_axes(ax)
    county.plot(ax=ax, facecolor=LAND, edgecolor="none", zorder=0)

    vals = mort["od_death_rate_per_100k"].to_numpy(dtype=float)
    # Jenks natural breaks. 7 units sit at exactly 0, which Jenks isolates into a zero-width
    # first break; requesting 6 classes and de-duplicating identical edges yields 5 clean,
    # well-populated classes (counts ~66/45/30/6/3) with good resolution in the dense mid-range.
    breaks = sorted(set(np.round(jenkspy.jenks_breaks(vals, n_classes=6), 1)))
    n_cls = len(breaks) - 1
    colors = [plt.cm.YlOrRd(x) for x in np.linspace(0.12, 0.95, n_cls)]  # CB-safe sequential
    # Class labels from ACTUAL data extents within each bin.
    class_labels = []
    for i in range(n_cls):
        lo, hi = breaks[i], breaks[i + 1]
        inbin = vals[(vals >= lo) & (vals <= hi)] if i == n_cls - 1 else vals[(vals >= lo) & (vals < hi)]
        if len(inbin):
            a, b = inbin.min(), inbin.max()
            class_labels.append(f"{a:g}" if a == b else f"{a:g} - {b:g}")
        else:
            class_labels.append(f"{lo:g} - {hi:g}")
    cmap = ListedColormap(colors)
    norm = BoundaryNorm(breaks, cmap.N)
    mort.plot(ax=ax, column="od_death_rate_per_100k", cmap=cmap, norm=norm,
              edgecolor="#6b6b6b", linewidth=0.35, zorder=1)

    # Suppression marker on the 138 count-suppressed units (diagonal hatch reads on all fills)
    supp = mort[mort["count_suppressed"]]
    supp.plot(ax=ax, facecolor="none", edgecolor="#333", linewidth=0.0, hatch="////",
              zorder=2, alpha=0.30)

    county_line.plot(ax=ax, color=COUNTY_EDGE, linewidth=1.5, zorder=6)

    set_extent(ax, county)
    add_scale_bar(ax)
    add_north_arrow(ax)

    ax.set_title("Figure 4.  Overdose mortality at CDC native reporting units",
                 loc="left", pad=12)
    ax.text(0.0, 1.006,
            "Model-based overdose death rate per 100,000 across the 150 CDC aggregation units "
            "covering Dallas County (Jenks natural breaks)",
            transform=ax.transAxes, fontsize=10, color="#333", va="bottom")

    # Legend: Jenks classes + suppression
    class_handles = []
    for i in range(len(colors)):
        class_handles.append(Patch(facecolor=colors[i], edgecolor="#6b6b6b",
                             label=class_labels[i]))
    class_handles.append(Patch(facecolor="white", edgecolor="#333", hatch="////",
                         label="Suppressed underlying\ncount (138 of 150 units)"))
    leg = ax.legend(handles=class_handles,
                    title="OD deaths per 100,000\n(12-mo period 2025-01/2025-12)",
                    loc="upper left", fontsize=7.9, title_fontsize=8.8, frameon=True,
                    framealpha=0.96, borderpad=0.8, labelspacing=0.8)
    leg._legend_box.align = "left"

    # Note box on CI availability + honest-geography framing
    q = np.percentile(vals, [25, 50, 75])
    note = (f"150 native reporting units (2-10 tracts each)\n"
            f"Rate range {vals.min():g}-{vals.max():g} per 100k; "
            f"quartiles {q[0]:.1f} / {q[1]:.1f} / {q[2]:.1f}\n"
            f"Each unit carries a published 95% CI\n"
            f"This is the finest honest mortality geography Texas\nsuppression permits")
    ax.text(0.985, 0.985, note, transform=ax.transAxes, fontsize=7.8, va="top", ha="right",
            zorder=15, linespacing=1.5,
            bbox=dict(boxstyle="round,pad=0.6", fc="white", ec="#bd0026", lw=1.2, alpha=0.96))

    srcs = [SRC["mort"], SRC["tiger"], SRC["proj"]]
    add_source_strip(fig, srcs, SUPPRESS_UNIT)
    fig.subplots_adjust(left=0.02, right=0.98, top=0.94, bottom=0.075)
    _save(fig, "figure4")
    _caption("figure4",
        "Figure 4. Overdose mortality at the CDC's native reporting units, Dallas County.",
        ("Each of the 150 CDC aggregation units (unions of 2-10 census tracts) is shaded by "
         "its model-based overdose death rate (deaths per 100,000, 12-month period ending "
         "December 2025), classified into five Jenks natural-break ranges. Rates span "
         f"{vals.min():g} to {vals.max():g} per 100,000 (quartiles {q[0]:.1f}, {q[1]:.1f}, "
         f"{q[2]:.1f}). Diagonal hatching marks the 138 units (92%) whose underlying death "
         "counts are CDC-suppressed. Texas mortality suppression prevents the CDC from "
         "publishing raw tract-level counts, so the tract product releases model-based rates "
         "aggregated to these native units; a 95% confidence interval is published for each "
         "unit. This is the honest mortality geography - the finest resolution the "
         "suppression regime permits without fabricating counts."),
        [SRC["mort"], SRC["tiger"], SRC["proj"]],
        SUPPRESS_UNIT + " No individual-level death records exist in the source or repository.")


if __name__ == "__main__":
    figure1()
    figure2()
    figure3()
    figure4()
    print("Done.")
