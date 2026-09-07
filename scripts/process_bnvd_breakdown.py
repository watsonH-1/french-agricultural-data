#!/usr/bin/env python3
"""
Department-level pesticide breakdown by function (fongicide/herbicide/
insecticide/...) and top substances, from the same raw BNV-D CSVs already
used by process_bnvd.py — that script only kept department totals; this
one keeps the substance/function detail needed for a "which areas use what
most intensively" map (built 2026-08-31 for the findings page).

Usage:
    python scripts/process_bnvd_breakdown.py --input-dir data/raw/bnvd --year 2024

Requires: pandas
"""

import argparse
import glob
import json
import sys
from pathlib import Path

import pandas as pd

DEPT_COL = "code_departement_acheteur"
FUNCTION_COL = "fonction_substance"
SUBSTANCE_COL = "substance"
YEAR_COL = "annee"
KG_COL = "quantite_substance"

# Collapse ~12 raw function labels into a small set for the map's color scale
FUNCTION_GROUPS = {
    "Fongicide": "Fongicide",
    "Herbicide": "Herbicide",
    "Herbicide - Antimousse": "Herbicide",
    "Insecticide": "Insecticide",
    "Insecticide - Acaricide": "Insecticide",
    "Insecticide - Médiateur chimique": "Insecticide",
    "Nématicide": "Insecticide",
    "Molluscicide": "Insecticide",
    "Régulateur de croissance": "Autre",
    "Autre usage – Adjuvant": "Autre",
    "Autre usage – Coformulant / Phytoprotecteur / Synergiste": "Autre",
    "SA non phyto - Divers": "Autre",
}


def load_and_filter(input_dir, year):
    frames = []
    for path in glob.glob(str(Path(input_dir) / "*.csv")):
        df = pd.read_csv(path, sep=";", encoding="utf-8", dtype={DEPT_COL: str}, low_memory=False)
        df = df[df[YEAR_COL] == year]
        if not df.empty:
            frames.append(df)
    if not frames:
        raise ValueError(f"No rows found for year {year} in {input_dir}")
    df = pd.concat(frames, ignore_index=True)
    return df[df[DEPT_COL] != "-"]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input-dir", default="data/raw/bnvd")
    parser.add_argument("--year", type=int, default=2024)
    parser.add_argument("--departments-boundary", default="data/processed/departments_france.geojson")
    parser.add_argument("--output", default="data/processed/departments_pesticide_breakdown.geojson")
    args = parser.parse_args()

    df = load_and_filter(args.input_dir, args.year)
    df["function_group"] = df[FUNCTION_COL].map(FUNCTION_GROUPS).fillna("Autre")

    by_dept_function = df.groupby([DEPT_COL, "function_group"])[KG_COL].sum().reset_index()
    by_dept_substance = df.groupby([DEPT_COL, SUBSTANCE_COL])[KG_COL].sum().reset_index()

    with open(args.departments_boundary) as f:
        boundary = json.load(f)

    matched = 0
    for feature in boundary["features"]:
        code = feature["properties"]["code"]
        dept_rows = by_dept_function[by_dept_function[DEPT_COL] == code]
        if dept_rows.empty:
            feature["properties"]["pesticide_breakdown"] = None
            continue

        total_kg = dept_rows[KG_COL].sum()
        function_shares = {row["function_group"]: round(row[KG_COL] / total_kg * 100, 1) for _, row in dept_rows.iterrows()}
        dominant_function = dept_rows.loc[dept_rows[KG_COL].idxmax(), "function_group"]

        top_substances_df = by_dept_substance[by_dept_substance[DEPT_COL] == code].nlargest(5, KG_COL)
        top_substances = [{"substance": row[SUBSTANCE_COL], "kg": round(row[KG_COL], 1)} for _, row in top_substances_df.iterrows()]

        feature["properties"]["pesticide_breakdown"] = {
            "year": args.year,
            "total_kg": round(total_kg, 1),
            "dominant_function": dominant_function,
            "function_share_pct": function_shares,
            "top_substances": top_substances,
        }
        matched += 1

    print(f"Matched pesticide breakdown for {matched} of {len(boundary['features'])} departments.", file=sys.stderr)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(boundary, f, ensure_ascii=False)

    print(f"Wrote {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
