# Forecast tail nulls

## Outcome

The bundle's default input, the literal `{}`, stops failing. Today it fails
whenever Open-Meteo's forecast grid advertises a day its model run has not yet
filled: the grid carries the date, all six daily variables on it are `null`,
the runner drops the day because null padding is not data, and the run then
aborts because that day is in the requested window. `{}` is the input the
Modelfile offers as its "Example to paste" and the input a daily schedule
re-sends every morning, so this is the production path.

When this is done, a run over the default window survives the publication lag
that causes this -- one advertised but unfilled day at the end of the grid --
rather than failing on it. That is a bounded promise, not a general one: a
larger shortfall is upstream degradation rather than the publication cycle, and
a day that genuinely cannot be served by either endpoint still fails the run
loudly rather than being interpolated or dropped. The window a run covers
remains a function of its inputs and the upstream data, not of the hour the run
happens to start.

## Scope

### In scope

- The fetch path: `daily_payload`, `fetch_window`, and the forecast-day
  arithmetic around them (`crop-weather/runner.py:59-62`, `:173`, `:218`,
  `:236-241`).
- The forecast-day defaults and caps, and whether the default should sit on the
  last reachable day at all.
- Whatever the output must say about the window it actually covers, given the
  decision recorded under Constraints.
- `check_weather.py`: extend `check_payload_guards` so the new behaviour is
  asserted against synthetic payloads, the way the existing malformed-payload
  guards are.
- The `forecast_days` documentation that this changes: its `[inputs.schema]`
  description in `Modelfile.toml`, `crop-weather/README.md`, and `CLAUDE.md`'s
  crop-weather task list item 5, which records this bug as outstanding.

### Out of scope

- The region table. `regions.csv`, the strata, the weights and everything brief
  0003 landed stay exactly as they are.
- The output schema: `TABLE_COLUMNS`, the two output names, the summary shape.
- The agromet columns: GDD, frost and heat-stress counts, their parameters.
- The ERA5-first, forecast-for-the-tail division of labour itself, and the
  choice of Open-Meteo as the source. This brief fixes the seam between the two
  endpoints, not either endpoint's role.
- Adding a second upstream source as a fallback. NASA POWER for the observed
  leg is already a separate follow-up.
- Retries and backoff for transport failures. `get_json` already has a retry
  helper and this is not a transport failure; the request succeeds and returns
  a well-formed payload.

## Acceptance criteria

- **AC-1** — A run whose window ends on a day the forecast endpoint returns as
  a fully null-padded trailing slot completes successfully and exits 0. This is
  asserted against a synthetic payload, not against the live API, so it can
  fail on demand.
- **AC-2** — A day that neither endpoint serves for any other reason still
  fails the run: non-zero exit, a reason on stderr naming the region and the
  missing day, no interpolated and no silently dropped rows. Asserted against a
  synthetic payload with a gap that is not a trailing null tail.
- **AC-3** — Two runs with the same explicit `date` and `forecast_days`, over
  the same upstream data, produce the same set of days. The rows a run covers
  do not depend on the hour it starts beyond the existing `today` default and
  the `retrieved_at` stamp.
- **AC-4** — A consumer can tell from the output alone which days were
  requested and which were served, without diffing row counts against the
  input. (This is the "not silent" half of the decision recorded under
  Constraints; strike it at review if that decision goes the other way.)
- **AC-5** — `check_weather.py` carries the new assertions inside
  `check_payload_guards`, the full suite passes, and each new assertion is
  shown to fail when the fix it guards is reverted, as brief 0002's
  reconstruction check was.
- **AC-6** — Inputs that already work are unchanged: the committed
  `sample_input.json` produces byte-identical rows before and after, metadata
  aside.
- **AC-7** — `Modelfile.toml` validates clean, its `[inputs.schema]` default
  stays the runnable "Example to paste" it is meant to be, and the
  `forecast_days` description says what the parameter now guarantees.
  `CLAUDE.md`'s task list item 5 is retired rather than left describing an open
  bug.

## Constraints and dependencies

### Measured on 2026-09-21 at 05:03-05:04 UTC

The bug reproduces. That is one point in the daily cycle, not a survey of it.

- The forecast endpoint with `forecast_days=16` returned a 16-day grid,
  2026-09-21 .. 2026-10-06. The last day is present in `daily.time` and all six
  requested variables are `null` on it: `temperature_2m_min`,
  `temperature_2m_max`, `shortwave_radiation_sum`, `dew_point_2m_mean`,
  `wind_speed_10m_mean`, `precipitation_sum`. The two preceding days are fully
  populated. So the grid advertises a day the model run has not filled.
- It is not location-specific. Identical at the Iowa point (42.2639,
  -93.5370), the Kansas irrigated point and a central Illinois point: 6 of 6
  variables null on 2026-10-06 at all three.
- `forecast_days=15` returned a 15-day grid, 2026-09-21 .. 2026-10-05, with no
  null in any variable on any day.
- ERA5's last fully populated day was 2026-09-15, six days behind today, which
  matches the lag the error message already quotes.
- End to end, one region, `season_start` 2026-09-01 and `forecast_days`
  defaulted: exit 1 with
  `ia: Open-Meteo served neither ERA5 nor a forecast for 1 day(s): 2026-10-06.`

Reproduce with:

```
curl -s "https://api.open-meteo.com/v1/forecast?latitude=42.2639&longitude=-93.5370&daily=temperature_2m_min,temperature_2m_max,shortwave_radiation_sum,dew_point_2m_mean,wind_speed_10m_mean,precipitation_sum&timezone=UTC&wind_speed_unit=ms&forecast_days=16"
```

**Not measured: the UTC hours over which this bites.** A single 05:04 UTC
observation cannot distinguish a narrow small-hours window from most of the
day, and that difference bears on how much margin is worth buying. Sampling the
same request across a day, recording for each hour whether the 16th slot is
populated, is cheap and worth doing before the plan commits to a number.

### Nothing in the bundle is misbehaving on its own

- `runner.py:59-62` — `DEFAULT_FORECAST_DAYS = 15`, `MAX_FORECAST_DAYS = 15`,
  `OPEN_METEO_FORECAST_DAYS_MAX = 16`. The comment above them already records
  that Open-Meteo counts today as its own first day, so its 16 reaches only 15
  days past today. The default therefore lands exactly on the last reachable
  day: zero margin by construction, and `MAX` equals `DEFAULT`, so there is no
  headroom to spend either.
- `runner.py:173` — `daily_payload` drops a day short of any variable, because
  null padding is not data. Correct.
- `runner.py:218` — `ahead` is computed to cover the missing tail, capped at 16.
- `runner.py:236-241` — `fetch_window` raises when a day is served by neither
  endpoint rather than interpolating or silently dropping it. Correct, and
  deliberate.

Each piece is right. The combination turns one null-padded slot into a failed
run.

### The two repository rules that pull against the obvious fixes

`CLAUDE.md` requires that a bundle fetching at run time **fail clearly** when a
fetch fails, and that it introduce **no wall-clock dependence** beyond the
explicit `today` default and the `retrieved_at` stamp. Measured against those:

- Truncating the window to the last populated day makes the row count a
  function of the hour the run starts. Two runs on the same `date` disagree.
- Lowering the default to 14 costs every run a forecast day, permanently, to
  dodge a condition that may only bite for part of the day. It is also the only
  option that changes nothing about how the failure is detected.
- Retrying with one fewer day hides the condition unless the run reports it.
- Leaving it alone means the daily schedule fails some mornings, and the
  Modelfile ships an example that does not run.

The plan chooses among these; the brief does not. What the plan must satisfy is
AC-1 through AC-3 together, which is the real constraint: succeed on a
null-padded tail, still fail on a genuine gap, and keep the served window a
function of the inputs.

### The decision this turns on, John's to confirm at review

**Is a shortened window allowed to be silent?** This brief says **no**: if a
run covers fewer days than were asked for, the output must say so, which is
AC-4. That reading follows from the determinism rule -- a consumer that cannot
see the difference cannot reproduce the run -- and it rules out the quietest
fixes. Confirm it or strike AC-4 at review, because every other choice in the
plan follows from the answer.

### Already recorded elsewhere

- `CLAUDE.md`, crop-weather task list item 5, states this bug and marks it out
  of brief 0003's scope.
- `CLAUDE.md`, verified results for 2026-09-21, records it as a pre-existing
  baseline failure that reproduces on `main`, which is why 0003's baseline and
  final runs both used `{"forecast_days": 14}`.
- Found at baseline while implementing brief 0003, which landed as PR #3,
  commit `0618aed`. The bug predates that change and is untouched by it.

## General guidance

- Before you write the plan, ask any questions you need to in order to best implement the brief
