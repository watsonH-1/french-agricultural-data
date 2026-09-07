# Gotchas — read before touching the scripts

Everything here cost real hours to find. Most of these bugs failed silently — no error, no crash, just wrong data that looked plausible until someone checked it against a second source. If you're extending this pipeline, check the relevant section here before you assume an API behaves the way its sibling endpoints do.

## Hub'Eau: three APIs, three different dialects

Hub'Eau's drinking water, rivers, and groundwater endpoints look like one consistent family. They are not.

| | Drinking water (v1) | Rivers (v2) | Groundwater (v1) |
|---|---|---|---|
| Result field | `resultat_numerique` | `resultat` | `resultat` |
| Date params | `date_min_prelevement` / `date_max_prelevement` | `date_debut_prelevement` / `date_fin_prelevement` | `date_debut_prelevement` / `date_fin_prelevement` |
| Department filter | `code_departement` | `code_departement` | **`num_departement`** |
| Matrix field | none (always tap water) | `code_support` (water/biota/sediment — see below) | none (always water phase) |
| Pagination cap | none observed | none observed | **`page × size ≤ 20000`**, hard 400 error |

**The groundwater department-filter bug was the worst one.** `code_departement` is silently ignored by `qualite_nappes/analyses` — it doesn't error, it just returns an unfiltered, nationally-mixed result set. A department-by-department fetch loop using the wrong parameter produces plausible-looking, internally-consistent, completely wrong data: in our case, 91 of 96 "completed" departments collapsed into two duplicate signatures because every fetch was actually pulling the same unfiltered pool. The only way this was caught was by using the data for cross-department comparison and noticing two supposedly-different departments had identical numbers to four decimal places. **The fix: use `num_departement`, not `code_departement`, for this specific endpoint.**

**Rivers mix matrices unless you filter.** Without `code_support=3` ("Eau" / water column), a river query returns biota (fish/gammares tissue, µg/kg wet weight) and sediment results alongside water-column concentrations (µg/L) for the same parameter code. For bioaccumulative substances like copper, tissue concentrations read 1,000×+ higher than water — mixing them in silently corrupts any mean/max you compute. Always pass `code_support=3` for rivers when you want water concentration.

**Watch units, not just matrix.** Even within `code_support=3`, individual records can report in `mg/L` or `µg/L` for the same nominal substance. Normalize explicitly (check `symbole_unite` per record) rather than assuming a fixed unit for a given `code_parametre`.

## RPG (agricultural parcels) via IGN WFS

**Bounding-box fetches leak neighboring departments.** A department's real shape is irregular; its bounding box is a rectangle that extends into neighbors. Fetching "department 28" by bbox returns real parcels belonging to up to 7 different neighboring departments. Filter by centroid-within-true-polygon (`rpg_common._filter_to_department`), not bbox membership.

**The server silently caps page size at 5,000** regardless of what `COUNT` you request — asking for 10,000 returns exactly 5,000. Using a smaller requested page size (e.g. 1,000) just means 5× more sequential requests than necessary, which for a large/dense department (300,000+ parcels) meaningfully increases the odds of hitting a transient failure partway through a multi-hour fetch.

**A `200` response can still have a non-JSON body.** A handful of large-department fetches failed with a JSON-decode error despite the HTTP status being 200 — the server occasionally returns an empty or HTML error body with a "successful" status code. Retry the *parse*, not just the HTTP call.

**Don't compute the same expensive geometric overlay twice.** The parcel-vs-grid intersection (`overlay_parcels_on_grid`) is the single most expensive operation in this pipeline. An earlier version of `process_department_combined.py` called it twice per department (once for the maize aggregate, once for the diversity index) — for most departments this just wasted time, but for Finistère's ~300,000 tiny fragmented parcels it turned a manageable job into 6+ hours. Compute it once, reuse the result.

## BNV-D (pesticide sales)

**Check the export's geographic grouping before trusting per-department figures.** BNV-D's own export tool defaults to "Tous lieux d'achat confondus" (nationally aggregated) unless you explicitly select "Département de l'acheteur" as the grouping. A nationally-aggregated export will *look* like per-department data (same columns, same shape) but every department will show identical figures for a given substance/year — check for that before assuming the file is what you asked for.

**Treat the crop's first mandatory-reporting year as unreliable.** 2013 BNV-D figures (24.2M kg nationally) are roughly a third of 2014's (70.9M kg) — not a real usage collapse, an artifact of partial compliance in the reporting requirement's first year. Use 2014 as the earliest reliable baseline for any trend calculation.

**BNV-D is purchase-location data, not use-location data.** A department showing an oddly high pesticide-per-hectare ratio may just be where a distributor or agrochemical company is registered, not where the product was applied. Always sanity-check the actual crop area for that department before trusting a per-hectare ratio — a Paris-suburb department with under 1 hectare of a given crop can produce a nonsense ratio from a real (correctly recorded) purchase.

## General

**A script that catches its own errors can lie about success.** If a fetch function wraps API errors in a try/except and just returns `None` on failure, the calling script exits 0 whether it got real data or nothing at all. Verify success by checking that the output file actually contains distinct, plausible values — not by trusting the exit code or a "N succeeded" counter from a wrapper script.

**Wrap long unattended fetches in something that prevents sleep.** On macOS, `caffeinate -i -w <pid>` attached to an already-running process (no need to restart it) prevents the machine sleeping mid-fetch. This pipeline hit three separate overnight network outages before this was applied — each one silently stalled or corrupted whatever was mid-run at the time.
