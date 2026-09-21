# Plan: Forecast tail nulls

Source brief: docs/features/0004-forecast-tail-nulls.md
Status: implemented
Planned against commit: 0618aed (branch claude/open-meteo-forecast-nulls-427360; tree is main 0618aed plus the untracked brief)
Base commit: 0618aed (branch feat/0004-forecast-tail-nulls)

## Outcome

The default input `{}` stops failing when Open-Meteo's forecast grid advertises
a day its model run has not yet filled. `fetch_window` learns to tell a
null-padded **trailing** day apart from every other kind of gap: the trailing
day shortens the window by one, is logged and is recorded in the output
metadata; anything else still fails the run loudly, as it does today.

The run therefore covers up to `forecast_days` days past `date`, and the output
says how many it actually got. A consumer reading `crop_weather_daily` can see
the difference between "15 days were requested and 15 arrived" and "15 were
requested and 14 arrived" without diffing row counts against an input it may
not have.

## Scope

### In scope

- `crop-weather/runner.py`: the trailing-tail tolerance in `fetch_window`, the
  window trim in `main`, the three new metadata fields in `run_metadata`, and
  the constants and docstring that describe them.
- `crop-weather/check_weather.py`: synthetic-payload assertions for the new
  fetch behaviour, and metadata assertions on a real output.
- `crop-weather/Modelfile.toml`: the `forecast_days` input description and the
  `metadata` output description.
- `crop-weather/README.md`: the input table, the `forecast_days` paragraph, the
  metadata list and the determinism section.
- `CLAUDE.md`: retire crop-weather task list item 5, add a design note and a
  verified-results entry.

### Out of scope

Everything the brief excludes, unchanged: the region table and the strata, the
output schema (`TABLE_COLUMNS`, the two output names, the summary shape), the
agromet columns, the ERA5-first division of labour, a second upstream source,
and transport-level retries. Also out: any change to `DEFAULT_FORECAST_DAYS` or
`MAX_FORECAST_DAYS`, which stay at 15 by the decision recorded below.

## Assumptions and decisions

### D-1. A shortened window is not allowed to be silent (answered by John)

The brief's open decision, put to John at planning time and answered: **not
silent -- tolerate and record**. `fetch_window` tolerates a null-padded
trailing day rather than failing, and the output metadata records what was
requested against what was served. AC-4 stands as written. This rules out both
the silent-truncation option and the "never shortens, buy margin instead"
option.

### D-2. The default stays 15 (answered by John)

`DEFAULT_FORECAST_DAYS` and `MAX_FORECAST_DAYS` both stay at 15. The tolerance
is what buys the margin, so a run keeps the full reach on every day upstream
can serve it, and falls to 14 only when it cannot. The consequence, accepted:
a daily `{}` schedule produces 15 forecast days on most days and 14 on others,
and the metadata explains which. A downstream consumer must not assume a fixed
row count. This goes in the README.

### D-3. Only a trailing gap in the forecast leg is tolerated, and only one day

`fetch_window` classifies the days it could not serve:

| Case | Behaviour |
|---|---|
| No day served at all | `RunError`, as today |
| A missing day at or before `today` (the archive leg) | `RunError`, as today |
| A missing day with a served day after it (interior gap) | `RunError`, as today |
| 2 or more missing days after the last served day | `RunError`, naming the shortfall |
| Exactly 1 missing day after the last served day, after `today` | Tolerated: window shortens by one day, logged to stderr |

The one-day cap is a named constant, `MAX_TRAILING_SHORTFALL_DAYS = 1`, with
the measurement behind it in a comment. The observed upstream behaviour is a
one-day pad: the grid advertises one day the model run has not filled. A
two-day shortfall is a different condition -- upstream degradation, not the
publication cycle -- and `CLAUDE.md`'s fail-clearly rule says that should fail.
This is also what makes AC-2 testable: a two-day null tail must still raise.

### D-4. The window is trimmed uniformly across regions, so the table stays rectangular

Regions are fetched one at a time, so in principle one region could truncate
while another does not, leaving a ragged table and a metadata field that is
true for some regions and not others. `main` therefore becomes two-phase:
fetch every region's window first, take the **minimum** last served day across
regions, trim every window to it, and only then build rows. One run, one
window, one `window_served` in metadata.

In practice all regions hit the same endpoint within seconds of each other and
will truncate identically; the trim is there so that "in practice" is not load
bearing. The cost is that all twelve windows are held in memory at once, which
at roughly 300 days x 12 regions x six floats is negligible.

The trim happens **before** `daily_rows`, so the Angstrom estimate and the GDD
accumulation are computed over exactly the days that are emitted.

### D-5. Metadata shape

`run_metadata` gains three fields, in the shape shown to John with D-1:

```json
"forecast_days": 15,
"forecast_days_served": 14,
"window_requested": ["2026-01-01", "2026-10-06"],
"window_served":    ["2026-01-01", "2026-10-05"]
```

`forecast_days` keeps its present meaning -- what was requested -- so no
existing consumer changes meaning under its feet. `window_requested` is
`[season_start, date + forecast_days]` and is redundant with the fields beside
it; it is included anyway so a consumer can compare two ranges rather than
recompute one. On a run that gets everything it asked for,
`forecast_days_served == forecast_days` and the two windows are equal.

The summary output shares the same `metadata` dict, so it gains the fields for
free.

### D-6. The new fetch assertions go in a sibling function, not inside `check_payload_guards`

AC-5 says the new assertions go "inside `check_payload_guards`". They will
instead go in `check_fetch_window()`, called from `main()` immediately after
it, in the same synthetic-payload style and with the same offline guarantee.
The reason is that `check_payload_guards` tests `daily_payload` -- one function,
pure, no transport -- while these test `fetch_window`'s classification of a
whole window and need `runner.get_json` stubbed. Folding both into one function
would make it do two jobs and obscure which one failed. **John can reject this
and have them inlined; it is presentation, not coverage.**

### D-7. `MAX_PAST_DAYS`, retries and the error wording are untouched

The existing `RunError` message for a genuinely unservable day keeps its exact
present wording, so an operator who has seen it before still recognises it. The
new two-day-shortfall error is a separate, new message.

## Acceptance-criteria traceability

| ID | Acceptance criterion | Implementation | Verification | Status |
|---|---|---|---|---|
| AC-1 | A run whose window ends on a fully null-padded trailing slot completes and exits 0 | `runner.py:253-282` trailing-gap branch; `main:615-634` trim | `check_fetch_window` "a null-padded last forecast day shortens the window instead of failing" (+3 more); live `date` 2026-09-22 run exited 0, served to 2026-10-06 | pass |
| AC-2 | Any other unservable day still fails: non-zero exit, region and day on stderr | `runner.py:256-280` classification | Four checks: interior gap, two-day tail, missing observed day, nothing served; live `date` 2026-09-23 run exited 1 with the shortfall and the cap named | pass |
| AC-3 | Same `date` and `forecast_days` over the same upstream data give the same days | `fetch_window` is a pure function of the payloads; uniform trim in `main` | `check_fetch_window` "the same payloads give the same served window"; `check_output` "every region covers the same days" | pass |
| AC-4 | The output alone says which days were requested and which were served | `run_metadata:536-548` | Five `check_output` assertions tying `window_served` / `forecast_days_served` to the rows; live short run reported 15 requested / 14 served | pass |
| AC-5 | The assertions live in `check_weather.py`, the suite passes, and each new assertion fails when its fix is reverted | `check_fetch_window`, extended `check_output` | 165/165 with an output file, 83/83 offline; with `MAX_TRAILING_SHORTFALL_DAYS = 0` the suite reports 79/80 and exits 1, naming the tolerated-tail check | pass |
| AC-6 | Inputs that already work are unchanged | No change to conversion, GDD or table code | `sample_input.json` rows byte-identical before and after (819 rows); metadata differs only by `retrieved_at` and the three new fields | pass |
| AC-7 | `Modelfile.toml` validates clean, its default stays runnable, `forecast_days` says what it now guarantees, `CLAUDE.md` item 5 retired | `Modelfile.toml:55,129`; `README.md`; `CLAUDE.md` | `orchestration.modelfile validate` -> `OK`; capped fields untouched at 569 / 592 / 503; `{}` (the schema default) runs | pass |

## Verification

| Command | Purpose | Baseline result (2026-09-21 06:57 UTC) | Final result |
|---|---|---|---|
| `uv run --no-project --python 3.12 --with pcse==6.0.13 --with numpy python check_weather.py` (no argument: the offline leg) | The synthetic guards, including the new window cases. This is the falsifiable evidence for AC-1..AC-3, independent of the API's mood | 75/75 pass | **83/83 pass** (+8 forecast-tail checks) |
| `python3 runner.py sample_input.json ../run/crop_weather_summary.output.json > ../run/baseline_sample_daily.json` | AC-6: the committed sample, rows diffed before and after | exit 0, 819 rows, 45 forecast | exit 0, 819 rows, rows **byte-identical**, metadata differing only by `retrieved_at` and the three new fields |
| `printf '{}' > /tmp/empty.json && python3 runner.py /tmp/empty.json ../run/crop_weather_summary.output.json > ../run/crop_weather_daily.output.json` | The production path end to end. **Record the UTC time with the result** -- see the note below | exit 0, 3,348 rows, 252 forecast, 22.4 s -- **passing only because 06:57 UTC is outside the bad window** | exit 0, 3,348 rows, 252 forecast, `forecast_days_served` 15 (07:00 UTC, still outside the window) |
| `uv run --no-project --python 3.12 --with pcse==6.0.13 --with numpy python check_weather.py ../run/crop_weather_daily.output.json` | The full suite on a real output, including the WOFOST run | 147/147 pass | **165/165 pass** (+18) |
| `docker build -t crop-weather . && docker run --rm crop-weather` | Image parity: rows identical to the local run, metadata identical apart from `retrieved_at` | not run: parity is a final-only comparison against the local run at the same code state | bare `CMD` and the mounted layout both produce rows **identical** to the local run, metadata differing only by `retrieved_at`; the image's output passes 165/165 |
| `uv run python -m orchestration.modelfile validate <modelhome>/…/crop-weather/Modelfile.toml` | AC-7. Run from the sibling `modelhome` repo | `OK` | `OK`, capped fields untouched at 569 / 592 / 503 |

**Correction to the planned command.** The plan called the argument-less
`check_weather.py` an offline leg runnable as `python3 check_weather.py`. It is
not: `check_units` imports `pcse.util`, so the offline leg needs the same `uv
run` wrapper as the full suite. It is offline in the sense that matters -- it
makes no network call and needs no output file -- but the command in the table
above is the corrected one.

**The `{}` baseline is time-dependent and cannot be trusted on its own.**
Measured on 2026-09-21: the same one-region run exited 1 at 05:04 UTC and
exited 0 at 06:47 UTC, unchanged. A baseline taken outside the bad window will
pass on unfixed code. `run` must record the UTC time beside every `{}` result,
and must not treat a passing baseline as evidence that the bug is absent. The
deterministic evidence is the synthetic leg, which is why AC-1 is written
against a stubbed payload.

## Implementation steps

1. **Constants** (`runner.py`, the defaults block at :57-63). Add
   `MAX_TRAILING_SHORTFALL_DAYS = 1` with a comment recording the measurement:
   Open-Meteo's `forecast_days=16` grid can carry a final day with all six
   variables null; observed null at 05:04 UTC and populated at 06:47 UTC on
   2026-09-21 at three separate points; `best_match` takes that last day from
   GFS, while `ecmwf_ifs025` was two days shorter and `icon_seamless` three.
   Leave `DEFAULT_FORECAST_DAYS` and `MAX_FORECAST_DAYS` at 15 (D-2), and
   extend the existing comment to say the last day may not be populated yet.

2. **`fetch_window`** (`runner.py:182-244`). Leave the fetching and
   `daily_payload` use exactly as they are. Replace the `still_missing` block
   with the classification in D-3. Return the window as now -- the caller reads
   the served last day from its keys, so the signature does not change. Log one
   stderr line when a day is tolerated, naming the region and the dropped day.

3. **`main`** (`runner.py:549-591`). Split the region loop into two phases:
   fetch every window, compute `served_last = min(max(keys))` across regions
   and `requested_last = end_date + forecast_days`, trim each window to
   `served_last`, then run `daily_rows` over the trimmed windows. Log a single
   line when `served_last < requested_last`. Keep the existing per-region
   progress logging.

4. **`run_metadata`** (`runner.py:496`). Add `forecast_days_served`,
   `window_requested` and `window_served` per D-5. It needs `served_last` and
   `requested_last`; pass them in rather than recomputing.

5. **`check_fetch_window`** in `check_weather.py`, after
   `check_payload_guards`, called from `main()`. Build Open-Meteo-shaped
   payloads in a helper and stub `runner.get_json` (restore it in a `finally`).
   Cases: T1 null-padded last day tolerated, window one day short, no raise;
   T2 interior gap raises, naming the day; T3 two-day null tail raises; T4
   nothing served raises; T5 a gap at or before `today` raises even as the last
   day; T6 the same stubs twice give the same served window.

6. **`check_output`** in `check_weather.py`. Add the three metadata fields to
   the `for field in (...)` list, and assert: `window_served[1]` equals the
   maximum row date; `forecast_days_served` equals `window_served[1]` minus
   `date`; `forecast_days_served <= forecast_days`; and every region covers the
   identical set of dates (the rectangularity D-4 buys, which nothing checks
   today).

7. **Docs.** `runner.py`'s module docstring (the `forecast_days` line at :13);
   `README.md` input table at :37, the `forecast_days` paragraph at :44-46, the
   metadata list at :75, and the determinism section around :406. Note that
   README:46 currently reads "Asking for more fails the run rather than
   silently returning a short window" -- half of that sentence is what this
   change qualifies. Asking for more than 15 still fails; a short window is now
   possible but never silent, and the sentence must say both; and
   `Modelfile.toml`'s `forecast_days` description at :55 plus the
   `[outputs.schema.properties.metadata]` description at :129. Each must say
   that a run may fall one day short of the request and that the metadata
   records it. `validity_domain` (:19) is already at 592 of the platform's 600
   characters and its text stays true, so leave it alone unless validation
   forces a change; if it must change, shorten before adding.

8. **`CLAUDE.md`.** Retire crop-weather task list item 5 (strike it as done and
   point at brief 0004, the way item 4 points at 0003), add a design note under
   the bundle's design notes stating D-1 through D-4 in two or three sentences,
   and add a verified-results entry for the date `run` executes.

9. **Negative test for AC-5.** Set `MAX_TRAILING_SHORTFALL_DAYS` to 0, confirm
   T1 fails and the script exits 1, restore it, confirm the suite is green
   again. Record both numbers in the PR body, as brief 0002 did with
   `IRRIGATION_AMOUNT_CM`.

## Files likely to change

- `crop-weather/runner.py` -- constants, `fetch_window`, `main`,
  `run_metadata`, module docstring.
- `crop-weather/check_weather.py` -- `check_fetch_window`, `check_output`.
- `crop-weather/Modelfile.toml` -- two descriptions.
- `crop-weather/README.md` -- four passages.
- `CLAUDE.md` -- task list, design notes, verified results.
- `docs/plans/0004-forecast-tail-nulls.md` -- status, base commit, results.

No change to `regions.csv`, `build_regions.py`, `sample_input.json`, the
`Dockerfile`, or any output column.

## Deviations from the plan, found during implementation

- **The offline leg is not runnable as `python3 check_weather.py`.** The plan
  said it was; `check_units` imports `pcse.util`, so it needs the same `uv run`
  wrapper as the full suite. It is still offline in the sense that matters --
  no network call, no output file -- and the verification table above carries
  the corrected command. A planning error, not a code change.
- **D-6 stands, and John was told at planning time.** The new assertions are in
  `check_fetch_window()`, called from `main()` right after
  `check_payload_guards()`, rather than inside it as AC-5 words it. Same file,
  same style, same offline guarantee; they test `fetch_window` rather than
  `daily_payload` and need `runner.get_json` stubbed.
- **The tolerated-tail check was restructured after the negative test.** As
  first written it let the `RunError` propagate, so removing the tolerance
  ended the suite with a traceback instead of a reported failure. It now
  catches the error and records a `FAIL`, which is the house style and what
  makes the negative test read properly (79/80, exit 1).
- **Live evidence was added beyond the plan.** The plan relied on synthetic
  payloads for AC-1 and AC-2 because the live condition comes and goes. Asking
  for a window one day past the grid (`date` 2026-09-22 with the default 15)
  exercises the tolerance against the live API at any hour, and two days past
  exercises the failure. Both were run; neither is committed as a test.
- **The over-cap message says "day(s)".** It fires only at two or more with the
  cap at 1, but the negative test drops the cap to 0, where "1 days" read
  badly. Matches the wording of the message beside it.

## Risks and follow-ups

- **The one-day cap is a guess at upstream's worst case.** It is grounded in
  one day's observation. If Open-Meteo ever pads two days, runs fail until the
  constant changes -- deliberately, per D-3 and AC-2, but it would present as a
  fresh outage. The stderr message must therefore name the shortfall and the
  cap, so the fix is obvious from the log alone.
- **Row counts now wobble between scheduled runs.** Accepted under D-2 and
  documented, but any downstream code that assumes a fixed series length will
  notice. `wofost-bundles/corn-yield` does not exist yet, so this is the moment
  to set the expectation.
- **Uniform trimming couples the regions.** One region served short shortens
  every region's window. That is the point (D-4), but it means a single
  misbehaving grid point can cost every region a day.
- **The UTC-hour survey is still not done.** This plan no longer depends on it:
  the tolerance handles the condition whenever it occurs. It remains worth an
  hour of sampling to know how often a scheduled run will produce 14 days
  rather than 15, and it would tell us which UTC hours make the worst schedule
  times. Follow-up, not a blocker.
- **Pre-existing and untouched:** the maize variety question, NASA POWER for
  the observed leg, crop-reporting-district granularity, and the dispersion
  guard on representative points all stay where `CLAUDE.md` leaves them.
