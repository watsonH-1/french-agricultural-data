#!/usr/bin/env python3
"""
Part 2, Section 3 — custom crop diversity index, per grid cell, per year.
Four sub-components (compositional, temporal/rotation, patch structure,
functional), each normalized 0-1 and kept as separate columns, combined as
a weighted geometric mean. All four are written out — the clustering step
(Section 9) needs the raw sub-scores, not just the combined number.

No RPG download needed — the grid (scripts/build_grid.py, already generated
for real, see data/processed/grid_5km.geojson) and the crop-family lookup
table (data/reference/crop_code_to_family.csv, built for real from the
official 2025 "codes cultures PAC" reference) are both ready. Pass
--department to fetch parcels live via IGN's WFS (verified working
2026-08-29, see rpg_common.py) instead of --input for a local file.

Usage (live, no download):
    # Compositional, patch, and functional components need only one year:
    python scripts/compute_diversity_index.py --department 28 --year 2024

    # The temporal component additionally needs several years for the same
    # department, sharing ID_PARCEL:
    python scripts/compute_diversity_index.py --department 28 --year 2024 \
        --history-years 2020 2021 2022 2023

Usage (local files, if you already have them):
    python scripts/compute_diversity_index.py \
        --input data/raw/rpg/PARCELLES_GRAPHIQUES_2024.shp --year 2024 \
        --history data/raw/rpg/PARCELLES_GRAPHIQUES_2020.shp \
                  data/raw/rpg/PARCELLES_GRAPHIQUES_2021.shp \
                  data/raw/rpg/PARCELLES_GRAPHIQUES_2022.shp \
                  data/raw/rpg/PARCELLES_GRAPHIQUES_2023.shp

Requires: geopandas, pandas, shapely, requests

Design notes / deviations from a literal read of the spec, and why:

- Output is data/processed/grid_diversity_index.json keyed by cell_id, NOT
  a .geojson with embedded polygons — see Section 2's explicit "output
  grid_cell_id -> value ... joined back to the grid at render time"
  directive. The grid itself (data/processed/grid_5km.geojson) is ~6MB;
  duplicating that geometry in every one of Part 2's layer files would
  make the repo unnecessarily heavy for no benefit, since the frontend can
  join on cell_id at render time instead.
- Normalization for the compositional and functional Simpson scores uses
  (D-1)/(N-1) where N is the total number of distinct crop codes (143, from
  the reference table) or crop families (16) SEEN NATIONALLY, not per-cell —
  normalizing against a per-cell N would make cells with naturally fewer
  possible crop types (e.g. a cell mostly under permanent grassland) look
  artificially more "diverse" than they are relative to the rest of France.
- The spec doesn't specify exactly how the two patch-structure sub-metrics
  (mean parcel area, boundary density) combine into one 0-1 score before
  going into the geometric mean. This script min-max normalizes each
  across all cells nationally for that year, then averages them — documented
  here rather than left implicit, since it's a real judgment call.
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from rpg_common import (
    add_rpg_source_args, resolve_rpg_parcels, load_rpg_parcels,
    fetch_rpg_parcels_wfs, load_crop_family_lookup, overlay_parcels_on_grid,
)
from compute_field_size_trend import compute_field_size_metrics

DEFAULT_WEIGHTS = {"comp": 0.25, "temp": 0.25, "patch": 0.25, "func": 0.25}


def simpsons_diversity(overlaid_df, group_col, area_col="area_ha"):
    """
    D = 1 / sum(p_i^2) per cell_id, where p_i is group i's share of total
    area in that cell. Returns a Series indexed by cell_id.
    """
    totals = overlaid_df.groupby("cell_id")[area_col].transform("sum")
    shares_sq = (overlaid_df[area_col] / totals) ** 2
    sum_sq = shares_sq.groupby(overlaid_df["cell_id"]).sum()
    return 1 / sum_sq


def normalize_simpson(d_series, n_categories):
    """(D - 1) / (N - 1), clipped to [0, 1] — see module docstring for why N is national, not per-cell."""
    normalized = (d_series - 1) / (n_categories - 1)
    return normalized.clip(lower=0, upper=1)


def compute_compositional(overlaid_df, n_crop_codes):
    d = simpsons_diversity(overlaid_df, "CODE_CULTU")
    return normalize_simpson(d, n_crop_codes).rename("diversity_compositional")


def compute_functional(overlaid_df, crop_family_lookup, n_families):
    df = overlaid_df.copy()
    df["crop_family"] = df["CODE_CULTU"].map(crop_family_lookup).fillna("other")
    d = simpsons_diversity(df, "crop_family")
    return normalize_simpson(d, n_families).rename("diversity_functional")


def compute_patch_structure(field_size_df):
    """field_size_df: output of compute_field_size_trend.compute_field_size_metrics, indexed by cell_id."""
    df = field_size_df.set_index("cell_id")
    inv_area = 1 / df["mean_parcel_area_ha"]
    inv_area_norm = (inv_area - inv_area.min()) / (inv_area.max() - inv_area.min())
    density = df["boundary_density_m_per_km2"]
    density_norm = (density - density.min()) / (density.max() - density.min())
    return ((inv_area_norm + density_norm) / 2).rename("diversity_patch")


def compute_temporal(history_gdfs, current_year_gdf, grid_path, n_crop_codes):
    """
    Section 3.2 — per-parcel Simpson's index on crop-history frequency
    across the given years (via ID_PARCEL), averaged to the cell as the
    mean across parcels whose ID was found in at least 2 of the years.

    history_gdfs: list of already-loaded GeoDataFrames (one per prior year,
    from either a local file or a live WFS fetch — see main()), each with
    at least ID_PARCEL and CODE_CULTU columns.

    Caveat (per spec Section 4/3.2): RPG parcel IDs can shift between
    editions. Parcels whose ID_PARCEL isn't found across years are silently
    dropped from this component rather than guessed at — read IGN's
    parcel-matching documentation for the specific editions/years used here
    before trusting the coverage this achieves.
    """
    import geopandas as gpd

    if "ID_PARCEL" not in current_year_gdf.columns:
        raise ValueError("current_year_gdf has no ID_PARCEL column — cannot compute temporal diversity without stable parcel IDs.")

    history_frames = [current_year_gdf[["ID_PARCEL", "CODE_CULTU"]]]
    for gdf in history_gdfs:
        if "ID_PARCEL" not in gdf.columns:
            print("Warning: a history year has no ID_PARCEL column, skipping it for temporal diversity.", file=sys.stderr)
            continue
        history_frames.append(gdf[["ID_PARCEL", "CODE_CULTU"]])

    combined = pd.concat(history_frames, ignore_index=True)
    coverage = combined.groupby("ID_PARCEL").size()
    matched_ids = coverage[coverage >= 2].index
    print(f"{len(matched_ids)} of {combined['ID_PARCEL'].nunique()} parcel IDs found in >=2 of {len(history_frames)} years.", file=sys.stderr)

    matched = combined[combined["ID_PARCEL"].isin(matched_ids)]
    freq = matched.groupby(["ID_PARCEL", "CODE_CULTU"]).size().rename("n").reset_index()
    freq["total"] = freq.groupby("ID_PARCEL")["n"].transform("sum")
    freq["q_sq"] = (freq["n"] / freq["total"]) ** 2
    d_per_parcel = 1 / freq.groupby("ID_PARCEL")["q_sq"].sum()

    # Assign each matched parcel to a grid cell via its current-year centroid.
    parcels_with_id = current_year_gdf[current_year_gdf["ID_PARCEL"].isin(matched_ids)].copy()
    parcels_with_id["centroid"] = parcels_with_id.geometry.centroid
    centroids = parcels_with_id.set_geometry("centroid")[["ID_PARCEL", "centroid"]].set_geometry("centroid")

    grid = gpd.read_file(grid_path)
    grid = grid.to_crs(centroids.crs) if grid.crs != centroids.crs else grid
    joined = gpd.sjoin(centroids, grid[["cell_id", "geometry"]], how="inner", predicate="within")
    joined = joined.merge(d_per_parcel.rename("d_temp"), left_on="ID_PARCEL", right_index=True)

    d_by_cell = joined.groupby("cell_id")["d_temp"].mean()
    return normalize_simpson(d_by_cell, n_crop_codes).rename("diversity_temporal")


def combine_geometric_mean(components_df, weights):
    """Custom_Index = D_comp^w1 * D_temp^w2 * D_patch^w3 * D_func^w4, weights summing to 1."""
    cols = {"comp": "diversity_compositional", "temp": "diversity_temporal", "patch": "diversity_patch", "func": "diversity_functional"}
    log_sum = sum(weights[k] * np.log(components_df[cols[k]].clip(lower=1e-6)) for k in weights)
    return np.exp(log_sum).rename("diversity_index_combined")


def write_grid_output(df, year, output_path):
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cells = {}
    if out_path.exists():
        with open(out_path) as f:
            cells = json.load(f).get("cells", {})

    for cell_id in list(cells.keys()):
        cells[cell_id] = [rec for rec in cells[cell_id] if rec.get("year") != year]

    for cell_id, row in df.iterrows():
        rec = {k: (None if v is None or v != v else round(float(v), 4)) for k, v in row.items()}  # NaN/None -> null
        rec["year"] = year
        cells.setdefault(cell_id, []).append(rec)

    with open(out_path, "w") as f:
        json.dump({"grid_cell_size_km": 5, "layer": "diversity_index", "cells": cells}, f, separators=(",", ":"))

    print(f"Wrote diversity index for {len(df)} cells (year {year}) to {out_path}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_rpg_source_args(parser)
    parser.add_argument("--year", required=True, type=int)
    parser.add_argument("--grid", default="data/processed/grid_5km.geojson")
    parser.add_argument("--crop-family-lookup", default="data/reference/crop_code_to_family.csv")
    parser.add_argument("--history", nargs="*", default=[], help="Additional years' local RPG files (same edition lineage) for the temporal component — local-file mode only.")
    parser.add_argument("--history-years", nargs="*", type=int, default=[], help="Additional years to fetch live via WFS for the temporal component — --department mode only, same department as --department.")
    parser.add_argument("--output", default="data/processed/grid_diversity_index.json")
    parser.add_argument("--weight-comp", type=float, default=DEFAULT_WEIGHTS["comp"])
    parser.add_argument("--weight-temp", type=float, default=DEFAULT_WEIGHTS["temp"])
    parser.add_argument("--weight-patch", type=float, default=DEFAULT_WEIGHTS["patch"])
    parser.add_argument("--weight-func", type=float, default=DEFAULT_WEIGHTS["func"])
    args = parser.parse_args()

    weights = {"comp": args.weight_comp, "temp": args.weight_temp, "patch": args.weight_patch, "func": args.weight_func}
    total_weight = sum(weights.values())
    if abs(total_weight - 1.0) > 1e-6:
        print(f"Warning: weights sum to {total_weight}, not 1.0 — normalizing.", file=sys.stderr)
        weights = {k: v / total_weight for k, v in weights.items()}

    family_lookup = load_crop_family_lookup(args.crop_family_lookup)
    n_crop_codes = 143  # from data/reference/crop_code_to_family.csv (2025 RPG nomenclature) — update if the lookup table is regenerated from a different edition
    n_families = len(set(family_lookup.values()))

    gdf = resolve_rpg_parcels(args, args.year, keep_columns=["ID_PARCEL", "CODE_CULTU"])
    overlaid = overlay_parcels_on_grid(gdf, args.grid)

    comp = compute_compositional(overlaid, n_crop_codes)
    func = compute_functional(overlaid, family_lookup, n_families)
    field_size = compute_field_size_metrics(gdf, args.grid)
    patch = compute_patch_structure(field_size)

    parts = [comp, func, patch]

    if args.history:
        history_gdfs = [load_rpg_parcels(p, keep_columns=["ID_PARCEL", "CODE_CULTU"]) for p in args.history]
    elif args.history_years:
        if not args.department:
            parser.error("--history-years requires --department (same department, different years)")
        history_gdfs = [fetch_rpg_parcels_wfs(args.department, y) for y in args.history_years]
    else:
        history_gdfs = None

    if history_gdfs:
        temp = compute_temporal(history_gdfs, gdf, args.grid, n_crop_codes)
        parts.append(temp)
    else:
        print("No --history/--history-years passed — skipping the temporal component for this run (Section 3.2 needs several years' RPG data).", file=sys.stderr)
        temp = pd.Series(dtype=float, name="diversity_temporal")
        parts.append(temp)

    combined_df = pd.concat(parts, axis=1)
    combined_df["diversity_index_combined"] = combine_geometric_mean(combined_df.fillna(combined_df.mean()), weights) if history_gdfs else None

    write_grid_output(combined_df, args.year, args.output)


if __name__ == "__main__":
    main()
