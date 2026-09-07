#!/usr/bin/env python3
"""
Fetch one department's RPG parcels ONCE and compute multiple layers from
that single fetch, instead of separate scripts each re-fetching the same
(potentially 100,000+) parcels independently. Built 2026-08-30 after
noticing department 12 alone was 477,464 parcels — running maize,
field-size-trend, and diversity-index as separate nationwide loops would
fetch that same data three times.

Computes, per department, per year:
- Maize % (department-level AND grid-cell-level) — same logic as process_rpg.py
- Field size / boundary density (grid-cell-level) — same logic as compute_field_size_trend.py
- Crop diversity index, SINGLE-YEAR components only (compositional, functional,
  patch — NOT temporal, which needs multiple years' parcels per department and
  would defeat the point of this script). Run compute_diversity_index.py
  separately with --history-years for the temporal component once you have
  specific departments/years you want that for.

Grid-cell cross-department accumulation: a grid cell can legitimately span
two departments (this is normal — the grid doesn't respect department
borders). For ADDITIVE quantities (maize area, parcel count, boundary
length) this script sums contributions correctly and re-derives percentages/
rates, same fix as process_rpg.py's write_grid_output. For the diversity
index's normalized 0-1 scores (not simple sums), this script uses an
AREA-WEIGHTED AVERAGE of each department's contribution for a shared cell —
an approximation, not a re-computation of Simpson's index from pooled raw
crop-area data across both departments. Documented here rather than
silently approximated: for a fully correct value at a cross-department
cell, recompute compositional/functional diversity from both departments'
raw parcel data pooled together, not from the two departments' already-
normalized scores averaged. Left as a refinement — the approximation error
is bounded (both scores are already 0-1) and only affects grid cells that
straddle a department boundary, not the (large) majority of cells fully
inside one department.

Riparian buffer compliance is NOT included here — it needs an additional
hydrography fetch and buffering/intersection step, and its accumulation
semantics (percent-overlap needs area-weighted combination too, per-segment
not per-parcel) are enough of a separate concern to keep it in
compute_riparian_buffers.py's own nationwide pass rather than folding in
here and making this script's scope harder to reason about.

Usage:
    python scripts/process_department_combined.py --department 28 --year 2024

Requires: geopandas, pandas, shapely, requests
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from rpg_common import fetch_rpg_parcels_wfs, overlay_parcels_on_grid, load_crop_family_lookup
from process_rpg import MAIZE_CROP_CODES, aggregate_by_department, aggregate_by_grid_cell, write_grid_output as write_maize_grid_output
from compute_field_size_trend import compute_field_size_metrics
from compute_diversity_index import (
    compute_compositional, compute_functional, compute_patch_structure,
)

N_CROP_CODES = 143
CROP_FAMILY_LOOKUP_PATH = "data/reference/crop_code_to_family.csv"


def load_surface_category_lookup(path=CROP_FAMILY_LOOKUP_PATH):
    """RPG's coarse arable/permanent-grassland/permanent-crop split (TA/PP/CP/SB) —
    same reference file as the crop-family lookup, different column."""
    df = pd.read_csv(path)
    return dict(zip(df["rpg_code"], df["surface_category"]))


def load_crop_name_lookup(path=CROP_FAMILY_LOOKUP_PATH):
    df = pd.read_csv(path)
    return dict(zip(df["rpg_code"], df["crop_name_fr"]))


def herfindahl_index(area_by_code):
    """Standard concentration index: sum of squared market shares, 0-1 scale
    (1/n_crops if perfectly even, 1.0 if a single crop is 100% of area).
    More rigorous than "top-3 %" alone since it accounts for the whole
    distribution, not just the head — included alongside top-3 rather than
    instead of it since top-3-with-names is far more readable at a glance."""
    total = area_by_code.sum()
    if total == 0:
        return None
    shares = area_by_code / total
    return round(float((shares ** 2).sum()), 4)


def aggregate_crop_breakdown(gdf, year, family_lookup, category_lookup, department_code):
    """
    Field-count AND area breakdown by crop family, requested directly: 'number
    of fields dedicated to maize vs other crops, full breakdown, average field
    size'. Distinct from the maize-only department aggregate in process_rpg.py
    (area-% only) and from the diversity index (a single compositional score,
    not a readable breakdown) — this is meant to be read as a table, not
    reduced to one number.
    """
    df = gdf.copy()
    df["crop_family"] = df["CODE_CULTU"].map(family_lookup).fillna("unclassified")
    df["surface_category"] = df["CODE_CULTU"].map(category_lookup).fillna("unclassified")
    is_maize = df["CODE_CULTU"].isin(MAIZE_CROP_CODES.keys())

    def field_size_stats(areas):
        stats = {
            "field_count": int(len(areas)),
            "area_ha": round(float(areas.sum()), 2),
            "mean_field_size_ha": round(float(areas.mean()), 3) if len(areas) else None,
            "median_field_size_ha": round(float(areas.median()), 3) if len(areas) else None,
        }
        if len(areas) >= 10:  # percentiles are noisy/meaningless on tiny samples
            stats["percentiles_ha"] = {
                p: round(float(areas.quantile(p / 100)), 3) for p in (10, 25, 50, 75, 90)
            }
        return stats

    by_family = {}
    for family, sub in df.groupby("crop_family"):
        by_family[family] = field_size_stats(sub["area_ha"])

    by_surface_category = {}
    for cat, sub in df.groupby("surface_category"):
        by_surface_category[cat] = field_size_stats(sub["area_ha"])

    # Crop concentration: top-3 individual crops (not families) by area, plus
    # the full Herfindahl index across every crop code present.
    name_lookup = load_crop_name_lookup(CROP_FAMILY_LOOKUP_PATH)
    area_by_crop_code = df.groupby("CODE_CULTU")["area_ha"].sum().sort_values(ascending=False)
    total_area = area_by_crop_code.sum()
    top3 = [
        {
            "crop_code": code,
            "crop_name": name_lookup.get(code, code),
            "area_ha": round(float(area), 2),
            "pct_of_total_area": round(float(area) / total_area * 100, 2) if total_area else None,
        }
        for code, area in area_by_crop_code.head(3).items()
    ]

    return {
        "code": department_code,
        "year": year,
        "overall": field_size_stats(df["area_ha"]),
        "maize": field_size_stats(df.loc[is_maize, "area_ha"]),
        "by_crop_family": by_family,
        "by_surface_category": by_surface_category,
        "top3_crops_by_area": top3,
        "top3_pct_of_total_area": round(sum(c["pct_of_total_area"] for c in top3), 2) if top3 else None,
        "herfindahl_index": herfindahl_index(area_by_crop_code),
    }


def write_crop_breakdown_output(record, output_path):
    """Merge-safe like the maize output — dedupe by (code, year)."""
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if out_path.exists():
        with open(out_path) as f:
            existing = json.load(f).get("departments", [])
    existing = [r for r in existing if not (r["code"] == record["code"] and r["year"] == record["year"])]
    existing.append(record)
    with open(out_path, "w") as f:
        json.dump({"layer": "crop_breakdown", "departments": existing}, f, ensure_ascii=False)
    print(f"Wrote crop breakdown for department {record['code']} to {out_path}", file=sys.stderr)


def write_field_size_grid_output(field_size_df, department_code, year, output_path):
    """
    Same cross-department accumulation problem as process_rpg.py's maize
    output, but for AVERAGES (mean/median parcel area, boundary density)
    rather than a simple sum — re-derives them from underlying per-
    department sums (total area, total boundary length, parcel count)
    rather than naively averaging two already-computed means, which would
    be wrong (unweighted average of two means with different sample sizes).
    """
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    data = {"grid_cell_size_km": 5, "layer": "field_size_trend", "cells": {}, "_department_contributions": {}}
    if out_path.exists():
        with open(out_path) as f:
            data = json.load(f)
            data.setdefault("_department_contributions", {})

    cells = data["cells"]
    contributions = data["_department_contributions"]
    year_key = str(year)

    for row in field_size_df.to_dict("records"):
        cell_id = row["cell_id"]
        # Reconstruct sums from the mean/count this function receives (computed
        # from the same underlying parcel set, so this round-trip is exact).
        total_area_ha = row["mean_parcel_area_ha"] * row["parcel_count"]
        total_boundary_m = row["boundary_density_m_per_km2"] * (total_area_ha / 100)  # ha -> km2
        contributions.setdefault(cell_id, {}).setdefault(year_key, {})[department_code] = {
            "total_area_ha": total_area_ha,
            "total_boundary_m": total_boundary_m,
            "parcel_count": row["parcel_count"],
            "median_parcel_area_ha": row["median_parcel_area_ha"],  # median can't be exactly recombined — see note below
        }

    for cell_id, by_year in contributions.items():
        dept_contribs = by_year.get(year_key)
        if not dept_contribs:
            continue
        total_area = sum(c["total_area_ha"] for c in dept_contribs.values())
        total_boundary = sum(c["total_boundary_m"] for c in dept_contribs.values())
        total_count = sum(c["parcel_count"] for c in dept_contribs.values())
        area_km2 = total_area / 100
        # Median across a cross-department cell isn't exactly recoverable without raw parcel
        # areas — approximated here as the count-weighted mean of each department's own median,
        # which is a documented approximation, not the true pooled median.
        weighted_median = sum(c["median_parcel_area_ha"] * c["parcel_count"] for c in dept_contribs.values()) / total_count if total_count else None

        record = {
            "year": year,
            "mean_parcel_area_ha": round(total_area / total_count, 3) if total_count else None,
            "median_parcel_area_ha": round(weighted_median, 3) if weighted_median is not None else None,
            "parcel_count": total_count,
            "boundary_density_m_per_km2": round(total_boundary / area_km2, 1) if area_km2 else None,
        }
        existing = [r for r in cells.get(cell_id, []) if r.get("year") != year]
        existing.append(record)
        cells[cell_id] = existing

    with open(out_path, "w") as f:
        json.dump(data, f, separators=(",", ":"))

    print(f"Wrote field-size grid values (year {year}, department {department_code}) to {out_path}", file=sys.stderr)


def write_diversity_grid_output(diversity_df, department_code, year, output_path):
    """
    Area-weighted average across departments for a shared cell — see module
    docstring's discussion of why this is an approximation for the
    compositional/functional/patch scores, not an exact recomputation.
    """
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    data = {"grid_cell_size_km": 5, "layer": "diversity_index", "cells": {}, "_department_contributions": {}}
    if out_path.exists():
        with open(out_path) as f:
            data = json.load(f)
            data.setdefault("_department_contributions", {})

    cells = data["cells"]
    contributions = data["_department_contributions"]
    year_key = str(year)

    for cell_id, row in diversity_df.iterrows():
        contributions.setdefault(cell_id, {}).setdefault(year_key, {})[department_code] = {
            "diversity_compositional": row.get("diversity_compositional"),
            "diversity_functional": row.get("diversity_functional"),
            "diversity_patch": row.get("diversity_patch"),
            "weight": row.get("_weight", 1.0),
        }

    for cell_id, by_year in contributions.items():
        dept_contribs = by_year.get(year_key)
        if not dept_contribs:
            continue
        total_weight = sum(c["weight"] for c in dept_contribs.values())
        record = {"year": year}
        for col in ("diversity_compositional", "diversity_functional", "diversity_patch"):
            values = [(c[col], c["weight"]) for c in dept_contribs.values() if c[col] is not None]
            record[col] = round(sum(v * w for v, w in values) / sum(w for _, w in values), 4) if values else None
        existing = [r for r in cells.get(cell_id, []) if r.get("year") != year]
        existing.append(record)
        cells[cell_id] = existing

    with open(out_path, "w") as f:
        json.dump(data, f, separators=(",", ":"))

    print(f"Wrote diversity-index grid values (year {year}, department {department_code}) to {out_path}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--department", required=True, help="INSEE department code")
    parser.add_argument("--year", required=True, type=int)
    parser.add_argument("--grid", default="data/processed/grid_5km.geojson")
    parser.add_argument("--departments-boundary", default="data/processed/departments_france.geojson")
    parser.add_argument("--max-features", type=int, default=None)
    args = parser.parse_args()

    gdf = fetch_rpg_parcels_wfs(args.department, args.year, max_features=args.max_features)
    print(f"Fetched {len(gdf)} parcels for department {args.department}, computing all layers from this one fetch ...", file=sys.stderr)

    # Compute the expensive parcel-vs-grid geometric overlay exactly ONCE and reuse it for
    # maize-grid, and diversity — this used to be computed twice independently (once inside
    # aggregate_by_grid_cell, once again for diversity), which for a normal department just
    # wastes a little time but for a huge, heavily-fragmented department (Finistère: 300k+
    # tiny parcels) turned a several-hour job into 6+ hours by doing the same costly GEOS
    # sweep-line intersection twice. Found by profiling a stuck-looking run with `sample`
    # and seeing it deep in libgeos twice in a row rather than once.
    overlaid = overlay_parcels_on_grid(gdf, args.grid)

    # --- Maize (department + grid) ---
    dept_result = aggregate_by_department(gdf, args.year, args.departments_boundary, restrict_to_code=args.department)
    maize_overlaid = overlaid[overlaid["CODE_CULTU"].isin(MAIZE_CROP_CODES.keys())]
    total_by_cell = overlaid.groupby("cell_id")["area_ha"].sum().rename("total_declared_area_ha")
    maize_by_cell = maize_overlaid.groupby("cell_id")["area_ha"].sum().rename("maize_area_ha")
    grid_maize_result = total_by_cell.to_frame().join(maize_by_cell, how="left")
    grid_maize_result["maize_area_ha"] = grid_maize_result["maize_area_ha"].fillna(0)
    grid_maize_result["maize_pct_of_declared_area"] = (grid_maize_result["maize_area_ha"] / grid_maize_result["total_declared_area_ha"] * 100).round(2)
    grid_maize_result["year"] = args.year
    grid_maize_result = grid_maize_result.reset_index()
    write_maize_grid_output(grid_maize_result, "data/processed/grid_maize.json", args.department)

    out_path = Path("data/processed/departments_maize.geojson")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    new_geojson = json.loads(dept_result.to_json())
    new_keys = {(f["properties"]["code"], f["properties"]["year"]) for f in new_geojson["features"]}
    existing_features = []
    if out_path.exists():
        with open(out_path) as f:
            existing_features = [
                feat for feat in json.load(f).get("features", [])
                if (feat["properties"].get("code"), feat["properties"].get("year")) not in new_keys
            ]
    with open(out_path, "w") as f:
        json.dump({"type": "FeatureCollection", "features": existing_features + new_geojson["features"]}, f, ensure_ascii=False)
    print(f"Maize: wrote department {args.department} to {out_path}", file=sys.stderr)

    # --- Field size trend (grid) ---
    field_size_df = compute_field_size_metrics(gdf, args.grid)
    write_field_size_grid_output(field_size_df, args.department, args.year, "data/processed/grid_field_size.json")

    # --- Diversity index, single-year components (grid) ---
    family_lookup = load_crop_family_lookup(CROP_FAMILY_LOOKUP_PATH)
    n_families = len(set(family_lookup.values()))

    # --- Crop breakdown: field count + area by crop family, department-level ---
    category_lookup = load_surface_category_lookup(CROP_FAMILY_LOOKUP_PATH)
    crop_breakdown = aggregate_crop_breakdown(gdf, args.year, family_lookup, category_lookup, args.department)
    write_crop_breakdown_output(crop_breakdown, "data/processed/departments_crop_breakdown.json")
    comp = compute_compositional(overlaid, N_CROP_CODES)
    func = compute_functional(overlaid, family_lookup, n_families)
    patch = compute_patch_structure(field_size_df)
    diversity_df = pd.concat([comp, func, patch], axis=1)
    diversity_df["_weight"] = overlaid.groupby("cell_id")["area_ha"].sum().reindex(diversity_df.index).fillna(1.0)
    write_diversity_grid_output(diversity_df, args.department, args.year, "data/processed/grid_diversity_index.json")

    print(f"Done: department {args.department}, year {args.year} — maize, field size, and single-year diversity all written.", file=sys.stderr)


if __name__ == "__main__":
    main()
