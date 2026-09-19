# Plan: Crop weather

Source brief: docs/features/0001-crop-weather.md
Status: blocked (AC-9 only; every other criterion passes)
Planned against commit: 21b4f41 (main, `modelhome/agromet-bundles`)
Base commit: 00d80d6 (branch feat/0001-crop-weather)

## Outcome

`crop-weather/` is a self-contained Model Home model that, given a date,
produces for each US corn-growing region a daily weather series -- season start
through today plus a forecast horizon -- in PCSE/WOFOST's native variable names
and units, plus accumulated growing degree days and frost / heat-stress flags.
Node 2 (`wofost-bundles/corn-yield/`, separate repo) loads it into a
`WeatherDataProvider` with no unit conversion and no column renaming.

## Scope

### In scope

The `crop-weather/` bundle: `Modelfile.toml`, `Dockerfile`, `runner.py`,
`sample_input.json`, `regions.csv`, `build_regions.py` (one-time, not in the
image), `check_weather.py` (validation, not in the image), `README.md`; plus the
top-level `README.md` bundle-table row and the `CLAUDE.md` bundle section. The
repo scaffold itself is already on `main` (AC-1, partially -- `crop-weather/`
lands with this work).

### Out of scope

Nodes 2 and 3 and their repos; the flow definition; planting-date, soil,
production-weight and price tables; crop-reporting-district granularity; the
single-page app; NASA POWER (documented as future work, not built).

## Assumptions and decisions

Four questions were put to John before planning. All four were answered; the
answers are recorded here because the reader of this plan has not seen that
conversation.

### D1. `E0`, `ES0` and `ET0` are node 2's job (answered)

The brief lists six PCSE variables. PCSE 6.0.13's
`WeatherDataContainer.required` is actually
`["IRRAD", "TMIN", "TMAX", "VAP", "RAIN", "E0", "ES0", "ET0", "WIND"]`, and
WOFOST reads all three evaporation terms directly
(`pcse/crop/evapotranspiration.py`: `drv.E0`, `drv.ES0`, `drv.ET0`). A missing
required variable only logs a warning at container construction, so the failure
surfaces later and further from its cause.

**Decision:** node 1 does not compute them. It emits everything
`pcse.util.reference_ET(DAY, LAT, ELEV, TMIN, TMAX, IRRAD, VAP, WIND, ANGSTA,
ANGSTB, ETMODEL)` needs, including the Angstrom A and B coefficients it
estimated, and node 2 makes that one call (dividing the mm/day result by 10 for
the container's cm/day) before building its containers. This honours the brief's
constraint that PCSE is not a runtime dependency of node 1, and puts
PCSE-specific physics where PCSE already lives.

Consequence for AC-5: "loads without reformatting" means **no unit conversion
and no column renaming**, not zero lines of code. The bundle README must say so
explicitly, and must show the exact `reference_ET` call node 2 needs, so the
contract is not something node 2's author has to rediscover.

### D2. Container units, proven by a real WOFOST run (answered)

PCSE has two unit conventions and the brief conflates them.
`WeatherDataContainer` takes `IRRAD` in J/m2/day, `VAP` in hPa and `RAIN` in
cm/day; `pcse.input.CSVWeatherDataProvider` reads a *file* in kJ/m2/day, kPa and
mm and converts on read.

**Decision:** the output uses **container units** throughout -- JSON and the
off-platform CSV alike, one convention per bundle. `check_weather.py` proves
AC-5 by wrapping the output rows in a small `WeatherDataProvider` subclass
(feeding each row to `WeatherDataContainer(**row)` after the `reference_ET`
call from D1) and running a full WOFOST simulation to a finished yield. Loading
is necessary but not sufficient evidence; a completed run is the real test. The
README notes that the CSV is *not* in `CSVWeatherDataProvider`'s file format and
says why.

### D3. `build_regions.py` from USDA NASS (answered)

**Decision:** a one-time build script, mirroring
`thermofeel-bundles/thermal-indices/build_climatology.py`, pulls county-level
corn production from the USDA NASS QuickStats API, joins county ANSI codes to
county centroids from the US Census Gazetteer, computes a production-weighted
centroid per state, and writes `regions.csv`. The script is committed and
documented but not in the image; `regions.csv` is the committed artifact.

Verified: QuickStats returns HTTP 401 without a key, so the script needs a free
QuickStats API key, read from the environment the way `build_climatology.py`
reads `~/.cdsapirc`. The Census Gazetteer county file is public and needs no
key.

### D4. The repo is public (answered)

`modelhome/agromet-bundles` was created public and `main` pushed at 21b4f41,
matching `thermofeel-bundles`, `QuantLib-bundles` and `heat-damage-bundles`.
Model Home saves a repo-built model's GitHub visibility at save time, and a
model saved from a private repo gets `github-private`, whose output links 404
anonymously.

### D5. A long-format JSON table, with the CSV as a sidecar (repo convention)

The brief calls the CSV the primary artifact. Model Home collects only
`/run/<name>.output.json` per declared output and discards every other file a
runner writes. `thermal-indices` already resolved this: the long-format table
ships as JSON (`{metadata, columns, rows}`) and the CSV is written beside it for
off-platform use only. Same here. Not treated as ambiguity -- it is settled by
repository convention.

### D6. One daily-only Open-Meteo call per endpoint per region (verified)

Verified against the live API on 2026-09-19:

- Both the archive and forecast endpoints serve `dew_point_2m_mean` and
  `wind_speed_10m_mean` as **daily** aggregations, so no hourly block is needed
  at all. PCSE's own provider fetches hourly and averages; this is simpler and
  cheaper.
- `wind_speed_unit=ms` is honoured on both endpoints.
- The ERA5 archive (`models=era5`) lags about 6 days: at a 42.0 / -93.5 test
  point on 2026-09-19 the last non-null day was 2026-09-13.
- The forecast endpoint's `past_days` reaches back about 70 days in practice
  even when 92 are requested (the far end comes back null), and forward 16 days,
  with the final forecast day occasionally null for wind.

**Decision:** archive first for every day it fully covers, forecast for the
rest, exactly like `thermal-indices`; two calls per region, about 20 per default
run. Rows sourced from the forecast endpoint get `is_forecast = true`. A day
neither endpoint covers fails the run with a clear message rather than being
silently dropped or interpolated.

### D7. Unit conversions, taken from PCSE's own provider but corrected (verified)

Sourced from `pcse/input/openmeteo.py` and `pcse/util.py`, re-derived here:

| Target | From | Conversion |
|---|---|---|
| `TMIN`, `TMAX` (degC) | `temperature_2m_min` / `_max` (degC) | none |
| `IRRAD` (J/m2/day) | `shortwave_radiation_sum` (MJ/m2/day) | x 1e6 |
| `RAIN` (cm/day) | `precipitation_sum` (mm) | x 0.1 |
| `WIND` (m/s at 2 m) | `wind_speed_10m_mean` (m/s, with `wind_speed_unit=ms`) | x log10(2/0.033)/log10(10/0.033) = 0.71833 |
| `VAP` (hPa) | `dew_point_2m_mean` (degC) | Magnus: 6.108 * exp(17.27 * Td / (Td + 237.3)) |

**PCSE's own provider has a bug that must not be copied:** it requests
`wind_speed_10m` without `wind_speed_unit`, and Open-Meteo's default is km/h,
not m/s (verified against the live API). It then applies `wind10to2` to km/h, so
its `WIND` is about 3.6x too high. `crop-weather` passes `wind_speed_unit=ms`
explicitly, with a comment saying why.

The 0.71833 log-wind factor is `pcse.util.wind10to2`'s exact value, not the
0.75 rule of thumb the brief cites. Use PCSE's, so node 2 sees the same number
PCSE would have produced.

Angstrom A and B are estimated the way PCSE does it (5th and 98th percentiles of
IRRAD over a FAO-56 top-of-atmosphere estimate) when at least 200 days are
available, else the 0.29 / 0.49 defaults, and the values used are stamped in the
metadata for node 2.

### D8. GDD and stress definitions

- **GDD, capped-average method:** `gdd_daily = max(0, (min(TMAX, gdd_cap_c) +
  max(TMIN, gdd_base_c)) / 2 - gdd_base_c)`, with `TMIN` first floored at
  `gdd_base_c` and `TMAX` ceilinged at `gdd_cap_c`. This is the US corn
  convention (base 10 degC / 50 degF, cap 30 degC / 86 degF). Documented in the
  README and the Modelfile validity domain, with the alternative simple-average
  method named and rejected.
- **Accumulation start** is `season_start`, so `gdd_cumulative` is
  season-to-date, not planting-to-date. Node 2 owns planting dates and can
  re-accumulate from its own start using `gdd_daily`; that is why `gdd_daily` is
  emitted alongside the cumulative column.
- **Stress flags:** `frost_day = TMIN <= frost_threshold_c` (default 0 degC, a
  killing frost for corn at the documented threshold) and
  `heat_stress_day = TMAX >= heat_threshold_c` (default 32 degC, the onset of
  pollination stress in corn). Both thresholds are declared parameters with corn
  defaults, per the brief's reusability constraint. `frost_days_to_date` and
  `heat_stress_days_to_date` are running counts from `season_start`.
- Flags and counts are computed for forecast days too; `is_forecast` marks them.

**Open for review:** the 0 degC / 32 degC defaults are the ones this plan
proposes and will cite. If you want the killing-frost threshold at -2 degC
(common for a hard freeze) or heat stress at 35 degC, say so before `run`.

### D9. Region set

The ten states, from USDA NASS corn-for-grain production shares (vintage cited
in `regions.csv` and the README): IA, IL, NE, MN, IN, SD, KS, OH, MO, WI.
`region_key` is the two-letter state postal code, lower-cased -- human-legible,
stable, and short enough to read in a flow graph. `build_regions.py` confirms
the ranking against NASS at build time rather than trusting this list; if NASS
disagrees, the table wins and the README records the vintage.

## Acceptance-criteria traceability

| ID | Acceptance criterion | Implementation | Verification | Status |
|---|---|---|---|---|
| AC-1 | Repo scaffold matching `thermofeel-bundles` conventions | `main` at 21b4f41; `.gitignore` keeps `.claude/` and ignores `.nass-cache/` | tree inspected; `git status` clean of cache and output dirs | pass |
| AC-2 | `crop-weather/` contains Modelfile, Dockerfile, runner, sample input, region table | all eight files under `crop-weather/` | `orchestration.modelfile validate` -> `OK`, no annotation warnings | pass |
| AC-3 | Runner runs end to end and writes both outputs for the full window | `crop-weather/runner.py` | sample: 819 rows over 273 days, 51 forecast, 3 s; both JSON documents and the CSV written | pass |
| AC-4 | `docker build` succeeds; `docker run` reproduces the outputs | `crop-weather/Dockerfile` (no pip layer) | image built; rows **identical** to the local run (819 vs 819), metadata identical apart from `retrieved_at`; bare `CMD` run works | pass |
| AC-5 | Output loads into a PCSE `WeatherDataProvider` unchanged | container-native columns and units; `check_weather.py` `build_provider` | `Wofost72_PP` maize run on the Iowa series to maturity: sown 2026-05-01, anthesis 06-30, maturity 08-13, TWSO 10,926 kg/ha, LAImax 4.22; six variables read back unchanged; PCSE's own unit strings match | pass |
| AC-6 | GDD and stress flags correct against a hand-worked example; parameters documented | `growing_degree_days`, `daily_rows`; four Modelfile inputs | `check_weather.py`: capped TMAX, floored TMIN, zero-GDD day, at-the-base day, a wheat-like base, and the five-day accumulation and flag series | pass |
| AC-7 | ~10 top corn states, one representative point each, stable key, NASS-sourced, method documented | `build_regions.py` -> `regions.csv` | 10 rows, unique keys, every point in the corn belt and inside its own counties' hull, `method` and `source` populated on every row | pass |
| AC-8 | Empty input yields today / built-in regions / Jan-1 start / forecast horizon / corn defaults | `parse_request` defaults; `default = {}` | run with `{}`: 2026-09-19 / Jan 1 / 15 / 10 / 30 / 0 / 32, ten built-in regions, 2,770 rows, 210 forecast, 19.6 s | pass |
| AC-9 | Subfolder URL creates a working model on the local stack | n/a (platform action) | **blocked**: the local stack is running (Vite :5173, port-forward :8000, context `kind-modelhome`) but sits behind Auth0 login, and this session does not sign in on the user's behalf | blocked |
| AC-10 | README documents endpoints, every conversion, GDD and thresholds, regions, determinism, NASA POWER | `crop-weather/README.md` | all six sections present and checked against D6, D7, D8, D9; plus the provider class the crop model owes | pass |

## Verification

The repo has no test harness yet; `check_weather.py` is this bundle's, mirroring
`thermal-indices/check_indices.py`. PCSE is a dev-only dependency of the check,
never of the image.

| Command | Purpose | Baseline result | Final result |
|---|---|---|---|
| `python3 crop-weather/runner.py crop-weather/sample_input.json run/crop_weather_summary.output.json > run/crop_weather_daily.output.json` | AC-3 | no such file (nothing existed on the branch) | pass: 819 rows, 51 forecast, 3.1 s |
| `python3 crop-weather/runner.py run/_empty_input.json run/crop_weather_summary.output.json > run/crop_weather_daily.output.json` | AC-8 | no such file | pass: 2,770 rows, 210 forecast, 19.6 s, defaults as declared |
| `uv run --no-project --python 3.12 --with pcse==6.0.13 --with numpy python check_weather.py ../run/crop_weather_daily.output.json` | AC-5, AC-6, AC-7 | no such file | pass: **118/118 checks**, including the WOFOST run |
| `cd crop-weather && docker build -t agromet-crop-weather:local . && docker run --rm -v "$PWD/run:/run" ...` | AC-4 | no such file | pass: build OK; rows identical to local |
| `uv run python -m orchestration.modelfile validate .../crop-weather/Modelfile.toml` | AC-2 | no such file | pass: `OK`, after trimming `validity_domain` to the validator's 600-character cap |

There was no pre-existing test harness and no pre-existing code on this branch,
so every baseline is "the file did not exist yet". Nothing here can be a
regression; there is no prior behaviour to regress.

## Implementation steps

1. **`build_regions.py` and `regions.csv`.** Pull NASS QuickStats county corn-
   for-grain production for the most recent complete year; join county ANSI
   codes to Census Gazetteer county centroids; compute the production-weighted
   centroid per state for the top ten states; take `elev_m` from Open-Meteo's
   reported `elevation` at that point; write
   `region_key,state,lat,lon,elev_m,method,source`. Key on the QuickStats API
   key from the environment and fail with a clear message when it is absent.
   Sanity-check every point lands in its own state and inside the corn belt.
   Commit `regions.csv`.
2. **Fetch layer in `runner.py`.** A `fetch_window(region, season_start, end)`
   that calls the ERA5 archive for the days it covers and the forecast endpoint
   for the rest (D6), with the retry-and-fail-loudly helper from
   `thermal-indices` (`urllib.request`, no `requests`). Keep the provider behind
   one narrow seam so NASA POWER could slot in later without touching the output
   layer. Fail the run on a day neither endpoint covers.
3. **Conversions.** One `to_pcse_row()` boundary applying D7, with every factor
   commented and both units in the variable names. `wind_speed_unit=ms` on both
   requests, with the comment explaining PCSE's bug.
4. **Angstrom A/B.** Port PCSE's percentile estimator (FAO-56 top-of-atmosphere
   plus 5th/98th percentiles), falling back to 0.29 / 0.49 below 200 days, and
   stamp the values in the metadata.
5. **GDD and stress.** D8, from `season_start`, with the parameters resolved
   from the input and echoed into the metadata.
6. **Outputs.** `crop_weather_daily` (`{metadata, columns, rows}`) to stdout;
   `crop_weather_summary` (per region: metadata plus the full daily series) to
   the `{output:...}` arg; `crop_weather_daily.csv` beside the summary for
   off-platform use, same columns and units, with a README note that it is not
   `CSVWeatherDataProvider` format.
7. **`Modelfile.toml`.** Mirror `thermal-indices`: `required = []` everywhere,
   `default = {}`, `determinism` / `expected_runtime` / `validity_domain` /
   `not_for` / `provenance`, per-property `unit`. The validity domain carries the
   GDD formulation, the thresholds and the PCSE unit convention.
8. **`Dockerfile`.** `python:3.12-slim`, one pinned `pip install` layer (numpy
   only -- the rest is stdlib), `COPY runner.py regions.csv sample_input.json
   ./`, `ENTRYPOINT ["python", "runner.py"]`, `CMD ["sample_input.json"]`.
9. **`check_weather.py`.** The AC-5 WOFOST run, the AC-6 hand-worked GDD and
   stress cases, and the AC-7 region-table checks. Dev-only; PCSE pinned to
   6.0.13, the version every claim in `CLAUDE.md` was verified against.
10. **Docs.** `crop-weather/README.md` (AC-10, including the exact
    `reference_ET` call node 2 owes per D1, and the NASA POWER evolution); the
    top-level `README.md` bundle-table row; the `CLAUDE.md` bundle section with
    design notes, verified results and a task list.
11. **Verify and record.** Run every command in the table above, paste the real
    numbers into `CLAUDE.md`'s verified-results section, then open the PR.
    AC-9 is a platform action and is recorded as outstanding on the PR rather
    than blocking it, exactly as `thermal-indices` did.

## Files likely to change

```
crop-weather/Modelfile.toml        new
crop-weather/Dockerfile            new
crop-weather/runner.py             new
crop-weather/regions.csv           new  (built by build_regions.py)
crop-weather/build_regions.py      new  (one-time, not in the image)
crop-weather/check_weather.py      new  (validation, not in the image)
crop-weather/sample_input.json     new
crop-weather/README.md             new
README.md                          bundle-table row
CLAUDE.md                          bundle section, verified results, task list
```

## Deviations from this plan

Recorded as required by `feat`; none change the brief's intent.

1. **D3 superseded: no API key needed.** The plan had `build_regions.py` calling
   the NASS Quick Stats API with a free key. The keyless bulk export of the
   **2022 Census of Agriculture** (`nass.usda.gov/datasets/qs.census2022.txt.gz`,
   ~300 MB) carries the same county corn-for-grain series, so the script uses
   that instead. This removes the plan's stated key risk entirely and makes the
   build reproducible by anyone. The Census is a complete enumeration with
   county coverage; counties NASS withholds for disclosure are excluded and
   counted per state (0 to 5).
2. **Row columns use PCSE's `LAT`/`LON`/`ELEV`,** not the brief's
   `lat`/`lon`/`elev_m`. AC-5's "no reformatting" is the brief's own decisive
   criterion, and renaming three keys would have violated it. The unit lives in
   the Modelfile's `unit` annotation instead of the column name. The `regions`
   *input* still uses `lat`/`lon`/`elev_m`, which is what a person types.
3. **`forecast_days` defaults to 15, not ~16.** Open-Meteo's own `forecast_days`
   counts today as its first day, so 16 reaches only 15 days past today. Our
   field counts days *after* `date`, so 15 is the same horizon honestly
   expressed. The empty-input run caught this as a hard failure; the sample,
   whose window was entirely in the past, did not.
4. **Zero pip dependencies, not "numpy only".** Once `E0`/`ES0`/`ET0` moved to
   node 2 (D1), nothing needed numpy: `percentile` is hand-rolled to match
   numpy's default (checked against it) and the FAO-56 top-of-atmosphere
   calculation is `math`. The Dockerfile has no `pip install` layer at all.
5. **`frost_threshold_c` and `heat_threshold_c` are declared Modelfile inputs,**
   as the brief's reusability constraint implies. The plan left them as
   "parameters"; making them first-class inputs is what lets a wheat or soy run
   change them without a code change.
6. **`validity_domain` was trimmed to 551 characters.** The platform validator
   caps it at 600 and says long prose belongs elsewhere; the full detail is in
   the bundle README. Worth knowing before writing the next Modelfile.
7. **The sample input moved to `2026-09-15`,** from an earlier `2026-08-15`. The
   window has to contain a whole maize season for AC-5's WOFOST run to be more
   than a skip, and a recent date also exercises the forecast leg (51 of its 819
   rows).

## Risks and follow-ups

- **The unit boundary is the whole risk.** Four conversions, one of which PCSE
  itself gets wrong. Mitigated by D7's table, by naming variables with their
  units, and by AC-5 running WOFOST rather than inspecting columns. PCSE's range
  checks (`IRRAD` 0 to 4.0e7 J/m2/day, `RAIN` 0 to 25 cm/day, `VAP` 0.06 to
  199.3 hPa) raise on a slip, so the WOFOST run catches an order-of-magnitude
  error loudly.
- ~~NASS QuickStats needs a free API key~~ **Resolved** by deviation 1: the
  keyless Census bulk export. `build_regions.py` ran clean and `regions.csv` is
  committed.
- **The forecast endpoint's `past_days` reach is shorter than documented** (~70
  days observed against 92 requested). The archive covers the gap today, but if
  ERA5's lag grows past the forecast reach a day could fall through. The run
  fails loudly rather than interpolating; watch for it.
- **`E0`/`ES0`/`ET0` live in node 2** (D1). If node 2's brief does not pick them
  up, the flow breaks at the first WOFOST step. This must be an explicit line in
  the node 2 brief, and `crop-weather/README.md` states the contract.
- **Whole-season GDD off-season.** From January the accumulation runs through
  winter, so `gdd_cumulative` at planting is not zero. Node 2 re-accumulates
  from its planting date using `gdd_daily`; the README says so.
- **AC-9 is the one outstanding criterion.** The local stack is up
  (`kind-modelhome`, Vite on :5173, a port-forward on :8000) but the app is
  behind Auth0 login and this session does not sign in on the user's behalf. To
  close it: sign in, add the model at `http://localhost:5173/models/new/repo`
  from
  `https://github.com/modelhome/agromet-bundles/tree/feat/0001-crop-weather/crop-weather`,
  and run it with `{}`. Note the memory-recorded footgun that a `kubectl`
  port-forward on :8000 shadows a local backend.
- **The Angstrom coefficients move with the window.** They are percentiles of a
  ~275-day series, so Iowa's A ran 0.2156 to 0.2692 across runs four days apart.
  Both are inside PCSE's valid range, and the crop model should read them from
  the same run it reads the weather from; the README says so.
- **Follow-ups:** NASA POWER as the observed leg; crop-reporting-district
  granularity; wheat and soy parameter sets; a soil-moisture carry-over column
  if node 2 wants one.
