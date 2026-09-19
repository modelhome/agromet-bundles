#!/usr/bin/env python3
"""
One-time build script for crop-weather/regions.csv. Not part of the model image.

Picks the top corn-for-grain states by production and gives each one a single
production-weighted representative point, which is the location the model
fetches weather for.

Sources, both public and neither needing an API key:

- USDA NASS, 2022 Census of Agriculture, county-level "CORN, GRAIN -
  PRODUCTION, MEASURED IN BU", from the Quick Stats bulk export at
  https://www.nass.usda.gov/datasets/qs.census2022.txt.gz (about 310 MB
  gzipped). The Census is a complete enumeration with county coverage, so it
  needs none of the Quick Stats API's key handling.
- US Census Bureau 2023 Gazetteer county file, for each county's internal point
  (INTPTLAT / INTPTLONG).

Method: for each state, the representative point is the mean of its counties'
internal points weighted by county corn-for-grain production. Counties whose
production NASS withholds for disclosure reasons are excluded, and the script
reports how much production that leaves covered. Over a single state's extent,
averaging latitude and longitude on the plane is accurate to well under a
kilometre, so no spherical correction is applied.

Elevation comes from Open-Meteo's reported elevation at the chosen point, so it
is the same terrain height the weather is taken from.

Usage:

    python build_regions.py            # writes regions.csv beside this file
    python build_regions.py --states 12

Downloads are cached in .nass-cache/ (gitignored); delete it to force a refetch.
"""
import argparse
import csv
import gzip
import io
import json
import sys
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".nass-cache"
REGIONS_PATH = HERE / "regions.csv"

NASS_URL = "https://www.nass.usda.gov/datasets/qs.census2022.txt.gz"
NASS_VINTAGE = "USDA NASS 2022 Census of Agriculture"
GAZETTEER_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
    "2023_Gazetteer/2023_Gaz_counties_national.zip"
)
GAZETTEER_VINTAGE = "US Census Bureau 2023 Gazetteer county file"
ELEVATION_URL = "https://api.open-meteo.com/v1/forecast"

# The exact Quick Stats series. CLASS/PRODN/UTIL are pinned too, because
# "CORN - PRODUCTION" also covers silage, which is a different crop area.
SHORT_DESC = "CORN, GRAIN - PRODUCTION, MEASURED IN BU"
DEFAULT_STATES = 10

USER_AGENT = "modelhome-agromet-bundles/crop-weather (build_regions.py)"

# NASS withholds a value rather than publishing it when disclosure would
# identify an operation. These are the markers it uses in VALUE.
SUPPRESSED = {"(D)", "(Z)", "(NA)", "(X)", "(S)", ""}


def log(message):
    print(message, file=sys.stderr)


def download(url, path):
    """Fetch url to path unless it is already cached."""
    if path.exists():
        log(f"cached  {path.name}")
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    log(f"fetching {url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    partial = path.with_suffix(path.suffix + ".partial")
    with urllib.request.urlopen(request, timeout=300) as response, open(partial, "wb") as fh:
        while chunk := response.read(1 << 20):
            fh.write(chunk)
    partial.rename(path)
    log(f"saved   {path.name} ({path.stat().st_size / 1e6:.0f} MB)")
    return path


def county_points():
    """{5-digit FIPS: (lat, lon)} from the Census Gazetteer."""
    archive = download(GAZETTEER_URL, CACHE / "gaz_counties.zip")
    points = {}
    with zipfile.ZipFile(archive) as zf:
        name = next(n for n in zf.namelist() if n.endswith(".txt"))
        with zf.open(name) as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8")
            for row in csv.DictReader(text, delimiter="\t"):
                # The Gazetteer pads its last column name with trailing spaces.
                row = {k.strip(): (v.strip() if v else v) for k, v in row.items()}
                points[row["GEOID"]] = (float(row["INTPTLAT"]), float(row["INTPTLONG"]))
    log(f"gazetteer: {len(points)} counties")
    return points


def county_corn_production():
    """{state_alpha: {5-digit FIPS: bushels}} plus the withheld-county tally."""
    archive = download(NASS_URL, CACHE / "qs.census2022.txt.gz")
    by_state = defaultdict(dict)
    withheld = defaultdict(int)
    with gzip.open(archive, mode="rt", encoding="utf-8", errors="replace") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            if row["SHORT_DESC"] != SHORT_DESC:
                continue
            if row["AGG_LEVEL_DESC"] != "COUNTY" or row["DOMAIN_DESC"] != "TOTAL":
                continue
            state, county = row["STATE_ANSI"], row["COUNTY_ANSI"]
            if not state or not county:
                continue
            value = row["VALUE"].strip()
            if value in SUPPRESSED:
                withheld[row["STATE_ALPHA"]] += 1
                continue
            by_state[row["STATE_ALPHA"]][state + county] = int(value.replace(",", ""))
    if not by_state:
        raise SystemExit(f"no rows matched {SHORT_DESC!r}; the NASS export may have changed")
    log(f"nass: {sum(len(v) for v in by_state.values())} counties with a published value")
    return by_state, withheld


def weighted_centroid(counties, points):
    """Production-weighted mean of county internal points. Returns (lat, lon, covered)."""
    total = lat_sum = lon_sum = 0.0
    covered = 0
    for fips, bushels in counties.items():
        point = points.get(fips)
        if point is None:  # a county NASS reports that the Gazetteer does not
            continue
        lat_sum += point[0] * bushels
        lon_sum += point[1] * bushels
        total += bushels
        covered += 1
    if total == 0:
        raise SystemExit("no matched counties with production")
    lat, lon = lat_sum / total, lon_sum / total
    # A weighted mean must land inside the hull of its inputs; a point outside
    # the state's own county points would mean a bad join, not a bad weight.
    matched = [points[f] for f in counties if f in points]
    if not (min(p[0] for p in matched) <= lat <= max(p[0] for p in matched)
            and min(p[1] for p in matched) <= lon <= max(p[1] for p in matched)):
        raise SystemExit(f"representative point {lat:.4f},{lon:.4f} falls outside its counties")
    return lat, lon, covered


def elevation_m(lat, lon):
    """Open-Meteo's terrain elevation at a point, so it matches the weather grid."""
    params = {"latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}"}
    url = f"{ELEVATION_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return float(json.load(response)["elevation"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--states", type=int, default=DEFAULT_STATES,
                        help=f"how many top corn states to include (default {DEFAULT_STATES})")
    args = parser.parse_args()

    points = county_points()
    production, withheld = county_corn_production()

    ranked = sorted(production.items(), key=lambda kv: sum(kv[1].values()), reverse=True)
    top = ranked[:args.states]
    log(f"top {args.states} states by published county production: "
        + ", ".join(state for state, _ in top))

    rows = []
    for rank, (state, counties) in enumerate(top, start=1):
        lat, lon, covered = weighted_centroid(counties, points)
        elev = elevation_m(lat, lon)
        bushels = sum(counties.values())
        log(f"{rank:2d}. {state}  {lat:.4f}, {lon:.4f}  {elev:.0f} m  "
            f"{covered} counties, {bushels / 1e6:.0f}M bu, {withheld[state]} withheld")
        rows.append({
            "region_key": state.lower(),
            "state": state,
            "lat": round(lat, 4),
            "lon": round(lon, 4),
            "elev_m": round(elev, 1),
            "method": (f"production-weighted centroid of {covered} county internal points "
                       f"({withheld[state]} counties withheld by NASS for disclosure)"),
            "source": (f"{NASS_VINTAGE}, {SHORT_DESC}; {GAZETTEER_VINTAGE}; "
                       f"elevation from Open-Meteo"),
        })

    with open(REGIONS_PATH, "w", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["region_key", "state", "lat", "lon", "elev_m", "method", "source"])
        writer.writeheader()
        writer.writerows(rows)
    log(f"wrote {REGIONS_PATH} ({len(rows)} regions)")


if __name__ == "__main__":
    main()
