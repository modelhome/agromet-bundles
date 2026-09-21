#!/usr/bin/env python3
"""
Validation for the crop-weather bundle. Not part of the model image.

This is the decisive test for the whole node: the output exists to be eaten by
PCSE/WOFOST, so the check loads it into a real WeatherDataProvider and runs a
maize simulation to a finished yield. Loading alone is not enough -- a unit slip
can load fine and produce nonsense -- so the run has to complete and the yields
have to stand in the right relationship to each other.

The maize runs at three production levels: potential, water-limited rainfed,
and water-limited with soil-moisture-triggered irrigation. Potential production
is what this bundle reports, and it is the right default here because it
isolates the weather from soil parameters this node does not own -- but it
assumes water is never limiting, which is the same thing as perfect irrigation
on every acre. Running all three makes that explicit and gives assertions that
can fail: irrigating a water-limited run has to reproduce the potential yield.
The soil is PCSE's generic DummySoilDataProvider, not a real one, so the
numbers show direction and rough magnitude only.

It also checks the unit conversions against PCSE's own helpers, the growing
degree day and stress-flag arithmetic against hand-worked examples, and the
committed region table.

PCSE is a development dependency only; it must never appear in the bundle's
Dockerfile.

Usage:

    uv run --no-project --python 3.12 --with pcse==6.0.13 \
        python check_weather.py run/crop_weather_daily.output.json
"""
import csv
import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path

import runner

HERE = Path(__file__).resolve().parent

FAILURES = []
CHECKS = 0


def check(label, condition, detail=""):
    global CHECKS
    CHECKS += 1
    if condition:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}  {detail}")
        FAILURES.append(f"{label} {detail}".strip())


def close(a, b, tol):
    return abs(a - b) <= tol


# --- unit conversions --------------------------------------------------------

def check_units():
    """Every conversion in to_pcse_row, against PCSE's own helpers where they exist."""
    print("unit conversions")
    from pcse.util import wind10to2

    check("WIND_10M_TO_2M equals pcse.util.wind10to2",
          close(runner.WIND_10M_TO_2M, wind10to2(1.0), 1e-12),
          f"{runner.WIND_10M_TO_2M} vs {wind10to2(1.0)}")
    check("the 2 m factor is 0.71833, not the 0.75 rule of thumb",
          close(runner.WIND_10M_TO_2M, 0.71833, 1e-5))

    # Magnus at 20 degC dew point: saturation vapour pressure at 20 degC.
    check("vapour pressure at 20 degC dew point is 23.4 hPa",
          close(runner.vapour_pressure_hpa(20.0), 23.4, 0.1),
          f"{runner.vapour_pressure_hpa(20.0):.3f}")
    check("vapour pressure at 0 degC dew point is 6.108 hPa",
          close(runner.vapour_pressure_hpa(0.0), 6.108, 1e-6))

    row = runner.to_pcse_row({
        "temperature_2m_min": 12.0,
        "temperature_2m_max": 28.0,
        "precipitation_sum": 25.0,          # mm
        "shortwave_radiation_sum": 24.0,    # MJ/m2/day
        "dew_point_2m_mean": 15.0,          # degC
        "wind_speed_10m_mean": 4.0,         # m/s at 10 m
    })
    check("IRRAD: 24 MJ/m2/day -> 2.4e7 J/m2/day", close(row["IRRAD"], 2.4e7, 1.0))
    check("RAIN: 25 mm -> 2.5 cm/day", close(row["RAIN"], 2.5, 1e-9))
    check("WIND: 4 m/s at 10 m -> 2.873 m/s at 2 m", close(row["WIND"], 2.8733, 1e-3))
    check("TMIN/TMAX pass through in degC", row["TMIN"] == 12.0 and row["TMAX"] == 28.0)

    # A slip would usually land outside PCSE's own range checks.
    from pcse.base.weather import WeatherDataContainer
    for name, (lo, hi) in WeatherDataContainer.ranges.items():
        if name in row:
            check(f"{name} inside PCSE's range {lo}..{hi}", lo <= row[name] <= hi, str(row[name]))


def check_percentile():
    """runner.percentile must match numpy's default, which is what PCSE uses."""
    print("percentile")
    try:
        import numpy as np
    except ImportError:
        print("  skip  numpy not available")
        return
    values = sorted([0.12, 0.45, 0.33, 0.78, 0.51, 0.22, 0.66, 0.9, 0.05, 0.41, 0.6])
    for q in (5, 25, 50, 98):
        check(f"percentile({q}) matches numpy",
              close(runner.percentile(values, q), float(np.percentile(values, q)), 1e-12))


def check_payload_guards():
    """A malformed Open-Meteo response must fail clearly, not raise a KeyError."""
    print("payload guards")
    full = {
        "daily": {
            "time": ["2026-06-01", "2026-06-02"],
            "temperature_2m_max": [28.0, 27.0],
            "temperature_2m_min": [15.0, 14.0],
            "precipitation_sum": [0.0, 1.0],
            "shortwave_radiation_sum": [25.0, 20.0],
            "dew_point_2m_mean": [12.0, 11.0],
            "wind_speed_10m_mean": [3.0, 2.5],
        }
    }
    parsed = runner.daily_payload(full, "test")
    check("a complete payload yields every day", len(parsed) == 2, str(len(parsed)))
    check("every parsed day carries all six variables",
          all(set(v) == set(runner.DAILY_VARIABLES) for v in parsed.values()))

    missing_block = {"daily": {k: v for k, v in full["daily"].items()
                               if k != "shortwave_radiation_sum"}}
    try:
        runner.daily_payload(missing_block, "test")
        check("a payload missing a variable raises RunError", False, "no error raised")
    except runner.RunError as exc:
        check("a payload missing a variable raises RunError, naming it",
              "shortwave_radiation_sum" in str(exc), str(exc))
    except KeyError as exc:
        check("a payload missing a variable raises RunError, not KeyError",
              False, f"KeyError {exc}")

    null_day = {"daily": {k: (list(v) if k == "time" else [v[0], None])
                          for k, v in full["daily"].items()}}
    parsed = runner.daily_payload(null_day, "test")
    check("a day with a null value is dropped whole",
          list(parsed) == ["2026-06-01"], str(list(parsed)))

    try:
        runner.daily_payload({"daily": {}}, "test")
        check("an empty daily block raises RunError", False, "no error raised")
    except runner.RunError:
        check("an empty daily block raises RunError", True)


# --- the forecast tail -------------------------------------------------------
# Open-Meteo publishes the last slot of its 16-day grid before its model run
# has filled it: the date is in `time` and every variable on it is null. These
# checks pin down which shapes of missing day are tolerated and which fail.
# They stub runner.get_json, so they make no network call and cannot be turned
# green or red by what the live API happens to be serving.

TAIL_TODAY = date(2026, 9, 21)
TAIL_START = date(2026, 9, 10)
# ERA5 lags about six days, so the archive leg stops well short of today.
TAIL_ARCHIVE_END = date(2026, 9, 15)


def days_between(first, last):
    return [first + timedelta(days=n) for n in range((last - first).days + 1)]


def daily_block(days, blank=()):
    """An Open-Meteo daily block over `days`, with every variable null on `blank`."""
    block = {"time": [day.isoformat() for day in days]}
    for offset, name in enumerate(runner.DAILY_VARIABLES):
        block[name] = [None if day in blank else float(offset + 1) for day in days]
    return {"daily": block}


def fetch_stubbed(first_day, last_day, archive, forecast, today=TAIL_TODAY):
    """runner.fetch_window over canned payloads instead of the live API."""
    real = runner.get_json
    runner.get_json = (
        lambda url, params: archive if url == runner.ARCHIVE_URL else forecast)
    try:
        return runner.fetch_window(
            {"region_key": "test", "lat": 42.0, "lon": -93.0},
            first_day, last_day, today)
    finally:
        runner.get_json = real


def expect_run_error(label, first_day, last_day, archive, forecast, needle,
                     today=TAIL_TODAY):
    try:
        fetch_stubbed(first_day, last_day, archive, forecast, today)
        check(label, False, "no error raised")
    except runner.RunError as exc:
        check(label, needle in str(exc), str(exc))


def check_fetch_window():
    print("forecast tail")
    last_day = TAIL_TODAY + timedelta(days=runner.DEFAULT_FORECAST_DAYS)  # 2026-10-06
    archive = daily_block(days_between(TAIL_START, TAIL_ARCHIVE_END))
    forecast_days = days_between(TAIL_ARCHIVE_END + timedelta(days=1), last_day)

    # The condition this brief exists for: the grid's last day is all nulls.
    padded = daily_block(forecast_days, blank=[last_day])
    served_last = (last_day - timedelta(days=1)).isoformat()
    try:
        window = fetch_stubbed(TAIL_START, last_day, archive, padded)
    except runner.RunError as exc:
        # Report it rather than letting it end the run: this is the assertion
        # that fails when the tolerance is removed, and it should read as a
        # failed check like any other.
        check("a null-padded last forecast day shortens the window instead of failing",
              False, str(exc))
        window = None

    if window is not None:
        check("a null-padded last forecast day shortens the window instead of failing",
              max(window) == served_last, max(window))
        check("every other requested day is still served",
              len(window) == len(days_between(TAIL_START, last_day - timedelta(days=1))),
              str(len(window)))
        check("the archive days are observed and the forecast days are flagged",
              all(window[day.isoformat()][1] is False
                  for day in days_between(TAIL_START, TAIL_ARCHIVE_END))
              and all(window[day.isoformat()][1] is True
                      for day in days_between(TAIL_ARCHIVE_END + timedelta(days=1),
                                              last_day - timedelta(days=1))))

        # Same payloads, same window: the served days are a function of the
        # data, not of when the run happened.
        again = fetch_stubbed(TAIL_START, last_day, archive, padded)
        check("the same payloads give the same served window", list(again) == list(window))

    # A hole with data after it is a real gap, not a publication lag.
    interior = daily_block(forecast_days, blank=[date(2026, 9, 20)])
    expect_run_error("a gap in the middle of the forecast still fails the run",
                     TAIL_START, last_day, archive, interior, "2026-09-20")

    # Two null days is upstream degradation, not the publication cycle.
    two_short = daily_block(
        forecast_days, blank=[last_day, last_day - timedelta(days=1)])
    expect_run_error("a two-day null tail exceeds the tolerance and fails",
                     TAIL_START, last_day, archive, two_short,
                     "2 day(s) short of the requested window")

    # A missing day the archive owes is never a trailing tail, even when it is
    # the last day asked for.
    short_archive = daily_block(days_between(TAIL_START, TAIL_ARCHIVE_END))
    up_to_today = daily_block(
        days_between(TAIL_ARCHIVE_END + timedelta(days=1), TAIL_TODAY),
        blank=[TAIL_TODAY])
    expect_run_error("a missing observed day fails even as the window's last day",
                     TAIL_START, TAIL_TODAY, short_archive, up_to_today,
                     "2026-09-21")

    # Nothing in the requested window served at all.
    elsewhere = daily_block(days_between(date(2025, 6, 1), date(2025, 6, 5)))
    expect_run_error("a window served by neither endpoint fails",
                     TAIL_START, last_day, elsewhere, elsewhere,
                     "neither ERA5 nor a forecast")


# --- growing degree days and stress -----------------------------------------

def check_gdd():
    """Hand-worked cases for the capped-average method and the stress flags."""
    print("growing degree days and stress flags")
    gdd = runner.growing_degree_days

    # Ordinary day: (28 + 14) / 2 - 10 = 11.
    check("ordinary day: TMIN 14, TMAX 28, base 10 -> 11.0",
          close(gdd(14.0, 28.0, 10.0, 30.0), 11.0, 1e-9), str(gdd(14.0, 28.0, 10.0, 30.0)))
    # TMAX above the cap is clipped: (30 + 20) / 2 - 10 = 15, not (38+20)/2-10 = 19.
    check("hot day: TMAX 38 is capped at 30 -> 15.0",
          close(gdd(20.0, 38.0, 10.0, 30.0), 15.0, 1e-9), str(gdd(20.0, 38.0, 10.0, 30.0)))
    # TMIN below the base is floored: (24 + 10) / 2 - 10 = 7, not (24+2)/2-10 = 3.
    check("cold night: TMIN 2 is floored at 10 -> 7.0",
          close(gdd(2.0, 24.0, 10.0, 30.0), 7.0, 1e-9), str(gdd(2.0, 24.0, 10.0, 30.0)))
    # A day that never reaches the base accumulates nothing, and never negative.
    check("cold day: TMIN -5, TMAX 8 -> 0.0",
          close(gdd(-5.0, 8.0, 10.0, 30.0), 0.0, 1e-9), str(gdd(-5.0, 8.0, 10.0, 30.0)))
    check("exactly at the base -> 0.0", close(gdd(10.0, 10.0, 10.0, 30.0), 0.0, 1e-9))
    # Reusability: a wheat-like base of 0 on the same day gives a different answer.
    check("base 0 (wheat-like): TMIN 2, TMAX 24 -> 13.0",
          close(gdd(2.0, 24.0, 0.0, 30.0), 13.0, 1e-9), str(gdd(2.0, 24.0, 0.0, 30.0)))

    # Accumulation and the flags, over a hand-built five-day region.
    window = {}
    for day, tmin, tmax in [
        ("2026-04-01", -2.0, 8.0),    # frost, no gdd
        ("2026-04-02", 0.0, 16.0),    # frost (<= 0), gdd = (16+10)/2-10 = 3
        ("2026-04-03", 12.0, 24.0),   # gdd = 8
        ("2026-04-04", 18.0, 33.0),   # heat (>= 32), gdd = (30+18)/2-10 = 14
        ("2026-04-05", 20.0, 38.0),   # heat, gdd = (30+20)/2-10 = 15
    ]:
        window[day] = ({
            "temperature_2m_min": tmin,
            "temperature_2m_max": tmax,
            "precipitation_sum": 0.0,
            "shortwave_radiation_sum": 20.0,
            "dew_point_2m_mean": 5.0,
            "wind_speed_10m_mean": 3.0,
        }, False)
    region = {"region_key": "xx", "state": "XX", "lat": 42.0, "lon": -93.5, "elev_m": 300.0}
    params = {"gdd_base_c": 10.0, "gdd_cap_c": 30.0,
              "frost_threshold_c": 0.0, "heat_threshold_c": 32.0}
    rows, angstrom = runner.daily_rows(region, window, params)

    expected_daily = [0.0, 3.0, 8.0, 14.0, 15.0]
    check("daily gdd matches the hand-worked series",
          all(close(r["gdd_daily"], e, 1e-9) for r, e in zip(rows, expected_daily)),
          str([r["gdd_daily"] for r in rows]))
    check("cumulative gdd is the running total (0, 3, 11, 25, 40)",
          [r["gdd_cumulative"] for r in rows] == [0.0, 3.0, 11.0, 25.0, 40.0],
          str([r["gdd_cumulative"] for r in rows]))
    check("frost_day is TMIN <= threshold, inclusive",
          [r["frost_day"] for r in rows] == [True, True, False, False, False],
          str([r["frost_day"] for r in rows]))
    check("heat_stress_day is TMAX >= threshold, inclusive",
          [r["heat_stress_day"] for r in rows] == [False, False, False, True, True],
          str([r["heat_stress_day"] for r in rows]))
    check("frost_days_to_date counts up and holds",
          [r["frost_days_to_date"] for r in rows] == [1, 2, 2, 2, 2],
          str([r["frost_days_to_date"] for r in rows]))
    check("heat_stress_days_to_date counts up and holds",
          [r["heat_stress_days_to_date"] for r in rows] == [0, 0, 0, 1, 2],
          str([r["heat_stress_days_to_date"] for r in rows]))
    check("under 200 days the Angstrom estimate falls back to PCSE's defaults",
          angstrom["angstrom_a"] == runner.ANGSTROM_A_DEFAULT
          and angstrom["angstrom_b"] == runner.ANGSTROM_B_DEFAULT,
          str(angstrom))


# --- region table ------------------------------------------------------------

# The corn belt, generously drawn. A representative point outside this box would
# mean the production weighting or the county join went wrong.
CORN_BELT = {"lat": (36.0, 49.0), "lon": (-104.0, -80.0)}
EXPECTED_STATES = {"IA", "IL", "NE", "MN", "IN", "SD", "KS", "OH", "MO", "WI"}
STRATA = {"all", "irrigated", "rainfed"}

# The single production-weighted point each split state had before the strata
# existed, as committed at 8c79072. Weighting the two stratum points by their
# weights has to reproduce these: the strata partition one production total, so
# a weighted mean over the parts equals the mean over the whole. Any deviation
# is a bug in the county join or the apportionment, not a modelling choice.
#
# The tolerance is 1e-3 degrees, about 100 m, because regions.csv stores
# weights and coordinates rounded to 4 decimal places; the observed error is
# 1.4e-4. It is far tighter than the 73 km and 206 km the strata sit apart, so
# the check still fails loudly on a real error.
PRE_SPLIT_POINTS = {"NE": (41.1658, -98.3301), "KS": (38.6509, -98.4763)}
RECOMBINATION_TOLERANCE = 1e-3


def check_regions():
    print("region table")
    with open(HERE / "regions.csv", newline="") as fh:
        rows = list(csv.DictReader(fh))
    check("regions.csv has 12 regions", len(rows) == 12, str(len(rows)))
    keys = [r["region_key"] for r in rows]
    check("region_key values are unique", len(set(keys)) == len(keys))
    check("region_key is the state code, suffixed with the stratum when split",
          all(r["region_key"] == (r["state"].lower() if r["stratum"] == "all"
                                  else f"{r['state'].lower()}_{r['stratum']}") for r in rows))
    check("the states are the top ten corn states",
          {r["state"] for r in rows} == EXPECTED_STATES,
          str(sorted({r["state"] for r in rows} ^ EXPECTED_STATES)))
    check("every stratum is one of the three allowed values",
          all(r["stratum"] in STRATA for r in rows),
          str(sorted({r["stratum"] for r in rows} - STRATA)))

    # A state is either unsplit or split in two; no other shape is meaningful,
    # and any other shape would double-count or drop production.
    by_state = {}
    for row in rows:
        by_state.setdefault(row["state"], []).append(row["stratum"])
    check("each state appears once as 'all' or twice as irrigated and rainfed",
          all(sorted(v) == ["all"] or sorted(v) == ["irrigated", "rainfed"]
              for v in by_state.values()),
          str({k: sorted(v) for k, v in by_state.items()
               if sorted(v) not in (["all"], ["irrigated", "rainfed"])}))
    check("exactly NE and KS are split",
          {s for s, v in by_state.items() if sorted(v) == ["irrigated", "rainfed"]}
          == set(PRE_SPLIT_POINTS))

    # The weights partition one production total, so they sum to 1. The
    # tolerance absorbs 4-decimal rounding on twelve rows, nothing more.
    total_weight = sum(float(r["weight"]) for r in rows)
    check("the weights sum to 1.0", abs(total_weight - 1.0) <= 5e-4, f"{total_weight:.6f}")
    check("every weight is a positive fraction",
          all(0 < float(r["weight"]) < 1 for r in rows))

    for row in rows:
        lat, lon, elev = float(row["lat"]), float(row["lon"]), float(row["elev_m"])
        check(f"{row['region_key']} sits in the corn belt",
              CORN_BELT["lat"][0] <= lat <= CORN_BELT["lat"][1]
              and CORN_BELT["lon"][0] <= lon <= CORN_BELT["lon"][1],
              f"{lat}, {lon}")
        check(f"{row['region_key']} elevation is plausible", 0 < elev < 2000, str(elev))
    check("every region records its method", all(r["method"].strip() for r in rows))
    check("every region cites its source",
          all("NASS" in r["source"] and "Gazetteer" in r["source"] for r in rows))
    # AC-3: the threshold and the state's own measured share are in the row, so
    # the file explains its own split decision without the README.
    check("every region's method states the split threshold and its own share",
          all("20 percent split threshold" in r["method"]
              and "percent of this state's harvested corn acres" in r["method"]
              for r in rows))
    check("a split row's source cites the irrigated series it rests on",
          all(("IRRIGATED - ACRES HARVESTED" in r["source"]
               and "IRRIGATED, ENTIRE CROP - YIELD" in r["source"])
              for r in rows if r["stratum"] != "all"))

    # The decisive region check: recombining the strata reproduces the point
    # the bundle published before the split.
    for state, (want_lat, want_lon) in PRE_SPLIT_POINTS.items():
        strata = [r for r in rows if r["state"] == state]
        weight = sum(float(r["weight"]) for r in strata)
        got_lat = sum(float(r["weight"]) * float(r["lat"]) for r in strata) / weight
        got_lon = sum(float(r["weight"]) * float(r["lon"]) for r in strata) / weight
        check(f"{state}'s strata recombine to its pre-split point",
              abs(got_lat - want_lat) <= RECOMBINATION_TOLERANCE
              and abs(got_lon - want_lon) <= RECOMBINATION_TOLERANCE,
              f"{got_lat:.4f}, {got_lon:.4f} vs {want_lat}, {want_lon}")


# --- the decisive check: PCSE actually runs on this --------------------------

def build_provider(rows, angstrom_a, angstrom_b):
    """
    The whole contract, in one small class.

    This is what the downstream crop model owes: parse the ISO date (JSON has no
    date type), derive the three evaporation terms PCSE requires, and hand the
    columns straight over. No renaming and no unit conversion.
    """
    from pcse.base.weather import WeatherDataContainer, WeatherDataProvider
    from pcse.util import reference_ET

    class CropWeatherProvider(WeatherDataProvider):
        def __init__(self):
            super().__init__()
            first = rows[0]
            self.latitude = first["LAT"]
            self.longitude = first["LON"]
            self.elevation = first["ELEV"]
            self.angstA, self.angstB = angstrom_a, angstrom_b
            self.description = ["crop-weather bundle output"]
            for row in rows:
                day = date.fromisoformat(row["date"])
                # reference_ET returns mm/day; the container wants cm/day.
                e0, es0, et0 = reference_ET(
                    day, row["LAT"], row["ELEV"], row["TMIN"], row["TMAX"],
                    row["IRRAD"], row["VAP"], row["WIND"], angstrom_a, angstrom_b, "PM")
                container = WeatherDataContainer(
                    DAY=day, LAT=row["LAT"], LON=row["LON"], ELEV=row["ELEV"],
                    TMIN=row["TMIN"], TMAX=row["TMAX"], IRRAD=row["IRRAD"],
                    VAP=row["VAP"], WIND=row["WIND"], RAIN=row["RAIN"],
                    E0=e0 / 10.0, ES0=es0 / 10.0, ET0=et0 / 10.0)
                self._store_WeatherDataContainer(container, day)

    return CropWeatherProvider()


# Soil-moisture-triggered irrigation for the water-limited comparison below.
#
# Amounts are in cm, not mm: WaterbalanceFD._on_IRRIGATE sets
# RIRR = amount * efficiency, and RIRR is cm/day
# (pcse/soil/classic_waterbalance.py, verified against PCSE 6.0.13). PCSE's own
# TimedEvents docstring says "All irrigation amounts in mm"; that is free text
# in an example and it is wrong for the classic water balance.
#
# DummySoilDataProvider has SMFCF 0.30 and SMW 0.10, so triggering at SM 0.25
# is about 75 percent of available water -- a typical centre-pivot trigger.
IRRIGATION_TRIGGER_SM = 0.25
IRRIGATION_AMOUNT_CM = 2.5
IRRIGATION_EFFICIENCY = 0.90

# WAV is initial profile water in cm. The potential run uses 100, the top of
# PCSE's allowed range, which is harmless where the soil is ignored and absurd
# under a water balance; the water-limited runs start near field capacity.
WAV_POTENTIAL_CM = 100.0
WAV_WATER_LIMITED_CM = 20.0
SMLIM_WATER_LIMITED = 0.30

MAIZE_VARIETY = "Grain_maize_201"


def season_rain_cm(rows, year):
    """Total RAIN over the maize season, in cm. Used only to pick a region."""
    start, end = f"{year}-05-01", f"{year}-09-30"
    return sum(r["RAIN"] for r in rows if start <= r["date"] <= end)


def driest_region(output):
    """The region key with the least in-season rain in this output.

    The three production levels only say anything where water actually binds,
    and that is not reliably the first region in the file: on the 2026 series
    Iowa's rainfed crop came within 1.2 percent of potential while Kansas's was
    32 percent below it. Picking by rainfall keeps the check meaningful for the
    sample input and the full ten-region run alike.
    """
    by_region = {}
    for row in output["rows"]:
        by_region.setdefault(row["region_key"], []).append(row)
    year = date.fromisoformat(output["rows"][0]["date"]).year
    rain = {k: season_rain_cm(rows, year) for k, rows in by_region.items()}
    key = min(rain, key=rain.get)
    return key, by_region[key], rain[key]


def maize_agro(first_day, sowing, end, irrigated):
    """Agromanagement for one maize season, optionally irrigated."""
    state_events = None
    if irrigated:
        state_events = [{
            "event_signal": "irrigate",
            "event_state": "SM",
            "zero_condition": "falling",
            "name": "soil-moisture-triggered irrigation",
            "comment": "amounts in cm",
            "events_table": [{IRRIGATION_TRIGGER_SM: {
                "amount": IRRIGATION_AMOUNT_CM,
                "efficiency": IRRIGATION_EFFICIENCY,
            }}],
        }]
    campaigns = [{
        first_day: {
            "CropCalendar": {
                "crop_name": "maize",
                "variety_name": MAIZE_VARIETY,
                "crop_start_date": sowing,
                "crop_start_type": "sowing",
                "crop_end_date": end,
                "crop_end_type": "harvest",
                "max_duration": 300,
            },
            "TimedEvents": None,
            "StateEvents": state_events,
        }
    }]
    if state_events:
        # PCSE refuses a final campaign carrying StateEvents unless a trailing
        # empty campaign bounds it, and that date has to be inside the series.
        campaigns.append({end + timedelta(days=1): None})
    return campaigns


def run_maize(model_cls, rows, angstrom, first_day, sowing, end, wav, irrigated):
    """One WOFOST run. Returns (summary row, total irrigation in cm)."""
    from pcse.input import YAMLCropDataProvider, WOFOST72SiteDataProvider, DummySoilDataProvider
    from pcse.base import ParameterProvider

    parameters = ParameterProvider(
        cropdata=YAMLCropDataProvider(),
        soildata=DummySoilDataProvider(),
        sitedata=WOFOST72SiteDataProvider(WAV=wav, SMLIM=SMLIM_WATER_LIMITED))
    model = model_cls(
        parameters,
        build_provider(rows, angstrom["angstrom_a"], angstrom["angstrom_b"]),
        maize_agro(first_day, sowing, end, irrigated))
    model.run_till_terminate()
    summary = model.get_summary_output()
    # TOTIRR is a state of the water balance, so the potential-production
    # model does not have one. Ask the class rather than catching a lookup
    # failure, which would also hide a real one.
    totirr = model.get_variable("TOTIRR") if model_cls.__waterbalance__ else None
    return (summary[0] if summary else None), totirr


def check_pcse(output):
    print("PCSE / WOFOST")
    from pcse.models import Wofost72_PP, Wofost72_WLP_CWB

    rows = [r for r in output["rows"] if r["region_key"] == output["rows"][0]["region_key"]]
    key = rows[0]["region_key"]
    angstrom = output["metadata"]["angstrom"][key]

    provider = build_provider(rows, angstrom["angstrom_a"], angstrom["angstrom_b"])
    check(f"every {key} row became a WeatherDataContainer",
          len(provider.store) == len(rows), f"{len(provider.store)} of {len(rows)}")

    # Read one day back and confirm the values survived the round trip unchanged.
    sample = rows[len(rows) // 2]
    day = date.fromisoformat(sample["date"])
    back = provider(day)
    for name in runner.PCSE_COLUMNS:
        check(f"{name} reads back unchanged on {sample['date']}",
              close(getattr(back, name), sample[name], 1e-6),
              f"{getattr(back, name)} vs {sample[name]}")
    check("PCSE agrees these are the units it documents",
          back.units["IRRAD"] == "J/m2/day" and back.units["RAIN"] == "cm/day"
          and back.units["VAP"] == "hPa" and back.units["WIND"] == "m/sec",
          str({k: back.units[k] for k in runner.PCSE_COLUMNS}))
    check("the three evaporation terms are present and sane",
          all(0.0 <= getattr(back, n) <= 2.5 for n in ("E0", "ES0", "ET0")),
          str([getattr(back, n) for n in ("E0", "ES0", "ET0")]))

    # The real test: run maize to a finished yield on this weather.
    #
    # Potential production is the right default for a weather bundle -- it
    # isolates the weather from soil parameters this node does not own -- but
    # it assumes water is never limiting, which is the same thing as perfect
    # irrigation on every acre. So run all three levels and assert the
    # relationship between them, rather than asserting that one unlabelled
    # number falls in a wide band.
    crop_key, crop_rows, rain_cm = driest_region(output)
    crop_angstrom = output["metadata"]["angstrom"][crop_key]
    first_day = date.fromisoformat(crop_rows[0]["date"])
    last_day = date.fromisoformat(crop_rows[-1]["date"])
    sowing = date(first_day.year, 5, 1)
    # One spare day at the end: the irrigated run's trailing campaign needs it.
    end = min(last_day - timedelta(days=1), date(first_day.year, 9, 30))
    if not (first_day <= sowing and (end - sowing).days >= 120):
        print(f"  skip  window {first_day}..{last_day} is too short for a maize season; "
              f"run the model with a date in or after September to exercise this check")
        return

    print(f"        {crop_key} is the driest region in this run "
          f"({rain_cm:.1f} cm of rain from {sowing} to 30 September); "
          f"maize sown {sowing}, {MAIZE_VARIETY}")
    levels = {}
    for label, model_cls, wav, irrigated in (
            ("potential", Wofost72_PP, WAV_POTENTIAL_CM, False),
            ("rainfed", Wofost72_WLP_CWB, WAV_WATER_LIMITED_CM, False),
            ("irrigated", Wofost72_WLP_CWB, WAV_WATER_LIMITED_CM, True)):
        result, totirr = run_maize(model_cls, crop_rows, crop_angstrom,
                                   first_day, sowing, end, wav, irrigated)
        check(f"WOFOST ran {label} production to termination", bool(result), str(result))
        if not result:
            return
        levels[label] = (result, totirr)
        print(f"        {label:10} anthesis {result.get('DOA')}, maturity {result.get('DOM')}, "
              f"TWSO {result.get('TWSO'):.0f} kg/ha, LAImax {result.get('LAIMAX'):.2f}"
              + ("" if totirr is None else f", irrigation {totirr:.1f} cm"))

    potential = levels["potential"][0]["TWSO"]
    rainfed = levels["rainfed"][0]["TWSO"]
    irrigated_twso, applied_cm = levels["irrigated"][0]["TWSO"], levels["irrigated"][1]

    # The claim this bundle publishes: the potential figure is the perfectly
    # irrigated case. Irrigating a water-limited run has to reproduce it.
    check("irrigation reconstructs potential production to within 1 percent",
          abs(irrigated_twso - potential) <= 0.01 * potential,
          f"irrigated {irrigated_twso:.0f} vs potential {potential:.0f} kg/ha")
    # Ordering, with a tolerance: a mild deficit lowers LAI and WOFOST's
    # partitioning can repay a little of that, so rainfed is allowed to edge
    # just above potential. A RAIN unit slip would break this by far more.
    check("potential production is at least rainfed production, within 1 percent",
          potential >= rainfed - 0.01 * potential,
          f"potential {potential:.0f} vs rainfed {rainfed:.0f} kg/ha")
    check("the water balance actually applied irrigation",
          applied_cm is not None and applied_cm > 0.0, str(applied_cm))

    # Smoke test, no longer the only assertion about the yield.
    check("potential grain yield is in the right order of magnitude (5-25 t/ha)",
          5000 <= potential <= 25000, str(potential))
    check("the crop reached anthesis", levels["potential"][0].get("DOA") is not None)
    check("the crop reached maturity inside the window",
          levels["potential"][0].get("DOM") is not None)
    check("peak leaf area index is credible (2-10)",
          levels["potential"][0].get("LAIMAX") is not None
          and 2.0 <= levels["potential"][0]["LAIMAX"] <= 10.0,
          str(levels["potential"][0].get("LAIMAX")))


# --- output document ---------------------------------------------------------

def check_output(output):
    print("output document")
    check("has metadata, columns and rows",
          all(k in output for k in ("metadata", "columns", "rows")))
    check("columns match the runner's declared table", output["columns"] == runner.TABLE_COLUMNS)
    rows = output["rows"]
    check("every row has exactly the declared columns",
          all(set(r) == set(runner.TABLE_COLUMNS) for r in rows))
    check("no null values anywhere",
          all(v is not None for r in rows for v in r.values()))
    meta = output["metadata"]
    for field in ("date", "season_start", "retrieved_at", "data_source", "endpoints",
                  "gdd_base_c", "gdd_cap_c", "frost_threshold_c", "heat_threshold_c",
                  "gdd_method", "angstrom", "regions", "pcse_convention",
                  "forecast_days", "forecast_days_served",
                  "window_requested", "window_served"):
        check(f"metadata records {field}", field in meta)

    # A run can end a day short of what was asked for, but it may not do that
    # silently: the served window has to match the rows and be readable from
    # the output alone.
    last_row_day = max(r["date"] for r in rows)
    check("window_served ends on the last day in the table",
          meta["window_served"][1] == last_row_day,
          f"{meta['window_served'][1]} vs {last_row_day}")
    check("window_served starts on the first day in the table",
          meta["window_served"][0] == min(r["date"] for r in rows))
    check("window_requested ends on date + forecast_days",
          meta["window_requested"][1]
          == (date.fromisoformat(meta["date"])
              + timedelta(days=meta["forecast_days"])).isoformat(),
          str(meta["window_requested"]))
    check("forecast_days_served is the served days past date",
          meta["forecast_days_served"]
          == (date.fromisoformat(meta["window_served"][1])
              - date.fromisoformat(meta["date"])).days,
          f"{meta['forecast_days_served']} vs {meta['window_served'][1]}")
    check("forecast_days_served never exceeds what was requested",
          0 <= meta["forecast_days_served"] <= meta["forecast_days"],
          f"{meta['forecast_days_served']} of {meta['forecast_days']}")

    by_region = {}
    for row in rows:
        by_region.setdefault(row["region_key"], []).append(row)
    for key, region_rows in by_region.items():
        dates = [r["date"] for r in region_rows]
        check(f"{key} days are in order with no gaps or repeats",
              dates == sorted(set(dates)) and len(dates) == len(set(dates)))
        check(f"{key} cumulative gdd never decreases",
              all(a["gdd_cumulative"] <= b["gdd_cumulative"]
                  for a, b in zip(region_rows, region_rows[1:])))
        check(f"{key} observed days come before forecast days",
              [r["is_forecast"] for r in region_rows] == sorted(r["is_forecast"] for r in region_rows))
    # One run, one window: a region whose forecast tail arrived short trims
    # every region, so the table stays rectangular and window_served is true
    # of all of them.
    spans = {key: (rs[0]["date"], rs[-1]["date"]) for key, rs in by_region.items()}
    check("every region covers the same days",
          len(set(spans.values())) == 1 and len(set(map(len, by_region.values()))) == 1,
          str(sorted(set(spans.values()))))


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    check_units()
    check_percentile()
    check_payload_guards()
    check_fetch_window()
    check_gdd()
    check_regions()
    if path is None:
        print("\nno output file given; skipping the output and PCSE checks")
    else:
        output = json.loads(path.read_text())
        check_output(output)
        check_pcse(output)

    print(f"\n{CHECKS - len(FAILURES)}/{CHECKS} checks passed")
    if FAILURES:
        for failure in FAILURES:
            print(f"  FAILED: {failure}")
        sys.exit(1)


if __name__ == "__main__":
    main()
