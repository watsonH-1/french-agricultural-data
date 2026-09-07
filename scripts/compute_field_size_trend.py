#!/usr/bin/env python3
"""
Part 2, Section 7.1 — field size / boundary density (landscape structure),
per grid cell, per year. Pure RPG reprocessing, same data as maize and the
diversity index — no new source needed.

This is distinct from "average farm size" (Section 7.2, an economic-unit
concept from Agreste's agricultural census, not a landscape metric) — do not
conflate the two outputs.

No download needed — pass --department to fetch parcels live via IGN's WFS
(see rpg_common.py, verified working 2026-08-29), or --input if you already
have a local RPG file.

Usage (live, no download):
    python scripts/compute_field_size_trend.py --department 28 --year 2024

Usage (local file):
    python scripts/compute_field_size_trend.py \
        --input data/raw/rpg/PARCELLES_GRAPHIQUES.shp \
        --year 2024 \
        --grid data/processed/grid_5km.geojson

Requires: geopandas, pandas, shapely, requests
"""

import argparse
import json
import sys
from pathlib import Path

from rpg_common import add_rpg_source_args, resolve_rpg_parcels


def compute_field_size_metrics(gdf, grid_path):
    """
    Per grid cell: mean and median parcel area (ha), and total parcel-
    boundary length per km² (a structural-complexity proxy — more boundary
    length per unit area generally means smaller, more numerous fields,
    which often tracks hedgerow-adjacent landscape structure even though
    RPG itself doesn't record hedgerows directly).

    Uses whole-parcel assignment by centroid rather than the area-weighted
    overlay used elsewhere in Part 2: splitting a parcel across a cell
    boundary would double-count its perimeter on both sides, which would
    bias boundary-density upward right along every cell edge.
    """
    import geopandas as gpd

    grid = gpd.read_file(grid_path)
    grid_crs_gdf = grid.to_crs(gdf.crs) if grid.crs != gdf.crs else grid

    parcels = gdf.copy()
    parcels["boundary_length_m"] = parcels.geometry.length
    parcels["centroid"] = parcels.geometry.centroid
    centroids = parcels.set_geometry("centroid")[["area_ha", "boundary_length_m", "centroid"]].set_geometry("centroid")

    joined = gpd.sjoin(centroids, grid_crs_gdf[["cell_id", "geometry"]], how="inner", predicate="within")

    grid_area_km2 = grid.set_index("cell_id")["area_km2"]

    grouped = joined.groupby("cell_id").agg(
        mean_parcel_area_ha=("area_ha", "mean"),
        median_parcel_area_ha=("area_ha", "median"),
        parcel_count=("area_ha", "count"),
        total_boundary_length_m=("boundary_length_m", "sum"),
    )
    grouped = grouped.join(grid_area_km2, how="left")
    grouped["boundary_density_m_per_km2"] = (grouped["total_boundary_length_m"] / grouped["area_km2"]).round(1)
    grouped["mean_parcel_area_ha"] = grouped["mean_parcel_area_ha"].round(3)
    grouped["median_parcel_area_ha"] = grouped["median_parcel_area_ha"].round(3)

    return grouped[["mean_parcel_area_ha", "median_parcel_area_ha", "parcel_count", "boundary_density_m_per_km2"]].reset_index()


def write_grid_output(result_df, year, output_path):
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cells = {}
    if out_path.exists():
        with open(out_path) as f:
            cells = json.load(f).get("cells", {})

    for cell_id in list(cells.keys()):
        cells[cell_id] = [rec for rec in cells[cell_id] if rec.get("year") != year]

    for row in result_df.to_dict("records"):
        cell_id = row.pop("cell_id")
        row["year"] = year
        cells.setdefault(cell_id, []).append(row)

    with open(out_path, "w") as f:
        json.dump({"grid_cell_size_km": 5, "layer": "field_size_trend", "cells": cells}, f, separators=(",", ":"))

    print(f"Wrote field-size metrics for {len(result_df)} cells (year {year}) to {out_path}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_rpg_source_args(parser)
    parser.add_argument("--year", required=True, type=int)
    parser.add_argument("--grid", default="data/processed/grid_5km.geojson")
    parser.add_argument("--output", default="data/processed/grid_field_size.json")
    args = parser.parse_args()

    gdf = resolve_rpg_parcels(args, args.year)
    result = compute_field_size_metrics(gdf, args.grid)
    write_grid_output(result, args.year, args.output)


if __name__ == "__main__":
    main()
