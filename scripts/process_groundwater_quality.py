#!/usr/bin/env python3
"""
Nationwide groundwater (aquifer) quality aggregation from Hub'Eau, by
department — same pattern as process_river_quality.py / process_water_quality.py.
Fills a real gap: groundwater has only ever been live-fetched per-department
in the atlas UI, never turned into a static nationwide layer, despite being
directly relevant to the Beauce/Champagne aquifer story (nitrate leaching
under the arable belt) that the surface-water layers can't see.

Uses Hub'Eau's v1/qualite_nappes/analyses API — confirmed working with
code_departement directly (same convention as rivers), field names
code_param/resultat/date_debut_prelevement (different from drinking water's
v1 qualite_eau_potable convention, which uses code_parametre/resultat_numerique/
date_min_prelevement — confirmed by direct API inspection, not assumed).

Usage:
    python scripts/process_groundwater_quality.py --since 2023-01-01
"""

import argparse
import calendar
import json
import sys
import time
from datetime import date
from pathlib import Path

import requests

BASE = "https://hubeau.eaufrance.fr/api/v1/qualite_nappes/analyses"

PARAMETERS = {
    "1340": {"label": "nitrates", "limit": 50.0, "unit": "mg/L"},
    "1506": {"label": "glyphosate", "limit": 0.1, "unit": "µg/L"},
    "1092": {"label": "prosulfocarbe", "limit": 0.1, "unit": "µg/L"},
}

PAGE_SIZE = 2000
MAX_PAGES_PER_DEPT_PARAM = 20


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


def month_windows(since_date, until_date):
    """Hub'Eau caps page*size at 20000 — groundwater's continuous monitoring network
    is dense enough (29k+ records/dept/year for nitrates in heavily-tested departments)
    that a full-year single query can exceed that. Chunk by calendar month instead.

    NOTE: bound the end date explicitly rather than using date.today() — a first
    version used "today", which on this machine's clock is 2026, silently turning a
    "since 2023" request into ~44 months of chunked pagination per parameter instead
    of the ~2 years every other layer (rivers, drinking water) actually covers. That
    made a single department take 3+ hours instead of ~15-20 minutes."""
    start = date.fromisoformat(since_date)
    end = date.fromisoformat(until_date)
    windows = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        last_day = calendar.monthrange(y, m)[1]
        window_start = date(y, m, 1)
        window_end = date(y, m, last_day)
        windows.append((window_start.isoformat(), window_end.isoformat()))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return windows


def fetch_department_parameter(code_departement, code_param, since_date, until_date):
    records = []
    for win_start, win_end in month_windows(since_date, until_date):
        page = 1
        while page <= MAX_PAGES_PER_DEPT_PARAM:
            params = {
                "num_departement": code_departement,  # NOT code_departement -- confirmed by direct testing that
                # code_departement is silently ignored by this specific Hub'Eau endpoint (qualite_nappes/analyses),
                # unlike rivers/drinking-water which do use code_departement. Using the wrong name here didn't
                # error -- it just returned an unfiltered, department-mixed result set, which produced two
                # internally-consistent but completely wrong "signatures" copied across 91 of 96 departments.
                "code_param": code_param,
                "date_debut_prelevement": win_start,
                "date_fin_prelevement": win_end,
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
    values = [r["resultat"] for r in records if r.get("resultat") is not None]
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
    parser.add_argument("--until", default="2024-12-31")
    parser.add_argument("--parameters", nargs="*", default=list(PARAMETERS.keys()))
    parser.add_argument("--departments-boundary", default="data/processed/departments_france.geojson")
    parser.add_argument("--output", default="data/processed/departments_groundwater_quality.geojson")
    parser.add_argument("--only-department", default=None, help="Restrict to a single department code (for incremental/background runs)")
    args = parser.parse_args()

    with open(args.departments_boundary) as f:
        boundary = json.load(f)

    # merge-safe: load existing output if present so re-running a subset doesn't wipe other departments
    existing = {}
    out_path = Path(args.output)
    if out_path.exists():
        with open(out_path) as f:
            prev = json.load(f)
        for feat in prev["features"]:
            existing[feat["properties"]["code"]] = feat["properties"].get("groundwater_quality", {})

    features = boundary["features"]
    if args.only_department:
        features = [f for f in features if f["properties"]["code"] == args.only_department]

    for i, feature in enumerate(features):
        code = feature["properties"]["code"]
        gw = dict(existing.get(code, {}))
        for param_code in args.parameters:
            meta = PARAMETERS.get(param_code, {"label": param_code, "limit": None})
            print(f"[{i+1}/{len(features)}] department {code}, {meta['label']} ...", file=sys.stderr)
            try:
                records = fetch_department_parameter(code, param_code, args.since, args.until)
                summary = summarize(records, meta["limit"]) if meta["limit"] is not None else None
                gw[meta["label"]] = summary
            except Exception as e:
                print(f"  FAILED for {code}/{meta['label']}: {e}", file=sys.stderr)
                gw[meta["label"]] = None
        existing[code] = gw

    for feature in boundary["features"]:
        code = feature["properties"]["code"]
        feature["properties"]["groundwater_quality"] = existing.get(code, {})

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(boundary, f, ensure_ascii=False)

    print(f"Wrote {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
