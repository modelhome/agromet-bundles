#!/usr/bin/env python3
"""
Validation for the crop-weather bundle. Not part of the model image.

This is the decisive test for the whole node: the output exists to be eaten by
PCSE/WOFOST, so the check loads it into a real WeatherDataProvider and runs a
maize simulation to a finished yield. Loading alone is not enough -- a unit slip
can load fine and produce nonsense -- so the run has to complete and the yield
has to be credible.

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
from datetime import date
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


def check_regions():
    print("region table")
    with open(HERE / "regions.csv", newline="") as fh:
        rows = list(csv.DictReader(fh))
    check("regions.csv has 10 regions", len(rows) == 10, str(len(rows)))
    keys = [r["region_key"] for r in rows]
    check("region_key values are unique", len(set(keys)) == len(keys))
    check("region_key is the lower-cased state code",
          all(r["region_key"] == r["state"].lower() for r in rows))
    check("the states are the top ten corn states",
          {r["state"] for r in rows} == EXPECTED_STATES,
          str(sorted({r["state"] for r in rows} ^ EXPECTED_STATES)))
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


def check_pcse(output):
    print("PCSE / WOFOST")
    from pcse.input import YAMLCropDataProvider, WOFOST72SiteDataProvider, DummySoilDataProvider
    from pcse.base import ParameterProvider
    from pcse.models import Wofost72_PP

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
    first_day = date.fromisoformat(rows[0]["date"])
    last_day = date.fromisoformat(rows[-1]["date"])
    sowing = date(first_day.year, 5, 1)
    end = min(last_day, date(first_day.year, 9, 30))
    if not (first_day <= sowing and (end - sowing).days >= 120):
        print(f"  skip  window {first_day}..{last_day} is too short for a maize season; "
              f"run the model with a date in or after September to exercise this check")
        return
    agro = [{
        first_day: {
            "CropCalendar": {
                "crop_name": "maize",
                "variety_name": "Grain_maize_201",
                "crop_start_date": sowing,
                "crop_start_type": "sowing",
                "crop_end_date": end,
                "crop_end_type": "harvest",
                "max_duration": 300,
            },
            "TimedEvents": None,
            "StateEvents": None,
        }
    }]
    parameters = ParameterProvider(
        cropdata=YAMLCropDataProvider(),
        soildata=DummySoilDataProvider(),
        sitedata=WOFOST72SiteDataProvider(WAV=100))
    model = Wofost72_PP(parameters, provider, agro)
    model.run_till_terminate()
    summary = model.get_summary_output()
    check("WOFOST ran to termination and produced a summary", bool(summary), str(summary))
    if not summary:
        return
    result = summary[0]
    twso = result.get("TWSO")
    print(f"        {key}: sown {result.get('DOS')}, anthesis {result.get('DOA')}, "
          f"maturity {result.get('DOM')}, TWSO {twso} kg/ha, LAIMAX {result.get('LAIMAX')}")
    check("potential grain yield is credible for corn (5-25 t/ha)",
          twso is not None and 5000 <= twso <= 25000, str(twso))
    check("the crop reached anthesis", result.get("DOA") is not None)
    check("the crop reached maturity inside the window", result.get("DOM") is not None)
    check("peak leaf area index is credible (2-10)",
          result.get("LAIMAX") is not None and 2.0 <= result["LAIMAX"] <= 10.0,
          str(result.get("LAIMAX")))


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
                  "gdd_method", "angstrom", "regions", "pcse_convention"):
        check(f"metadata records {field}", field in meta)
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


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    check_units()
    check_percentile()
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
