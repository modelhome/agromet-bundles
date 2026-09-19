#!/usr/bin/env python3
"""
Model Home runner: daily crop weather for US corn regions, in PCSE/WOFOST form.

Reads one JSON input file (positional arg, default ``sample_input.json``). Every
field is optional, so a daily schedule can send ``{}``:

- ``date``          -- ISO date, the last observed day. Defaults to today (UTC).
- ``regions``       -- ``[{region_key, lat, lon, elev_m}]``. Defaults to regions.csv.
- ``season_start``  -- ISO date the window starts. Defaults to 1 January of
                       ``date``'s year.
- ``forecast_days`` -- days of forecast past ``date``. Defaults to 15, which is
                       as far past today as Open-Meteo reaches.
- ``gdd_base_c`` / ``gdd_cap_c``           -- defaults 10 / 30 (corn).
- ``frost_threshold_c`` / ``heat_threshold_c`` -- defaults 0 / 32 (corn).

For each region it fetches daily weather from Open-Meteo (ERA5 archive for the
days the reanalysis covers, the forecast endpoint for the rest), converts it
into PCSE's WeatherDataContainer convention, adds growing degree days and
stress-day flags, and writes:

- stdout: the long-format table as JSON (the ``crop_weather_daily`` output; the
  Modelfile redirects stdout to run/crop_weather_daily.output.json);
- the second positional arg (default run/crop_weather_summary.output.json): the
  same values grouped per region (``crop_weather_summary``);
- crop_weather_daily.csv beside the summary. Model Home keeps only JSON outputs,
  so that file exists only when the model is run off-platform.

Units are PCSE's WeatherDataContainer convention, NOT PCSE's CSV *file*
convention (which uses kJ/m2/day, kPa and mm). Open-Meteo serves degC, mm,
MJ/m2/day and m/s; conversion happens once, in ``to_pcse_row``, and variable
names carry their unit (``rain_mm``, ``RAIN`` in cm/day). Logs go to stderr;
stdout carries only the result.

This runner deliberately has no third-party dependencies: PCSE belongs to the
downstream crop model, not here. See README.md for the three evaporation terms
(E0, ES0, ET0) that PCSE requires and this node does not compute.
"""
import csv
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REGIONS_PATH = HERE / "regions.csv"
DEFAULT_INPUT_PATH = HERE / "sample_input.json"
DEFAULT_SUMMARY_PATH = Path("run") / "crop_weather_summary.output.json"

# --- defaults ----------------------------------------------------------------
# forecast_days counts days AFTER `date`. Open-Meteo's own forecast_days
# parameter counts `today` as its first day, so its 16 reaches only 15 days
# past today; asking for a 16th silently falls off the end of the window.
DEFAULT_FORECAST_DAYS = 15
MAX_FORECAST_DAYS = 15
OPEN_METEO_FORECAST_DAYS_MAX = 16  # the API parameter's cap, today inclusive
MAX_PAST_DAYS = 92  # the forecast endpoint's cap on past_days
# Corn. Base 10 degC / 50 degF and cap 30 degC / 86 degF are the US convention.
DEFAULT_GDD_BASE_C = 10.0
DEFAULT_GDD_CAP_C = 30.0
# A killing frost for corn, and the temperature at which pollination starts to
# suffer. Both are parameters so this node also serves wheat or soy.
DEFAULT_FROST_THRESHOLD_C = 0.0
DEFAULT_HEAT_THRESHOLD_C = 32.0

# --- weather source ----------------------------------------------------------
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
# Six daily aggregations. Open-Meteo serves dew point and wind as daily means,
# so unlike PCSE's own Open-Meteo provider this needs no hourly block at all.
DAILY_VARIABLES = [
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "shortwave_radiation_sum",
    "dew_point_2m_mean",
    "wind_speed_10m_mean",
]
DATA_SOURCE = (
    "Open-Meteo: ERA5 reanalysis (archive-api.open-meteo.com/v1/archive, models=era5) "
    "for every day it covers; forecast endpoint (api.open-meteo.com/v1/forecast) for "
    "the rest. Weather data by Open-Meteo.com, CC BY 4.0."
)
USER_AGENT = "modelhome-agromet-bundles/crop-weather"
RETRIES = 4

# --- PCSE contract -----------------------------------------------------------
# pcse.base.WeatherDataContainer, verified against PCSE 6.0.13. These column
# names and units are emitted verbatim so the downstream crop model needs no
# renaming and no unit conversion.
PCSE_COLUMNS = ["TMIN", "TMAX", "IRRAD", "VAP", "WIND", "RAIN"]
PCSE_SITE_COLUMNS = ["LAT", "LON", "ELEV"]
AGROMET_COLUMNS = [
    "gdd_daily",
    "gdd_cumulative",
    "frost_day",
    "heat_stress_day",
    "frost_days_to_date",
    "heat_stress_days_to_date",
]
TABLE_COLUMNS = (
    ["region_key", "state", "date", "is_forecast"]
    + PCSE_SITE_COLUMNS
    + PCSE_COLUMNS
    + AGROMET_COLUMNS
)

# pcse.util.wind10to2: a log wind profile over a 0.033 m roughness length.
# 0.71833, not the 0.75 rule of thumb; using PCSE's exact value means the crop
# model sees the number PCSE itself would have produced.
WIND_10M_TO_2M = math.log10(2.0 / 0.033) / math.log10(10.0 / 0.033)
# Angstrom A/B fallbacks, as used by pcse.input.OpenMeteoWeatherDataProvider.
ANGSTROM_A_DEFAULT = 0.29
ANGSTROM_B_DEFAULT = 0.49
ANGSTROM_MIN_DAYS = 200
SOLAR_CONSTANT_W_M2 = 1361.0


class RunError(Exception):
    """A failure the operator needs to see, not a stack trace."""


def log(message):
    print(message, file=sys.stderr)


# --- fetching ----------------------------------------------------------------

def get_json(base_url, params):
    """GET with a few retries. Raises RunError rather than returning junk."""
    url = f"{base_url}?{urllib.parse.urlencode(params, doseq=True)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last = None
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:300]
            # 4xx other than 429 will not get better by trying again.
            if exc.code != 429 and exc.code < 500:
                raise RunError(f"{base_url} returned HTTP {exc.code}: {body}") from exc
            last = f"HTTP {exc.code}: {body}"
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as exc:
            last = str(exc)
        if attempt < RETRIES - 1:
            time.sleep(2 ** attempt)
    raise RunError(f"{base_url} failed after {RETRIES} attempts: {last}")


def daily_payload(payload, url):
    """Turn an Open-Meteo daily block into {iso date: {variable: value}}."""
    daily = payload.get("daily")
    if not daily or "time" not in daily:
        raise RunError(f"{url} returned no daily block")
    # A variable absent from the whole block means Open-Meteo renamed or dropped
    # it, which is a contract change, not a data gap. Say so here rather than
    # letting it surface as a KeyError deep inside the conversion.
    absent = [name for name in DAILY_VARIABLES if name not in daily]
    if absent:
        raise RunError(
            f"{url} returned no {', '.join(absent)}; the API's daily variables may have "
            f"changed. Expected all of: {', '.join(DAILY_VARIABLES)}.")
    out = {}
    for index, day in enumerate(daily["time"]):
        values = {name: daily[name][index] for name in DAILY_VARIABLES}
        # Open-Meteo pads days it cannot serve with nulls; those are not data.
        # A day short of any variable is dropped whole, so every day that
        # reaches to_pcse_row carries all six.
        if any(value is None for value in values.values()):
            continue
        out[day] = values
    return out


def fetch_window(region, first_day, last_day, today):
    """
    {iso date: (values, is_forecast)} covering first_day..last_day inclusive.

    ERA5 is authoritative for every day it covers and lags about six days; the
    forecast endpoint fills the tail. A day neither endpoint serves fails the
    run rather than being interpolated or silently dropped.
    """
    base = {
        "latitude": f"{region['lat']:.4f}",
        "longitude": f"{region['lon']:.4f}",
        "daily": ",".join(DAILY_VARIABLES),
        "timezone": "UTC",
        # Open-Meteo defaults wind to km/h. PCSE's own Open-Meteo provider omits
        # this and feeds km/h into its 10m-to-2m conversion as if it were m/s,
        # overstating WIND by about 3.6x. Always ask for m/s.
        "wind_speed_unit": "ms",
    }
    observed = {}
    archive_end = min(last_day, today)
    if first_day <= archive_end:
        payload = get_json(ARCHIVE_URL, {
            **base,
            "start_date": first_day.isoformat(),
            "end_date": archive_end.isoformat(),
            "models": "era5",
        })
        observed = daily_payload(payload, ARCHIVE_URL)

    wanted = [first_day + timedelta(days=n) for n in range((last_day - first_day).days + 1)]
    missing = [day for day in wanted if day.isoformat() not in observed]

    forecast = {}
    if missing:
        # past_days and forecast_days count backwards and forwards from today.
        past_days = min(max((today - min(missing)).days, 0), MAX_PAST_DAYS)
        ahead = min(max((max(missing) - today).days + 1, 1), OPEN_METEO_FORECAST_DAYS_MAX)
        payload = get_json(FORECAST_URL, {
            **base,
            "past_days": past_days,
            "forecast_days": ahead,
        })
        forecast = daily_payload(payload, FORECAST_URL)

    window = {}
    for day in wanted:
        key = day.isoformat()
        if key in observed:
            window[key] = (observed[key], False)
        elif key in forecast:
            window[key] = (forecast[key], True)

    still_missing = [day.isoformat() for day in wanted if day.isoformat() not in window]
    if still_missing:
        raise RunError(
            f"{region['region_key']}: Open-Meteo served neither ERA5 nor a forecast for "
            f"{len(still_missing)} day(s): {still_missing[0]}"
            + (f" .. {still_missing[-1]}" if len(still_missing) > 1 else "")
            + f". The forecast reaches {OPEN_METEO_FORECAST_DAYS_MAX - 1} days past today "
              f"({today}); the ERA5 archive lags it by about six days."
        )
    return window


# --- conversions -------------------------------------------------------------

def vapour_pressure_hpa(dewpoint_c):
    """Magnus, as pcse.input.OpenMeteoWeatherDataProvider does it. degC -> hPa."""
    return 6.108 * math.exp((17.27 * dewpoint_c) / (dewpoint_c + 237.3))


def to_pcse_row(values):
    """One Open-Meteo day -> PCSE WeatherDataContainer variables and units."""
    return {
        # degC -> degC
        "TMIN": round(values["temperature_2m_min"], 2),
        "TMAX": round(values["temperature_2m_max"], 2),
        # MJ/m2/day -> J/m2/day
        "IRRAD": round(values["shortwave_radiation_sum"] * 1e6, 1),
        # degC dew point -> hPa
        "VAP": round(vapour_pressure_hpa(values["dew_point_2m_mean"]), 4),
        # m/s at 10 m -> m/s at 2 m
        "WIND": round(values["wind_speed_10m_mean"] * WIND_10M_TO_2M, 4),
        # mm/day -> cm/day
        "RAIN": round(values["precipitation_sum"] * 0.1, 4),
    }


def percentile(sorted_values, q):
    """numpy.percentile's default linear interpolation, without numpy."""
    if not sorted_values:
        raise RunError("percentile of an empty series")
    position = (q / 100.0) * (len(sorted_values) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return sorted_values[int(position)]
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low)


def toa_radiation_mj(day, latitude):
    """FAO-56 top-of-atmosphere daily radiation, MJ/m2/day. Mirrors PCSE."""
    doy = day.timetuple().tm_yday
    dr = 1 + 0.033 * math.cos(2 * math.pi * doy / 365)
    declination = math.radians(23.45 * math.sin(2 * math.pi * (doy - 81) / 365))
    phi = math.radians(latitude)
    # Clamped because the product leaves [-1, 1] inside the polar circles.
    cos_hs = max(-1.0, min(1.0, -math.tan(phi) * math.tan(declination)))
    hs = math.acos(cos_hs)
    h0 = (24 * 3600 / math.pi) * SOLAR_CONSTANT_W_M2 * dr * (
        math.cos(phi) * math.cos(declination) * math.sin(hs)
        + hs * math.sin(phi) * math.sin(declination)
    )
    return h0 / 1e6


def angstrom_ab(days, irrad_j, latitude):
    """
    Angstrom A and B, the way PCSE estimates them from a measured series.

    A is the 5th percentile and A+B the 98th percentile of measured over
    top-of-atmosphere radiation. Below 200 days PCSE falls back to 0.29/0.49,
    and so do we. The crop model needs these for reference_ET; emitting them
    means it does not have to re-derive them from a shorter series.
    """
    if len(days) < ANGSTROM_MIN_DAYS:
        return ANGSTROM_A_DEFAULT, ANGSTROM_B_DEFAULT, "default (fewer than 200 days)"
    ratios = []
    for day, irrad in zip(days, irrad_j):
        toa = toa_radiation_mj(day, latitude)
        if toa > 0:
            ratios.append((irrad / 1e6) / toa)
    if len(ratios) < ANGSTROM_MIN_DAYS:
        return ANGSTROM_A_DEFAULT, ANGSTROM_B_DEFAULT, "default (fewer than 200 usable days)"
    ratios.sort()
    a = percentile(ratios, 5)
    ab = percentile(ratios, 98)
    b = ab - a
    # pcse.util.check_angstromAB's bounds.
    if not (0.1 <= a <= 0.4 and 0.3 <= a + b <= 0.9 and b > 0):
        return ANGSTROM_A_DEFAULT, ANGSTROM_B_DEFAULT, "default (estimate out of range)"
    return round(a, 4), round(b, 4), "estimated from this run's radiation series"


# --- agromet -----------------------------------------------------------------

def growing_degree_days(tmin, tmax, base_c, cap_c):
    """
    Capped-average growing degree days, the US corn convention.

    TMAX is capped at cap_c and TMIN floored at base_c before averaging, so a
    day hotter than the cap adds no extra development and a night colder than
    the base does not cancel the day's accumulation. The simple-average method
    (mean of the raw extremes, then minus base) overstates development in hot
    weather and is not used here.
    """
    capped_max = min(tmax, cap_c)
    floored_min = max(tmin, base_c)
    return max(0.0, (capped_max + floored_min) / 2.0 - base_c)


def daily_rows(region, window, params):
    """The full per-day table for one region, with running accumulations."""
    days = sorted(window)
    irrad = []
    rows = []
    gdd_total = 0.0
    frost_total = 0
    heat_total = 0
    for key in days:
        values, is_forecast = window[key]
        pcse = to_pcse_row(values)
        irrad.append(pcse["IRRAD"])

        gdd = growing_degree_days(
            pcse["TMIN"], pcse["TMAX"], params["gdd_base_c"], params["gdd_cap_c"])
        gdd_total += gdd
        frost = pcse["TMIN"] <= params["frost_threshold_c"]
        heat = pcse["TMAX"] >= params["heat_threshold_c"]
        frost_total += int(frost)
        heat_total += int(heat)

        rows.append({
            "region_key": region["region_key"],
            "state": region["state"],
            "date": key,
            "is_forecast": is_forecast,
            "LAT": region["lat"],
            "LON": region["lon"],
            "ELEV": region["elev_m"],
            **pcse,
            "gdd_daily": round(gdd, 3),
            "gdd_cumulative": round(gdd_total, 3),
            "frost_day": frost,
            "heat_stress_day": heat,
            "frost_days_to_date": frost_total,
            "heat_stress_days_to_date": heat_total,
        })

    a, b, how = angstrom_ab([date.fromisoformat(d) for d in days], irrad, region["lat"])
    return rows, {"angstrom_a": a, "angstrom_b": b, "angstrom_source": how}


# --- input -------------------------------------------------------------------

def load_regions():
    if not REGIONS_PATH.exists():
        raise RunError(f"missing {REGIONS_PATH.name}; build it with build_regions.py")
    with open(REGIONS_PATH, newline="") as fh:
        return [
            {
                "region_key": row["region_key"],
                "state": row["state"],
                "lat": float(row["lat"]),
                "lon": float(row["lon"]),
                "elev_m": float(row["elev_m"]),
            }
            for row in csv.DictReader(fh)
        ]


def _blank(value):
    """A present-but-empty value falls back exactly like a missing key."""
    return value is None or value == "" or value == [] or value == {}


def parse_request(spec, today):
    if not isinstance(spec, dict):
        raise RunError("input must be a JSON object")

    raw_date = spec.get("date")
    end_date = today if _blank(raw_date) else _parse_date(raw_date, "date")

    raw_start = spec.get("season_start")
    season_start = (date(end_date.year, 1, 1) if _blank(raw_start)
                    else _parse_date(raw_start, "season_start"))
    if season_start > end_date:
        raise RunError(f"season_start {season_start} is after date {end_date}")

    raw_forecast = spec.get("forecast_days")
    forecast_days = DEFAULT_FORECAST_DAYS if _blank(raw_forecast) else int(raw_forecast)
    if not 0 <= forecast_days <= MAX_FORECAST_DAYS:
        raise RunError(f"forecast_days must be 0 to {MAX_FORECAST_DAYS}, got {forecast_days}")

    params = {
        "gdd_base_c": _number(spec, "gdd_base_c", DEFAULT_GDD_BASE_C),
        "gdd_cap_c": _number(spec, "gdd_cap_c", DEFAULT_GDD_CAP_C),
        "frost_threshold_c": _number(spec, "frost_threshold_c", DEFAULT_FROST_THRESHOLD_C),
        "heat_threshold_c": _number(spec, "heat_threshold_c", DEFAULT_HEAT_THRESHOLD_C),
    }
    if params["gdd_cap_c"] <= params["gdd_base_c"]:
        raise RunError("gdd_cap_c must be above gdd_base_c")

    raw_regions = spec.get("regions")
    if _blank(raw_regions):
        regions = load_regions()
    else:
        regions = [_parse_region(item, index) for index, item in enumerate(raw_regions)]
    keys = [r["region_key"] for r in regions]
    if len(set(keys)) != len(keys):
        raise RunError("region_key values must be unique")

    return end_date, season_start, forecast_days, regions, params


def _parse_date(value, field):
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise RunError(f"{field} must be an ISO date (YYYY-MM-DD), got {value!r}") from exc


def _number(spec, field, default):
    value = spec.get(field)
    if _blank(value):
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise RunError(f"{field} must be a number, got {value!r}") from exc


def _parse_region(item, index):
    if not isinstance(item, dict):
        raise RunError(f"regions[{index}] must be an object")
    missing = [k for k in ("region_key", "lat", "lon", "elev_m") if _blank(item.get(k))]
    if missing:
        raise RunError(f"regions[{index}] is missing {', '.join(missing)}")
    region = {
        "region_key": str(item["region_key"]),
        "state": str(item.get("state") or ""),
        "lat": float(item["lat"]),
        "lon": float(item["lon"]),
        "elev_m": float(item["elev_m"]),
    }
    if not -90 <= region["lat"] <= 90 or not -180 <= region["lon"] <= 180:
        raise RunError(f"regions[{index}] has coordinates outside the globe")
    return region


# --- output ------------------------------------------------------------------

def run_metadata(end_date, season_start, forecast_days, params, regions, retrieved_at, angstrom):
    return {
        "date": end_date.isoformat(),
        "season_start": season_start.isoformat(),
        "forecast_days": forecast_days,
        "retrieved_at": retrieved_at,
        "data_source": DATA_SOURCE,
        "endpoints": {"archive": ARCHIVE_URL, "forecast": FORECAST_URL},
        "pcse_convention": (
            "pcse.base.WeatherDataContainer (PCSE 6.0.13): TMIN/TMAX degC, IRRAD J/m2/day, "
            "VAP hPa, WIND m/s at 2 m, RAIN cm/day, LAT/LON degrees, ELEV m. NOT PCSE's CSV "
            "file convention. E0, ES0 and ET0 are not computed here: see README.md."
        ),
        "angstrom": angstrom,
        **{k: params[k] for k in
           ("gdd_base_c", "gdd_cap_c", "frost_threshold_c", "heat_threshold_c")},
        "gdd_method": (
            "capped average, accumulated from season_start: "
            "max(0, (min(TMAX, gdd_cap_c) + max(TMIN, gdd_base_c)) / 2 - gdd_base_c)"
        ),
        "regions": regions,
    }


def summary_document(metadata, rows_by_region, regions):
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "metadata": metadata,
        "regions": [
            {
                "region_key": region["region_key"],
                "state": region["state"],
                "LAT": region["lat"],
                "LON": region["lon"],
                "ELEV": region["elev_m"],
                "days": [
                    {k: row[k] for k in TABLE_COLUMNS
                     if k not in ("region_key", "state", "LAT", "LON", "ELEV")}
                    for row in rows_by_region[region["region_key"]]
                ],
            }
            for region in regions
        ],
    }


def write_csv(path, rows):
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=TABLE_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def main():
    input_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_INPUT_PATH
    summary_path = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_SUMMARY_PATH
    try:
        with open(input_path) as fh:
            spec = json.load(fh)
        today = datetime.now(timezone.utc).date()
        end_date, season_start, forecast_days, regions, params = parse_request(spec, today)
        last_day = end_date + timedelta(days=forecast_days)

        retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        log(f"{len(regions)} regions, {season_start} .. {last_day} "
            f"({(last_day - season_start).days + 1} days)")

        rows = []
        rows_by_region = {}
        angstrom = {}
        for number, region in enumerate(regions, start=1):
            log(f"[{number}/{len(regions)}] {region['region_key']}")
            window = fetch_window(region, season_start, last_day, today)
            region_rows, region_angstrom = daily_rows(region, window, params)
            rows_by_region[region["region_key"]] = region_rows
            angstrom[region["region_key"]] = region_angstrom
            rows.extend(region_rows)
    except (RunError, OSError, json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
        log(f"error: {exc}")
        sys.exit(1)

    metadata = run_metadata(
        end_date, season_start, forecast_days, params, regions, retrieved_at, angstrom)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    # Compact JSON: 10 regions x ~290 days is ~2,900 rows, which indentation
    # would roughly double.
    compact = {"separators": (",", ":")}
    summary_path.write_text(
        json.dumps(summary_document(metadata, rows_by_region, regions), **compact) + "\n")
    write_csv(summary_path.parent / "crop_weather_daily.csv", rows)
    print(json.dumps({"metadata": metadata, "columns": TABLE_COLUMNS, "rows": rows}, **compact))
    log(f"{len(rows)} rows, {sum(1 for r in rows if r['is_forecast'])} forecast")


if __name__ == "__main__":
    main()
