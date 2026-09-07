#!/usr/bin/env python3
"""
Nationwide drinking-water quality aggregation from Hub'Eau, by department —
built 2026-08-31 to support cross-referencing water contamination against
pesticide sales (BNV-D) and population data, both already nationwide.

Unlike the frontend's live per-commune fetch (src/data/hubeau.js), this
queries Hub'Eau's resultats_dis endpoint DIRECTLY BY DEPARTMENT
(code_departement param, confirmed working 2026-08-31) — no need to loop
~35,000 communes individually. Restricted to a recent date window
(--since, default 2 years back) so results reflect current water quality,
not decades of history diluting a average.

Parameters queried, by Sandre code:
- 1340: Nitrates — the parameter already used elsewhere in this project.
- 1506: Glyphosate — the substance-to-water link this script exists for.
  Sandre code confirmed directly from a real BNV-D download (this project's
  own data.raw/bnvd CSVs carry code_sandre_substance per substance), not
  guessed.
- 1092: Prosulfocarbe — same sourcing.
Add more via --parameters if you want other top substances from
compute pesticide substance breakdown (see the crop_code_to_family-style
approach: pull code_sandre_substance for whichever substance interests you
from the raw BNV-D CSVs directly, don't guess a Sandre code).

Regulatory limits (for computing exceedance rate), per the EU Drinking
Water Directive as applied in France: nitrates 50 mg/L, each individual
pesticide substance 0.1 µg/L. Hardcoded here with that citation — verify
before treating as authoritative for anything beyond this exploratory
analysis.

Usage:
    python scripts/process_water_quality.py --since 2023-01-01

Requires: requests, pandas
"""

import argparse
import json
import sys
import time
from pathlib import Path

import requests

BASE = "https://hubeau.eaufrance.fr/api/v1/qualite_eau_potable/resultats_dis"

PARAMETERS = {
    "1340": {"label": "nitrates", "limit": 50.0, "unit": "mg/L"},
    "1506": {"label": "glyphosate", "limit": 0.1, "unit": "µg/L"},
    "1092": {"label": "prosulfocarbe", "limit": 0.1, "unit": "µg/L"},
}

PAGE_SIZE = 2000
MAX_PAGES_PER_DEPT_PARAM = 15  # caps at 30,000 records/department/parameter — generous for a department-level aggregate


def fetch_with_retry(url, params, retries=3, backoff_s=2):
    last_error = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, params=params, timeout=60)
            if resp.status_code == 429 or resp.status_code >= 500:
                last_error = Exception(f"HTTP {resp.status_code}")
            else:
                resp.raise_for_status()
                return resp
        except requests.exceptions.RequestException as e:
            last_error = e
        if attempt < retries:
            time.sleep(backoff_s * 2 ** attempt)
    raise last_error


def fetch_department_parameter(code_departement, code_parametre, since_date):
    """Paginated fetch of all resultats_dis records for one department + parameter, since a given date."""
    records = []
    page = 1
    while page <= MAX_PAGES_PER_DEPT_PARAM:
        params = {
            "code_departement": code_departement,
            "code_parametre": code_parametre,
            "date_min_prelevement": since_date,
            "size": PAGE_SIZE,
            "page": page,
        }
        resp = fetch_with_retry(BASE, params)
        body = resp.json()
        records.extend(body.get("data", []))
        if not body.get("next"):
            break
        page += 1
    return records


def summarize(records, limit):
    values = [r["resultat_numerique"] for r in records if r.get("resultat_numerique") is not None]
    if not values:
        return None
    exceedances = sum(1 for v in values if v > limit)

    # conformite_limites_pc_prelevement is a PER-SAMPLE flag covering every pesticide tested in
    # that sampling event (often 50-100+ substances), not specific to the parameter this query
    # asked for. Since nitrates are tested in nearly every sampling event, using it here gives a
    # broad "any pesticide non-compliant" rate essentially for free — found by actually reading a
    # real non-compliant record closely (Famechon, Somme) rather than assumed from the field name.
    compliance_flags = [r.get("conformite_limites_pc_prelevement") for r in records if r.get("conformite_limites_pc_prelevement")]
    any_pesticide_non_compliant = sum(1 for c in compliance_flags if c == "N")

    return {
        "n_samples": len(values),
        "mean": round(sum(values) / len(values), 4),
        "max": round(max(values), 4),
        "pct_exceeding_limit": round(exceedances / len(values) * 100, 2),
        "n_exceeding_limit": exceedances,
        "pct_any_pesticide_non_compliant": round(any_pesticide_non_compliant / len(compliance_flags) * 100, 2) if compliance_flags else None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", default="2023-01-01", help="Only include samples from this date onward")
    parser.add_argument("--parameters", nargs="*", default=list(PARAMETERS.keys()), help="Sandre codes to query, default: nitrates + glyphosate + prosulfocarbe")
    parser.add_argument("--departments-boundary", default="data/processed/departments_france.geojson")
    parser.add_argument("--output", default="data/processed/departments_water_quality.geojson")
    args = parser.parse_args()

    # Merge into any existing output rather than overwriting — re-running with a subset of
    # --parameters (e.g. just nitrates, to backfill pct_any_pesticide_non_compliant without
    # re-fetching glyphosate/prosulfocarbe) would otherwise wipe out the other parameters'
    # already-computed results, since each department's water_quality dict is rebuilt fresh.
    out_path_check = Path(args.output)
    existing_water_quality = {}
    if out_path_check.exists():
        with open(out_path_check) as f:
            existing_data = json.load(f)
        existing_water_quality = {
            feat["properties"]["code"]: feat["properties"].get("water_quality", {})
            for feat in existing_data.get("features", [])
        }

    with open(args.departments_boundary) as f:
        boundary = json.load(f)

    for i, feature in enumerate(boundary["features"]):
        code = feature["properties"]["code"]
        feature["properties"]["water_quality"] = dict(existing_water_quality.get(code, {}))
        for param_code in args.parameters:
            meta = PARAMETERS.get(param_code, {"label": param_code, "limit": None})
            print(f"[{i+1}/{len(boundary['features'])}] department {code}, {meta['label']} ...", file=sys.stderr)
            try:
                records = fetch_department_parameter(code, param_code, args.since)
                summary = summarize(records, meta["limit"]) if meta["limit"] is not None else None
                feature["properties"]["water_quality"][meta["label"]] = summary
            except Exception as e:
                print(f"  FAILED for {code}/{meta['label']}: {e}", file=sys.stderr)
                feature["properties"]["water_quality"][meta["label"]] = None

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(boundary, f, ensure_ascii=False)

    print(f"Wrote {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
