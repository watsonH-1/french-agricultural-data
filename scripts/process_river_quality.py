#!/usr/bin/env python3
"""
Nationwide river water quality aggregation from Hub'Eau, by department —
same pattern as process_water_quality.py (drinking water), extended to
rivers to address the gap that drinking water is treated/blended before
reaching the tap and can mask raw environmental contamination (found while
investigating why Brittany showed almost no pesticide signal in drinking
water despite well-documented agricultural water pollution there).

Uses Hub'Eau's rivers API v2 (qualite_rivieres/analyse_pc), confirmed
working with code_departement directly, same as drinking water — but note
the date filter parameter name is DIFFERENT: date_debut_prelevement, not
date_min_prelevement (drinking water's v1 API convention). Found by testing
both, not assumed from the drinking-water script's convention.

Usage:
    python scripts/process_river_quality.py --since 2023-01-01

Requires: requests
"""

import argparse
import json
import sys
import time
from pathlib import Path

import requests

BASE = "https://hubeau.eaufrance.fr/api/v2/qualite_rivieres/analyse_pc"

PARAMETERS = {
    "1340": {"label": "nitrates", "limit": 50.0, "unit": "mg/L"},
    "1506": {"label": "glyphosate", "limit": 0.1, "unit": "µg/L"},
    "1092": {"label": "prosulfocarbe", "limit": 0.1, "unit": "µg/L"},
}

PAGE_SIZE = 2000
MAX_PAGES_PER_DEPT_PARAM = 15


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
    records = []
    page = 1
    while page <= MAX_PAGES_PER_DEPT_PARAM:
        params = {
            "code_departement": code_departement,
            "code_parametre": code_parametre,
            "date_debut_prelevement": since_date,  # NOT date_min_prelevement — rivers v2 uses a different param name
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
    values = [r["resultat"] for r in records if r.get("resultat") is not None]  # NOTE: field is "resultat", not "resultat_numerique" (drinking water's field name)
    if not values:
        return None
    exceedances = sum(1 for v in values if v > limit)
    return {
        "n_samples": len(values),
        "mean": round(sum(values) / len(values), 4),
        "max": round(max(values), 4),
        "pct_exceeding_limit": round(exceedances / len(values) * 100, 2),
        "n_exceeding_limit": exceedances,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", default="2023-01-01")
    parser.add_argument("--parameters", nargs="*", default=list(PARAMETERS.keys()))
    parser.add_argument("--departments-boundary", default="data/processed/departments_france.geojson")
    parser.add_argument("--output", default="data/processed/departments_river_quality.geojson")
    args = parser.parse_args()

    with open(args.departments_boundary) as f:
        boundary = json.load(f)

    for i, feature in enumerate(boundary["features"]):
        code = feature["properties"]["code"]
        feature["properties"]["river_quality"] = {}
        for param_code in args.parameters:
            meta = PARAMETERS.get(param_code, {"label": param_code, "limit": None})
            print(f"[{i+1}/{len(boundary['features'])}] department {code}, {meta['label']} ...", file=sys.stderr)
            try:
                records = fetch_department_parameter(code, param_code, args.since)
                summary = summarize(records, meta["limit"]) if meta["limit"] is not None else None
                feature["properties"]["river_quality"][meta["label"]] = summary
            except Exception as e:
                print(f"  FAILED for {code}/{meta['label']}: {e}", file=sys.stderr)
                feature["properties"]["river_quality"][meta["label"]] = None

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(boundary, f, ensure_ascii=False)

    print(f"Wrote {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
