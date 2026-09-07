#!/usr/bin/env python3
"""
Process a downloaded INSEE historical population series into a
department-level population-change GeoJSON.

Verified against a real export 2026-08-30 (INSEE Melodi "Séries historiques
de population" DS_RP_SERIE_HISTORIQUE_2023, downloaded from
https://api.insee.fr/melodi/file/DS_RP_SERIE_HISTORIQUE/DS_RP_SERIE_HISTORIQUE_2023_CSV_FR).

The real file is a long/SDMX-style CSV (semicolon-delimited, quoted), NOT
the wide per-commune-column format this script originally assumed. Relevant
columns: GEO (area code), GEO_OBJECT (area type — COM/DEP/REG/AAV2020/etc,
several types mixed in one file), RP_MEASURE (POP/BRTH/DEATH/DWELLINGS/...),
OCS (breakdown category, "_T" = total/no breakdown), TIME_PERIOD (census
year: 1968, 1975, 1982, 1990, 1999, 2007, 2012, 2017, 2023), OBS_VALUE.

Department-level population (GEO_OBJECT="DEP") is available DIRECTLY in
this file — no commune-to-department aggregation needed, which simplifies
this considerably versus the original plan. GEO codes for departments are
plain 2-character codes ("28", "02", ...), matching
data/processed/departments_france.geojson's `code` property exactly.

Usage:
    python scripts/process_insee_population.py \
        --input data/raw/insee/DS_RP_SERIE_HISTORIQUE_2023_data.csv \
        --year-from 2017 --year-to 2023 \
        --output data/processed/population_change.geojson

Requires: pandas (pip install pandas)

Note: the file is ~175MB / 3M rows covering every GEO_OBJECT type and
RP_MEASURE — this script reads it in chunks and filters early rather than
loading the whole thing into memory at once.
"""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

CHUNK_SIZE = 200_000


def load_department_population(input_path, year_from, year_to):
    print(f"Reading {input_path} (filtering to department-level POP as we go) ...", file=sys.stderr)
    chunks = []
    for chunk in pd.read_csv(input_path, sep=";", dtype={"GEO": str}, chunksize=CHUNK_SIZE):
        filtered = chunk[
            (chunk["GEO_OBJECT"] == "DEP")
            & (chunk["RP_MEASURE"] == "POP")
            & (chunk["OCS"] == "_T")
            & (chunk["TIME_PERIOD"].isin([year_from, year_to]))
        ]
        if not filtered.empty:
            chunks.append(filtered)

    if not chunks:
        raise ValueError(
            f"No department-level POP rows found for years {year_from}/{year_to}. "
            "Check --year-from/--year-to are among the file's actual TIME_PERIOD values "
            "(this export has 1968, 1975, 1982, 1990, 1999, 2007, 2012, 2017, 2023)."
        )
    return pd.concat(chunks, ignore_index=True)


def compute_pct_change(df, year_from, year_to):
    pivoted = df.pivot(index="GEO", columns="TIME_PERIOD", values="OBS_VALUE")
    pivoted = pivoted.rename(columns={year_from: "population_from", year_to: "population_to"})
    pivoted["population_pct_change"] = (
        (pivoted["population_to"] - pivoted["population_from"]) / pivoted["population_from"] * 100
    ).round(2)
    return pivoted.reset_index().rename(columns={"GEO": "code"})


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", required=True)
    parser.add_argument("--year-from", required=True, type=int)
    parser.add_argument("--year-to", required=True, type=int)
    parser.add_argument("--output", default="data/processed/population_change.geojson")
    parser.add_argument("--departments-boundary", default="data/processed/departments_france.geojson")
    args = parser.parse_args()

    df = load_department_population(args.input, args.year_from, args.year_to)
    result = compute_pct_change(df, args.year_from, args.year_to)

    with open(args.departments_boundary) as f:
        boundary = json.load(f)

    by_dept = result.set_index("code").to_dict("index")

    matched = 0
    for feature in boundary["features"]:
        code = feature["properties"]["code"]
        stats = by_dept.get(code)
        if stats:
            feature["properties"]["population_from"] = stats["population_from"]
            feature["properties"]["population_to"] = stats["population_to"]
            feature["properties"]["population_pct_change"] = stats["population_pct_change"]
            matched += 1

    print(f"Matched population data for {matched} of {len(boundary['features'])} departments.", file=sys.stderr)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(boundary, f, ensure_ascii=False)

    print(f"Wrote {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
