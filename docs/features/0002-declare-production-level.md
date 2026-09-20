# Declare production level

## Outcome

Every place the repo reports a WOFOST yield says which production level produced
it. Today `check_weather.py` runs `Wofost72_PP` and the result is recorded as
"TWSO 10,926 kg/ha ... all credible for central Iowa" with no mention that
potential production assumes water is never limiting. Measured on the same
weather series, irrigated water-limited production reproduces the potential
number to the kilogram, so the figure the repo publishes is the
perfectly-irrigated case: unlimited water, applied instantly, at no cost, on
100 percent of acres. A reader has no way to know that.

When this is done, a reader of `crop-weather/README.md`, `Modelfile.toml` or
`CLAUDE.md` can tell at a glance that the validation yield is a potential-
production figure, what it excludes, and that it is not a yield forecast. The
check that blesses it can fail.

## Scope

### In scope

- State the production level and what it excludes wherever the WOFOST run or
  its yield is described: `crop-weather/README.md`, the `Modelfile.toml`
  annotation fields where it belongs (`validity_domain`, `not_for`), and the
  verified-results and design-notes sections of `CLAUDE.md`.
- Replace the unfalsifiable yield assertion in `crop-weather/check_weather.py`.
  It currently asserts `5000 <= TWSO <= 25000`, an interval so wide that every
  observed outcome passes it, including a 1,792 kg/ha rainfed crop failure and
  a 10,926 kg/ha potential yield. The replacement must be capable of failing on
  a plausible regression.
- Record, next to the check, that potential production is a deliberate choice
  for a weather bundle -- it isolates the weather from soil parameters this
  node does not own -- rather than an oversight.

### Out of scope

- Splitting `regions.csv` into irrigated and rainfed strata. That is its own
  brief, and it depends on NASS county-level disclosure coverage.
- Any irrigation or water-balance modelling. That belongs to
  `wofost-bundles/corn-yield`, which owns the soil and the crop.
- The maize variety. `Grain_maize_201` sown 1 May reaches maturity 13 August, a
  104-day season against roughly 140 days for a US Corn Belt hybrid. This is a
  real accuracy problem and probably a larger one than water in Iowa, but it is
  a separate change.
- Any change to `runner.py`, the output schema, or the bundle's runtime
  dependencies. This brief changes documentation and a dev-only check.

## Acceptance criteria

- **AC-1** — `crop-weather/README.md` names the production level of the
  validation run and says what it assumes away (water, nutrient and pest
  limitation), in the section that reports the WOFOST result.
- **AC-2** — `Modelfile.toml` carries the same statement in its annotation
  fields, with `not_for` ruling out use as a yield forecast. The file still
  passes `uv run python -m orchestration.modelfile validate` from the
  `modelhome` repo with no new warnings, and `validity_domain` stays inside the
  platform's 600-character cap.
- **AC-3** — `CLAUDE.md`'s verified-results entry for the WOFOST run states the
  production level rather than describing the yield as simply "credible".
- **AC-4** — `check_weather.py` no longer asserts only `5000 <= TWSO <= 25000`.
  Its replacement fails when given a yield that is wrong in a way the old
  assertion accepted.
- **AC-5** — The full check still passes on a real default run, and the run's
  pass count is recorded in `CLAUDE.md` as the existing entry does.

## Constraints and dependencies

- No new runtime dependencies and no new data sources. `check_weather.py` is
  dev-only and may keep using PCSE; the image stays dependency-free.
- One candidate for AC-4 is to run water-limited production alongside potential
  and assert the ordering. Note a measured wrinkle before relying on it: for
  `Grain_maize_205` on the 2026 Iowa series, rainfed TWSO came out slightly
  *above* potential (9,075 against 9,010 kg/ha), because the mild deficit
  lowers LAI and WOFOST's partitioning repays some of that. A strict
  `PP >= rainfed` assertion is therefore not safe everywhere; a region where
  water actually binds, or a tolerance, is needed. Comparing against published
  NASS state yields is the stronger option but brings a data source with it,
  which this brief otherwise avoids.
- The measurements above came from `Wofost72_WLP_CWB` with PCSE's generic
  `DummySoilDataProvider`, not a real soil, so they indicate direction and
  rough magnitude only.

## General guidance

- Before you write the plan, ask any questions you need to in order to best implement the brief
