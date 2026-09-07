#!/usr/bin/env python3
"""
Joins every processed department-level layer into one master table --
the single file meant to be used without touching any other script.
One row per department (96 rows, metropolitan France), one column per metric.

Deliberately excludes: groundwater (invalid dataset, see docs/GOTCHAS.md),
grid-cell-only layers (field-size trend, diversity index -- no department
rollup exists yet), and crop-breakdown (only 24/96 departments, would leave
72 rows null on those columns).

Usage: python3 scripts/build_master_table.py
Output: data/processed/departments_master.csv and .geojson
"""
import csv
import json
from pathlib import Path

D = Path("data/processed")


def load_geojson(path):
    with open(path) as f:
        return json.load(f)["features"]


def main():
    france = load_geojson(D / "departments_france.geojson")
    rows = {f["properties"]["code"]: {"code": f["properties"]["code"], "name": f["properties"]["nom"]} for f in france}

    pop = {f["properties"]["code"]: f["properties"] for f in load_geojson(D / "population_change.geojson")}
    for code, r in rows.items():
        p = pop.get(code, {})
        r["population_2023"] = p.get("population_to")
        r["population_pct_change_2017_2023"] = p.get("population_pct_change")

    areas = json.load(open(D / "department_areas_km2.json"))
    for code, r in rows.items():
        area = areas.get(code)
        r["area_km2"] = area
        r["population_density_per_km2"] = round(r["population_2023"] / area, 2) if area and r.get("population_2023") else None

    pest = {f["properties"]["code"]: f["properties"] for f in load_geojson(D / "departments_pesticides.geojson")}
    for code, r in rows.items():
        p = pest.get(code, {})
        by_year = {row["year"]: row["substance_kg"] for row in p.get("pesticide_sales_by_year", [])}
        r["pesticide_kg_2024"] = by_year.get(2024)
        r["pesticide_kg_2014"] = by_year.get(2014)
        r["pesticide_kg_per_capita_2024"] = round(by_year[2024] / r["population_2023"], 3) if by_year.get(2024) and r.get("population_2023") else None

    breakdown = {f["properties"]["code"]: f["properties"]["pesticide_breakdown"] for f in load_geojson(D / "departments_pesticide_breakdown.geojson")}
    for code, r in rows.items():
        bd = breakdown.get(code, {})
        r["pesticide_dominant_function"] = bd.get("dominant_function")
        shares = bd.get("function_share_pct", {})
        r["pesticide_fongicide_share_pct"] = shares.get("Fongicide")
        r["pesticide_herbicide_share_pct"] = shares.get("Herbicide")
        r["pesticide_insecticide_share_pct"] = shares.get("Insecticide")

    water = {f["properties"]["code"]: f["properties"]["water_quality"] for f in load_geojson(D / "departments_water_quality.geojson")}
    for code, r in rows.items():
        w = water.get(code, {}).get("nitrates") or {}
        r["drinkingwater_nitrate_mean_mgL"] = w.get("mean")
        r["drinkingwater_nitrate_max_mgL"] = w.get("max")
        r["drinkingwater_any_pesticide_noncompliant_pct"] = w.get("pct_any_pesticide_non_compliant")

    river = {f["properties"]["code"]: f["properties"]["river_quality"] for f in load_geojson(D / "departments_river_quality.geojson")}
    for code, r in rows.items():
        rv = river.get(code, {}).get("nitrates") or {}
        r["river_nitrate_mean_mgL"] = rv.get("mean")
        r["river_nitrate_max_mgL"] = rv.get("max")
        r["river_nitrate_pct_exceeding_limit"] = rv.get("pct_exceeding_limit")

    copper = {f["properties"]["code"]: f["properties"].get("river_copper") for f in load_geojson(D / "departments_river_copper.geojson")}
    for code, r in rows.items():
        cu = copper.get(code) or {}
        r["river_copper_mean_when_detected_ugL"] = cu.get("mean_when_detected")
        r["river_copper_max_ugL"] = cu.get("max")

    maize_path = D / "departments_maize.geojson"
    if maize_path.exists():
        maize = {f["properties"]["code"]: f["properties"] for f in load_geojson(maize_path)}
        for code, r in rows.items():
            m = maize.get(code, {})
            r["maize_pct_of_declared_area"] = m.get("maize_pct_of_declared_area")
            r["maize_area_ha"] = m.get("maize_area_ha")
            r["total_declared_agricultural_area_ha"] = m.get("total_declared_area_ha")

    cancer_path = D / "departments_cancer.geojson"
    if cancer_path.exists():
        cancer = {f["properties"]["code"]: f["properties"] for f in load_geojson(cancer_path)}
        for code, r in rows.items():
            c = cancer.get(code, {})
            r["cancer_data_available"] = code in cancer

    ordered_rows = [rows[code] for code in sorted(rows)]
    fieldnames = list(ordered_rows[0].keys()) if ordered_rows else []

    out_csv = D / "departments_master.csv"
    with open(out_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(ordered_rows)
    print(f"Wrote {out_csv} ({len(ordered_rows)} departments, {len(fieldnames)} columns)")

    for feature in france:
        code = feature["properties"]["code"]
        feature["properties"].update(rows[code])
    out_geojson = D / "departments_master.geojson"
    with open(out_geojson, "w") as f:
        json.dump({"type": "FeatureCollection", "features": france}, f, ensure_ascii=False)
    print(f"Wrote {out_geojson}")


if __name__ == "__main__":
    main()
