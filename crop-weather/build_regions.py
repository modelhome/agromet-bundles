#!/usr/bin/env python3
"""
One-time build script for crop-weather/regions.csv. Not part of the model image.

Picks the top corn-for-grain states by production and gives each one a
production-weighted representative point, which is the location the model
fetches weather for. A state where irrigation covers a large share of the corn
gets two points instead of one -- an irrigated stratum and a rainfed one --
because a single point averages two crops that experience different weather.

Sources, both public and neither needing an API key:

- USDA NASS, 2022 Census of Agriculture, from the Quick Stats bulk export at
  https://www.nass.usda.gov/datasets/qs.census2022.txt.gz (about 310 MB
  gzipped). The Census is a complete enumeration with county coverage, so it
  needs none of the Quick Stats API's key handling. Four series are read, all
  at DOMAIN_DESC = TOTAL: county "CORN, GRAIN - PRODUCTION, MEASURED IN BU",
  county "CORN, GRAIN - ACRES HARVESTED", county and state "CORN, GRAIN,
  IRRIGATED - ACRES HARVESTED", and the two state-level stratum yields below.
- US Census Bureau 2023 Gazetteer county file, for each county's internal point
  (INTPTLAT / INTPTLONG).

Method: for each state, the representative point is the mean of its counties'
internal points weighted by county corn-for-grain production. Counties whose
production NASS withholds for disclosure reasons are excluded, and the script
reports how much production that leaves covered. Over a single state's extent,
averaging latitude and longitude on the plane is accurate to well under a
kilometre, so no spherical correction is applied.

Strata: a state is split when irrigation covers SPLIT_THRESHOLD or more of its
harvested corn acres. NASS publishes no irrigated *production* at any
aggregation level, so a stratum's weight cannot be read off; it is
reconstructed by apportioning each county's published production between the
two strata in proportion to acres times a state-level stratum yield:

    share_irrigated(county) = a_irr * Y_irr / (a_irr * Y_irr + a_rain * Y_rain)

Because the published county production is divided rather than re-estimated,
the strata sum exactly to the state's undivided weight, and recombining the two
stratum points by weight reproduces the single point this script used to emit.
Only the *ratio* Y_irr / Y_rain matters, not the yield levels; a bias common to
both cancels. The cost is that county acres are multiplied by a state yield, so
one irrigated-to-rainfed yield ratio is applied to every county in a state and
within-state variation in that ratio is lost.

Note that Y_irr and Y_rain are operation-level classes -- farms that irrigate
all of their corn and farms that irrigate none. Operations irrigating only part
of their corn appear in neither yield series, though their acres are still
apportioned using the ratio taken from the two that do.

The strata are not "the better half and the worse half". The sign of the
irrigated yield gap flips by state: irrigating operations out-yield non-
irrigating ones by 105 percent in Kansas and 55 percent in Nebraska, but yield
8 percent less in Iowa and 20 percent less in Ohio, where irrigation sits on
marginal ground.

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
import math
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
# Only used to report how far apart a state's two strata sit.
KM_PER_DEGREE = 111.0

# The exact Quick Stats series. CLASS/PRODN/UTIL are pinned too, because
# "CORN - PRODUCTION" also covers silage, which is a different crop area.
SHORT_DESC = "CORN, GRAIN - PRODUCTION, MEASURED IN BU"
ACRES_DESC = "CORN, GRAIN - ACRES HARVESTED"
IRRIGATED_ACRES_DESC = "CORN, GRAIN, IRRIGATED - ACRES HARVESTED"
# Operation-level yield classes, published at state level only; see the module
# docstring. Their ratio apportions county production between the two strata.
IRRIGATED_YIELD_DESC = "CORN, GRAIN, IRRIGATED, ENTIRE CROP - YIELD, MEASURED IN BU / ACRE"
RAINFED_YIELD_DESC = "CORN, GRAIN, IRRIGATED, NONE OF CROP - YIELD, MEASURED IN BU / ACRE"
DEFAULT_STATES = 10

# A state is split into strata when irrigation covers this share or more of its
# harvested corn acres. Measured on the 2022 Census, the ten states fall either
# side of it with a wide gap and nothing near the line:
#
#   NE 52.7 %  split          MO  9.4 %  IL 3.3 %
#   KS 25.4 %  split          IN  6.3 %  IA 1.2 %
#                             WI  4.7 %  OH 0.5 %
#                             MN  3.9 %
#                             SD  3.5 %
#
# Below about 10 percent a split buys nothing, and two further measurements say
# to stop at NE and KS. Counties whose irrigated figure NASS withholds hold 0.0
# percent of Nebraska's production and 4.9 percent of Kansas's, against 33.8
# percent in Missouri and 47.5 percent in Iowa, so treating a withheld figure as
# zero is almost costless here and a real distortion elsewhere. And Missouri's
# irrigated corn is bimodal -- 58 percent in the Bootheel, 9 percent in the
# northwest river valley 390 km away -- so its stratum mean would land at
# 37.60, -91.08, in the Ozarks, where no irrigated corn grows.
SPLIT_THRESHOLD = 0.20

STRATUM_IRRIGATED = "irrigated"
STRATUM_RAINFED = "rainfed"
STRATUM_ALL = "all"

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


def read_nass():
    """
    Every NASS series this script needs, from one pass over the 295 MB export.

    Returns a dict of:
      production        {state: {FIPS: bushels}}   published county corn for grain
      acres             {state: {FIPS: acres}}     published county harvested acres
      irrigated         {state: {FIPS: acres}}     published county irrigated acres
      withheld          {state: count}             counties whose production is withheld
      withheld_irrigated{state: count}             counties whose irrigated acres are withheld
      state_acres       {state: acres}             state harvested acres
      state_irrigated   {state: acres}             state irrigated acres
      yields            {state: (irrigated, rainfed)}  bu/acre, operation-level classes
    """
    archive = download(NASS_URL, CACHE / "qs.census2022.txt.gz")
    county = {key: defaultdict(dict) for key in (SHORT_DESC, ACRES_DESC, IRRIGATED_ACRES_DESC)}
    withheld = defaultdict(int)
    withheld_irrigated = defaultdict(int)
    state_level = defaultdict(dict)
    with gzip.open(archive, mode="rt", encoding="utf-8", errors="replace") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            if row["DOMAIN_DESC"] != "TOTAL":
                continue
            desc, level, alpha = row["SHORT_DESC"], row["AGG_LEVEL_DESC"], row["STATE_ALPHA"]
            value = row["VALUE"].strip()
            if level == "COUNTY" and desc in county:
                state, fips_county = row["STATE_ANSI"], row["COUNTY_ANSI"]
                if not state or not fips_county:
                    continue
                if value in SUPPRESSED:
                    if desc == SHORT_DESC:
                        withheld[alpha] += 1
                    elif desc == IRRIGATED_ACRES_DESC:
                        withheld_irrigated[alpha] += 1
                    continue
                county[desc][alpha][state + fips_county] = float(value.replace(",", ""))
            elif level == "STATE" and value not in SUPPRESSED and desc in (
                    ACRES_DESC, IRRIGATED_ACRES_DESC, IRRIGATED_YIELD_DESC, RAINFED_YIELD_DESC):
                state_level[desc][alpha] = float(value.replace(",", ""))
    if not county[SHORT_DESC]:
        raise SystemExit(f"no rows matched {SHORT_DESC!r}; the NASS export may have changed")
    log(f"nass: {sum(len(v) for v in county[SHORT_DESC].values())} counties with a "
        f"published production value")
    return {
        "production": county[SHORT_DESC],
        "acres": county[ACRES_DESC],
        "irrigated": county[IRRIGATED_ACRES_DESC],
        "withheld": withheld,
        "withheld_irrigated": withheld_irrigated,
        "state_acres": state_level[ACRES_DESC],
        "state_irrigated": state_level[IRRIGATED_ACRES_DESC],
        "yields": {state: (state_level[IRRIGATED_YIELD_DESC].get(state),
                           state_level[RAINFED_YIELD_DESC].get(state))
                   for state in state_level[ACRES_DESC]},
    }


def irrigated_share(nass, state):
    """Irrigated share of the state's harvested corn acres, as a fraction."""
    total = nass["state_acres"].get(state)
    if not total:
        raise SystemExit(f"{state}: no state-level {ACRES_DESC!r}")
    return nass["state_irrigated"].get(state, 0.0) / total


def irrigated_coverage(nass, state):
    """Share of the state's irrigated acres its published counties account for."""
    total = nass["state_irrigated"].get(state, 0.0)
    if not total:
        return 1.0
    return sum(nass["irrigated"][state].values()) / total


def apportion(nass, state):
    """
    Split a state's published county production between irrigated and rainfed.

    Returns (irrigated_weights, rainfed_weights), each {FIPS: bushels}, summing
    together to the state's published production. NASS publishes no irrigated
    production anywhere, so the split comes from county acres times a state
    stratum yield; only the ratio of the two yields matters. See the module
    docstring.
    """
    production, acres, irrigated = (nass[k][state] for k in ("production", "acres", "irrigated"))
    yield_irrigated, yield_rainfed = nass["yields"].get(state, (None, None))
    if not yield_irrigated or not yield_rainfed:
        raise SystemExit(
            f"{state}: needs both {IRRIGATED_YIELD_DESC!r} and {RAINFED_YIELD_DESC!r} "
            f"at state level to apportion production between strata")

    irrigated_weights, rainfed_weights = {}, {}
    for fips, bushels in production.items():
        acres_total = acres.get(fips)
        # Measured on the 2022 Census: every county with published production
        # also publishes harvested acres, and irrigated never exceeds total.
        # Both are assumptions the apportionment rests on, so a future vintage
        # that breaks one should stop the build rather than be worked around.
        if acres_total is None:
            raise SystemExit(
                f"{state} county {fips}: production is published but "
                f"{ACRES_DESC!r} is not, so it cannot be apportioned")
        acres_irrigated = irrigated.get(fips, 0.0)  # withheld is treated as zero
        if acres_irrigated > acres_total:
            raise SystemExit(
                f"{state} county {fips}: irrigated acres {acres_irrigated:.0f} exceed "
                f"harvested acres {acres_total:.0f}")
        acres_rainfed = acres_total - acres_irrigated
        notional_irrigated = acres_irrigated * yield_irrigated
        notional_rainfed = acres_rainfed * yield_rainfed
        notional = notional_irrigated + notional_rainfed
        if notional <= 0:  # a county with production but no harvested acres at all
            rainfed_weights[fips] = rainfed_weights.get(fips, 0.0) + bushels
            continue
        share = notional_irrigated / notional
        if share > 0:
            irrigated_weights[fips] = bushels * share
        if share < 1:
            rainfed_weights[fips] = bushels * (1 - share)
    return irrigated_weights, rainfed_weights


def strata_for(nass, state):
    """
    [(stratum, {FIPS: weight})] for one state: one entry, or two when split.

    Splitting is decided by SPLIT_THRESHOLD against the irrigated share of
    harvested acres.
    """
    if irrigated_share(nass, state) < SPLIT_THRESHOLD:
        return [(STRATUM_ALL, nass["production"][state])]
    irrigated_weights, rainfed_weights = apportion(nass, state)
    return [(STRATUM_IRRIGATED, irrigated_weights), (STRATUM_RAINFED, rainfed_weights)]


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


def region_key(state, stratum):
    """ia for an unsplit state; ne_irrigated / ne_rainfed for a split one."""
    if stratum == STRATUM_ALL:
        return state.lower()
    return f"{state.lower()}_{stratum}"


def method_text(nass, state, stratum, covered):
    """The row's `method` column: how this point was placed, and why it exists."""
    share = irrigated_share(nass, state) * 100
    threshold = SPLIT_THRESHOLD * 100
    withheld = nass["withheld"][state]
    if stratum == STRATUM_ALL:
        return (f"single point, not split into strata: production-weighted centroid of "
                f"{covered} county internal points ({withheld} counties withheld by NASS "
                f"for disclosure); irrigation covers {share:.1f} percent of this state's "
                f"harvested corn acres, below the {threshold:.0f} percent split threshold")
    return (f"{stratum} stratum: production-weighted centroid of {covered} county internal "
            f"points ({withheld} counties withheld by NASS for disclosure), weighting each "
            f"county by the share of its published corn production apportioned to this "
            f"stratum (county acres times a state-level stratum yield, so county and state "
            f"resolution are mixed; NASS publishes no irrigated production at any level); "
            f"irrigation covers {share:.1f} percent of this state's harvested corn acres, "
            f"at or above the {threshold:.0f} percent split threshold, and published "
            f"counties account for {irrigated_coverage(nass, state) * 100:.1f} percent of "
            f"its irrigated acres")


def source_text(stratum):
    """The row's `source` column: every series the point rests on."""
    series = [SHORT_DESC]
    if stratum != STRATUM_ALL:
        series += [ACRES_DESC, IRRIGATED_ACRES_DESC,
                   IRRIGATED_YIELD_DESC, RAINFED_YIELD_DESC]
    return (f"{NASS_VINTAGE}, {'; '.join(series)}; {GAZETTEER_VINTAGE}; "
            f"elevation from Open-Meteo")


def log_diagnostics(nass, states, points):
    """
    Per state: the numbers behind the split decision, for every state.

    The unsplit states are reported too, deliberately. The threshold excludes
    Missouri on its irrigated share, but the reason to be glad about that is
    the distance between the strata it would have produced, and that only shows
    up if the script prints it for states it does not split.
    """
    log("")
    log("state  irr.share  area.cov  withheld.prod  strata.apart  split")
    for state in states:
        share = irrigated_share(nass, state) * 100
        coverage = irrigated_coverage(nass, state) * 100
        placed = nass["production"][state]
        withheld_share = 100 * sum(
            bushels for fips, bushels in placed.items() if fips not in nass["irrigated"][state]
        ) / sum(placed.values())
        # A state with no irrigated corn, or without both stratum yields, has no
        # second point to measure against. That is worth printing, not raising:
        # this is a diagnostic, and only a state over the threshold is built.
        apart = "n/a"
        if all(nass["yields"].get(state, (None, None))) and nass["irrigated"][state]:
            irrigated_weights, rainfed_weights = apportion(nass, state)
            if irrigated_weights and rainfed_weights:
                lat_i, lon_i, _ = weighted_centroid(irrigated_weights, points)
                lat_r, lon_r, _ = weighted_centroid(rainfed_weights, points)
                # Plane distance, good to well under a kilometre over one state.
                apart = "%.0f km" % math.hypot(
                    (lat_i - lat_r) * KM_PER_DEGREE,
                    (lon_i - lon_r) * KM_PER_DEGREE * math.cos(math.radians(lat_r)))
        split = "yes" if share >= SPLIT_THRESHOLD * 100 else "no"
        log(f"{state:5s}  {share:8.1f}%  {coverage:7.1f}%  {withheld_share:12.1f}%  "
            f"{apart:>12s}  {split}")
    log("")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--states", type=int, default=DEFAULT_STATES,
                        help=f"how many top corn states to include (default {DEFAULT_STATES})")
    args = parser.parse_args()

    points = county_points()
    nass = read_nass()
    production = nass["production"]

    ranked = sorted(production.items(), key=lambda kv: sum(kv[1].values()), reverse=True)
    top = [state for state, _ in ranked[:args.states]]
    log(f"top {args.states} states by published county production: " + ", ".join(top))

    log_diagnostics(nass, top, points)

    # Every row's weight is its share of the production of the whole region set,
    # so the weights sum to 1 whether or not a state is split.
    total_bushels = sum(sum(production[state].values()) for state in top)

    rows = []
    for rank, state in enumerate(top, start=1):
        for stratum, weights in strata_for(nass, state):
            lat, lon, covered = weighted_centroid(weights, points)
            elev = elevation_m(lat, lon)
            bushels = sum(weights.values())
            key = region_key(state, stratum)
            log(f"{rank:2d}. {key:13s} {lat:.4f}, {lon:.4f}  {elev:.0f} m  "
                f"{covered} counties, {bushels / 1e6:.0f}M bu, "
                f"weight {bushels / total_bushels:.4f}")
            rows.append({
                "region_key": key,
                "state": state,
                "stratum": stratum,
                "weight": round(bushels / total_bushels, 4),
                "lat": round(lat, 4),
                "lon": round(lon, 4),
                "elev_m": round(elev, 1),
                "method": method_text(nass, state, stratum, covered),
                "source": source_text(stratum),
            })

    # The weights partition one production total, so they must sum to 1. A
    # failure here means a county was counted twice or dropped, not a rounding
    # artefact: the tolerance only absorbs the 4-decimal rounding above.
    total_weight = sum(row["weight"] for row in rows)
    if abs(total_weight - 1.0) > 5e-4:
        raise SystemExit(f"weights sum to {total_weight}, not 1.0")

    with open(REGIONS_PATH, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=[
            "region_key", "state", "stratum", "weight",
            "lat", "lon", "elev_m", "method", "source"])
        writer.writeheader()
        writer.writerows(rows)
    log(f"wrote {REGIONS_PATH} ({len(rows)} regions from {len(top)} states, "
        f"weights sum to {total_weight:.4f})")


if __name__ == "__main__":
    main()
