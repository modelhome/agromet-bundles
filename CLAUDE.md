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

## Task list

1. Create `modelhome/agromet-bundles` on GitHub and push `main` (this scaffold
   plus the vendored feat skill), so `/feat run` has a base to branch from.
2. `crop-weather/` (brief 0001): plan, review, then run.
3. Sibling repos, composed through a Flow: `wofost-bundles/corn-yield/`,
   `ag-commodity-bundles/corn-price/`.
