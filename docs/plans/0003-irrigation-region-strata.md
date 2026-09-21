# Plan: Irrigation region strata

Source brief: docs/features/0003-irrigation-region-strata.md
Status: implemented
Planned against commit: eb87de3 (branch claude/plan-irrigation-region-strata-7d995e; tree is main 8c79072 plus the brief)
Base commit: eb87de3 (branch feat/0003-irrigation-region-strata)

## Outcome

`regions.csv` stops averaging irrigated and rainfed corn into one point in the
two states where that averaging is material. Nebraska's single point is
weighted by acres 52.7 percent irrigated and Kansas's 25.4 percent, so each
drifts towards places that are dry but productive *because* of water this node
cannot see. After this change NE and KS each carry two rows -- an irrigated
stratum and a rainfed one, each with its own coordinates and production weight
-- and the other eight states carry the single row they have today, visibly
marked as unsplit. Every row carries a weight, and the weights sum to 1.

The split is arithmetic on the region table only. No crop model runs here, no
output column is added, and using the strata (running a water-limited model on
the rainfed one and an irrigated model on the other, then weighting the
results) remains `wofost-bundles/corn-yield`'s job.

## Scope

### In scope

- Extend `crop-weather/build_regions.py` to read `CORN, GRAIN, IRRIGATED -
  ACRES HARVESTED` and the state-level stratum yields alongside the total
  production series, and emit strata for states over a stated threshold.
- Rebuild `crop-weather/regions.csv` with `stratum` and `weight` columns and
  per-row method and source text.
- Carry `stratum` and `weight` through `runner.py`'s region loader into the
  output metadata, without changing the daily table's columns.
- Update `crop-weather/check_weather.py`'s region-table checks.
- Update `crop-weather/README.md`, `crop-weather/Modelfile.toml` prose, and
  `CLAUDE.md`.

### Out of scope

Carried from the brief: any use of the strata; crop-reporting-district
granularity; real soils; and any change to the output schema beyond the region
rows that flow through it. Also unchanged: the maize variety, NASA POWER, and
the ten states themselves -- this change splits two of them, it does not add or
drop any.

## Assumptions and decisions

### D1. A split state's `region_key` is replaced, not retained (answered)

Asked whether `ne` becomes `ne_irrigated` and `ne_rainfed`, or whether those
are added beside a retained `ne`. The answer was **replace**. So:

- `ne` and `ks` disappear; `ne_irrigated`, `ne_rainfed`, `ks_irrigated` and
  `ks_rainfed` take their place.
- The eight unsplit states keep their keys exactly (`ia` stays `ia`), so most
  of the join surface is untouched.
- Weights sum to 1.0 and every acre is counted once.

The rejected alternative keeps `ne` working but counts Nebraska's production
twice and fetches its weather three times; anything that sums rows or weights
without filtering on `stratum` silently overcounts split states. That is the
error the weight column exists to prevent, so it should not be designed in.

`CLAUDE.md`'s region-identity rule says downstream nodes join on `region_key`
and never redefine it. This change redefines two keys, which is exactly the
kind of break the rule exists to prevent -- but the rule binds *downstream*
nodes, and this bundle is the upstream owner of the key. The break is free
today because `wofost-bundles/corn-yield` and `ag-commodity-bundles/corn-price`
do not exist yet; it would not be free later. AC-5 makes the break explicit.

### D2. Only NE and KS are split, at a 20 percent threshold (answered)

Asked whether to split only the material states or all ten. The answer was
**NE and KS only**, giving twelve regions.

The threshold is stated as: **a state is split when irrigation covers 20
percent or more of its harvested corn acres.** Measured shares (verified
against the cached 2022 Census export while planning, reproducing the brief's
figures exactly):

| State | Irrigated share of acres | Split |
|---|---|---|
| NE | 52.7 % | yes |
| KS | 25.4 % | yes |
| MO | 9.4 % | no |
| IN | 6.3 % | no |
| WI | 4.7 % | no |
| MN | 3.9 % | no |
| SD | 3.5 % | no |
| IL | 3.3 % | no |
| IA | 1.2 % | no |
| OH | 0.5 % | no |

20 percent sits in a wide empty gap between KS at 25.4 and MO at 9.4, so the
threshold is not finely balanced against any state.

Two further measurements support stopping there rather than at MO:

- **Withholding bias is near nil where we split.** Counties whose irrigated
  figure NASS withholds hold **0.0 percent** of Nebraska's published production
  and **4.9 percent** of Kansas's, against 33.8 percent in Missouri and 47.5
  percent in Iowa. Treating a withheld irrigated figure as zero (D7) is
  therefore almost costless in NE and KS and would be a real distortion
  elsewhere.
- **Missouri's irrigated corn is bimodal, so its stratum mean is a place with
  no corn.** 58 percent sits in the Bootheel (Scott, Stoddard, Mississippi and
  New Madrid counties, around 36.6 to 37.0 N) and 9 percent in the northwest
  river valley (Holt County, 40.1 N, 390 km away). The production-weighted mean
  of those clusters lands at **37.60, -91.08**, in the Ozarks. The existing
  hull guard in `weighted_centroid` would not catch it, because that point is
  inside the county bounding box. Nebraska's irrigated stratum, by contrast, is
  a well-spread central Platte valley cluster with no county over 4 percent.

### D3. A stratum's weight comes from apportioning published county production

NASS publishes **no irrigated production series at any aggregation level** --
verified while planning by enumerating every `CORN, GRAIN*` series in the
export; county, state and national all lack it. So a stratum weight has to be
reconstructed. The method:

For each county `c` in a split state, with published corn-for-grain production
`P_c` (the weight `regions.csv` already uses):

```
a_irr   = county "CORN, GRAIN, IRRIGATED - ACRES HARVESTED"   (0 if withheld)
a_rain  = county "CORN, GRAIN - ACRES HARVESTED" - a_irr
Y_irr   = state "CORN, GRAIN, IRRIGATED, ENTIRE CROP - YIELD, MEASURED IN BU / ACRE"
Y_rain  = state "CORN, GRAIN, IRRIGATED, NONE OF CROP - YIELD, MEASURED IN BU / ACRE"

share_irr(c) = a_irr * Y_irr / (a_irr * Y_irr + a_rain * Y_rain)
P_irr(c)     = P_c * share_irr(c)
P_rain(c)    = P_c * (1 - share_irr(c))
```

Each stratum's point is then the production-weighted centroid of its
`P_irr` (or `P_rain`) values, using the same `weighted_centroid` and the same
Gazetteer internal points as today.

Three things this buys, and one it costs:

- **It apportions rather than estimates.** County production stays the ground
  truth and is only divided; the strata sum exactly to the state's current
  weight, so the region set's total production is unchanged.
- **Only the yield *ratio* matters**, not the yield levels, because the
  published production is being split proportionally. A constant bias in both
  yields cancels. The ratios are KS 173.1 / 84.4 = **2.05** and NE 197.8 /
  127.3 = **1.55**.
- **It is checkable.** Recombining the two strata by weight must reproduce the
  current single point (D9).
- **It mixes county and state resolution**, since county acres are multiplied
  by a state yield. Every county in a state shares one irrigated-to-rainfed
  yield ratio, so within-state variation in that ratio is lost. This must be
  said wherever the weight appears: in the `method` column, in the README and
  in `build_regions.py`'s docstring.

One further caveat to document: `ENTIRE CROP` and `NONE OF CROP` are
*operation-level* classes -- farms that irrigate all of their corn and farms
that irrigate none. Operations that irrigate part of their corn are in neither
yield series, though their acres are still apportioned using the ratio from the
two that are.

### D4. `weight` is the row's share of the ten-state published production

A fraction in [0, 1], rounded to 4 decimal places, summing to 1.0 across the
file. Expected values, from the planning-time prototype:

| region_key | stratum | weight | lat, lon |
|---|---|---|---|
| ia | all | 0.2220 | 42.2639, -93.5370 |
| il | all | 0.1893 | 40.3194, -89.2176 |
| mn | all | 0.1307 | 44.7087, -94.6365 |
| ne_irrigated | irrigated | 0.0808 | 41.1699, -98.6459 |
| ne_rainfed | rainfed | 0.0462 | 41.1585, -97.7774 |
| in | all | 0.0918 | 40.1508, -86.3355 |
| sd | all | 0.0540 | 44.5025, -98.1339 |
| oh | all | 0.0538 | 40.4759, -83.4936 |
| wi | all | 0.0471 | 43.8520, -89.8864 |
| ks_irrigated | irrigated | 0.0173 | 38.1752, -99.7654 |
| ks_rainfed | rainfed | 0.0257 | 38.9711, -97.6086 |
| mo | all | 0.0413 | 39.1575, -92.8634 |

These are predictions from a prototype run outside the repo, not results. `run`
must reproduce the coordinates to 4 decimal places or explain the difference;
the eight unsplit states' coordinates must come out **byte-identical** to the
committed file, since their weights are unchanged. Elevations are fetched from
Open-Meteo at build time and are not predicted here.

Note that Nebraska's irrigated stratum is 63.6 percent of its production while
being 52.7 percent of its acres, and Kansas's is 40.2 percent of production on
25.4 percent of acres. That gap is the yield ratio, and it is the reason a
weight had to be reconstructed rather than read off acres.

### D5. `regions.csv` gains `stratum` and `weight`; `state` is unchanged

Column order becomes `region_key, state, stratum, weight, lat, lon, elev_m,
method, source`. `state` stays the two-letter postal code (`NE` on both
Nebraska rows), so grouping by state still works. `stratum` is one of
`irrigated`, `rainfed`, or `all` for an unsplit state -- which is how AC-2's
"a state that is not split is visibly not split" is satisfied in data as well
as in prose.

### D6. The strata reach the output through metadata, not new table columns

`load_regions()` carries `stratum` and `weight` into each region dict, and they
appear in `metadata["regions"]` (and therefore in both outputs, since the
summary embeds the same metadata). `TABLE_COLUMNS` is **not** changed: the
daily table keeps its current columns, and a consumer that wants a row's weight
joins `metadata.regions` on `region_key`.

This follows the brief's out-of-scope line ("any change to the output schema
beyond the region rows that flow through it") and the bundle's compactness
convention -- the default run is ~3,300 rows after this change, and `stratum`
and `weight` are constant per region, so putting them on every row would add
bulk without adding information.

`_parse_region` accepts `stratum` and `weight` as optional fields for
user-supplied regions, defaulting to `"all"` and `null`, consistent with
`required = []` everywhere else.

### D7. Rainfed acres are total minus irrigated, with withheld treated as zero

There is no non-irrigated county series, so rainfed acres are a subtraction.
Where NASS withholds a county's irrigated figure it is treated as zero, which
biases the rainfed stratum slightly large; the bound on that bias is the
placeable share, and in the two split states it is 0.0 percent (NE) and 4.9
percent (KS) of production.

Two degenerate cases were measured across all ten states and **do not occur**:
no county has published production without published total acres, and no
county's irrigated acres exceed its total acres. `build_regions.py` should
therefore *fail loudly* on either rather than branching around them, so a
future Census vintage that breaks the assumption is caught at build time.

### D8. Diagnostics are reported for every state, including unsplit ones

`build_regions.py` logs, per state: the irrigated share of acres, the share of
state irrigated acres its published counties account for (AC-1), the share of
production held by counties with a withheld irrigated figure, and -- for every
state, not just the split ones -- the distance between the two stratum points
it *would* produce. That keeps the Missouri evidence in D2 reproducible from
the script itself without adding a gate that would be dead code for the chosen
region set.

### D9. The decisive region check is recombination

`check_weather.py` gains a check that weighting the two stratum points by their
weights reproduces the pre-split single point:

```
NE  (0.0808 * 41.1699 + 0.0462 * 41.1585) / 0.1270  ->  41.1658   committed 41.1658
    (0.0808 * -98.6459 + 0.0462 * -97.7774) / 0.1270 -> -98.3300   committed -98.3301
KS  ->  38.6509, -98.4763                                committed 38.6509, -98.4763
```

**The tolerance must be 1e-3 degrees, not exact equality.** Verified while
planning: recombining from the 4-decimal-place weights and coordinates that
`regions.csv` actually stores reproduces three of the four values exactly and
NE's longitude to 1e-4, because the stored weights are rounded. 1e-3 degrees is
about 100 m, far tighter than the 73 km and 206 km the strata are apart, so the
check still fails loudly on any real error. Asserting exact equality would make
the check fail on arithmetic that is correct.

The pre-split points are hard-coded as constants with a comment citing commit
`8c79072`, the last commit before the split. This is the assertion that proves
the county join and the apportionment did not quietly move the corn -- the
mathematical guarantee is that a weighted mean of partitioned weights equals
the mean over the whole, so any deviation is a bug, not a modelling choice. It
was verified to hold in the planning prototype for both states and for MO and
IA as controls.

The existing checks change as follows: 12 rows not 10; `region_key is the
lower-cased state code` is replaced by a rule that allows `<state>_<stratum>`
for split states; `EXPECTED_STATES` is unchanged (still ten states); new checks
that `stratum` is one of the three allowed values, that weights sum to 1.0
within rounding, and that a state appears either once with `all` or twice with
`irrigated` and `rainfed` and never in any other combination.

### D10. The Modelfile's prose is corrected, which the brief does not list

The brief's in-scope list names the README and `CLAUDE.md` but not the
Modelfile, which nonetheless says "the ten biggest US corn states", "the ten
built-in corn states", "One point per state" and "Ten regions, two HTTP calls
each" in `description`, `validity_domain`, the input descriptions and the
`[resources]` comment. Leaving those would make the published annotations
factually wrong about the default region set, so they are corrected as part of
this change. No schema property, type or `required` list is touched, so this is
annotation prose, not the output-schema change the brief excludes.

Budget: `validity_domain` is at 551 of the platform's 600-character cap and
`not_for` at 503, so `validity_domain` has 49 characters of headroom. The
sentence "One point per state, the production-weighted centroid of its county
corn production" must be rewritten in place rather than extended, and the
Modelfile must be re-validated (see Verification).

### D11. The driest region in the check is expected to change

`check_weather.py` picks whichever region in the output saw the least rain
between 1 May and 30 September and runs the three WOFOST production levels
there. On the current ten-region set that is South Dakota. `ks_irrigated` sits
in the southwest Kansas High Plains, which is materially drier, so the check's
crop runs will very likely move there and report different numbers.

That is expected and makes the assertions sharper, not weaker -- the rainfed
shortfall against potential should be larger than South Dakota's 42 percent.
`run` must record the new region and figures as a **new** verified-results
entry in `CLAUDE.md`, leaving the 2026-09-20 entry describing South Dakota
intact, exactly as brief 0002 left the 2026-09-19 Iowa entry in place.

## Acceptance-criteria traceability

| ID | Acceptance criterion | Implementation | Verification | Status |
|---|---|---|---|---|
| AC-1 | `build_regions.py` derives the strata from the NASS export and the Gazetteer with no new data source and no API key, and reports per state how much irrigated area its published counties account for | `crop-weather/build_regions.py`: `read_nass()` reads all five series in the existing single pass; `apportion()`, `strata_for()`, `log_diagnostics()`. No new URL constant, no key handling | Rebuilt from the cached export in 26 s. The diagnostics table reports irrigated share, area coverage, withheld-production share and strata separation for **all ten states**, including the ones it does not split: NE coverage 100.0 %, KS 99.6 %, OH 83.6 % | **pass** |
| AC-2 | `regions.csv` carries a stratum column (or equivalent) and a weight for every row, and every row still carries the method and source text; a state that is not split is visibly not split | `crop-weather/regions.csv`: 12 rows, columns `region_key, state, stratum, weight, lat, lon, elev_m, method, source`; unsplit rows carry `stratum = all` and a method reading "single point, not split into strata" | `check_weather.py` region checks: 12 regions, unique keys, stratum vocabulary, each state once as `all` or twice as `irrigated`+`rainfed`, weights sum to 1.0 (measured 1.0000), every method and source non-empty | **pass** |
| AC-3 | The threshold is stated in the file's method text and in the README, with the measured irrigated share that puts each state on its side of it | `method_text()` writes the 20 percent threshold and that state's own measured share into every row; `README.md` "Why two states are split" carries the ten-state table; `build_regions.py`'s `SPLIT_THRESHOLD` comment carries it too | Automated check "every region's method states the split threshold and its own share" passes on all 12 rows; README table read back | **pass** |
| AC-4 | The runner produces a valid output over the new region set, and `check_weather.py` passes, including its region-table checks | `runner.py` `load_regions()` and `_parse_region()` carry `stratum` and `weight`; `TABLE_COLUMNS` unchanged | Full run: 3,336 rows, 240 forecast, 12 regions, 18 s, weights sum to 1.0 in `metadata.regions`. `check_weather.py`: **147/147 pass** (128 at baseline), including the recombination check and all three WOFOST production levels. Sample input: 819 rows. Docker: rows identical to local, both layouts | **pass** |
| AC-5 | The README and `CLAUDE.md` say what a downstream consumer must do differently, including what happens to a join on `region_key` | `README.md` "What a downstream consumer must do" with a before/after key table; `CLAUDE.md` region-identity rule gains a restratification paragraph, plus design notes; `Modelfile.toml` `region_key` description names `ne_irrigated` | Read back: both state that `ne` and `ks` no longer exist, name the four replacements, and say to combine strata by `weight` or group by `state` | **pass** |

## Verification

Run from `crop-weather/` unless stated otherwise.

| Command | Purpose | Baseline result | Final result |
|---|---|---|---|
| `uv run --no-project --python 3.12 python build_regions.py` | Rebuilds `regions.csv`; AC-1, AC-2 | **pass** -- reproduced the committed 10-row file byte-identically (`git diff --exit-code` clean), 25 s | **pass** -- 12 regions from 10 states, weights sum to 1.0000, 26 s. All four new coordinates match the plan's D4 predictions exactly; the eight unsplit states are byte-identical to baseline |
| `uv run --no-project --python 3.12 python runner.py /tmp/base_input.json ../run/crop_weather_summary.output.json > ../run/crop_weather_daily.output.json` with input `{"forecast_days": 14}` | Full default run over the built-in region set; AC-4 | **pass** -- 2,780 rows, 200 forecast, 10 regions, 16 s | **pass** -- 3,336 rows, 240 forecast, 12 regions, 18 s, 1.1 MB |
| `uv run --no-project --python 3.12 --with pcse==6.0.13 --with numpy python check_weather.py ../run/crop_weather_daily.output.json` | The decisive check; AC-2, AC-4 | **128/128 pass**, driest region `sd`, potential 10,242 / rainfed 5,942 / irrigated 10,242 kg/ha, 38.2 cm applied -- identical to `CLAUDE.md`'s 2026-09-20 entry | **147/147 pass**. Driest region moved to `ks_irrigated` as D11 predicted (24.5 cm): potential 8,575 / rainfed 3,071 / irrigated 8,575 kg/ha, 63.0 cm applied. 19 new region-table checks |
| `uv run --no-project --python 3.12 python runner.py sample_input.json /tmp/s.json > /tmp/d.json` | The committed sample still runs; AC-4 | **pass** -- 819 rows, 51 forecast, region `ne` | **pass** -- 819 rows, 45 forecast, region `ne_irrigated`, stratum carried through, weight absent as designed |
| `uv run python -m orchestration.modelfile validate <modelhome>/crop-weather/Modelfile.toml` | Modelfile validates, both capped fields under 600 chars; D10 | **OK**, no annotation warnings (`validity_domain` 551, `not_for` 503) | **OK**, no annotation warnings. `description` 569, `validity_domain` 592, `not_for` 503 -- all under 600 |
| `docker build` + bare `docker run` + the Modelfile's mounted layout | The image carries the new `regions.csv` and matches the local run | not run at baseline (unchanged by this work) | **pass** -- bare `CMD` and the Modelfile's mounted layout both produce rows **identical** to the local run, metadata identical apart from `retrieved_at`; the image carries all 12 regions |

### Pre-existing baseline failure, unrelated to this brief

The literal `{}` default input **fails at baseline**, before any change here:

```
error: ia: Open-Meteo served neither ERA5 nor a forecast for 1 day(s): 2026-10-06.
```

Diagnosed rather than assumed. At 03:07 UTC on 2026-09-21, Open-Meteo's
forecast endpoint returns a 16-day `time` grid through 2026-10-06 but pads that
last day with `null` for all six daily variables. `daily_payload` correctly
drops a day short of any variable, so the run legitimately fails. The default
asks for exactly 15 days past today, which is the 16th slot, so early in the
UTC day the default input has no margin.

This is a real latent fragility in the bundle and it is **out of scope for
brief 0003**, which changes the region table and touches nothing in the fetch
path. It is recorded here, reported, and left for a follow-up rather than
fixed inside this change.

Consequence for verification: baseline and final runs both use
`{"forecast_days": 14}` instead of `{}`, so they are directly comparable to
each other and exercise the whole region set. Every other resolved default is
untouched. The `{}` figures in `CLAUDE.md` are therefore not re-measured by
this change and its 2026-09-20 entry is left as it stands.

`check_weather.py` is the narrowest existing check that covers this feature;
there is no separate unit-test harness in this repo, and none should be
invented for this change.

**Note on the sample input.** `sample_input.json` is 2026-09-15 with Iowa,
Illinois and Nebraska, and it **does** name `ne`, with the blended coordinates
41.1658, -98.3301. It supplies those coordinates inline, so the run will not
fail -- `_parse_region` accepts any key -- which is precisely the problem: left
alone it would silently keep demonstrating a retired key and the pre-split
point this change exists to replace. Replace that entry with `ne_irrigated`
(41.1699, -98.6459) so the committed sample shows the new shape; the elevation
must come from the rebuilt `regions.csv`, not be carried over.

## Implementation steps

1. **Read the three NASS series in one pass.** Extend
   `county_corn_production()` (or add a sibling that shares the single
   `gzip` scan, since the file is 295 MB and a second pass costs ~25 s) to
   collect, at `DOMAIN_DESC == "TOTAL"`: county `CORN, GRAIN - PRODUCTION,
   MEASURED IN BU` (as today), county `CORN, GRAIN - ACRES HARVESTED`, county
   `CORN, GRAIN, IRRIGATED - ACRES HARVESTED`, state `CORN, GRAIN, IRRIGATED -
   ACRES HARVESTED`, and the two state yield series in D3. Keep `SUPPRESSED`
   handling as it is.
2. **Add the guards from D7**: raise `SystemExit` if a county has published
   production but no published total acres, or if irrigated acres exceed total
   acres. Both are measured to be impossible on the 2022 Census; the guards
   exist for the next vintage.
3. **Compute the split decision** per state from the state-level irrigated and
   total acres: `share = irrigated / total`, split when `share >= 0.20`. Name
   the threshold as a module constant with a comment carrying D2's table.
4. **Apportion and build the strata** per D3, reusing `weighted_centroid`
   unchanged on each stratum's weight map so the existing hull guard applies
   per stratum. Fetch elevation per stratum point, as today.
5. **Compute weights** per D4 as each row's share of the ten-state published
   production total, rounded to 4 dp. Assert in the script that they sum to
   1.0 within rounding tolerance before writing.
6. **Write `regions.csv`** with D5's columns, composing `method` per row so it
   carries the stratum, the measured irrigated share, the threshold, the
   county count, the withheld count, and -- for split rows -- the county-acres
   times state-yield derivation and its resolution mixing. Extend `source` to
   cite the irrigated-acres and stratum-yield series by name.
7. **Add D8's diagnostics** to the script's stderr logging.
8. **Run the script** and inspect the result against D4's predicted table.
9. **Thread `stratum` and `weight` through `runner.py`** per D6: `load_regions`
   reads the two new columns; `_parse_region` accepts them as optional;
   `TABLE_COLUMNS` untouched. Confirm `metadata["regions"]` carries them.
10. **Update `check_weather.py`** per D9, including the hard-coded pre-split
    points and the recombination check.
11. **Update `sample_input.json`**: replace its `ne` entry with
    `ne_irrigated`, taking the coordinates and elevation from the rebuilt
    `regions.csv`. It names `ne` today.
12. **Run the full default run and `check_weather.py`**, then record the new
    check count and the new driest region per D11.
13. **Update the documentation**: `README.md`'s Regions section (D2's table,
    the threshold, the derivation and its caveats, the key change and what a
    consumer must do), `Modelfile.toml` prose within the character budget
    (D10), and `CLAUDE.md`'s design notes, region-identity rule, bundle file
    listing, verified-results and task list.
14. **Validate the Modelfile** from the `modelhome` repo, and **rebuild and run
    the image** for the standing parity check.

## Files likely to change

| File | Change |
|---|---|
| `crop-weather/build_regions.py` | The substance: three more series, the threshold, the apportionment, the guards, the diagnostics, the two new columns |
| `crop-weather/regions.csv` | Rebuilt: 12 rows, 9 columns |
| `crop-weather/runner.py` | `load_regions` and `_parse_region` only; no schema or column change |
| `crop-weather/check_weather.py` | Region-table checks rewritten per D9 |
| `crop-weather/README.md` | Regions section; the consumer-facing key change |
| `crop-weather/Modelfile.toml` | Prose only, within the 600-character caps |
| `crop-weather/sample_input.json` | Its `ne` entry becomes `ne_irrigated` |
| `CLAUDE.md` | Design notes, region-identity rule, file listing, verified results, task list |

## Deviations from the plan, found during implementation

1. **The Modelfile's `regions` input schema gained `stratum` and `weight`.**
   D6 said `_parse_region` would accept them as optional but did not say the
   schema should document them. Leaving them undeclared would have meant the
   runner accepting input the published schema does not describe. Two optional
   properties added; no `required` list or type changed.

2. **`validity_domain` needed a shorter sentence than planned.** D10 budgeted
   49 characters of headroom. The first rewrite, which named the 20 percent
   threshold, came to 646 characters and was rejected by the cap. The threshold
   lives in the README and in every row's `method`, which is what AC-3 requires,
   so the annotation now says only "split into irrigated and rainfed points in
   NE and KS" (592 characters).

3. **The README's three-production-level table had to be re-measured, not
   relabelled.** It carried rows for `NE` and `KS`, which no longer exist. It
   now reports `ia`, both NE strata and both KS strata, measured with
   `check_weather.py`'s own `run_maize`. Iowa reproduced its previous numbers
   exactly (10,926 / 10,799 / 18.0 cm), which confirms only NE and KS moved.
   This was implied by AC-5 but not listed as a file to change.

4. **`log_diagnostics` tolerates states it cannot stratify.** The plan had it
   report strata separation for every state. A state with no irrigated corn, or
   without both stratum yields, has no second point; it now prints `n/a` rather
   than raising, because a diagnostic should not be able to stop a build that
   would otherwise succeed. Only a state over the threshold is ever built.

5. **The `{}` default fails at baseline for an unrelated, pre-existing
   reason** (see the Verification section). Both baseline and final runs used
   `{"forecast_days": 14}`. This is recorded as a follow-up in `CLAUDE.md`, not
   fixed here.

## Risks and follow-ups

- **The key change is a real break, deliberately taken.** `ne` and `ks` stop
  existing. Nothing in the flow consumes them yet, which is why now is the
  moment; AC-5 is the mitigation and it is documentation, not a technical
  guard. If a consumer appears before this merges, revisit D1.
- **A stratum centroid can still land where no corn grows.** Missouri is the
  measured example (D2) and is excluded by the threshold, but the hull guard
  does not detect the condition and the 20 percent rule only excludes it by
  luck of correlation, not by construction. A dispersion or nearest-corn guard
  is the natural follow-up if the region set ever widens. D8's logging is what
  would surface it.
- **Kansas's irrigated stratum is itself concentrated** in five southwest High
  Plains counties (Stevens, Haskell, Meade, Seward, Gray, about 30 percent of
  the stratum) with a secondary northwest cluster, so its point at 38.18,
  -99.77 sits north and east of the bulk of the irrigated acres. It is inside
  western Kansas cropland, so it is defensible, but it is the weakest of the
  four new points and the README should not oversell it.
- **The yield ratio is a single state-wide number** (D3), so within-state
  variation in the irrigated advantage is lost, and partial irrigators are in
  neither source yield series.
- **The strata are not "the better half and the worse half".** The sign of the
  irrigated yield gap flips by state -- +105 percent in Kansas and +55 in
  Nebraska, but -8 in Iowa and -20 in Ohio, where irrigation sits on marginal
  ground. Both split states happen to have a positive gap, which makes the
  wrong reading easy and the documentation obligation sharper.
- **Cost:** 12 regions means 24 Open-Meteo calls and roughly 3,300 rows against
  today's 2,770, so about 24 s against 19.6 s and about 1.1 MB against 900 KB.
  `expected_runtime = "seconds"` and `[resources]` stay valid.
- **Follow-ups unchanged by this work:** crop-reporting-district granularity
  (which would supersede the whole representative-point approach), the maize
  variety, NASA POWER, wheat and soy parameter sets, and `TEMP` as an explicit
  column.
