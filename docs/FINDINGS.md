# Findings

Verified results this dataset supports, condensed from the full research process. Each is reproducible from the files in `data/processed/`.

## Water & pesticides

- **France leads the EU in pesticide tonnage (22–23% of EU sales) but not intensity** — it also has the EU's largest farmland area (27.2M ha). Per hectare (~2.47 kg/ha), it sits level with Germany, below Italy (~3.30 kg/ha).
- **The pesticide map is a viticulture map, not just a cereal map.** Nationally, fungicides (46.1%) and herbicides (41.4%) dominate by weight; sulfur (24.4%, the classic anti-mildew vine treatment) and glyphosate (11.9%) are the top two substances.
- **Cereal country has the nitrate problem; wine country doesn't.** Herbicide-dominant (cereal) departments average 17.9 mg/L nitrate and 4.77% pesticide non-compliance in drinking water vs. 9.8 mg/L / 2.21% in fungicide-dominant (wine) departments.
- **Copper is wine country's own water problem.** River copper is 3.2× higher in fungicide-dominant departments (3.46 vs. 1.09 µg/L mean-when-detected). Source: copper sulfate, one of the only fungicides permitted in organic viticulture — organic doesn't remove this pathway. Of six heavy metals tested, copper is the only one with an agricultural signature; the other five (lead, cadmium, nickel, arsenic, chromium) track industrial or geological legacy instead.
- **Rivers mostly track drinking water, except at the extremes.** National averages are nearly identical (14.73 vs. 14.53 mg/L nitrate, r=+0.851 across departments) but rivers show acute peaks no treated supply does (Finistère 571 mg/L, 11× the limit).
- **Brittany is a nitrate story, not a pesticide story.** Rivers are genuinely elevated (livestock geography); pesticide non-compliance stays near-zero (0.14–0.3%, arable geography). Don't conflate the two.
- **Brittany's E. coli signal holds once weighted for sample size.** The raw ranking was dominated by small-n noise; restricted to departments with ≥500 samples, Brittany runs roughly double the rest.
- **The Famechon event**: one 14.955 µg/L glyphosate reading led to chloridazone desphényl (a banned sugar-beet herbicide's metabolite) — the largest cause of pesticide-metabolite non-compliance in French tap water nationally, affecting 2,009 communes. Risk known since 2007; monitoring didn't start until 2021.

## Land use & overproduction (cross-referenced against Agreste's national balance sheets, not in this repo's data files — see `README.md` sources)

- **89% of the French maize harvest never reaches a French plate** (49.6% animal feed, 39.4% export). More goes to fuel ethanol (530,000 t) than direct human food (395,000 t).
- **Landes, the #1 maize department (57.4% of farmland), is not the worst offender** — cleanest drinking water in the sample, growing population. Maize concentration alone doesn't predict the damage found elsewhere; cereal-rotation cash-crop farming does.
- **Established maize regions use ~20× less herbicide per hectare than marginal ones** (Landes 0.245 kg/ha vs. Eure/Eure-et-Loir 4.76–4.89 kg/ha) — mechanism not yet identified.
- **Barley: 66.8% exported, 0.19% eaten domestically** — the most extreme case found.
- **Rapeseed: 98.4% industrial**, tied to biodiesel (83% of French biodiesel comes from food-competing crops, mostly rapeseed).
- **Sugar beet connects four separate problems**: ~2× overproduction relative to consumption, 16% of the crop diverted to industrial alcohol, a 2020 law reintroducing banned neonicotinoids to protect its yield (ruled illegal by the EU Court of Justice in 2023), and its banned herbicide (chloridazone) is the single largest cause of the drinking-water contamination above.

## Political economy

- **Sainte-Soline's mega-bassine serves 12 farms** averaging 147 ha (2× the national average), growing almost exclusively maize and winter wheat "often destined for export" (Mediapart investigation). The host department (Deux-Sèvres) shows real elevated river nitrate (21.2 mg/L mean, 76.0 mg/L max) — genuine contamination, but the 12-farm claim is farm-level, not department-wide (Deux-Sèvres overall is only 11.2% maize).
- **CAP subsidy concentration is real but crop-type-specific, not size-specific.** Top 10% of recipients capture ~30% of funds. But the departments receiving the *most total* CAP money are mostly livestock/pasture country (Aveyron, Cantal, Puy-de-Dôme, Allier) — and show low pesticide/nitrate. The one cereal-belt department in that top-10 (Marne) runs 7–40× more pesticide-intensive per capita than the livestock departments beside it. CAP's per-hectare structure is crop-blind; the environmental outcome isn't.

## Explicitly excluded (tested, found nothing)

- **Cancer**: tested three ways (crude proxy, real exposure indicator, full 24-site sweep) — no result survives scrutiny except one flagged pancreatic-cancer/fungicide correlation (r=+0.587, n=83) that passed four robustness checks but came out of 100+ untested-for-multiple-comparisons pairs. Most apparent cancer correlations trace to a screening/diagnostic-access confound (urbanization), not disease.

## Still open

- **PFAS shows three different, unexplained geographies**: river concentration peaks in the southwest/Garonne basin, river detection-rate is universal-but-low across the north, drinking-water concentration peaks in a third cluster entirely (Bouches-du-Rhône highest at 0.300 µg/L). A specific Toulouse-area spike (751 ng/L, 7× the EU limit, ~100,000 people affected part of the year) remains unexplained and was reportedly worsening as of the last check.
