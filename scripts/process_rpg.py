#!/usr/bin/env python3
"""
Process a downloaded RPG (Registre Parcellaire Graphique) regional package into
a lightweight department-level maize choropleth for the frontend.

NOT YET RUN AGAINST A FULL DEPARTMENT — but the underlying fetch is verified
working (see rpg_common.py's WFS functions, tested live 2026-08-29). No RPG
download is actually required any more: pass --department instead of
--input to fetch parcels live via IGN's WFS at data.geopf.fr, the same
live-query pattern as Hub'Eau. --input (a local shapefile) still works if
you already downloaded one.

Usage (live, no download):
    python scripts/process_rpg.py --department 28 --year 2024 \
        --grid data/processed/grid_5km.geojson

Usage (local file, if you already have one):
    python scripts/process_rpg.py \
        --input data/raw/rpg/PARCELLES_GRAPHIQUES.shp \
        --year 2023 \
        --output data/processed/departments_maize.geojson \
        --departments-boundary data/processed/departments_france.geojson \
        --grid data/processed/grid_5km.geojson

Requires: geopandas, pandas, shapely, requests (pip install geopandas pandas shapely requests)

Live WFS is best for one or a few departments at a time — see the caveat in
rpg_common.py's module docstring about why a genuine full-France run still
favors a bulk download over paging through tens of millions of parcels.

Part 2 note: passing --grid additionally emits a grid-cell-level maize output
(data/processed/grid_maize.json, cell_id -> value, no duplicated geometry —
see scripts/build_grid.py and the Part 2 spec's Section 2 on why layers key
to grid_cell_id rather than embedding polygons per layer). The department
output above is kept as the default/primary output since the existing
frontend (Part 1) already consumes it — --grid is additive, not a
replacement.

IMPORTANT — verify crop codes before trusting output:
RPG crop codes (CODE_CULTU field) have changed across editions. The codes
below are the ones named in this project's spec (MIS/MID for grain maize,
MCR for a fodder/silage code) but have NOT been verified against an actual
downloaded edition's "notice technique" data dictionary — that PDF ships
alongside every RPG regional package download and is the authoritative
source. Open it and confirm/update MAIZE_CROP_CODES before running this
for real, especially when a new edition is downloaded.
"""

import argparse
import json
import sys
from pathlib import Path

from rpg_common import add_rpg_source_args, resolve_rpg_parcels, overlay_parcels_on_grid

# TODO: verify against the notice technique for the specific RPG edition downloaded.
MAIZE_CROP_CODES = {
    "MIS": "Maize grain and silage (undifferentiated in some editions)",
    "MID": "Sweet maize (maïs doux)",
    "MCR": "Fodder/silage maize (verify — code varies by edition)",
}


def aggregate_by_department(gdf, year, departments_boundary_path, restrict_to_code=None):
    """
    Returns ONLY the departments that actually had parcels in `gdf` — NOT the
    full 96-department boundary file. This matters when called once per
    department (--department mode, e.g. scripts/run_nationwide_maize.sh):
    the original version of this function joined against the FULL boundary
    file every time, producing 95 bogus zero/NaN rows alongside the one real
    department each run, which then clobbered previously-written departments
    in main()'s merge step. Fixed 2026-08-30 before running a 96-department
    batch — caught by reasoning through what a repeated single-department
    run would actually produce, not by a failure in the wild.

    restrict_to_code: when set (--department mode), drop every OTHER
    department code from the result even if parcels for it showed up. This
    matters because a --department fetch is BBOX-limited, and a
    department's bounding box almost always clips slivers of its neighbors
    too — real test with department 28 (Eure-et-Loir) picked up sliver
    parcels sjoin'd into 7 neighboring departments (27, 41, 45, 61, 72, 78,
    91), each PARTIAL/incomplete for that neighbor. Without restricting to
    the requested code, main()'s (code, year) merge would let whichever
    department the loop happens to process LAST among a set of neighbors
    overwrite an already-complete figure with an incomplete sliver. Only
    that department's own dedicated run produces a trustworthy total for it
    (its full area is within its own bbox), so partial neighbor rows are
    just discarded here, not used for anything.
    """
    import geopandas as gpd

    maize = gdf[gdf["CODE_CULTU"].isin(MAIZE_CROP_CODES.keys())]
    print(f"{len(maize)} of {len(gdf)} parcels match maize crop codes.", file=sys.stderr)

    departments = gpd.read_file(departments_boundary_path)
    departments = departments.to_crs(gdf.crs)

    joined = gpd.sjoin(maize, departments[["code", "nom", "geometry"]], how="inner", predicate="within")
    maize_by_dept = joined.groupby("code")["area_ha"].sum().rename("maize_area_ha")

    # SAU (surface agricole utile) per department needs a separate total-parcel-area pass
    # over the same RPG file (all crops, not just maize) to compute % of SAU. Left as an
    # explicit TODO rather than guessed — Agreste publishes SAU totals separately if a
    # faster path is preferred over summing the full RPG file.
    all_parcels_joined = gpd.sjoin(gdf, departments[["code", "geometry"]], how="inner", predicate="within")
    sau_by_dept = all_parcels_joined.groupby("code")["area_ha"].sum().rename("total_declared_area_ha")

    if restrict_to_code:
        maize_by_dept = maize_by_dept[maize_by_dept.index == restrict_to_code]
        sau_by_dept = sau_by_dept[sau_by_dept.index == restrict_to_code]

    departments_with_data = departments[departments["code"].isin(sau_by_dept.index)]
    result = departments_with_data.merge(maize_by_dept, on="code", how="left").merge(sau_by_dept, on="code", how="left")
    result["maize_area_ha"] = result["maize_area_ha"].fillna(0)
    result["maize_pct_of_declared_area"] = (result["maize_area_ha"] / result["total_declared_area_ha"] * 100).round(2)
    result["year"] = year

    return result[["code", "nom", "year", "maize_area_ha", "total_declared_area_ha", "maize_pct_of_declared_area", "geometry"]]


def aggregate_by_grid_cell(gdf, year, grid_path):
    """Grid-cell equivalent of aggregate_by_department, for the Part 2 national grid."""
    all_overlaid = overlay_parcels_on_grid(gdf, grid_path)
    maize_overlaid = all_overlaid[all_overlaid["CODE_CULTU"].isin(MAIZE_CROP_CODES.keys())]

    total_by_cell = all_overlaid.groupby("cell_id")["area_ha"].sum().rename("total_declared_area_ha")
    maize_by_cell = maize_overlaid.groupby("cell_id")["area_ha"].sum().rename("maize_area_ha")

    combined = total_by_cell.to_frame().join(maize_by_cell, how="left")
    combined["maize_area_ha"] = combined["maize_area_ha"].fillna(0)
    combined["maize_pct_of_declared_area"] = (combined["maize_area_ha"] / combined["total_declared_area_ha"] * 100).round(2)
    combined["year"] = year
    return combined.reset_index()


def write_grid_output(grid_result, output_path, department_code):
    """
    Merge new-year grid results into the existing cell_id -> value JSON,
    keeping other years intact. Deliberately not GeoJSON: the grid geometry
    itself (data/processed/grid_5km.geojson) is ~6MB — duplicating it in
    every layer file would be wasteful. Join by cell_id at render time
    instead (see Part 2 spec Section 2).

    IMPORTANT for a multi-department nationwide run (e.g.
    scripts/run_nationwide_maize.sh): a grid cell straddling a department
    border gets a PARTIAL-area record from each department's own WFS fetch
    (parcels are bbox-limited per department). A naive "replace this cell's
    year record" merge would silently drop department A's contribution the
    moment department B's run touches the same border cell — but a naive
    "always accumulate" fix would double-count if the same department is
    ever re-run (e.g. retrying one that failed mid-batch). Both edge cases
    were reasoned through before running a 96-department batch, not
    discovered by a failure in the wild — --department is not necessarily
    disjoint at the grid-cell level even though it is at the RPG-parcel
    level.

    Fix: keep per-department contributions in a separate top-level
    "_department_contributions" structure (keyed by cell_id -> year ->
    department_code), not inside "cells" — every other Part 2 layer file
    keeps cells[cell_id] as a plain list of year-records (see
    cluster_analysis.py's generic loader, which assumes exactly that shape
    for every grid_*.json file), so this keeps that convention intact and
    does the department-level bookkeeping alongside it instead of nesting
    inside it.
    """
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    data = {"grid_cell_size_km": 5, "layer": "maize", "cells": {}, "_department_contributions": {}}
    if out_path.exists():
        with open(out_path) as f:
            data = json.load(f)
            data.setdefault("_department_contributions", {})

    cells = data["cells"]
    contributions = data["_department_contributions"]

    year = int(grid_result["year"].iloc[0])
    year_key = str(year)
    for row in grid_result.to_dict("records"):
        cell_id = row.pop("cell_id")
        contributions.setdefault(cell_id, {}).setdefault(year_key, {})[department_code] = {
            "maize_area_ha": row["maize_area_ha"],
            "total_declared_area_ha": row["total_declared_area_ha"],
        }

    # Re-derive this year's public record for every touched cell by summing its
    # per-department contributions (so accumulation and idempotent re-runs both work).
    for cell_id, dept_contribs_by_year in contributions.items():
        dept_contribs = dept_contribs_by_year.get(year_key)
        if not dept_contribs:
            continue
        maize_total = sum(c["maize_area_ha"] for c in dept_contribs.values())
        declared_total = sum(c["total_declared_area_ha"] for c in dept_contribs.values())
        record = {
            "year": year,
            "maize_area_ha": round(maize_total, 4),
            "total_declared_area_ha": round(declared_total, 4),
            "maize_pct_of_declared_area": round(maize_total / declared_total * 100, 2) if declared_total else None,
        }
        existing_records = [r for r in cells.get(cell_id, []) if r.get("year") != year]
        existing_records.append(record)
        cells[cell_id] = existing_records

    with open(out_path, "w") as f:
        json.dump(data, f, separators=(",", ":"))

    print(f"Wrote grid-cell maize values for {len(grid_result)} cells (year {year}, department {department_code}) to {out_path}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_rpg_source_args(parser)
    parser.add_argument("--year", required=True, type=int, help="RPG campaign year (declared crop year)")
    parser.add_argument("--output", default="data/processed/departments_maize.geojson")
    parser.add_argument("--departments-boundary", default="data/processed/departments_france.geojson")
    parser.add_argument("--grid", default=None, help="Path to a grid file from scripts/build_grid.py — if passed, also writes data/processed/grid_maize.json")
    args = parser.parse_args()

    gdf = resolve_rpg_parcels(args, args.year)
    result = aggregate_by_department(gdf, args.year, args.departments_boundary, restrict_to_code=args.department)

    if args.grid:
        grid_result = aggregate_by_grid_cell(gdf, args.year, args.grid)
        contribution_key = args.department or args.input
        write_grid_output(grid_result, "data/processed/grid_maize.json", contribution_key)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Merge into existing output, replacing only the (code, year) pairs this run produced —
    # NOT everything matching this year, which would drop other departments already written
    # for the same year (the bug this comment replaces: see aggregate_by_department's docstring).
    new_geojson = json.loads(result.to_json())
    new_keys = {(feat["properties"]["code"], feat["properties"]["year"]) for feat in new_geojson["features"]}

    existing_features = []
    if out_path.exists():
        with open(out_path) as f:
            existing = json.load(f)
        existing_features = [
            feat for feat in existing.get("features", [])
            if (feat["properties"].get("code"), feat["properties"].get("year")) not in new_keys
        ]

    combined = {"type": "FeatureCollection", "features": existing_features + new_geojson["features"]}

    with open(out_path, "w") as f:
        json.dump(combined, f, ensure_ascii=False)

    print(f"Wrote {len(new_geojson['features'])} department feature(s) for {args.year} to {out_path} ({len(combined['features'])} total).", file=sys.stderr)


if __name__ == "__main__":
    main()
