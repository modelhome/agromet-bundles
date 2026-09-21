# agromet-bundles

Standalone Model Home **model bundles** for agricultural meteorology: the
weather layer that crop models run on. Each bundle is a self-contained folder
with everything Model Home needs to run one model: a `Modelfile.toml`, a
`Dockerfile`, a `runner.py`, and sample input(s).

This repo is **not** a fork of any crop model. It holds only the Model Home
packaging layer. Upstream libraries are pulled in as pinned pip packages inside
a bundle's Docker image; nothing upstream is vendored here. Keep it that way.

```
agromet-bundles/
  CLAUDE.md                 <- you are here
  README.md
  LICENSE                   (MIT)
  .claude/skills/feat/      <- vendored feat skill (brief -> plan -> PR workflow)
  docs/features/            <- feature briefs (NNNN-name.md)
  docs/plans/               <- implementation plans, one per brief
  <bundle>/                 <- one self-contained model per folder
```

The planned gallery is the agronomic half of a climate -> agriculture ->
finance flow. The first bundle, `crop-weather/`, produces the daily weather
series a WOFOST run consumes; the crop model (`wofost-bundles/corn-yield/`) and
the price model (`ag-commodity-bundles/corn-price/`) live in sibling repos and
are composed with it through a Model Home Flow.

---

## The templates: `QuantLib-bundles/bond/` and `thermofeel-bundles/thermal-indices/`

[`modelhome/QuantLib-bundles`](https://github.com/modelhome/QuantLib-bundles)
(`bond/`) and
[`modelhome/thermofeel-bundles`](https://github.com/modelhome/thermofeel-bundles)
(`thermal-indices/`) are the authoritative templates for this repo. Read them
before starting a bundle and mirror them. `bond` is the minimal shape;
`thermal-indices` is the shape for a bundle that fetches live weather, which is
what this repo does. Consistency with them matters more than any local
preference:

- **`Modelfile.toml` keys.** `name`, `description`, `run`, `image`, `args`,
  `[resources]`, `[[inputs]]` with a documented `[inputs.schema]` (every
  property has a plain-language `description`; the schema's `default` is what
  the platform offers as the "Example to paste", so it must stay runnable), and
  `[[outputs]]` with `[outputs.schema]`. Add the annotation fields Model Home
  validates: `determinism`, `expected_runtime`, `validity_domain`, `not_for`,
  `provenance`, and per-property `unit`. Rationale the schema cannot express
  goes in TOML comments beside it.
- **Runner I/O contract.** Input JSON file path(s) arrive as positional args.
  The result JSON goes to **stdout** and nothing else does; logs go to stderr.
  The Modelfile's `run` redirects stdout to `run/<output>.output.json`. Further
  outputs are passed as `{output:NAME}` args.
- **`required = []` and defaults in the runner.** A present-but-empty value
  (`""` or `null`) falls back the same way a missing key does, so the model runs
  standalone or composed.
- **Dockerfile.** `python:3.12-slim`, `WORKDIR /app`, exact `==` pins installed
  in one `pip install --no-cache-dir` layer, `COPY` paths relative to the bundle
  folder, `ENTRYPOINT ["python", "runner.py"]`, and a `CMD` naming the bundled
  sample input so a bare `docker run` works.
- **Stdlib over dependencies.** `thermal-indices` fetches from Open-Meteo with
  `urllib.request` and a small retry helper rather than adding `requests`. Do
  the same.

## Model Home platform facts (verified against the platform code)

- **Build context is the bundle subfolder.** Adding a model from
  `github.com/modelhome/agromet-bundles/tree/main/<bundle>` promotes that folder
  to the build-context root, exactly like `cd <bundle> && docker build .`. Never
  use repo-relative `COPY <bundle>/...` paths; the on-platform build fails.
- **Every output is a JSON file.** The platform collects only
  `/run/<name>.output.json` for each declared `[[outputs]]` name and parses it
  with `json.loads`. Any other file a runner writes (a CSV, a PNG) is discarded
  on-platform. A long-format table therefore ships as JSON
  (`{metadata, columns, rows}`); a CSV written beside it is for off-platform use
  only, and its README must say so.
- **Model containers have outbound network access.** No NetworkPolicy restricts
  the runs namespace. A bundle that fetches at run time must still fail clearly
  (non-zero exit, reason on stderr) when a fetch fails.
- **A schedule re-sends a fixed input.** A scheduled run passes the same stored
  input every time, so anything that should change per run (such as "today")
  must be defaulted inside the runner, not baked into the schedule's input.
- **Flow steps connect by output shape, not by name.** Design a bundle's output
  as the next node's input.

## The PCSE weather contract (verified against PCSE 6.0.13)

Everything in this repo that feeds a crop model feeds
[PCSE](https://github.com/ajwdewit/pcse)/WOFOST. Read the source, not the
README, and re-verify against the pinned version before relying on any of it.
As of PCSE 6.0.13 (`pcse/base/weather.py`, `WeatherDataContainer`):

| Variable | Unit | Notes |
|---|---|---|
| `DAY` | `datetime.date` | not a string |
| `LAT`, `LON` | degrees | site variables, not per-day |
| `ELEV` | m | site variable |
| `TMIN`, `TMAX` | degC | range check -50 to 60 |
| `IRRAD` | J/m2/day | range check 0 to 4.0e7 |
| `VAP` | hPa | 24-hour mean; range check 0.06 to 199.3 |
| `WIND` | m/s **at 2 m** | 24-hour mean; range check 0 to 100 |
| `RAIN` | **cm**/day | range check 0 to 25 |
| `E0`, `ES0`, `ET0` | cm/day | **also required** |
| `TEMP` | degC | optional; defaults to (TMIN+TMAX)/2 |
| `SNOWDEPTH` | cm | optional |

Three things bite here:

1. **`E0`, `ES0` and `ET0` are in `WeatherDataContainer.required`,** not
   optional, and WOFOST reads them directly (`pcse/crop/evapotranspiration.py`
   uses `drv.E0`, `drv.ES0`, `drv.ET0`). A missing required variable only logs a
   warning at container construction; the run fails later, further from the
   cause. PCSE derives them with `pcse.util.reference_ET(DAY, LAT, ELEV, TMIN,
   TMAX, IRRAD, VAP, WIND, ANGSTA, ANGSTB, ETMODEL)`, which returns **mm/day**
   and is divided by 10 for the container's cm/day.
2. **The container's units are not the CSV file's units.**
   `pcse.input.CSVWeatherDataProvider` reads a file in kJ/m2/day (`IRRAD`), kPa
   (`VAP`) and mm (`RAIN`) and converts on read. "PCSE units" is ambiguous
   unless you say container or file; state which one an artifact uses.
3. **Range checks raise.** `WeatherDataContainer.__setattr__` raises `PCSEError`
   for an out-of-range value unless `settings.METEO_RANGE_CHECKS` is off. A unit
   slip usually shows up as a range error, not a wrong yield.

### PCSE already ships an Open-Meteo provider, and it has a bug

PCSE 6.0.13 includes `pcse.input.OpenMeteoWeatherDataProvider`
(`pcse/input/openmeteo.py`, WUR AI Group, Feb 2025). Read it for the canonical
conversions, but do not copy it uncritically:

- `IRRAD`: `shortwave_radiation_sum` (MJ/m2/day) x 1e6 -> J/m2/day.
- `RAIN`: `precipitation_sum` (mm) x 0.1 -> cm/day.
- `VAP`: Magnus on the **daily mean dewpoint**,
  `6.108 * exp(17.27 * Td / (Td + 237.3))` -> hPa.
- `WIND`: `pcse.util.wind10to2`, a log-wind profile with
  `log10(2/0.033) / log10(10/0.033)` = **0.71833** (not the 0.75 rule of thumb).
- `E0`/`ES0`/`ET0`: `reference_ET`, then `/10`. Angstrom A/B are estimated from
  the data when at least 200 days are available, else 0.29/0.49.
- **The bug:** it requests `wind_speed_10m` without `wind_speed_unit`, and
  Open-Meteo's default is **km/h**, not m/s (verified against the live API).
  `wind10to2` is then applied to km/h, so its `WIND` is about 3.6x too high.
  Always pass `wind_speed_unit=ms` explicitly and say so in a comment.

## Conventions for every bundle

- **Read the upstream source and tests before coding.** Take variable names,
  units and formulae from the source, not from a README or from this file.
- **Unit slips are the classic bug.** Convert at one boundary and name variables
  with their unit (`rain_mm`, `rain_cm`, `irrad_j_m2_day`). Every conversion
  gets a comment naming both units and the factor.
- **Commit a validation check** per bundle, run outside the image, that proves
  the bundle's output is what the downstream consumer expects. For a bundle that
  feeds PCSE, that means loading the output into a real `WeatherDataProvider`
  and running the model, not just eyeballing columns.
- **Pin everything** in the Dockerfile. Do not add a dependency when numpy or
  the standard library will do. PCSE belongs to the crop-model bundle, not to a
  weather bundle: conforming to PCSE's format must not mean importing it at run
  time. Using it in a dev-only check is fine.
- **Readable over clever.** These inputs are small (tens of sites x hundreds of
  days).
- **Parameters, not constants.** Agronomic thresholds (GDD base and cap, frost
  and heat-stress cut-offs) are declared, annotated inputs with crop defaults,
  so a bundle built for corn also serves wheat or soy.
- **No emojis** in source files.

### Determinism for bundles that fetch data

A bundle that pulls data at run time is deterministic given its inputs **and the
upstream data as of retrieval**, the same category as a market-data pull. State
this in the bundle's README and Modelfile comments, and make it auditable:

- stamp `retrieved_at`, the data source and the endpoints in the output
  metadata;
- flag any value the upstream source may still revise, per row
  (`is_forecast`);
- introduce no randomness and no wall-clock dependence beyond an explicit
  "today" default and the `retrieved_at` stamp.

### Region identity

Bundles in this flow share one region set. The upstream-most node owns it: the
`region_key`, its coordinates and its elevation originate there and propagate
unchanged. Downstream nodes join on the key and add their own attributes
(planting dates, soils, production weights, prices); they never redefine the
key. Commit the region table as a file, with the source and the method that
chose each representative point.

The owning node may still *restratify* -- split one region into two because the
single point averaged things that should not be averaged. That retires a key
rather than redefining it, and it is a breaking change for every consumer, so
it happens in a brief of its own with the old and new keys written down. Brief
0003 did this to `ne` and `ks`; see the bundle's design notes below.

## How features are built: `feat`

Features are developed from versioned briefs with the vendored
[`feat`](./.claude/skills/feat/SKILL.md) skill, so the brief, the plan and the
implementation land together in one pull request:

1. `/feat create <name>` scaffolds `docs/features/NNNN-<name>.md`. Hand-written
   briefs in the same template are fine.
2. `/feat plan <name>` writes `docs/plans/NNNN-<name>.md` and stops. John reviews
   and revises the plan before anything is built.
3. `/feat run <name>` implements the approved plan on `feat/NNNN-<name>` and
   stops at the pull request. It never merges, releases or deploys.

Repo-wide conventions live in this file; briefs reference them rather than
restating them.

## The `crop-weather/` bundle

**US Corn Crop Weather.** Given an optional `date`, fetches daily Open-Meteo
weather from 1 January through `date` plus 15 forecast days for twelve
production-weighted US corn points (ten states, two of them split by water
regime), converts it into PCSE's
`WeatherDataContainer` convention, and adds capped-average growing degree days
and frost / heat-stress day counts. Brief:
`docs/features/0001-crop-weather.md`; plan with every decision and its
reasoning: `docs/plans/0001-crop-weather.md`. User-facing documentation:
[`crop-weather/README.md`](./crop-weather/README.md).

```
crop-weather/
  Modelfile.toml      two JSON outputs; semantic annotations
  Dockerfile          python:3.12-slim, no pip layer at all
  runner.py           the model
  regions.csv         twelve regions: point, stratum, weight, method, source
  build_regions.py    one-time region build (not in the image)
  check_weather.py    validation incl. a real WOFOST run (not in the image)
  sample_input.json   2026-09-15, Iowa / Illinois / irrigated Nebraska
  README.md
```

### Design notes

- **Two JSON outputs, not CSV.** Model Home keeps only `<name>.output.json`, so
  the long-format table is `crop_weather_daily` (`{metadata, columns, rows}`)
  and the per-region view is `crop_weather_summary`. The CSV is written beside
  the summary for off-platform use only. `crop_weather_daily` comes from the
  stdout redirect; `crop_weather_summary` is an `{output:...}` arg.
- **Zero runtime dependencies.** The runner is standard library only, so the
  Dockerfile has no `pip install` layer. That is only possible because `E0`,
  `ES0` and `ET0` are left to the crop model (see below); everything else is
  arithmetic, including a hand-rolled `percentile` that matches numpy's default
  and the FAO-56 top-of-atmosphere calculation.
- **`E0`, `ES0` and `ET0` are node 2's job.** They are in
  `WeatherDataContainer.required` and WOFOST reads them directly, but they are
  PCSE's own physics (`pcse.util.reference_ET`), so they belong on PCSE's side
  of the boundary. This node emits everything `reference_ET` needs, including
  the Angstrom coefficients it estimated. `crop-weather/README.md` carries the
  exact provider class the crop model owes; `check_weather.py` runs it.
- **The validation yield is potential production, and says so.** `Wofost72_PP`
  assumes water is never limiting, which is perfect irrigation on every acre;
  measured on the 2026 series, a water-limited run with soil-moisture-triggered
  irrigation reproduces it to the kilogram. That is the right default for a
  weather bundle -- it isolates the weather from soil parameters this node does
  not own -- but it has to be labelled, so the README, the Modelfile's
  `not_for` and `check_weather.py` all name it. The check runs potential,
  rainfed and irrigated on the driest region in the output and asserts the
  relationship between them; the old 5-25 t/ha band could not fail. Brief
  `docs/features/0002-declare-production-level.md`.
- **Container units, not CSV-file units.** See the PCSE contract section above.
  The CSV sidecar matches the JSON, so it is deliberately not a drop-in for
  `pcse.input.CSVWeatherDataProvider`.
- **Column names are PCSE's.** `LAT`/`LON`/`ELEV` rather than `lat`/`lon`/
  `elev_m` in the output rows, so the crop model renames nothing. The unit lives
  in the Modelfile annotation instead of the column name.
- **ERA5 first, forecast for the tail.** The archive supplies every day it
  covers (it lags ~6 days); the forecast endpoint supplies the rest (~21 days)
  and those rows get `is_forecast = true`. A day neither covers fails the run.
- **Six daily aggregations, no hourly block.** Open-Meteo serves
  `dew_point_2m_mean` and `wind_speed_10m_mean` daily, so unlike PCSE's own
  provider this needs no hourly fetch. Two calls per region, 20 per default run.
- **`forecast_days` counts days after `date`, max 15.** Open-Meteo's own
  `forecast_days=16` counts today as its first day; asking for a 16th day past
  today falls off the end of the window. This was caught by the empty-input run,
  not by the sample.
- **GDD accumulates from `season_start`, not from planting.** This node knows
  nothing about planting dates. `gdd_daily` is emitted alongside
  `gdd_cumulative` so the crop model can re-accumulate from its own start.
- **Compact JSON outputs** (no indentation): 3,336 rows is ~1.1 MB compact.
- **NE and KS carry two points each; the other eight states carry one.** Brief
  `docs/features/0003-irrigation-region-strata.md`. A single point per state
  averages irrigated and rainfed corn as if they were one crop, and where
  irrigation is widespread it drifts towards places that are dry but productive
  *because* of water this node cannot see. A state is split when irrigation
  covers **20 percent or more** of its harvested corn acres: NE 52.7 percent
  and KS 25.4 percent are in, MO 9.4 percent is the next nearest and is out.
  `region_key` becomes `ne_irrigated` / `ne_rainfed` / `ks_irrigated` /
  `ks_rainfed`; `ne` and `ks` are retired, not kept alongside, so a stale join
  finds nothing rather than silently getting the wrong answer.
- **A stratum's weight is apportioned, not measured.** NASS publishes no
  irrigated production at county, state or national level -- verified by
  enumerating every `CORN, GRAIN*` series in the 2022 Census export -- so each
  county's *published* production is divided between the strata in proportion
  to acres times a state-level stratum yield (`CORN, GRAIN, IRRIGATED, ENTIRE
  CROP` against `NONE OF CROP`). Because production is divided rather than
  re-estimated, the strata sum exactly to the state's old weight and
  recombining the two points by weight reproduces the pre-split point, which is
  what `check_weather.py` asserts. Only the yield *ratio* matters; county acres
  times a state yield mixes resolutions, and that is stated in every split
  row's `method`.
- **The strata are not "the better half and the worse half".** The sign of the
  irrigated yield gap flips by state: +105 percent in KS and +55 in NE, but -8
  in IA and -20 in OH, where irrigation sits on marginal ground.
- **A one-day short forecast tail is tolerated; nothing else is.** Open-Meteo
  publishes the last slot of its 16-day grid before its model run fills it, so
  for part of each UTC day the requested window ends on a date that exists with
  six nulls on it. `fetch_window` now classifies what it could not serve: a
  trailing gap in the forecast leg, one day at most
  (`MAX_TRAILING_SHORTFALL_DAYS`), shortens the window and is logged; a hole
  with data after it, a day the archive owes, an empty result, or a two-day
  tail still fails the run with a reason on stderr. The default stays 15,
  because the tolerance is what buys the margin -- buying it by lowering the
  default would cost every run a forecast day. Brief
  `docs/features/0004-forecast-tail-nulls.md`.
- **A short window is never silent, and never ragged.** `metadata` carries
  `forecast_days_served`, `window_requested` and `window_served` beside the
  requested `forecast_days`, so a consumer reads what arrived instead of
  inferring it from a row count; `forecast_days` keeps its old meaning. All
  regions are fetched before any rows are built and trimmed to the last day
  every region can serve, so the table stays rectangular and one window
  describes the run. The consequence, accepted in the brief: a daily `{}`
  schedule returns 15 forecast days on most days and 14 on some.
- **`regions.csv` gained `stratum` and `weight`**, and both reach the output
  through `metadata.regions` only. `TABLE_COLUMNS` is unchanged: they are
  constant per region, so repeating them on 3,336 rows would add bulk and no
  information.

### Modelfile

Mirrors `thermal-indices`: `run` redirects stdout to
`run/crop_weather_daily.output.json`, `args = ["{input:crop_weather_request}",
"{output:crop_weather_summary}"]`, `required = []` everywhere and
`default = {}`. Note `validity_domain` is capped at **600 characters** by the
platform validator; longer prose belongs in the README. Validate from the
`modelhome` repo with
`uv run python -m orchestration.modelfile validate <path>/crop-weather/Modelfile.toml`
(currently OK, no annotation warnings).

### Verified results (2026-09-19)

- `check_weather.py` on the full default run: **123/123 checks pass**. That
  includes a real `Wofost72_PP` maize simulation on the Iowa series (sown
  2026-05-01, anthesis 2026-06-30, maturity 2026-08-13, TWSO 10,926 kg/ha,
  LAImax 4.22 -- **potential production**, so no water, nutrient or pest
  limitation: the perfectly irrigated case, not a yield forecast), the six
  weather variables
  reading back unchanged through `WeatherDataContainer`, PCSE's own unit strings
  matching, `WIND_10M_TO_2M` equal to `pcse.util.wind10to2`, and the
  hand-worked GDD and stress cases, and (added after the Copilot review) five
  payload guards proving a malformed Open-Meteo response exits 1 with a readable
  reason rather than a traceback.
### Verified results (2026-09-21, brief 0004)

- **The bug is time-of-day dependent, and that is now measured.** The same
  one-region run exited 1 at 05:04 UTC ("served neither ERA5 nor a forecast
  for 1 day(s): 2026-10-06") and exited 0 at 06:47 UTC, on unchanged code. At
  05:04 all six variables were null on the grid's last day at three separate
  points; `best_match` takes that day from GFS, while `ecmwf_ifs025` was two
  days shorter and `icon_seamless` three. A baseline taken outside that window
  passes on unfixed code, which is why the new assertions are synthetic.
- `check_weather.py`: **165/165 pass**, up from 147. Eight new checks stub
  `runner.get_json` and pin the classification (tolerated null tail, interior
  gap, two-day tail, missing observed day, nothing served, repeatability);
  nine more assert the new metadata against the rows and that every region
  covers the same days.
- **Negative test:** with `MAX_TRAILING_SHORTFALL_DAYS` at 0 the suite reports
  79/80 and exits 1, naming the tolerated-tail check; restored to 1 it is
  83/83 and exit 0 on the offline leg.
- **Live proof of both paths**, without waiting for the bad window: `date`
  2026-09-22 with the default 15 asks one day past the grid, and the run exits
  0 with `forecast_days` 15, `forecast_days_served` 14, `window_served`
  ending 2026-10-06, logged per region and once for the run. `date` 2026-09-23
  asks two days past and exits 1, naming the shortfall and the cap.
- Full `{}` run at 07:00 UTC: 3,348 rows, 252 forecast, 12 regions, 22 s,
  `forecast_days_served` 15. Sample run: 819 rows, 45 forecast, rows
  **byte-identical** to the pre-change run, metadata differing only by
  `retrieved_at` plus the three new fields.
- Docker build and run produce rows **identical** to the local run for both
  the bare `CMD` and the Modelfile's mounted layout, metadata identical apart
  from `retrieved_at`; the image's own output passes 165/165.
- `Modelfile.toml` validates clean (`OK`). The three capped fields are
  untouched at 569 / 592 / 503 characters.

### Verified results (2026-09-21, brief 0003)

- `build_regions.py` now emits **12 regions from 10 states**, weights summing
  to 1.0000. The eight unsplit states' coordinates are **byte-identical** to
  the pre-split file; the four new points are NE irrigated 41.1699, -98.6459
  (596 m) and rainfed 41.1585, -97.7774 (530 m); KS irrigated 38.1752,
  -99.7654 (676 m) and rainfed 38.9711, -97.6086 (410 m). The elevation spread
  is itself informative: the blended KS point sat at 582 m, between a 676 m
  High Plains irrigated stratum and a 410 m rainfed one.
- **Recombination holds.** Weighting each state's two points by their weights
  reproduces the pre-split point to 1.4e-4 degrees (NE 41.1658, -98.3300
  against the committed 41.1658, -98.3301; KS exact to 4 dp). The residual is
  4-decimal rounding in the stored weights, which is why the check's tolerance
  is 1e-3 degrees rather than exact equality.
- `check_weather.py` on the full run: **147/147 checks pass**, up from 128
  because of the new region-table checks. The driest region moved from `sd` to
  **`ks_irrigated`** (24.5 cm of rain from 1 May to 30 September, against SD's
  33.8 cm), so the three production levels now run there: potential 8,575
  kg/ha, rainfed 3,071 (64 percent below), irrigated 8,575 with 63.0 cm
  applied. The assertions got sharper, not weaker.
- **The Kansas split is visible in the yields.** Before it, the single KS point
  reported a 32 percent rainfed shortfall on 39 cm of in-season rain. That was
  an average of two different places: `ks_irrigated` loses 64 percent on 24.5
  cm, `ks_rainfed` loses 6.5 percent on 39.2 cm. Iowa is unchanged at 10,926 /
  10,799 / 18.0 cm, confirming only NE and KS moved. NE's two points are 73 km
  apart and differ much less (12.8 and 8.5 percent shortfalls).
- Full run (`{"forecast_days": 14}`, 12 regions): 3,336 rows, 240 forecast,
  18 s, 1.1 MB. Sample (2026-09-15, 3 regions): 819 rows, 45 forecast.
- Docker build and run produce rows **identical** to the local run, metadata
  identical apart from `retrieved_at`, for both the bare `CMD` and the
  Modelfile's mounted layout; the image carries the twelve-region table.
- `Modelfile.toml` validates clean (`OK`, no annotation warnings) with
  `description` at 569, `validity_domain` at 592 and `not_for` at 503
  characters, all inside the platform's 600-character cap.
- **Pre-existing failure, not caused by this change and not fixed by it:** the
  literal `{}` input fails at present because Open-Meteo returns the 16th
  forecast slot null-padded early in the UTC day, and the default asks for
  exactly 15 days past today. Diagnosed at 03:07 UTC on 2026-09-21; it
  reproduces on `main`. Baseline and final runs both used
  `{"forecast_days": 14}` so they stay comparable. See the task list.

### Verified results (2026-09-20, brief 0002)

- `check_weather.py` on the full default run: **128/128 checks pass**, up from
  123 because the maize now runs at three production levels instead of one. On
  the ten-region run the check picks **South Dakota**, the driest region that
  season (33.6 cm of rain from 1 May to 30 September): potential TWSO 10,242
  kg/ha, rainfed 5,942 (42 percent below), irrigated 10,242 with 38.2 cm of
  water applied. Irrigating the water-limited run reproduces the potential
  yield exactly, which is the assertion that pins down what the published
  figure means.
- Negative test for the new assertion: shrinking `IRRIGATION_AMOUNT_CM` from
  2.5 to 0.01 makes the reconstruction check fail (irrigated 5,947 vs potential
  10,242) and the script exit 1. Restored, it is 128/128 and exit 0. The old
  `5000 <= TWSO <= 25000` band passed every one of those cases, which is why it
  was never the assertion it looked like.
- `Modelfile.toml` still validates clean (`OK`, no annotation warnings) with
  `not_for` at 503 characters and `validity_domain` untouched at 551, both
  inside the platform's 600-character cap.

- Sample (2026-09-15, 3 regions): 819 rows, 51 forecast, 3 s.
- Full default run (empty input, 10 regions): 2,770 rows, 210 forecast, 19.6 s.
  Resolved defaults are today / Jan 1 / 15 / 10 / 30 / 0 / 32. Ranges are sane:
  IRRAD 1.7e6..3.1e7 J/m2/day, VAP 0.48..32.4 hPa, WIND 0.74..7.2 m/s,
  RAIN 0..6.6 cm/day, all inside PCSE's range checks.
- Docker build and run (both the default `CMD` and the Modelfile's mounted
  layout) produce rows **identical** to the local run, metadata identical apart
  from `retrieved_at`.
- `regions.csv` built from the NASS 2022 Census: the ten states are IA, IL, MN,
  NE, IN, SD, OH, WI, KS, MO, each from 64-103 counties with 0-5 withheld.
- **Not yet verified:** the Model Home import (AC-9). The local stack is up
  (`kind-modelhome`, Vite :5173, port-forward :8000) but behind Auth0 login, so
  it needs a signed-in human.

### Task list

1. AC-9: add the model on the local Model Home stack from the branch subfolder
   URL and run it with `{}`.
2. Mark the PR ready once AC-9 passes; John merges.
3. After merge: register on Model Home from `main` and put it on a daily
   schedule with `{}`.
4. ~~Irrigated / rainfed region strata.~~ Done, brief 0003: NE and KS are
   split. There is no irrigated *production* series at any aggregation level,
   so the stratum weight is reconstructed by apportioning published county
   production; see the design note below.
5. ~~The `{}` default has no forecast margin.~~ Done, brief 0004: a
   null-padded trailing day now shortens the window by one instead of failing
   the run, and the metadata says so. The default stays 15. See the design
   note below.
6. Follow-ups: the **maize variety**, since
   `Grain_maize_201` matures 13 August from a 1 May sowing, a 104-day season
   against roughly 140 for a US Corn Belt hybrid, probably a larger error
   source in Iowa than water is; NASA POWER for the observed leg;
   crop-reporting-district granularity; wheat and soy parameter sets; `TEMP` as
   an explicit column if the crop model wants it rather than PCSE's
   `(TMIN+TMAX)/2` default; and a **dispersion guard on representative
   points**, since a production-weighted mean of two distant clusters can land
   where no corn grows (Missouri's irrigated stratum would sit in the Ozarks)
   and the existing hull check does not catch it, because such a point is still
   inside the county bounding box.

## Task list

1. ~~Create `modelhome/agromet-bundles` on GitHub and push `main`.~~ Done
   2026-09-19.
2. Finish `crop-weather/` (brief 0001): see that bundle's task list above.
3. Sibling repos, composed through a Flow: `wofost-bundles/corn-yield/`
   (which owes `E0`/`ES0`/`ET0`), `ag-commodity-bundles/corn-price/`.
