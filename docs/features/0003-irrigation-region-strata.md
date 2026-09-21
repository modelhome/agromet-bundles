# Irrigation region strata

## Outcome

Each state's representative point stops averaging over irrigated and rainfed
corn as if they were the same crop. Today `regions.csv` gives every state one
production-weighted point over all of its corn for grain. In Nebraska that
point is weighted by acres that are 52.7 percent irrigated, and in Kansas 25.4
percent, so the point drifts towards places that are dry but productive
*because* of water this node cannot see. The weather it fetches is therefore
drier than the rainfed crop experiences, while the production it is weighted by
was bought with irrigation.

When this is done, the states where that distortion is material carry two
points -- an irrigated stratum and a rainfed one -- each with its own
production weight, and the states where it is not keep the single point they
have. A downstream crop model can then run the right water regime on the right
weather and weight the two results back together, instead of being handed one
point that is neither.

## Scope

### In scope

- Extend `build_regions.py` to read the irrigated series alongside the total
  and emit strata for the states that meet a stated threshold.
- Rebuild `regions.csv` with the strata, their coordinates, their weights and
  the method and source text for each row, as the existing rows carry.
- Decide and document how a stratum's production weight is derived, given that
  NASS publishes no irrigated production anywhere (see constraints).
- Update `crop-weather/README.md` and `CLAUDE.md` where the region set and the
  region-identity contract are described.

### Out of scope

- Any use of the strata. Running a water-limited model on the rainfed stratum
  and an irrigated one on the other, and weighting the results, is
  `wofost-bundles/corn-yield`'s job.
- Crop-reporting-district granularity, which is a different and larger change
  to the same file.
- Real soils. The strata describe where the corn is, not what it grows in.
- Any change to the output schema beyond the region rows that flow through it.

## Acceptance criteria

- **AC-1** — `build_regions.py` derives the strata from the NASS export and the
  Gazetteer with no new data source and no API key, and reports per state how
  much irrigated area its published counties account for.
- **AC-2** — `regions.csv` carries a stratum column (or equivalent) and a
  weight for every row, and every row still carries the method and source text
  that the current rows do. A state that is not split is visibly not split.
- **AC-3** — The threshold that decides whether a state is split is stated in
  the file's method text and in the README, with the measured irrigated share
  that puts each state on its side of it.
- **AC-4** — The runner produces a valid output over the new region set, and
  `check_weather.py` passes, including its region-table checks.
- **AC-5** — The README and `CLAUDE.md` say what a downstream consumer must do
  differently, including what happens to a join on `region_key`.

## Constraints and dependencies

Measured against the USDA NASS 2022 Census of Agriculture bulk export on
2026-09-21, the same file `build_regions.py` already caches:

- **Coverage is good where it matters.** Nationally only 1,074 of 1,710
  county-level `CORN, GRAIN, IRRIGATED - ACRES HARVESTED` records are
  published, 63 percent, but withholding falls on small counties. By area, the
  published counties account for 100.0 percent of Nebraska's irrigated corn
  acres, 99.6 percent of Kansas's, and 94 to 98 percent in seven more states.
  Ohio is the worst at 83.6 percent, on 15,124 acres.
- **Irrigated share of harvested corn acres:** NE 52.7, KS 25.4, MO 9.4, IN
  6.3, WI 4.7, MN 3.9, SD 3.5, IL 3.3, IA 1.2, OH 0.5 percent.
- **There is no irrigated production series at any aggregation level.** Not
  county, not state, not national. `regions.csv` weights by production today,
  so a stratum weight has to come from somewhere else: county irrigated acres
  are published, and state-level irrigated yield is published as `CORN, GRAIN,
  IRRIGATED, ENTIRE CROP - YIELD, MEASURED IN BU / ACRE`, so acres times a
  state yield is one route. It mixes county and state resolution and needs
  saying out loud wherever the weight appears.
- **There is no non-irrigated county series.** Rainfed acres are total minus
  irrigated, which is undefined in a county whose irrigated figure is withheld.
  A withheld figure usually means few irrigated operations, so treating it as
  zero biases the rainfed stratum slightly large; the bound on that bias is the
  placeable percentage above.
- **Irrigation status is confounded with soil quality, and the sign of the
  yield gap flips.** Operations irrigating their entire corn crop out-yield
  those irrigating none by 105 percent in Kansas and 55 percent in Nebraska,
  but yield 8 percent *less* in Iowa and 20 percent less in Ohio, where
  irrigation sits on marginal ground. The strata must not be documented as
  "the better half and the worse half".
- **This changes the default region set, which is a contract.** `regions.csv`
  is copied into the image and is the default when the input names no regions
  (`runner.py:51`, `runner.py:387`). More rows means more `region_key` values,
  more rows in the output and two more Open-Meteo calls per added region.
  `CLAUDE.md`'s region-identity rule says downstream nodes join on the key and
  never redefine it, so whether `ne` becomes `ne_irrigated` and `ne_rainfed`,
  or those are added beside a retained `ne`, is a decision with consequences
  for every consumer and for anything that sums the rows.

## General guidance

- Before you write the plan, ask any questions you need to in order to best implement the brief
