#!/usr/bin/env python3
"""
Phase 4A - MANUSCRIPT FIGURES for the Dallas County overdose / naloxone-placement pipeline.

Produces publication-grade (300 dpi) PNG + PDF cartography:
  Figure 1  Naloxone access gap  (mortality choropleth + supply + service radii + gap zones)
  Figure 2  Composite vulnerability index (HERO)  (composite choropleth + tiers + suppression hatch)
  Figure 3  Recommended placement strategy  (tier shading + Tier-1 candidate storefronts + nightlife corridors)

HARD PRIVACY RULE honoured: every layer is AGGREGATED (census-tract choropleths) or
FACILITY POINT locations (supply sites, candidate storefronts). No individual death
points exist in the repo and none are synthesised.

Projected CRS for all geometry work: EPSG:32138 (NAD83 / Texas North Central, metres).
"""

import json
import math
from pathlib import Path

import geopandas as gpd
import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, FancyArrowPatch
import matplotlib.patheffects as pe
from shapely.ops import unary_union

# --------------------------------------------------------------------------------------
# Paths & constants
# --------------------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
CLEAN = ROOT / "data" / "clean"
FIGDIR = ROOT / "figures"
FIGDIR.mkdir(exist_ok=True)

CRS_M = 32138          # NAD83 / Texas North Central (metres)
MI = 1609.344          # metres per statute mile

# Source + vintage strip shown on every figure
SOURCE_STRIP = (
    "Sources: CDC NCHS census-tract overdose death rates (4day-mt2f TTM 2025, data as of 2026-07)  |  "
    "TX DSHS NarcanSites (Apr 2026)  |  SAMHSA findtreatment.gov (Jul 2026)  |  ACS 2020-2024 5-yr  |  "
    "CDC/ATSDR SVI 2022  |  TABC (Jul 2026)  |  Dallas County Open Data (Jul 2026)  |  OpenStreetMap (Jul 2026).  "
    "Tract boundaries: Census TIGER 2024. Projection: NAD83 / Texas North Central (EPSG:32138)."
)
SUPPRESSION_NOTE = (
    "CDC suppresses tract death counts of 1-9; 592 of 645 tracts (91.8%) carry suppressed counts. "
    "Model-based rates (4day-mt2f) are shown for all tracts; tract-level values should be read as smoothed estimates, not exact counts."
)

# --------------------------------------------------------------------------------------
# Global publication style
# --------------------------------------------------------------------------------------
mpl.rcParams.update({
    "figure.dpi": 150,
    "savefig.dpi": 300,
    "font.family": "DejaVu Sans",
    "font.size": 9,
    "axes.titlesize": 14,
    "axes.titleweight": "bold",
    "pdf.fonttype": 42,      # embed TrueType (editable text in vector editors)
    "ps.fonttype": 42,
    "svg.fonttype": "none",
    "hatch.linewidth": 0.35,
})

LAND = "#f4f2ee"        # light neutral land fill
COUNTY_EDGE = "#3d3d3d"
TRACT_EDGE = "#b8b3ab"

# Existing-supply symbology (naloxone_locations.category)
SUPPLY_STYLE = {
    "health_clinic":        dict(marker="P", color="#0072B2", label="Health / county clinic", size=95),
    "recovery_services":    dict(marker="D", color="#009E73", label="Recovery services",       size=60),
    "otp_methadone":        dict(marker="^", color="#D55E00", label="OTP / methadone clinic",  size=70),
    "storefront_dispenser": dict(marker="s", color="#5b3a86", label="Storefront dispenser",    size=55),
    "harm_reduction":       dict(marker="p", color="#CC79A7", label="Harm-reduction access",   size=70),
    "nightlife_venue":      dict(marker="X", color="#111111", label="Nightlife venue",         size=60),
}

# Service-radius buckets (documented in captions)
#   2 mi  -> clinical / recovery facilities (staffed sites)
#   1 mi  -> dispensers, OTPs, harm-reduction access points
RADIUS_2MI_CATS = ["health_clinic", "recovery_services"]
RADIUS_1MI_CATS = ["storefront_dispenser", "otp_methadone", "harm_reduction"]

# Placement-candidate symbology (Okabe-Ito colourblind-safe palette)
CAND_STYLE = {
    "liquor_store":            dict(color="#D55E00", marker="o", label="Liquor store"),
    "convenience_corner_store":dict(color="#0072B2", marker="o", label="Convenience / corner store"),
    "barbershop_beauty":       dict(color="#CC79A7", marker="o", label="Barbershop / beauty"),
    "grocery_food_mart":       dict(color="#009E73", marker="o", label="Grocery / food mart"),
    "gas_station":             dict(color="#E69F00", marker="o", label="Gas station"),
    "laundromat":              dict(color="#56B4E9", marker="o", label="Laundromat"),
    "library":                 dict(color="#111111", marker="*", label="Public library"),
    "other_storefront":        dict(color="#8a8a8a", marker="o", label="Other storefront"),
}


# --------------------------------------------------------------------------------------
# Cartographic furniture
# --------------------------------------------------------------------------------------
def add_scale_bar(ax, length_m=10000, label="10 km", loc=(0.06, 0.06)):
    """Draw a two-tone scale bar in axes fraction, sized in map metres."""
    x0f, y0f = loc
    xlim = ax.get_xlim(); ylim = ax.get_ylim()
    span_x = xlim[1] - xlim[0]; span_y = ylim[1] - ylim[0]
    x0 = xlim[0] + x0f * span_x
    y0 = ylim[0] + y0f * span_y
    h = 0.010 * span_y
    seg = length_m / 2.0
    ax.add_patch(plt.Rectangle((x0, y0), seg, h, facecolor="#222", edgecolor="#222", zorder=20))
    ax.add_patch(plt.Rectangle((x0 + seg, y0), seg, h, facecolor="white", edgecolor="#222", zorder=20))
    tt = dict(ha="center", va="bottom", fontsize=7.5, zorder=21)
    ax.text(x0, y0 + h * 1.25, "0", **tt)
    ax.text(x0 + length_m, y0 + h * 1.25, label, **tt)
    # miles reference tick
    ax.text(x0 + length_m / 2, y0 - h * 1.6, f"({length_m/MI:.1f} mi)", ha="center", va="top",
            fontsize=6.5, color="#555", zorder=21)


def add_north_arrow(ax, loc=(0.955, 0.12)):
    xlim = ax.get_xlim(); ylim = ax.get_ylim()
    xf, yf = loc
    x = xlim[0] + xf * (xlim[1] - xlim[0])
    y = ylim[0] + yf * (ylim[1] - ylim[0])
    dy = 0.06 * (ylim[1] - ylim[0])
    ax.annotate("", xy=(x, y + dy), xytext=(x, y),
                arrowprops=dict(arrowstyle="-|>", color="#222", lw=1.6), zorder=21)
    ax.text(x, y + dy * 1.12, "N", ha="center", va="bottom", fontsize=9, fontweight="bold", zorder=21)


def add_source_strip(fig, extra=""):
    txt = SOURCE_STRIP + ("\n" + extra if extra else "")
    fig.text(0.012, 0.012, txt, ha="left", va="bottom", fontsize=6.0, color="#555",
             wrap=True, linespacing=1.35)


def style_map_axes(ax):
    ax.set_aspect("equal")
    ax.set_axis_off()
    ax.margins(0.02)


def finalize_extent(ax, county):
    minx, miny, maxx, maxy = county.total_bounds
    padx = (maxx - minx) * 0.03; pady = (maxy - miny) * 0.03
    ax.set_xlim(minx - padx, maxx + padx)
    ax.set_ylim(miny - pady, maxy + pady)


# --------------------------------------------------------------------------------------
# Load data (project everything to metres)
# --------------------------------------------------------------------------------------
print("Loading data ...")
tod = gpd.read_file(CLEAN / "tract_overdose.geojson").to_crs(CRS_M)
comp = gpd.read_file(CLEAN / "composite_index.geojson").to_crs(CRS_M)
nal = gpd.read_file(CLEAN / "naloxone_locations.geojson")
nal = nal[~nal.geometry.isna()].to_crs(CRS_M)           # drop 1 null-geometry mobile row
cand = gpd.read_file(CLEAN / "placement_candidates.geojson").to_crs(CRS_M)

cg = json.loads((CLEAN / "coverage_gap.json").read_text())
methods = json.loads((CLEAN / "composite_methods.json").read_text())

cg_tracts = pd.DataFrame(cg["tracts"])
county = gpd.GeoDataFrame(geometry=[unary_union(tod.geometry.values)], crs=CRS_M)
county_line = county.boundary

# Jenks cutoffs from methods JSON (primary equal-weight index)
BREAKS = methods["tiers"]["cutoffs"]["primary_equal"]["breaks"]  # [min, t3upper, t1lower, max]
T3_UPPER = methods["tiers"]["cutoffs"]["primary_equal"]["tier3_upper_bound"]
T1_LOWER = methods["tiers"]["cutoffs"]["primary_equal"]["tier1_lower_bound"]


def geodetic_circles(points_gdf, radius_m):
    """Circles are buffered in the projected metric CRS (EPSG:32138) so they are
    geodetically faithful at Dallas' scale, then returned in the same CRS."""
    return points_gdf.geometry.buffer(radius_m)


# --------------------------------------------------------------------------------------
# Gap-zone detection (contiguous high-mortality + underserved clusters)
# --------------------------------------------------------------------------------------
def detect_gap_zones():
    from shapely.strtree import STRtree
    from collections import defaultdict
    med_dist = cg["summary"]["dist_nearest_any_m"]["median_m"]
    g = tod.merge(cg_tracts[["geoid", "dist_nearest_any_m"]], on="geoid")
    rate_q75 = g["od_death_rate_per_100k"].quantile(0.75)
    gap = g[(g["od_death_rate_per_100k"] >= rate_q75) &
            (g["dist_nearest_any_m"] > med_dist)].copy().reset_index(drop=True)
    geoms = list(gap.geometry); n = len(geoms)
    parent = list(range(n))
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    buf = [gm.buffer(60) for gm in geoms]
    tree = STRtree(buf)
    for i in range(n):
        for j in tree.query(buf[i]):
            if j > i and buf[i].intersects(buf[j]):
                parent[find(i)] = find(j)
    clusters = defaultdict(list)
    for i in range(n):
        clusters[find(i)].append(i)
    comps = sorted(clusters.values(), key=len, reverse=True)
    return gap, comps, med_dist, rate_q75


def nearest_tract_point(gap, idxs, lat, lon):
    """Return the map-CRS centroid of the gap tract (within idxs) nearest a lat/lon anchor."""
    target = gpd.GeoSeries.from_xy([lon], [lat], crs=4326).to_crs(CRS_M).iloc[0]
    sub = gap.iloc[idxs]
    cents = sub.geometry.centroid
    d = cents.distance(target)
    c = cents.iloc[int(np.argmin(d.values))]
    return c.x, c.y


# --------------------------------------------------------------------------------------
# FIGURE 1 - Naloxone access gap
# --------------------------------------------------------------------------------------
def figure1():
    print("Building Figure 1 ...")
    gap, comps, med_dist, rate_q75 = detect_gap_zones()

    fig, ax = plt.subplots(figsize=(11, 12))
    style_map_axes(ax)

    # land + county
    county.plot(ax=ax, facecolor=LAND, edgecolor="none", zorder=0)

    # mortality choropleth (classified, sequential colourblind-safe magma; high = dark)
    vals = tod["od_death_rate_per_100k"]
    class_breaks = [0, 10, 15, 20, 25, 30, vals.max() + 0.1]
    magma = plt.cm.magma
    # sample magma so high mortality is dark, low is pale-warm (avoid pure white -> visible on white bg)
    colors = [magma(x) for x in np.linspace(0.92, 0.10, len(class_breaks) - 1)]
    cmap = ListedColormap(colors)
    norm = BoundaryNorm(class_breaks, cmap.N)
    tod.plot(ax=ax, column="od_death_rate_per_100k", cmap=cmap, norm=norm,
             edgecolor=TRACT_EDGE, linewidth=0.15, zorder=1)
    county_line.plot(ax=ax, color=COUNTY_EDGE, linewidth=1.4, zorder=6)

    # service radii (drawn as geodetically correct buffers in metric CRS)
    r2 = nal[nal["category"].isin(RADIUS_2MI_CATS)]
    r1 = nal[nal["category"].isin(RADIUS_1MI_CATS)]
    if len(r2):
        gpd.GeoSeries(geodetic_circles(r2, 2 * MI), crs=CRS_M).plot(
            ax=ax, facecolor="#1f6f8b", alpha=0.10, edgecolor="#1f6f8b",
            linewidth=0.6, linestyle="--", zorder=2)
    if len(r1):
        gpd.GeoSeries(geodetic_circles(r1, 1 * MI), crs=CRS_M).plot(
            ax=ax, facecolor="#4a2f7a", alpha=0.10, edgecolor="#4a2f7a",
            linewidth=0.5, linestyle=":", zorder=2)

    # existing supply points
    for catv, st in SUPPLY_STYLE.items():
        if catv == "nightlife_venue":
            continue  # nightlife shown in Fig 3; keep Fig 1 focused on distributed supply
        sub = nal[nal["category"] == catv]
        if len(sub):
            ax.scatter(sub.geometry.x, sub.geometry.y, marker=st["marker"], s=st["size"],
                       c=st["color"], edgecolors="white", linewidths=0.7, zorder=8,
                       label=st["label"])

    # annotate largest gap zones (verified against tract centroid coordinates)
    gap_anchors = [
        ("Pleasant Grove &\nSE Dallas County", 32.70, -96.63, comps[0]),
        ("South Oak Cliff /\nRed Bird", 32.665, -96.885, comps[0]),
        ("Northwest Dallas /\nBachman Lake", 32.885, -96.900, comps[1]),
    ]
    for label, lat, lon, idxs in gap_anchors:
        tx, ty = nearest_tract_point(gap, idxs, lat, lon)
        # label offset outward from county centroid
        cc = county.geometry.iloc[0].centroid
        ox = tx + (tx - cc.x) * 0.18
        oy = ty + (ty - cc.y) * 0.18
        ax.add_patch(FancyArrowPatch((ox, oy), (tx, ty), arrowstyle="-",
                                     connectionstyle="arc3,rad=0.0",
                                     color="#111", lw=1.0, zorder=11))
        ax.scatter([tx], [ty], s=520, facecolors="none", edgecolors="#111",
                   linewidths=1.6, zorder=10)
        ax.text(ox, oy, label, fontsize=9, fontweight="bold", ha="center", va="center",
                zorder=12,
                bbox=dict(boxstyle="round,pad=0.35", fc="white", ec="#111", lw=0.8, alpha=0.92))

    finalize_extent(ax, county)
    add_scale_bar(ax); add_north_arrow(ax)

    ax.set_title("Figure 1.  The naloxone access gap in Dallas County", loc="left", pad=12)
    ax.text(0.0, 1.005,
            "Model-based overdose mortality rate by census tract, existing naloxone supply, and service catchments",
            transform=ax.transAxes, fontsize=10, color="#333", va="bottom")

    # legends: choropleth (colorbar) + supply markers + radii
    sm = mpl.cm.ScalarMappable(cmap=cmap, norm=norm)
    cbar = fig.colorbar(sm, ax=ax, fraction=0.030, pad=0.028, aspect=32)
    cbar.set_ticks(class_breaks)
    cbar.set_ticklabels([f"{b:g}" for b in class_breaks])
    cbar.set_label("Overdose deaths per 100,000 (model-based, 2025 TTM)", fontsize=8.5)
    cbar.ax.tick_params(labelsize=7.5)

    supply_handles = [
        Line2D([0], [0], marker=st["marker"], color="none", markerfacecolor=st["color"],
               markeredgecolor="white", markersize=9, label=st["label"])
        for c, st in SUPPLY_STYLE.items() if c != "nightlife_venue"
    ]
    radius_handles = [
        Patch(facecolor="#1f6f8b", alpha=0.18, edgecolor="#1f6f8b", ls="--",
              label="2-mi catchment (clinic/recovery)"),
        Patch(facecolor="#4a2f7a", alpha=0.18, edgecolor="#4a2f7a", ls=":",
              label="1-mi catchment (dispenser/OTP)"),
        Line2D([0], [0], marker="o", markersize=13, markerfacecolor="none",
               markeredgecolor="#111", color="none", label="Priority access-gap zone"),
    ]
    leg1 = ax.legend(handles=supply_handles, title="Existing naloxone supply",
                     loc="upper left", fontsize=7.8, title_fontsize=8.5,
                     frameon=True, framealpha=0.94, borderpad=0.7)
    leg1._legend_box.align = "left"
    ax.add_artist(leg1)
    leg2 = ax.legend(handles=radius_handles, title="Service catchments & gaps",
                     loc="lower right", bbox_to_anchor=(0.985, 0.02),
                     fontsize=7.8, title_fontsize=8.5,
                     frameon=True, framealpha=0.94, borderpad=0.7)
    leg2._legend_box.align = "left"

    add_source_strip(fig, SUPPRESSION_NOTE)
    fig.subplots_adjust(left=0.02, right=0.98, top=0.94, bottom=0.075)
    _save(fig, "figure1")
    _write_caption("figure1",
        title="Figure 1. The naloxone access gap in Dallas County.",
        body=(
            "Choropleth shows the model-based overdose mortality rate (deaths per 100,000, 12-month "
            "period ending December 2025) for each of the 645 Dallas County census tracts, classified "
            "into six ranges. Point symbols mark the 31 fixed naloxone supply sites (nightlife venues "
            "excluded here; see Figure 3). Shaded catchments approximate walk/short-drive service areas: "
            "2-mile radii around clinic and recovery facilities and 1-mile radii around storefront "
            "dispensers, opioid-treatment programs, and harm-reduction access points, buffered in the "
            "projected metric CRS. Circled annotations flag the largest contiguous clusters of tracts "
            "that are simultaneously in the top mortality quartile ("
            f"≥{rate_q75:.1f} per 100k) and farther than the county-median distance "
            f"({med_dist/MI:.2f} mi) from any supply point; neighbourhood labels were verified against "
            "tract-centroid coordinates."
        ))


# --------------------------------------------------------------------------------------
# FIGURE 2 - Composite vulnerability index (HERO)
# --------------------------------------------------------------------------------------
def figure2():
    print("Building Figure 2 (HERO) ...")
    fig, ax = plt.subplots(figsize=(11, 12))
    style_map_axes(ax)

    ranked = comp[~comp["excluded"]].copy()
    excluded = comp[comp["excluded"]].copy()

    county.plot(ax=ax, facecolor=LAND, edgecolor="none", zorder=0)

    # continuous composite choropleth (viridis, colourblind-safe)
    vmin = ranked["composite_score"].min(); vmax = ranked["composite_score"].max()
    ranked.plot(ax=ax, column="composite_score", cmap="viridis",
                vmin=vmin, vmax=vmax, edgecolor=TRACT_EDGE, linewidth=0.12, zorder=1)
    if len(excluded):
        excluded.plot(ax=ax, facecolor="#dcdcdc", edgecolor=TRACT_EDGE, linewidth=0.2,
                      hatch="xx", zorder=1)

    # suppression hatch overlay (subtle) on count-suppressed tracts.
    # 592/645 tracts are suppressed, so the sparse neutral-grey dots read as a
    # near-ubiquitous texture; the 53 non-suppressed (observed-count) tracts stay clear.
    supp = ranked[ranked["count_suppressed"]]
    supp.plot(ax=ax, facecolor="none", edgecolor="#4d4d4d", linewidth=0.0,
              hatch="..", zorder=2, alpha=0.30)

    # tier boundary: outline only Tier 1 (highest vulnerability) heavily; Tier 2/3 are
    # already separated by the continuous fill and the colourbar Jenks cuts. Drawing all
    # three dissolved boundaries produced an unreadable tangle, so only Tier 1 is outlined.
    t1 = ranked[ranked["tier"] == 1]
    if len(t1):
        gpd.GeoSeries([unary_union(t1.geometry.values)], crs=CRS_M).boundary.plot(
            ax=ax, color="#111", linewidth=1.9, zorder=5)

    county_line.plot(ax=ax, color=COUNTY_EDGE, linewidth=1.4, zorder=6)

    finalize_extent(ax, county)
    add_scale_bar(ax); add_north_arrow(ax)

    ax.set_title("Figure 2.  Composite overdose-vulnerability index, Dallas County", loc="left", pad=12)
    ax.text(0.0, 1.005,
            "Equal-weighted index of mortality, poverty, uninsurance, naloxone distance, and transit access; "
            "Jenks natural-break tiers",
            transform=ax.transAxes, fontsize=10, color="#333", va="bottom")

    # colorbar with Jenks cutoffs marked as ticks + thin lines (labels live in the legend)
    sm = mpl.cm.ScalarMappable(cmap="viridis",
                               norm=mpl.colors.Normalize(vmin=vmin, vmax=vmax))
    cbar = fig.colorbar(sm, ax=ax, fraction=0.030, pad=0.028, aspect=32)
    cbar.set_label("Composite vulnerability score (0-1)", fontsize=8.5, labelpad=6)
    ticks = sorted([round(vmin, 2), T3_UPPER, T1_LOWER, 0.50, 0.60, round(vmax, 2)])
    cbar.set_ticks(ticks)
    cbar.set_ticklabels([f"{t:.3f}" if t in (T3_UPPER, T1_LOWER) else f"{t:.2f}" for t in ticks])
    cbar.ax.tick_params(labelsize=7.5)
    for cut in (T3_UPPER, T1_LOWER):
        cbar.ax.axhline(cut, color="#111", linewidth=1.2)

    # legend: tiers (with Jenks ranges) + suppression + excluded
    n1 = int((ranked["tier"] == 1).sum()); n2 = int((ranked["tier"] == 2).sum()); n3 = int((ranked["tier"] == 3).sum())
    handles = [
        Line2D([0], [0], color="#111", lw=2.4,
               label=f"Tier 1 – highest, ≥{T1_LOWER:.3f}  ({n1} tracts, outlined)"),
        Patch(facecolor=plt.cm.viridis(0.62), edgecolor="none",
               label=f"Tier 2 – moderate, {T3_UPPER:.3f}–{T1_LOWER:.3f}  ({n2} tracts)"),
        Patch(facecolor=plt.cm.viridis(0.28), edgecolor="none",
               label=f"Tier 3 – lower, <{T3_UPPER:.3f}  ({n3} tracts)"),
        Patch(facecolor="white", edgecolor="#4d4d4d", hatch="..",
              label="CDC-suppressed death counts (1–9);\nmodel-based rates shown (592 tracts)"),
        Patch(facecolor="#dcdcdc", edgecolor="#888", hatch="xx",
              label="Excluded – zero population (2 tracts)"),
    ]
    leg = ax.legend(handles=handles, title="Vulnerability tiers  (Jenks natural breaks)",
                    loc="upper left", fontsize=7.8, title_fontsize=9,
                    frameon=True, framealpha=0.95, borderpad=0.8, labelspacing=0.9)
    leg._legend_box.align = "left"

    add_source_strip(fig, SUPPRESSION_NOTE)
    fig.subplots_adjust(left=0.02, right=0.98, top=0.94, bottom=0.075)
    _save(fig, "figure2")
    _write_caption("figure2",
        title="Figure 2. Composite overdose-vulnerability index for Dallas County census tracts.",
        body=(
            "Fill colour is a composite vulnerability score computed as the equal-weighted mean (0.20 each) "
            "of five min-max-normalised indicators: overdose mortality rate, poverty rate (ACS 2020-2024), "
            "uninsured rate (ACS 2020-2024), distance to the nearest naloxone access point, and inverted "
            "transit connectivity (DART stops within 0.5 mi). 643 tracts were ranked; 2 zero-population "
            "tracts were excluded (cross-hatched). Tracts are grouped into three Jenks natural-break "
            f"tiers (Tier 3|2 cut = {T3_UPPER:.3f}; Tier 2|1 cut = {T1_LOWER:.3f}, both marked on the "
            f"colour bar); the boundary of Tier 1 (highest vulnerability, {n1} tracts) is outlined in heavy "
            "black. Fine dotted hatching marks the 592 tracts "
            "whose underlying death counts are CDC-suppressed (1-9); model-based rates are shown for these."
        ))


# --------------------------------------------------------------------------------------
# FIGURE 3 - Recommended placement strategy
# --------------------------------------------------------------------------------------
def figure3():
    print("Building Figure 3 ...")
    fig, ax = plt.subplots(figsize=(11, 12))
    style_map_axes(ax)

    county.plot(ax=ax, facecolor=LAND, edgecolor="none", zorder=0)

    # muted tier shading
    tier_fill = {1: "#c9b8d6", 2: "#e7e0ec", 3: "#f4f2f6"}
    for tier, fc in tier_fill.items():
        sub = comp[comp["tier"] == tier]
        if len(sub):
            sub.plot(ax=ax, facecolor=fc, edgecolor=TRACT_EDGE, linewidth=0.12, zorder=1)
    comp[comp["excluded"]].plot(ax=ax, facecolor="#e8e8e8", edgecolor=TRACT_EDGE, linewidth=0.15, zorder=1)

    # Tier-1 boundary emphasis
    t1 = comp[comp["tier"] == 1]
    t1_geoids = set(t1["geoid"])
    gpd.GeoSeries([unary_union(t1.geometry.values)], crs=CRS_M).boundary.plot(
        ax=ax, color="#5b3a86", linewidth=1.6, zorder=4)
    county_line.plot(ax=ax, color=COUNTY_EDGE, linewidth=1.4, zorder=6)

    # candidate storefronts inside Tier-1 tracts only
    cand_t1 = cand[cand["geoid"].isin(t1_geoids)].copy()
    for catv, st in CAND_STYLE.items():
        sub = cand_t1[cand_t1["category"] == catv]
        if len(sub):
            ax.scatter(sub.geometry.x, sub.geometry.y, marker=st["marker"],
                       s=(46 if st["marker"] == "*" else 12),
                       c=st["color"], edgecolors="none", alpha=0.80, zorder=7,
                       label=st["label"])

    # existing supply for contrast (hollow, dark)
    existing = nal[~nal["category"].isin(["nightlife_venue"])]
    ax.scatter(existing.geometry.x, existing.geometry.y, marker="s", s=42,
               facecolors="none", edgecolors="#111", linewidths=1.1, zorder=9,
               label="Existing naloxone supply")

    # nightlife corridors (actual nightlife_venue points)
    night = nal[nal["category"] == "nightlife_venue"].to_crs(CRS_M)
    ax.scatter(night.geometry.x, night.geometry.y, marker="X", s=90, c="#b30000",
               edgecolors="white", linewidths=0.8, zorder=10, label="Nightlife venue")
    # label the two named corridors from their actual venue clusters
    corridors = {
        "Deep Ellum": night[night["address"].str.contains("Elm|Crowdus", na=False)],
        "Cedar Springs": night[night["address"].str.contains("Cedar Springs|Throckmorton", na=False)],
    }
    for name, pts in corridors.items():
        if len(pts):
            cx = pts.geometry.x.mean(); cy = pts.geometry.y.mean()
            ax.annotate(name, xy=(cx, cy), xytext=(cx + 5500, cy + 5500),
                        fontsize=9, fontweight="bold", ha="center", zorder=12,
                        arrowprops=dict(arrowstyle="-", color="#b30000", lw=1.0),
                        bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#b30000", lw=0.9, alpha=0.92))

    finalize_extent(ax, county)
    add_scale_bar(ax); add_north_arrow(ax)

    ax.set_title("Figure 3.  Recommended naloxone placement strategy", loc="left", pad=12)
    ax.text(0.0, 1.005,
            "Candidate host sites within Tier-1 (highest-vulnerability) tracts, existing supply, and nightlife corridors",
            transform=ax.transAxes, fontsize=10, color="#333", va="bottom")

    # legends
    cand_handles = [
        Line2D([0], [0], marker=st["marker"], color="none", markerfacecolor=st["color"],
               markeredgecolor="none", markersize=(11 if st["marker"] == "*" else 8),
               label=f"{st['label']}  ({int((cand_t1['category']==c).sum())})")
        for c, st in CAND_STYLE.items()
    ]
    ctx_handles = [
        Patch(facecolor=tier_fill[1], edgecolor="#5b3a86", lw=1.2, label="Tier 1 tract (candidate zone)"),
        Patch(facecolor=tier_fill[2], edgecolor=TRACT_EDGE, label="Tier 2 tract"),
        Patch(facecolor=tier_fill[3], edgecolor=TRACT_EDGE, label="Tier 3 tract"),
        Line2D([0], [0], marker="s", color="none", markerfacecolor="none",
               markeredgecolor="#111", markersize=8, label="Existing naloxone supply"),
        Line2D([0], [0], marker="X", color="none", markerfacecolor="#b30000",
               markeredgecolor="white", markersize=9, label="Nightlife venue"),
    ]
    leg1 = ax.legend(handles=cand_handles, title=f"Candidate host sites in Tier 1  (n={len(cand_t1):,})",
                     loc="upper left", fontsize=7.6, title_fontsize=8.5,
                     frameon=True, framealpha=0.95, borderpad=0.7)
    leg1._legend_box.align = "left"
    ax.add_artist(leg1)
    leg2 = ax.legend(handles=ctx_handles, title="Context",
                     loc="lower right", fontsize=7.6, title_fontsize=8.5,
                     frameon=True, framealpha=0.95, borderpad=0.7)
    leg2._legend_box.align = "left"

    add_source_strip(fig, SUPPRESSION_NOTE)
    fig.subplots_adjust(left=0.02, right=0.98, top=0.94, bottom=0.075)
    _save(fig, "figure3")
    _write_caption("figure3",
        title="Figure 3. Recommended naloxone placement strategy for Dallas County.",
        body=(
            "Muted shading distinguishes the three vulnerability tiers from Figure 2. To keep the map legible, "
            f"candidate host storefronts are restricted to the {len(cand_t1):,} sites (of 5,538 county-wide) that "
            "fall within Tier-1 (highest-vulnerability) tracts; symbol colour encodes venue type "
            "(liquor, convenience/corner, barbershop/beauty, grocery, gas station, laundromat, public library, "
            "other). Hollow squares mark existing naloxone supply for contrast. Red crosses mark actual "
            "nightlife venues from the supply inventory; the Deep Ellum and Cedar Springs corridors are labelled "
            "as high-traffic, after-hours distribution opportunities. Candidate sites derive from Dallas County "
            "Open Data business licences, TABC off-premise permits, and OpenStreetMap points of interest."
        ))


# --------------------------------------------------------------------------------------
# IO helpers
# --------------------------------------------------------------------------------------
def _save(fig, stem):
    png = FIGDIR / f"{stem}.png"
    pdf = FIGDIR / f"{stem}.pdf"
    fig.savefig(png, dpi=300, bbox_inches="tight", facecolor="white")
    fig.savefig(pdf, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  wrote {png.name} + {pdf.name}")


def _write_caption(stem, title, body):
    note = ("Note. " + SOURCE_STRIP.replace("Sources:", "Sources for this figure:") +
            " " + SUPPRESSION_NOTE)
    txt = f"{title}\n\n{body}\n\n{note}\n"
    (FIGDIR / f"{stem}_caption.txt").write_text(txt)
    print(f"  wrote {stem}_caption.txt")


if __name__ == "__main__":
    figure1()
    figure2()
    figure3()
    print("Done.")
