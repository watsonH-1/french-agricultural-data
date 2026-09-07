"""
Shared RPG loading/grid-overlay helpers, used by process_rpg.py (Part 1, maize
only) and the Part 2 analysis scripts that need the full crop mix (diversity
index, field size trend, riparian buffers).

Not a standalone script — import from it, don't run it directly.

LIVE WFS FETCHING — confirmed working 2026-08-29, no bulk download required:
IGN serves RPG parcels AND BD TOPO (hydrography, department boundaries, etc.)
through the same live WFS backend at data.geopf.fr, queryable by bounding box
with COUNT/STARTINDEX pagination — the same live-query pattern as Hub'Eau,
just discovered later in this project. This means you do NOT need to
download a regional RPG shapefile or a BD TOPO package to run
process_rpg.py, compute_diversity_index.py, compute_field_size_trend.py, or
compute_riparian_buffers.py for one or a few departments — use --department
instead of --input on any of them. Verified: CQL_FILTER by code_insee
resolves a department to its real boundary/bbox, and paginated GetFeature
calls return non-overlapping pages.

This does NOT replace a bulk download for a genuine nationwide run — paging
through tens of millions of parcels for all of France one COUNT=1000
request at a time would be slow and is more likely to hit undocumented rate
limits than Hub'Eau's live endpoints have been in this project's Part 1
experience. Live WFS is the right choice to start working today on specific
departments; revisit bulk download for an eventual full-France pass once
you're ready to scale up.
"""

import geopandas as gpd

GRID_CRS = "EPSG:2154"  # Lambert-93, meters — matches scripts/build_grid.py
WFS_BASE = "https://data.geopf.fr/wfs/ows"
WFS_PAGE_SIZE = 5000  # server silently caps actual returned features at 5000 regardless
# of requested COUNT (confirmed: COUNT=10000 returned exactly 5000) — using 1000 meant
# 5x more sequential requests than necessary for large departments (300+ requests for a
# 300k-parcel department), which made transient mid-fetch server errors far more likely
# to hit at least one page over the course of a full department fetch.


_DEPARTMENT_GEOMETRY_CACHE = {}


def get_department_geometry(code_insee):
    """
    Resolve a department INSEE code to its real polygon/multipolygon
    geometry in WGS84, via a live WFS query against IGN's BD TOPO
    department layer. Cached per process — every fetch function below needs
    this to filter bbox-fetched results down to the department's true shape
    (see fetch_rpg_parcels_wfs's docstring for why the bbox alone isn't
    enough).
    """
    if code_insee in _DEPARTMENT_GEOMETRY_CACHE:
        return _DEPARTMENT_GEOMETRY_CACHE[code_insee]

    params = {
        "SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature",
        "TYPENAMES": "BDTOPO_V3:departement", "OUTPUTFORMAT": "application/json",
        "CQL_FILTER": f"code_insee='{code_insee}'",
        "SRSNAME": "EPSG:4326",
    }
    resp = _get_with_retry(WFS_BASE, params, timeout=30)
    data = resp.json()
    if not data["features"]:
        raise ValueError(f"No department found for code_insee={code_insee!r}")
    gdf = gpd.GeoDataFrame.from_features(data["features"], crs="EPSG:4326")
    geometry = gdf.geometry.iloc[0]
    _DEPARTMENT_GEOMETRY_CACHE[code_insee] = geometry
    return geometry


def get_department_bbox(code_insee):
    """
    Resolve a department INSEE code to its (minx, miny, maxx, maxy) bbox in
    WGS84 — no download needed, no local departments_france.geojson
    dependency (this works for any department nationwide).
    """
    return get_department_geometry(code_insee).bounds


def _get_with_retry(url, params, retries=3, backoff_s=2, timeout=60):
    """
    data.geopf.fr's WFS has been observed to time out transiently under this
    project (2026-08-29) — retry with backoff rather than letting one slow
    response kill a whole department fetch, same reasoning as Hub'Eau's
    client-side retry in src/data/http.js.
    """
    import time
    import requests

    last_error = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            if resp.status_code == 429 or resp.status_code >= 500:
                last_error = requests.exceptions.HTTPError(f"HTTP {resp.status_code}")
            else:
                resp.raise_for_status()
                return resp
        except requests.exceptions.RequestException as e:
            last_error = e
        if attempt < retries:
            time.sleep(backoff_s * 2 ** attempt)
    raise last_error


def _fetch_wfs_paginated(type_name, bbox, extra_params=None, page_size=WFS_PAGE_SIZE, max_features=None):
    """Generic paginated WFS GetFeature fetch, bbox in WGS84 (minx, miny, maxx, maxy)."""
    import time
    minx, miny, maxx, maxy = bbox
    all_features = []
    start_index = 0
    while True:
        count = page_size if max_features is None else min(page_size, max_features - len(all_features))
        if count <= 0:
            break
        params = {
            "SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature",
            "TYPENAMES": type_name, "OUTPUTFORMAT": "application/json",
            "BBOX": f"{minx},{miny},{maxx},{maxy},EPSG:4326",
            "COUNT": count, "STARTINDEX": start_index,
        }
        if extra_params:
            params.update(extra_params)
        # A handful of large-department fetches have failed with a JSONDecodeError even
        # though _get_with_retry saw HTTP 200 — the server occasionally returns a 200
        # with a non-JSON (empty/HTML error) body, which slips past the status-code-only
        # retry check above. Retry the parse itself, re-fetching on failure, rather than
        # letting one bad page abort a fetch that's otherwise hundreds of pages deep.
        page = None
        last_parse_error = None
        for parse_attempt in range(4):
            resp = _get_with_retry(WFS_BASE, params)
            try:
                page = resp.json()
                break
            except ValueError as e:
                last_parse_error = e
                if parse_attempt < 3:
                    time.sleep(3 * (parse_attempt + 1))
        if page is None:
            raise last_parse_error
        features = page.get("features", [])
        all_features.extend(features)
        if len(features) < count:
            break  # last page
        start_index += count
        if max_features is not None and len(all_features) >= max_features:
            break
    return all_features


def _filter_to_department(gdf_wgs84, code_insee):
    """
    Keep only features whose centroid falls within the department's real
    polygon — NOT its bounding box. A department's bbox rectangle almost
    always extends past its (irregular) true shape into neighboring
    departments, so a bbox-only WFS fetch returns real neighbor-owned
    features too. Confirmed with a real test: fetching "department 28"
    (Eure-et-Loir) returned parcels that sjoin correctly attributes to 7
    different neighboring departments. Without this filter, those slivers
    would (a) inflate/corrupt --department-mode department-level output for
    the wrong department, and worse, (b) get double-counted at the grid-cell
    level, since the SAME physical parcel sitting in a bbox-overlap zone
    would be fetched independently by both neighbors' own --department
    calls and then summed as if they were two disjoint contributions.
    Centroid-based (not full-geometry "within") so a parcel merely touching
    the border isn't dropped by both neighbors' fetches — it's attributed
    to exactly one, by wherever its centroid actually sits.
    """
    dept_geom = get_department_geometry(code_insee)
    centroids = gdf_wgs84.geometry.centroid
    return gdf_wgs84[centroids.within(dept_geom)]


def fetch_rpg_parcels_wfs(bbox_or_department, year, page_size=WFS_PAGE_SIZE, max_features=None):
    """
    Live-fetch RPG parcels for a bbox or department code (INSEE code, resolved
    via get_department_bbox), for the given RPG campaign year, via WFS —
    no download required. Returns a GeoDataFrame in the same shape
    load_rpg_parcels() would (CODE_CULTU, ID_PARCEL where present, area_ha,
    geometry), lowercased-to-uppercased to match the shapefile field naming
    convention used elsewhere in this project (WFS returns lowercase
    id_parcel/code_cultu; shapefiles use ID_PARCEL/CODE_CULTU).

    When called with a department code (not a raw bbox), results are
    filtered to that department's true shape — see _filter_to_department().
    """
    is_department = isinstance(bbox_or_department, str)
    bbox = get_department_bbox(bbox_or_department) if is_department else bbox_or_department
    type_name = f"RPG.{year}:parcelles_graphiques"
    features = _fetch_wfs_paginated(type_name, bbox, page_size=page_size, max_features=max_features)
    print(f"Fetched {len(features)} RPG parcels via live WFS for {bbox_or_department}, year {year}.")

    gdf = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
    gdf = gdf.rename(columns={"code_cultu": "CODE_CULTU", "id_parcel": "ID_PARCEL"})
    if is_department:
        before = len(gdf)
        gdf = _filter_to_department(gdf, bbox_or_department)
        print(f"  kept {len(gdf)} of {before} after filtering to {bbox_or_department}'s true shape (dropped neighbor slivers).")
    gdf = gdf.to_crs(GRID_CRS)
    gdf["area_ha"] = gdf.geometry.area / 10_000
    return gdf


def fetch_hydrography_wfs(bbox_or_department, layer="BDTOPO_V3:cours_d_eau", page_size=WFS_PAGE_SIZE):
    """
    Live-fetch BD TOPO hydrography (river/stream centerlines) for a bbox or
    department code, via WFS — no BD TOPO download required. Use
    layer="BDTOPO_V3:troncon_hydrographique" for the more detailed
    watercourse-segment layer if cours_d_eau proves too coarse.

    Filtered to the department's true shape when called by code — same
    reasoning as fetch_rpg_parcels_wfs's _filter_to_department() call.
    """
    is_department = isinstance(bbox_or_department, str)
    bbox = get_department_bbox(bbox_or_department) if is_department else bbox_or_department
    features = _fetch_wfs_paginated(layer, bbox, page_size=page_size)
    print(f"Fetched {len(features)} hydrography features via live WFS for {bbox_or_department}.")
    gdf = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
    if is_department:
        gdf = _filter_to_department(gdf, bbox_or_department)
    return gdf.to_crs(GRID_CRS)


def load_rpg_parcels(input_path, keep_columns=None):
    """
    Read an RPG parcels shapefile/geopackage and return a GeoDataFrame with
    all crop codes (not filtered to maize) plus an area_ha column.

    RPG field names have changed across editions — this expects CODE_CULTU
    (crop code) and, where present, ID_PARCEL (for multi-year joins). Check
    the edition's notice technique if either is missing.
    """
    print(f"Reading RPG parcels from {input_path} ...")
    gdf = gpd.read_file(input_path)
    if "CODE_CULTU" not in gdf.columns:
        raise ValueError(
            f"Expected column CODE_CULTU not found. Columns present: {list(gdf.columns)}. "
            "RPG field names have changed across editions — check the notice technique."
        )
    gdf["area_ha"] = gdf.geometry.area / 10_000
    if keep_columns:
        missing = [c for c in keep_columns if c not in gdf.columns]
        if missing:
            print(f"Warning: requested columns not found and will be skipped: {missing}")
        cols = [c for c in keep_columns if c in gdf.columns] + ["area_ha", "geometry"]
        gdf = gdf[cols]
    return gdf


def add_rpg_source_args(parser):
    """
    Add the shared --input/--department/--max-features arguments to an
    argparse parser, so every RPG-consuming script offers the same choice
    between a local shapefile and a live WFS fetch.
    """
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--input", help="Path to a local RPG parcels shapefile/geopackage")
    group.add_argument("--department", help="INSEE department code (e.g. 28) — fetches parcels live via WFS instead, no download needed")
    parser.add_argument("--max-features", type=int, default=None, help="Cap on parcels fetched via --department (WFS mode only) — useful for a quick test before a full department pull")


def resolve_rpg_parcels(args, year, keep_columns=None):
    """Dispatch to load_rpg_parcels() or fetch_rpg_parcels_wfs() based on which of --input/--department was passed."""
    if args.input:
        return load_rpg_parcels(args.input, keep_columns=keep_columns)
    gdf = fetch_rpg_parcels_wfs(args.department, year, max_features=args.max_features)
    if keep_columns:
        cols = [c for c in keep_columns if c in gdf.columns] + ["area_ha", "geometry"]
        gdf = gdf[cols]
    return gdf


def load_crop_family_lookup(path="data/reference/crop_code_to_family.csv"):
    """Load the RPG code -> functional family table (see Section 3.4)."""
    import pandas as pd
    df = pd.read_csv(path)
    return dict(zip(df["rpg_code"], df["crop_family"]))


def overlay_parcels_on_grid(parcels_gdf, grid_path):
    """
    Area-weighted overlay of RPG parcels onto grid cells: a parcel straddling
    two cells becomes two rows, each with area_ha recomputed as just the
    portion inside that cell. This matters more at 5km grid resolution than
    it did for Part 1's department-level `sjoin(..., predicate='within')` —
    department polygons are large enough that edge parcels are a rounding
    error; grid cells are not.

    Both inputs must be reprojected to the grid's CRS (Lambert-93) before
    the overlay, since RPG source CRS varies by download and overlay area
    math needs to happen in a planar meter CRS, not degrees.
    """
    grid = gpd.read_file(grid_path)
    if grid.crs is None:
        raise ValueError(f"{grid_path} has no CRS set — expected one from scripts/build_grid.py")

    parcels = parcels_gdf.to_crs(GRID_CRS)
    grid = grid.to_crs(GRID_CRS)

    overlaid = gpd.overlay(parcels, grid[["cell_id", "geometry"]], how="intersection")
    overlaid["area_ha"] = overlaid.geometry.area / 10_000
    return overlaid
