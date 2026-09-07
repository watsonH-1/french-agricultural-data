#!/usr/bin/env python3
"""
Process Santé Publique France / INCa / FRANCIM's department-level estimated
cancer incidence and mortality data into a department-joined GeoJSON, for
exploratory comparison against pesticide sales and water contamination.

Source: Odissé (Santé Publique France's open data platform), dataset
"cancer-incidence-et-mortalite-estimees-departement" — MODELED ESTIMATES
covering all departments (not just the ~20 with an actual cancer registry),
downloaded 2026-08-31 via the platform's OpenDataSoft Explore v2.1 API:
    https://odisse.santepubliquefrance.fr/api/explore/v2.1/catalog/datasets/cancer-incidence-et-mortalite-estimees-departement/exports/csv

REAL LIMITATION, not fixable by re-downloading: Corsica (2A/2B) has NO
records in this dataset at all — neither incidence nor mortality. Every
other metropolitan department is present. This script leaves Corsica's
cancer fields null rather than guessing or interpolating.

CRITICAL METHODOLOGICAL CAVEAT — read before using this for anything:
This data is DECADE-OLD by design: incidence covers 2007-2016, mortality
2007-2014. Cancer has long latency (often 10-30+ years from exposure to
diagnosis), so these rates mostly reflect exposure from the 1980s-2000s,
NOT current farming practices or current pesticide sales data (which starts
2013). Any correlation against current-day pesticide/water data is an
ECOLOGICAL correlation at best — useful for generating hypotheses, not for
inferring causation, and confounded by age structure, smoking rates,
diagnostic/screening access, occupational exposures unrelated to
agriculture, and dozens of other department-level differences. This
script embeds that caveat directly in the output file's metadata so it
travels with the data, not just this docstring.

Cancer sites kept (out of ~24 in the source): "TOUS CANCERS" (all cancers
combined) plus the specific sites with documented associations to
agricultural/pesticide exposure in the real epidemiological literature
(notably France's own Agrican farmer-cohort study) — non-Hodgkin lymphoma,
prostate, myeloid leukemia, and central nervous system tumors. Other sites
in the source data (lung, colon, breast, etc.) are dropped here as not
part of this project's specific research question, not because they're
uninteresting — pass --sites to keep others.

Usage:
    python scripts/process_cancer_data.py \
        --input data/raw/cancer_incidence_mortality_department.csv

Requires: pandas
"""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

DEFAULT_SITES = [
    "TOUS CANCERS",
    "Lymphome malin non Hodgkinien",
    "Prostate",
    "Leucémie myéloïde",
    "Système nerveux central",
]

METHODOLOGICAL_CAVEAT = (
    "Incidence covers 2007-2016, mortality 2007-2014 — decades before current pesticide/farming "
    "data. Cancer has 10-30+ year latency, so these rates mostly reflect exposure from the "
    "1980s-2000s. Any correlation against current pesticide/water data is ecological, not causal, "
    "and confounded by age structure, smoking, screening access, and non-agricultural occupational "
    "exposure. Corsica (2A/2B) has no records in the source dataset at all."
)


def load_cancer_data(input_path):
    df = pd.read_csv(input_path, sep=";", encoding="utf-8-sig", dtype={"dept": str})
    df = df[df["dept"].str.len() <= 2]  # drop overseas departments (971, 972, 973) — out of this project's metropolitan scope
    df["code"] = df["dept"].str.zfill(2)
    return df


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", default="data/raw/cancer_incidence_mortality_department.csv")
    parser.add_argument("--sites", nargs="*", default=DEFAULT_SITES)
    parser.add_argument("--departments-boundary", default="data/processed/departments_france.geojson")
    parser.add_argument("--output", default="data/processed/departments_cancer.geojson")
    args = parser.parse_args()

    df = load_cancer_data(args.input)
    df = df[df["lib"].isin(args.sites)]
    # Combine sex-specific rows into an overall (both-sexes) figure per department/site/indicator
    # by taking the "Les deux sexes" row if present, else averaging Hommes/Femmes weighted equally
    # (a simplification — true both-sexes rates need population weighting, not a plain average;
    # noted here rather than silently presented as exact).
    both_sexes = df[df["sexe"].isin(["Les deux sexes", "Tous sexes"])]
    if both_sexes.empty:
        print("No 'both sexes' rows found — averaging Hommes/Femmes unweighted as an approximation.", file=sys.stderr)
        both_sexes = df.groupby(["code", "lib", "indic", "periode"], as_index=False)["est"].mean()

    with open(args.departments_boundary) as f:
        boundary = json.load(f)

    matched = 0
    for feature in boundary["features"]:
        code = feature["properties"]["code"]
        rows = both_sexes[both_sexes["code"] == code]
        if rows.empty:
            feature["properties"]["cancer"] = None
            continue
        cancer_data = {}
        for _, row in rows.iterrows():
            key = f"{row['lib']}_{row['indic']}".lower().replace(" ", "_")
            cancer_data[key] = {"rate_per_100k": round(row["est"], 2), "period": row["periode"]}
        feature["properties"]["cancer"] = cancer_data
        matched += 1

    print(f"Matched cancer data for {matched} of {len(boundary['features'])} departments (Corsica expected to be missing).", file=sys.stderr)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump({"_methodological_caveat": METHODOLOGICAL_CAVEAT, **boundary}, f, ensure_ascii=False)

    print(f"Wrote {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
