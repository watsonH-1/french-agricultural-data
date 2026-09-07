# Data coverage status

Last updated: 2026-09-07

| Layer | Coverage | Notes |
|---|---|---|
| Population change | 96/96 | Complete |
| Pesticide sales + function/substance breakdown | 96/96 | Complete, 2013–2024 (treat 2013 as unreliable — see GOTCHAS.md) |
| Drinking water quality | 96/96 | Complete |
| River quality | 96/96 | Complete |
| River heavy metals + PFAS | 96/96 | Complete |
| River bacteriology | 79/96 | Genuine gap — not every department tests for it |
| River cyanotoxins | 25/96 | Genuine gap — only tested near known bloom-risk sites |
| Drinking water heavy metals + bacteriology | 96/96 | Complete |
| Drinking water PFAS | 48/96 | Genuine gap |
| Drinking water cyanotoxins | 41/96 | Genuine gap |
| Cancer incidence/mortality | 94/96 | Corsica (2A/2B) absent from the source dataset itself |
| Maize % / RPG land use | 91/96 | 5 departments (Finistère, Morbihan, Pas-de-Calais, Somme, Vendée) hit either extreme parcel-count runtimes or the WFS page-size bug — see GOTCHAS.md before re-attempting |
| Crop-family breakdown (field count/size) | 24/96 | New feature added late in the project; backfill for the remaining 72 not yet run |
| Field-size trend / crop diversity index | Grid-cell only | Computed at 5km-grid resolution; no department-level rollup has been built |
| Riparian buffer compliance | 0/96 | Never started |
| Wildness / land-cover index (OSO) | 1/96 | Only Seine-et-Marne; OSO is per-department vector shapefiles, not one national raster — would need either 95 more manual downloads or a switch to CORINE |
| Groundwater | **Invalid — not included** | Wrong API parameter used throughout; see GOTCHAS.md. Would need a full re-fetch with `num_departement`. |

## If you're picking this back up

The highest-value next steps, in order of effort-to-value:

1. **Finish the maize/RPG backfill** (5 departments) — mechanically identical to the other 91, just needs the timeout-and-retry pattern in `docs/GOTCHAS.md` applied with a longer budget (30–40 min) for the two or three genuinely huge/fragmented ones.
2. **Redo groundwater from scratch** with the corrected `num_departement` parameter — genuinely useful layer (raw aquifer contamination, distinct from treated drinking water), currently zero usable rows.
3. **Backfill crop-family breakdown** to the remaining 72 departments — same script, same fetch, just hasn't been run yet for departments processed before the feature was added.
4. **Build a department-level rollup for field-size trend and diversity index** — currently grid-cell only; would need a spatial join between `grid_5km.geojson` and `departments_france.geojson` to aggregate correctly, watching for the same cross-department-cell double-counting issue documented in `rpg_common.py` and `process_department_combined.py`.
