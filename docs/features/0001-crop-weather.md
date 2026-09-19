# Crop weather

## Outcome

`crop-weather/` is a self-contained Model Home model that, given a date,
produces for each US corn-growing region a daily weather series -- season start
through today plus a forecast horizon -- in exactly the variables and units
PCSE/WOFOST consumes, plus accumulated growing degree days to date and simple
stress-day flags.

Pasting the subfolder's GitHub URL into `http://localhost:5173/models/new/repo`
creates a working model. A downstream corn-yield model
(`wofost-bundles/corn-yield/`, a separate repo and brief) loads its output
straight into a PCSE weather-data provider with no reformatting, and Model Home
composes the two in a flow that runs daily.

This is node 1 of a three-node climate -> agriculture -> finance flow: forecast
weather -> corn crop model (WOFOST, phenology-aware yield) -> corn commodity
price impact. Node 1's job is defined entirely by what node 2 needs to eat, so
node 2's input schema is node 1's output schema.

## Scope

### In scope

**Repo scaffold.** Top-level `.gitignore`, `.dockerignore`, `LICENSE` (MIT),
`README.md` and `CLAUDE.md` matching `thermofeel-bundles`, plus the vendored
`feat` skill, then `crop-weather/` beneath it.

**The `crop-weather/` bundle:** `Modelfile.toml`, `Dockerfile`, `runner.py`, a
sample input JSON, the committed region-definition table, and a validation check
run outside the image.

**The region set and key.** Node 1 owns region identity. Roughly ten top US
corn-producing states, each represented by one production-weighted
representative point (lat, lon, elevation), committed as a table
(`region_key, state, lat, lon, elev_m, method, source`). State selection and
ordering come from USDA NASS production shares, with the vintage cited; choosing
the representative point is a documented modelling choice and the method is
stated in the table and the README. `region_key` is the stable identifier every
downstream node joins on: human-legible, stable, and unchanged as it propagates
to nodes 2 and 3.

**Input schema (JSON), mirroring `thermal-indices`' input declaration.** Every
field optional, so a daily schedule can send an empty object:

- `date` -- ISO date, the last observed day. Defaults to the current UTC date at
  run time.
- `regions` -- optional override array of `{region_key, lat, lon, elev_m}`.
  Defaults to the baked-in region table.
- `season_start` -- optional ISO date starting the fetch window. Defaults to
  1 January of `date`'s year. Node 1 stays ignorant of planting dates: it
  fetches the whole current-year series and lets node 2 start its simulation at
  the planting date it configures. That is what decouples the nodes.
- `forecast_days` -- optional, defaults to about 16 (Open-Meteo's horizon).
- `gdd_base_c`, `gdd_cap_c` -- optional, defaulting to corn's 10 degC base and
  30 degC cap.

**Weather fetch and window.** Open-Meteo (free, no key). Per region, fetch the
daily variables covering `season_start` through `date` + `forecast_days` in as
few calls as possible, and document which endpoints were used. Re-fetch the
whole window each run. Stamp each day `is_forecast = true` when it was forecast
or preliminary at retrieval, else `false`. Record run-level `data_source`,
`retrieved_at` and the endpoints in the output metadata and the README.

**The PCSE/WOFOST variable contract -- the crux.** The output must load into a
PCSE `WeatherDataProvider` with no reformatting. Confirm PCSE's exact expected
variables and units from its source before implementing; the verified table for
PCSE 6.0.13 is in [`CLAUDE.md`](../../CLAUDE.md). Unit conversion is where this
breaks: radiation to J/m2/day, precipitation mm to cm, wind 10 m to 2 m, vapour
pressure from humidity or dewpoint. Emit the series in PCSE's native column
names and units so node 2 needs no reformatting, and validate by having PCSE
actually load a sample.

**Agromet value-add**, per region, beyond the raw PCSE series:

- Accumulated growing degree days to date, using the configurable base and cap,
  accumulated from `season_start` (or a documented GDD start) through each day.
  Document the formulation (for example the capped-average method) and the
  accumulation start.
- Stress-day flags and counts: killing-frost days (TMIN below a documented
  threshold) and heat-stress days (TMAX above a documented threshold), as
  per-day booleans and season-to-date counts.

Base, cap and thresholds are declared, annotated parameters in the Modelfile's
validity domain, so the node also serves a future wheat or soy flow.

**Output.** Two artifacts, per the `thermal-indices` / `bond` output convention:

1. A long-format table, one row per region per day across the full window, with
   keys and flags (`region_key, state, date, is_forecast, lat, lon, elev_m`),
   the PCSE weather series in its native names and units, and the agromet
   columns (`gdd_daily, gdd_cumulative, frost_day, heat_stress_day,
   frost_days_to_date, heat_stress_days_to_date`). The same table is written as
   a CSV beside it for off-platform use.
2. A convenience document mirroring `thermal-indices`' nested shape: per region,
   run-level metadata plus the full daily series, so downstream consumers and a
   future single-page app get the whole season rather than just today.

Run-level metadata carries `data_source`, `retrieved_at`, the endpoints,
`gdd_base_c`, `gdd_cap_c` and the region table.

**Determinism semantics**, documented in the bundle README and reflected in the
Modelfile annotations: deterministic given `date`, the region set and the
Open-Meteo data as of retrieval. Recent days are forecast or preliminary and can
be revised; the whole window is recomputed each run, so the series always
reflects current data and current code.

### Out of scope

- The crop model (node 2, `wofost-bundles/corn-yield/`) and the price model
  (node 3, `ag-commodity-bundles/corn-price/`), their repos, and their briefs.
- The flow definition. Model Home composes flows in the platform and has no
  Flowfile; do not spec one.
- Planting-date, soil, production-weight and price tables. Those belong to nodes
  2 and 3, joined on node 1's region key. Node 1 owns region identity only --
  key, coordinates, elevation -- not agronomic or economic attributes.
- Finer geographic granularity (crop-reporting districts). A documented future
  refinement, not this build.
- The single-page app and any downstream visualisation.
- NASA POWER integration. Documented in the README as probable future work; not
  built.

## Acceptance criteria

- **AC-1** -- `modelhome/agromet-bundles` exists with top-level `.gitignore`,
  `.dockerignore`, `LICENSE` (MIT), `README.md`, `CLAUDE.md` and a
  `crop-weather/` subfolder, matching `thermofeel-bundles` conventions.
- **AC-2** -- `crop-weather/` contains `Modelfile.toml`, `Dockerfile`,
  `runner.py`, a sample input JSON and the committed region table.
- **AC-3** -- `python crop-weather/runner.py crop-weather/<sample_input>` runs
  end to end and writes the outputs with the schema above, for the full
  season-to-date plus forecast window.
- **AC-4** -- `docker build` from the bundle folder succeeds and `docker run`
  reproduces the same outputs, given network egress.
- **AC-5** -- The output loads into a PCSE `WeatherDataProvider` without
  reformatting, and a committed check demonstrates PCSE accepting a sample and
  reading back the expected variables and units. This is the decisive
  correctness test: the whole node exists to satisfy it.
- **AC-6** -- GDD accumulation and stress-day flags are correct against a
  hand-worked example, with base, cap and thresholds exposed as documented
  parameters (corn defaults 10 and 30 degC).
- **AC-7** -- The region table covers about ten top corn states with one
  representative point each, keyed by a stable `region_key`, sourced from USDA
  NASS shares with the representative-point method documented.
- **AC-8** -- With no input supplied, the model defaults to the current UTC date,
  the baked-in region set, a 1 January season start, an approximately 16-day
  forecast and the corn GDD defaults, so a scheduled run needs no parameters.
- **AC-9** -- Pasting the `crop-weather/` subfolder GitHub URL into
  `http://localhost:5173/models/new/repo` creates a working model whose run
  produces the expected artifacts.
- **AC-10** -- The bundle README documents the Open-Meteo endpoints and variables
  used and every unit conversion into PCSE's convention, the GDD formulation and
  stress thresholds, the region set with its source and method, the determinism
  semantics, and the probable future expansion to NASA POWER.

## Constraints and dependencies

- **The PCSE unit contract is the crux.** Confirm PCSE's expected variables and
  units from its documentation and source before coding, and pin the version the
  claim was verified against. The output must load into a `WeatherDataProvider`
  unchanged. Radiation to J/m2/day, precipitation mm to cm, wind 10 m to 2 m and
  vapour pressure from humidity are where this breaks; validate against PCSE
  actually loading it rather than against a reading of its docs.
- **Network egress at run time.** Like `thermal-indices`, this model calls
  Open-Meteo when it runs. Model Home's runs namespace permits outbound network;
  confirm that still holds, and if it does not, flag it -- the fetch would have
  to move to the scheduled trigger.
- **Node 1 owns region identity only.** The key, coordinates and elevation
  originate here; planting dates, soil, production weights and prices belong to
  nodes 2 and 3 and join on the key. Do not pull those attributes into node 1.
- **Reusability.** GDD base and cap and the stress thresholds are parameters with
  corn defaults, not hardcoded constants, so the node serves a future wheat or
  soy flow.
- **Pin dependencies** in the Dockerfile, mirroring `thermal-indices`' pinning
  style. **PCSE must not be a runtime dependency of node 1** -- it belongs to
  node 2, and node 1 only needs to conform to its format. Using PCSE in a
  dev-only check to prove AC-5 is fine, and expected, but it stays out of the
  runtime image.
- **Seasonality is expected.** The series is most meaningful during the corn
  growing season, roughly April to October. Off-season output is valid but quiet.
  Note it; it is not a defect.
- **Licence MIT.** Provenance is the Open-Meteo API and the documented
  conversions, not vendored code.
- **A fetch-at-run-time bundle stamps its provenance.** See the determinism
  section of [`CLAUDE.md`](../../CLAUDE.md).

## General guidance

Repo-wide conventions -- mirroring `bond/` and `thermal-indices/`, the Model
Home platform facts, the verified PCSE weather contract, unit discipline,
pinning, determinism stamping and region identity -- live in
[`CLAUDE.md`](../../CLAUDE.md) and are not restated here. Read it first, and
read the two template bundles before writing any of this one.

Beyond that:

- Design the output schema as node 2's input schema. The whole node exists to
  feed WOFOST cleanly: the PCSE-native series is the contract, and the agromet
  columns are additive.
- Keep the fetch layer structured so a second weather provider could slot in
  behind the same output contract. NASA POWER is the agromet standard for
  driving WOFOST and DSSAT and serves the crop-model variables natively (2 m
  wind, agricultural radiation), which would reduce conversions and add
  credibility -- but it has multi-day latency and no forecast, so it is a
  candidate for the observed leg later, with Open-Meteo retained for the
  forecast leg. Document that as the planned evolution and why; do not build it.
- Document every modelling choice -- region representative points, the GDD
  formulation, the stress thresholds, every unit conversion -- in the bundle
  README and in the Modelfile's validity-domain annotations.
- Update the top-level `README.md` bundle table to list `crop-weather/`
  (inputs -> outputs) in the style `thermal-indices` and `bond` use, and fill in
  `CLAUDE.md`'s bundle section with the design notes, verified results and task
  list.
