# Plan: Declare production level

Source brief: docs/features/0002-declare-production-level.md
Status: implemented
Planned against commit: 142bfdc (branch claude/irrigation-model-accuracy-2bf447, same tree as main)
Base commit: 142bfdc (branch feat/0002-declare-production-level)

## Outcome

The repo stops publishing an unlabelled yield. `check_weather.py` runs
`Wofost72_PP`, and measurement on the same weather series shows that
water-limited production with irrigation reproduces that number to the
kilogram -- so the figure in `CLAUDE.md` is the perfectly-irrigated case, with
unlimited water applied instantly at no cost on 100 percent of acres, and
nothing says so. After this change the README, the Modelfile and `CLAUDE.md`
each name the production level and what it assumes away, and the check that
blesses the yield can fail.

## Scope

### In scope

- Name the production level in `crop-weather/README.md`, in the Modelfile's
  `not_for`, and in `CLAUDE.md`'s verified-results and design-notes sections.
- Replace the sole yield assertion in `crop-weather/check_weather.py` with
  assertions that can fail (D2).
- Record beside the check that potential production is a deliberate choice for
  a weather bundle, not an oversight.

### Out of scope

Carried from the brief, unchanged: irrigated/rainfed strata in `regions.csv`;
any irrigation or water-balance modelling in this repo (that is
`wofost-bundles/corn-yield`); the maize variety (`Grain_maize_201` matures 13
August from a 1 May sowing, a 104-day season against roughly 140 for a US Corn
Belt hybrid); and any change to `runner.py`, the output schema or the image's
dependencies.

## Assumptions and decisions

### D1. The Modelfile change is one clause in `not_for` (answered)

The bundle emits weather, not yield, so it makes no production-level claim of
its own; and `validity_domain` is already 551 of the 600 characters the
platform validator allows, leaving 49 characters of headroom. Asked whether to
use `not_for` only, both fields with trimming, or to drop AC-2 entirely; the
answer was `not_for` only. `not_for` is 309 characters, so there is room. The
clause says the bundle's own WOFOST validation is a potential-production run
and implies nothing about water-limited yield downstream. `validity_domain` is
not touched, so there is no risk against the cap.

### D2. AC-4 becomes an ordering and reconstruction check, not a band (answered)

Asked whether to replace the `5000 <= TWSO <= 25000` band with an ordering
check against a rainfed run, a comparison against published NASS yields, or a
tighter per-region band; the answer was the ordering check. It adds no data
source, costs a few seconds, and is itself the demonstration the brief is
about.

Measured on the 2026 series while framing this brief, `Grain_maize_201`, sown
1 May, generic `DummySoilDataProvider`:

| region | potential | rainfed | irrigated | water applied |
|---|---|---|---|---|
| IA | 10,926 | 10,799 | 10,926 | 18.0 cm |
| NE | 9,892 | 8,673 | 9,892 | 29.2 cm |
| KS | 8,746 | 5,940 | 8,746 | 54.0 cm |

A margin keyed to the potential-minus-rainfed gap would be brittle: that gap is
32 percent in Kansas but 1.2 percent in Iowa, and a wet year would shrink it
further. The gap is also not strictly signed -- for `Grain_maize_205` on the
2026 Iowa series rainfed came out at 9,075 against potential's 9,010, because
the mild deficit lowers LAI and WOFOST's partitioning repays some of it. So the
check asserts three things instead, the middle one being the claim the brief
exists to make and the only one that is robust in both wet and dry years:

1. **Ordering.** Potential is at least rainfed less 1 percent. Holds
   everywhere, including the LAI inversion above, and a RAIN unit slip would
   swing the rainfed run far enough to break it.
2. **Reconstruction.** Water-limited production with soil-moisture-triggered
   irrigation reproduces the potential yield to within 1 percent. This is what
   pins the meaning of the published figure. It was exact in all three regions
   measured and does not depend on the season being dry.
3. **The water balance is alive.** Irrigation applied is greater than zero.
   Near-certain in any Corn Belt summer, and it catches a water balance that
   silently does nothing.

The existing `5000 <= TWSO <= 25000` band stays as an explicitly labelled smoke
test rather than being deleted; AC-4 requires only that it is no longer the
sole assertion.

### D3. The region the new runs use

`check_pcse` currently takes whichever region happens to be first in the
output. The new runs use the region with the lowest total `RAIN` between 1 May
and 30 September in the output at hand, so the sample input (IA/IL/NE) and the
default ten-region run both exercise the case where water actually binds. The
chosen region and its in-season rainfall are printed, so a reader can see which
one the numbers describe.

### D4. PCSE mechanics the implementation needs (verified against 6.0.13)

Read from the pinned wheel, because PCSE's own docstrings are wrong on two of
these:

- The class is `Wofost72_WLP_CWB`. `Wofost72_WLP_FD` is a backwards-
  compatibility alias for it in `pcse/models.py`.
- Irrigation is an AgroManager `StateEvents` entry: `event_signal: irrigate`,
  `event_state: SM`, `zero_condition: falling`.
- Event keywords are `amount` and `efficiency`. The dispatcher passes the
  events-table dict straight through to `_on_IRRIGATE(self, amount,
  efficiency)`, so the `irrigation_amount` in PCSE's docstring examples never
  reaches the handler.
- **Amounts are in cm, not mm.** `_on_IRRIGATE` sets `RIRR = amount *
  efficiency` and `RIRR` is documented cm/day in
  `pcse/soil/classic_waterbalance.py`. PCSE's `TimedEvents` docstring comment
  says "All irrigation amounts in mm"; that is free text in an example and it
  is wrong for the classic water balance. Per repo convention the conversion
  comment names both units.
- A campaign carrying `StateEvents` needs a trailing empty campaign to bound
  it, or the engine raises. The trailing campaign's date must still be inside
  the weather series, so the crop end date needs at least one spare day before
  the last row.
- Total water applied is the `TOTIRR` state, read with
  `model.get_variable("TOTIRR")`; it is not in the default output rows.
- For the water-limited runs, use `WAV=20` with `SMLIM=0.30` rather than the
  `WAV=100` the potential run uses. `WAV` is initial profile water in cm and
  100 is the top of its allowed range, which is harmless under potential
  production because the soil is ignored, and absurd under a water balance.
  Trigger irrigation at `SM` falling through 0.25, 2.5 cm at 0.90 efficiency:
  `DummySoilDataProvider` has `SMFCF` 0.30 and `SMW` 0.10, so 0.25 is about 75
  percent of available water, a typical centre-pivot trigger.

### D5. The soil is generic, and the plan says so

All of this runs on `DummySoilDataProvider`, not a real soil, so the numbers
indicate direction and rough magnitude only. The check must not present them as
calibrated, and the README wording must not either.

## Acceptance-criteria traceability

| ID | Acceptance criterion | Implementation | Verification | Status |
|---|---|---|---|---|
| AC-1 | README names the production level and what it assumes away | `crop-weather/README.md`: new section "The validation yield is potential production" after the provider contract, plus the Validation section | both sections name potential production and what it assumes away, with the measured three-level table and the generic-soil caveat | pass |
| AC-2 | Modelfile carries it in `not_for`; validator still clean; `validity_domain` under 600 | `crop-weather/Modelfile.toml`, `not_for` only (D1) | validator run from `/Users/john/repos/modelhome`: `OK`, no warnings, exit 0; `not_for` 503 chars, `validity_domain` untouched at 551 | pass |
| AC-3 | `CLAUDE.md` verified-results entry states the production level | `CLAUDE.md`, the 2026-09-19 verified-results bullet and the design-notes list | the 2026-09-19 bullet now reads "potential production ... not a yield forecast"; new 2026-09-20 entry and a design note added | pass |
| AC-4 | The check no longer asserts only the 5000-25000 band, and the replacement can fail | `crop-weather/check_weather.py`, `check_pcse` | all three pass on the default run; with `IRRIGATION_AMOUNT_CM` at 0.01 the reconstruction check fails (5,947 vs 10,242) and the script exits 1; restored, exit 0 | pass |
| AC-5 | The full check passes on a real default run and the count is recorded | `CLAUDE.md` verified-results | fresh ten-region run: 2,780 rows, exit 0; check 128/128, exit 0; recorded in CLAUDE.md. Sample input also checked: 107/107, exit 0 | pass |

## Verification

| Command | Purpose | Baseline result | Final result |
|---|---|---|---|
| `python3 runner.py <input> <summary> > <daily>` with `{}` as input | the bundle still runs and the output parses | 2,780 rows, 210 forecast, 18.5 s, exit 0 | 2,780 rows, 210 forecast, exit 0 -- unchanged; `runner.py` was not modified |
| `uv run --no-project --python 3.12 --with pcse==6.0.13 --with numpy python check_weather.py <daily>` | the whole check, before and after | 123/123 passed; ia TWSO 10,925.59, LAImax 4.2177 | 128/128 passed, exit 0; sd potential 10,242 / rainfed 5,942 / irrigated 10,242, 38.2 cm applied. Sample input: 107/107, exit 0 |
| `python3 -c` character-count assertion on `validity_domain` and `not_for` | AC-2's 600-character cap | `validity_domain` 551, `not_for` 309 | `validity_domain` 551 (untouched), `not_for` 503 -- both inside the cap |
| `uv run python -m orchestration.modelfile validate <path>/crop-weather/Modelfile.toml` | Modelfile still valid with no new annotation warnings; run from the `modelhome` repo, which is outside this one | not run before the change | `OK: .../crop-weather/Modelfile.toml`, no warnings, exit 0. The checkout was present at `/Users/john/repos/modelhome` |

The negative test for AC-4, performed: `IRRIGATION_AMOUNT_CM` temporarily set
to 0.01 so irrigation is still applied but cannot reconstruct potential. The
reconstruction check failed (irrigated 5,947 against potential 10,242), the run
reported 127/128 and exited 1. Restored to 2.5, it is 128/128 and exit 0. The
band this replaces accepted both cases.

Run the baseline before touching anything, so the before and after counts are
comparable.

## Implementation steps

1. Branch `feat/0002-declare-production-level` from the current commit. Carry
   the untracked brief across; it belongs in this pull request.
2. Record the baseline: run the bundle for the default ten regions and run
   `check_weather.py` on the result. Note the count and the WOFOST line.
3. `check_weather.py`: add a helper that picks the driest region (D3) and one
   that builds the agromanagement for a given production level, including the
   irrigation `StateEvents` and the trailing empty campaign (D4). Keep
   `build_provider` as it is.
4. `check_weather.py`: in `check_pcse`, run potential, rainfed and irrigated on
   the chosen region; print one line each with TWSO, LAImax and water applied;
   add the three assertions from D2; relabel the existing band as a smoke test.
   Add the comment recording why potential production is the right default for
   a weather bundle.
5. Update the module docstring, which currently says the yield "has to be
   credible" -- that is the assertion being replaced.
6. `crop-weather/README.md`: name the production level where the WOFOST run is
   described, and in the Validation section say what the three runs prove.
   Include the measured table from D2 with the generic-soil caveat from D5.
7. `crop-weather/Modelfile.toml`: append the clause to `not_for` (D1). Check
   both field lengths.
8. `CLAUDE.md`: amend the verified-results bullet and add a design note. Update
   the check count once step 9 has produced it.
9. Re-run the bundle and the check on the default ten regions; fill in the
   final results in the Verification table; perform and record the negative
   test.
10. Commit, push, open the pull request. Stop there.

## Files likely to change

- `crop-weather/check_weather.py` -- the substantive change
- `crop-weather/README.md`
- `crop-weather/Modelfile.toml` -- one field
- `CLAUDE.md`
- `docs/plans/0002-declare-production-level.md` -- statuses and results
- `docs/features/0002-declare-production-level.md` -- committed, not edited

## Risks and follow-ups

- The reconstruction assertion at 1 percent was exact in three regions on one
  season. If a region is found where irrigation overshoots or undershoots
  potential by more, widen the tolerance and record why rather than deleting
  the assertion.
- Two more WOFOST runs per check. The current check takes about 20 seconds
  including the fetch; this should stay well inside a minute.
- The Modelfile validator lives in the `modelhome` repo, so AC-2's validation
  step depends on that checkout being available. If it is not, say so in the
  pull request rather than claiming the check passed.
- Follow-up, already agreed: the region-stratification coverage question --
  whether NASS publishes county-level `CORN, GRAIN, IRRIGATED - PRODUCTION`
  widely enough, given disclosure withholding, to support irrigated and
  rainfed strata in `regions.csv`.
- Follow-up: the maize variety and season length, which is probably a larger
  error source in Iowa than water is.

## Deviations from this plan

- **D3's region choice landed on South Dakota, not Kansas.** The plan expected
  Kansas to be the driest region in a ten-region run; on the 2026 series it is
  South Dakota, at 33.6 cm of in-season rain. Picking by rainfall rather than
  hard-coding a state is what the plan specified, so this is the mechanism
  working, not a departure -- but the state named in the documentation changed
  accordingly.
- **The crop runs moved off the first region in the output.** The container
  round-trip checks still use the first region, as before; only the three crop
  runs use the driest one. So the WOFOST line in the check output now names a
  different region than it did before this change, which is why the earlier
  2026-09-19 verified-results entry in `CLAUDE.md` was left describing Iowa and
  a new entry was added rather than the old one being rewritten.
- **`end` moved one day earlier** (`last_day - 1` rather than `last_day`) for
  all three runs, so the irrigated run's mandatory trailing campaign always has
  a day inside the weather series. Applying it to all three keeps the calendars
  identical and therefore comparable. It only bites when the last row falls
  before 30 September, where the 120-day guard usually skips the check anyway.
- **Follow-ups were added to `CLAUDE.md`'s task list** for region strata and
  the maize variety. Both are named as out of scope in the brief; recording
  them in the repo's own follow-up list is bookkeeping, not scope.

## Limitation found during verification

On the committed sample input (IA/IL/NE, 2026-09-15) the driest region is
Illinois, where rainfed production equals potential exactly: water did not bind
there at all that season. The ordering assertion therefore has no contrast to
detect on that input, and the reconstruction assertion passes trivially because
irrigation adds water the crop did not need. Both assertions still have teeth
on the ten-region default run, where South Dakota's rainfed yield is 42 percent
below potential, and the "irrigation was applied" assertion has teeth on both.

This is a real limit on what the sample input proves, not a failure: it is the
same reason the plan rejected a required margin in favour of a tolerance. It is
recorded here rather than fixed, because narrowing it would mean changing the
check's design outside the approved plan. The obvious options if it matters --
require a minimum potential-minus-rainfed gap when in-season rain is below some
threshold, or run the assertions on more than one region -- belong in a follow-up.
