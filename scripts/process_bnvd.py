#!/usr/bin/env python3
"""
Process a downloaded BNV-D (Banque Nationale des Ventes de produits
phytopharmaceutiques par Distributeurs) export into a department -> total
active substance (kg), by year, GeoJSON joined to department boundaries.

Verified against a real export 2026-08-30 (BNVD_TRACABILITE export, "Achats"
type — purchases, attributed to the BUYER's department, not the
distributor's — actually the more useful framing for this project, since we
care about where pesticides are used, not where they're sold from). The
"Traçabilité" export tool at
https://ventes-produits-phytopharmaceutiques.eaufrance.fr/ produces ONE CSV
PER YEAR inside the zip, semicolon-delimited, quoted fields, plus a
_CRITERES.txt describing the export parameters and a metadata PDF.

Real column names (confirmed from an actual 2013-2024 export, NOT the
guessed names this script originally shipped with):
    annee, code_departement_acheteur, quantite_substance (kg, per the
    metadata PDF: "Quantité de substance active vendue ou achetée à
    l'étranger exprimée en kilogrammes")

IMPORTANT — check your export's _CRITERES.txt "Regroupement géographique"
setting before running this: if it says "Tous lieux d'achat confondus" (all
purchase locations combined), EVERY row's code_departement_acheteur will be
"-" and this script cannot produce a department breakdown — it can only
give you a national total per year. This has already happened once with a
real export in this project. Re-run the export at the query tool with
geographic grouping set to department (look for a "Département" option in
the export/grouping settings) to get a usable department-level file.

Usage:
    python scripts/process_bnvd.py \
        --input-dir data/raw/bnvd \
        --output data/processed/departments_pesticides.geojson \
        --departments-boundary data/processed/departments_france.geojson

Requires: pandas (pip install pandas)
"""

import argparse
import glob
import json
import sys
from pathlib import Path

import pandas as pd

COLUMN_MAP = {
    "department_code": "code_departement_acheteur",
    "year": "annee",
    "substance_kg": "quantite_substance",
}


def load_bnvd(input_dir):
    """
    Load every per-year CSV in the export directory (the real Traçabilité
    export ships one CSV per year, not one combined file) and concatenate.
    Skips the _CRITERES.txt and any non-CSV file automatically via the glob.
    """
    csv_paths = sorted(glob.glob(str(Path(input_dir) / "*.csv")))
    if not csv_paths:
        raise FileNotFoundError(f"No .csv files found in {input_dir}")

    print(f"Reading {len(csv_paths)} BNV-D year file(s) from {input_dir} ...", file=sys.stderr)
    frames = []
    for path in csv_paths:
        df = pd.read_csv(path, sep=";", encoding="utf-8", dtype={COLUMN_MAP["department_code"]: str}, low_memory=False)
        missing = [c for c in COLUMN_MAP.values() if c not in df.columns]
        if missing:
            raise ValueError(
                f"{path}: expected columns {missing} not found. Columns present: {list(df.columns)}. "
                "BNV-D export column names can vary — update COLUMN_MAP in this script to match."
            )
        frames.append(df)

    return pd.concat(frames, ignore_index=True)


def aggregate_by_department_year(df):
    dept_col = COLUMN_MAP["department_code"]

    total_rows = len(df)
    unattributed = (df[dept_col] == "-").sum()
    if unattributed:
        pct = unattributed / total_rows * 100
        print(
            f"WARNING: {unattributed} of {total_rows} rows ({pct:.0f}%) have no department "
            f"(code_departement_acheteur == '-'). Check this export's _CRITERES.txt — if "
            "'Regroupement géographique' is 'Tous lieux d'achat confondus', ALL rows will be "
            "unattributed and no department breakdown is possible from this file at all. "
            "Re-export with department-level geographic grouping.",
            file=sys.stderr,
        )

    df = df[df[dept_col] != "-"]
    if df.empty:
        print("No rows with a real department code — nothing to aggregate. See the warning above.", file=sys.stderr)
        return pd.DataFrame(columns=["code", "year", "substance_kg"])

    grouped = (
        df.groupby([dept_col, COLUMN_MAP["year"]])[COLUMN_MAP["substance_kg"]]
        .sum()
        .reset_index()
        .rename(columns={dept_col: "code", COLUMN_MAP["year"]: "year", COLUMN_MAP["substance_kg"]: "substance_kg"})
    )
    return grouped


def aggregate_national_total_by_year(df):
    """Fallback when no department is attributable: at least give a national total per year."""
    return df.groupby(COLUMN_MAP["year"])[COLUMN_MAP["substance_kg"]].sum().reset_index().rename(
        columns={COLUMN_MAP["year"]: "year", COLUMN_MAP["substance_kg"]: "substance_kg"}
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input-dir", required=True, help="Directory containing the extracted BNV-D per-year CSVs")
    parser.add_argument("--output", default="data/processed/departments_pesticides.geojson")
    parser.add_argument("--national-output", default="data/processed/national_pesticides_by_year.json", help="Written as a fallback when no department breakdown is possible")
    parser.add_argument("--departments-boundary", default="data/processed/departments_france.geojson")
    args = parser.parse_args()

    df = load_bnvd(args.input_dir)
    grouped = aggregate_by_department_year(df)

    if grouped.empty:
        national = aggregate_national_total_by_year(df)
        out_path = Path(args.national_output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(national.to_dict("records"), f, indent=2)
        print(f"Wrote national (non-geographic) totals by year to {out_path} instead.", file=sys.stderr)
        return

    with open(args.departments_boundary) as f:
        boundary = json.load(f)

    by_dept = {}
    for row in grouped.to_dict("records"):
        by_dept.setdefault(row["code"], []).append({"year": row["year"], "substance_kg": row["substance_kg"]})

    matched = 0
    for feature in boundary["features"]:
        code = feature["properties"]["code"]
        years = by_dept.get(code)
        feature["properties"]["pesticide_sales_by_year"] = years
        if years:
            matched += 1

    print(f"Matched pesticide sales data for {matched} of {len(boundary['features'])} departments.", file=sys.stderr)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(boundary, f, ensure_ascii=False)

    print(f"Wrote {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
