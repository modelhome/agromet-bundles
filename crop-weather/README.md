# crop-weather

**US Corn Crop Weather.** The daily weather a corn crop model runs on, for the
ten biggest US corn states as twelve regions: 1 January through today plus a
two-week forecast, in exactly the variables and units PCSE/WOFOST consumes,
with accumulated growing degree days and frost / heat-stress day counts
alongside.

This is node 1 of a climate -> agriculture -> finance flow. Its entire job is
defined by what the crop model downstream needs to eat, so **this bundle's
output schema is the crop model's input schema**.

```bash
cd crop-weather
docker build -t agromet-crop-weather:local .
docker run --rm agromet-crop-weather:local      # needs network access
```

Or run it directly, with no dependencies to install:

```bash
# from the repository root
mkdir -p run && echo '{}' > run/crop_weather_request.json
python3 crop-weather/runner.py run/crop_weather_request.json \
    run/crop_weather_summary.output.json > run/crop_weather_daily.output.json
```

## Input

Every field is optional. An empty object `{}` gives the season so far for the
twelve built-in regions, which is what a daily schedule should send.

| Field | Default | Meaning |
|---|---|---|
| `date` | today (UTC) | The last observed day. Days after it come from the forecast. |
| `season_start` | 1 January of `date`'s year | First day reported, and the day accumulation starts from. |
| `forecast_days` | 15 | Days of forecast after `date`, 0 to 15. |
| `regions` | `regions.csv` | `[{region_key, state, lat, lon, elev_m}]` to use instead of the built-in places. `stratum` and `weight` are optional. |
| `gdd_base_c` | 10 | Temperature below which the crop does not develop. |
| `gdd_cap_c` | 30 | Temperature above which extra heat stops speeding development up. |
| `frost_threshold_c` | 0 | `TMIN` at or below this marks a frost day. |
| `heat_threshold_c` | 32 | `TMAX` at or above this marks a heat-stress day. |

`forecast_days` counts days **after** `date`, and tops out at 15 because
Open-Meteo's own `forecast_days=16` counts today as its first day. Asking for
more fails the run rather than silently returning a short window.

The four crop parameters are inputs, not constants, so the same model serves a
future wheat or soy flow.

## Output

Two JSON documents. Model Home keeps only JSON outputs, so the long-format
table ships as JSON and the CSV beside it exists only for off-platform use.

**`crop_weather_daily`** (stdout) -- `{metadata, columns, rows}`, one row per
region per day, oldest first within each region:

| Column | Unit | |
|---|---|---|
| `region_key`, `state`, `date`, `is_forecast` | | Identity and provenance. `is_forecast` marks a day that came from the forecast rather than the ERA5 archive. |
| `LAT`, `LON`, `ELEV` | degrees, degrees, m | The point the weather was taken from. |
| `TMIN`, `TMAX` | degC | |
| `IRRAD` | J/m2/day | |
| `VAP` | hPa | |
| `WIND` | m/s at 2 m | |
| `RAIN` | cm/day | |
| `gdd_daily`, `gdd_cumulative` | degC d | |
| `frost_day`, `heat_stress_day` | boolean | |
| `frost_days_to_date`, `heat_stress_days_to_date` | days | Running counts from `season_start`. |

**`crop_weather_summary`** -- the same values grouped per region, each with its
whole season, for a chart or a map.

`metadata` on both carries `date`, `season_start`, `forecast_days`,
`retrieved_at`, `data_source`, `endpoints`, the four crop parameters, the GDD
method, the Angstrom coefficients per region, the region table, and the PCSE
convention the columns follow.

## The PCSE contract

`TMIN`, `TMAX`, `IRRAD`, `VAP`, `WIND`, `RAIN`, `LAT`, `LON` and `ELEV` are
named and scaled exactly as `pcse.base.WeatherDataContainer` wants them
(verified against PCSE 6.0.13), so the crop model renames nothing and converts
nothing.

**These are the container's units, not PCSE's CSV file units.**
`pcse.input.CSVWeatherDataProvider` reads a *file* in kJ/m2/day, kPa and mm and
converts on the way in. `crop_weather_daily.csv` is written in the container's
units to match the JSON, so it is *not* a drop-in for that provider.

### What the crop model still owes

PCSE requires three more variables that this node does not compute: `E0`, `ES0`
and `ET0`, the open-water, bare-soil and reference-crop evaporation rates in
cm/day. WOFOST reads them directly in `pcse/crop/evapotranspiration.py`, and a
missing one only logs a warning when the container is built, so the run fails
later and further from the cause.

They are PCSE's own physics, so they belong on the PCSE side of the boundary,
and keeping them there is what lets this image have no third-party dependencies
at all. The metadata carries the Angstrom coefficients so the crop model does
not have to re-derive them from a shorter series. The whole contract is this:

```python
from datetime import date
from pcse.base.weather import WeatherDataContainer, WeatherDataProvider
from pcse.util import reference_ET

class CropWeatherProvider(WeatherDataProvider):
    def __init__(self, rows, angstrom_a, angstrom_b):
        super().__init__()
        first = rows[0]
        self.latitude, self.longitude = first["LAT"], first["LON"]
        self.elevation = first["ELEV"]
        self.angstA, self.angstB = angstrom_a, angstrom_b
        for row in rows:
            day = date.fromisoformat(row["date"])   # JSON has no date type
            e0, es0, et0 = reference_ET(            # returns mm/day
                day, row["LAT"], row["ELEV"], row["TMIN"], row["TMAX"],
                row["IRRAD"], row["VAP"], row["WIND"], angstrom_a, angstrom_b, "PM")
            self._store_WeatherDataContainer(
                WeatherDataContainer(
                    DAY=day, LAT=row["LAT"], LON=row["LON"], ELEV=row["ELEV"],
                    TMIN=row["TMIN"], TMAX=row["TMAX"], IRRAD=row["IRRAD"],
                    VAP=row["VAP"], WIND=row["WIND"], RAIN=row["RAIN"],
                    E0=e0 / 10.0, ES0=es0 / 10.0, ET0=et0 / 10.0),   # cm/day
                day)
```

`check_weather.py` runs exactly that and then a full WOFOST maize simulation on
the result, which is how this bundle proves it works.

### The validation yield is potential production

The yield that simulation reports is **potential production**: WOFOST's
radiation- and temperature-limited level, with no water, nutrient or pest
limitation at all. Assuming water is never limiting is the same thing as
assuming perfect irrigation, applied the instant the crop wants it, free, on
every acre -- so the figure is the fully-irrigated case, and it is not a yield
forecast for anybody's field or state.

That is the right default for a *weather* bundle, because it isolates the
weather from soil parameters this node does not own and does not ship. It is
only misleading when it goes unlabelled, which is why the check now runs three
production levels and asserts the relationship between them rather than
asserting that one number falls in a wide band.

Measured on the 2026 season, maize sown 1 May, `Grain_maize_201`, on PCSE's
generic `DummySoilDataProvider`:

| region | potential | rainfed | irrigated | water applied | in-season rain |
|---|---|---|---|---|---|
| `ia` | 10,926 | 10,799 | 10,926 | 18.0 cm | 62.0 cm |
| `ne_irrigated` | 9,803 | 8,548 | 9,803 | 36.0 cm | 46.9 cm |
| `ne_rainfed` | 9,835 | 9,003 | 9,835 | 29.2 cm | 47.4 cm |
| `ks_irrigated` | 8,575 | 3,071 | 8,575 | 63.0 cm | 24.5 cm |
| `ks_rainfed` | 8,363 | 7,819 | 8,363 | 42.8 cm | 39.2 cm |

TWSO in kg/ha. Irrigating the water-limited run reproduces the potential yield
exactly, in every region -- which is the point. How far rainfed falls below it
is entirely a matter of where you are: 1.2 percent in Iowa that season, 64
percent on Kansas's irrigated stratum.

That Kansas pair is the clearest argument for splitting the state. Before the
split, Kansas's single point reported a 32 percent rainfed shortfall on 39 cm
of in-season rain. That was an average of two quite different places: where the
irrigated corn actually is, rain is 24.5 cm and rainfed maize loses 64 percent;
where the rainfed corn is, rain is 39.2 cm and it loses 6.5 percent. The
blended point described neither. Nebraska's two points sit only 73 km apart and
differ much less, which is what a split looks like when it matters less.

The soil is generic rather than real, so these show direction and rough
magnitude, not a calibrated yield. Water-limited production belongs to
`wofost-bundles/corn-yield`, which owns the soil and the crop; this node only
has to be honest about what its own validation run means.

## Where the weather comes from

[Open-Meteo](https://open-meteo.com/), free and keyless, two calls per region:

- **ERA5 archive** -- `archive-api.open-meteo.com/v1/archive`, `models=era5`,
  for every day it covers. It lags about six days.
- **Forecast** -- `api.open-meteo.com/v1/forecast`, for the rest. Those rows get
  `is_forecast = true`.

Six daily aggregations, no hourly block: `temperature_2m_max`,
`temperature_2m_min`, `precipitation_sum`, `shortwave_radiation_sum`,
`dew_point_2m_mean`, `wind_speed_10m_mean`, with `timezone=UTC` and
`wind_speed_unit=ms`. A day neither endpoint serves fails the run rather than
being interpolated or quietly dropped.

### Every unit conversion

All of it happens in one function, `to_pcse_row`.

| PCSE variable | Open-Meteo source | Conversion |
|---|---|---|
| `TMIN`, `TMAX` | `temperature_2m_min` / `_max`, degC | none |
| `IRRAD` | `shortwave_radiation_sum`, MJ/m2/day | x 1e6 |
| `RAIN` | `precipitation_sum`, mm | x 0.1 |
| `WIND` | `wind_speed_10m_mean`, m/s at 10 m | x 0.71833 |
| `VAP` | `dew_point_2m_mean`, degC | `6.108 * exp(17.27 * Td / (Td + 237.3))` |

The wind factor is `log10(2/0.033) / log10(10/0.033)`, a log wind profile over a
0.033 m roughness length. That is `pcse.util.wind10to2`'s exact value, so the
crop model sees the number PCSE itself would have produced -- not the 0.75 rule
of thumb, which would be 4% high.

**A note on PCSE's own Open-Meteo provider.** PCSE 6.0.13 ships
`pcse.input.OpenMeteoWeatherDataProvider`. Its conversions are the ones above,
and this bundle follows them deliberately -- but it requests `wind_speed_10m`
without `wind_speed_unit`, and Open-Meteo defaults to km/h, so it applies
`wind10to2` to km/h as if it were m/s and its `WIND` comes out about 3.6x too
high. This bundle passes `wind_speed_unit=ms` explicitly.

### Angstrom coefficients

`reference_ET` needs Angstrom A and B. They are estimated the way PCSE does it:
A is the 5th percentile and A+B the 98th percentile of measured over
top-of-atmosphere radiation (FAO-56), over the run's own series. Below 200 days,
or when the estimate falls outside `pcse.util.check_angstromAB`'s bounds, PCSE's
0.29 / 0.49 defaults are used instead. `metadata.angstrom` records the values
and which of the two happened. Because they are percentiles, they move slightly
as the window grows; the crop model should read them from the same run it reads
the weather from.

## Growing degree days and stress days

`gdd_daily` uses the **capped-average** method, the US corn convention:

```
gdd_daily = max(0, (min(TMAX, gdd_cap_c) + max(TMIN, gdd_base_c)) / 2 - gdd_base_c)
```

`TMAX` is capped before averaging, so a 38 degC day counts the same as a 30 degC
one; `TMIN` is floored, so a cold night does not cancel the day's accumulation.
The simple-average method (mean of the raw extremes, minus base) overstates
development in hot weather and is not used. Corn's base 10 degC / 50 degF and
cap 30 degC / 86 degF are the defaults.

`gdd_cumulative` accumulates from `season_start`, **not** from planting. That is
deliberate: this node knows nothing about planting dates, which belong to the
crop model. Starting from 1 January means `gdd_cumulative` is not zero when the
crop goes in; a crop model that wants planting-to-date should re-accumulate from
`gdd_daily`, which is why both columns are emitted.

`frost_day` is `TMIN <= frost_threshold_c` (default 0 degC, a killing frost for
corn) and `heat_stress_day` is `TMAX >= heat_threshold_c` (default 32 degC,
where corn pollination starts to suffer). Both are inclusive, both have running
season-to-date counts, and both are computed for forecast days as well --
`is_forecast` says which.

Off-season output is valid but quiet: the series is most meaningful from about
April to October, and a January-to-March window is mostly frost days and zero
GDD. That is the method, not a defect.

## Regions

Ten states, ranked by county corn-for-grain production in the USDA NASS **2022
Census of Agriculture**: IA, IL, MN, NE, IN, SD, OH, WI, KS, MO. They are
reported as **twelve regions**, because Nebraska and Kansas are each split into
an irrigated and a rainfed point.

Each point is the **production-weighted centroid** of county internal points,
weighting each county by its corn-for-grain production. County coordinates come
from the **US Census Bureau 2023 Gazetteer** county file; elevation is
Open-Meteo's terrain height at the chosen point, so it matches the grid the
weather comes from.

### Why two states are split

A single point per state averages irrigated and rainfed corn as if they were
one crop. Where irrigation is widespread that point drifts towards places that
are dry but productive *because* of water this model cannot see, so the weather
it reports is drier than the rainfed crop experiences while the production
weighting behind it was bought with irrigation.

**A state is split when irrigation covers 20 percent or more of its harvested
corn acres.** Measured on the 2022 Census:

| State | Irrigated share of harvested corn acres | |
|---|---|---|
| NE | 52.7 % | split |
| KS | 25.4 % | split |
| MO | 9.4 % | single point |
| IN | 6.3 % | single point |
| WI | 4.7 % | single point |
| MN | 3.9 % | single point |
| SD | 3.5 % | single point |
| IL | 3.3 % | single point |
| IA | 1.2 % | single point |
| OH | 0.5 % | single point |

Nothing sits near the line. Below about 10 percent a split buys nothing, and
two further measurements say to stop at NE and KS: counties whose irrigated
figure NASS withholds hold 0.0 percent of Nebraska's production and 4.9 percent
of Kansas's (against 33.8 percent in Missouri), and Missouri's irrigated corn is
bimodal enough that its stratum point would land in the Ozarks, between the
Bootheel and the northwest river valley, where no irrigated corn grows.

### How a stratum's weight is derived

NASS publishes **no irrigated production series at any aggregation level** --
not county, not state, not national. So the weight is reconstructed: each
county's *published* production is apportioned between the two strata in
proportion to acres times a state-level stratum yield.

```
share_irrigated = a_irr * Y_irr / (a_irr * Y_irr + a_rain * Y_rain)
```

`a_irr` is county `CORN, GRAIN, IRRIGATED - ACRES HARVESTED`, `a_rain` is total
harvested acres minus that, and `Y_irr` / `Y_rain` are the state-level yields of
operations that irrigate all of their corn and none of it.

Because published production is *divided* rather than re-estimated, the strata
sum exactly to the state's undivided weight, and recombining the two points by
weight reproduces the single point this bundle published before the split. Only
the yield *ratio* matters, not the levels; a bias common to both cancels.

Three limits worth knowing:

- **County acres are multiplied by a state yield**, so one irrigated-to-rainfed
  yield ratio applies to every county in a state, and within-state variation in
  that ratio is lost.
- **`Y_irr` and `Y_rain` are operation-level classes.** Farms that irrigate only
  part of their corn are in neither yield series, though their acres are still
  apportioned using the ratio taken from the two that are.
- **Rainfed acres are a subtraction**, and a county whose irrigated figure is
  withheld is treated as zero, which biases the rainfed stratum slightly large.
  The bound is the withheld share above: 0.0 percent in NE, 4.9 percent in KS.

The strata are **not "the better half and the worse half"**. Irrigation status
is confounded with soil quality and the sign of the yield gap flips by state:
irrigating operations out-yield non-irrigating ones by 105 percent in Kansas and
55 percent in Nebraska, but yield 8 percent *less* in Iowa and 20 percent less
in Ohio, where irrigation sits on marginal ground. The strata say where the corn
is and how it is watered, not which half is better.

### What a downstream consumer must do

`region_key` is **this flow's join key** and it must not change between runs.
This change moves it, once:

| Before | After |
|---|---|
| `ne` | `ne_irrigated`, `ne_rainfed` |
| `ks` | `ks_irrigated`, `ks_rainfed` |
| the other eight | unchanged (`ia`, `il`, `mn`, `in`, `sd`, `oh`, `wi`, `mo`) |

**`ne` and `ks` no longer exist.** A consumer that joins on them will find
nothing rather than silently getting the wrong answer, which is the reason the
old keys were retired rather than kept alongside the new ones.

A consumer that wants one result per state should run its model on each stratum
and combine the two results using `weight`, which is each region's share of the
whole region set's corn production and sums to 1 across the file. A consumer
that wants one series per state regardless should group by `state`, which is
still the two-letter code on every row. Summing rows without either will now
double-count nothing -- each acre appears once -- but will treat Nebraska's two
points as two independent places, which they are.

`stratum` is `irrigated`, `rainfed`, or `all` for a state that is not split.
Both `stratum` and `weight` appear in each output's `metadata.regions`, so a
consumer reading the long table joins them on `region_key` rather than finding
them repeated on all 3,336 rows.

### The file

`regions.csv` is the committed artifact, with `stratum`, `weight`, `method` and
`source` on every row; each row's `method` states the threshold and that state's
own measured irrigated share, so the file explains its own split decision.
`build_regions.py` rebuilds it; it needs no API key, but it downloads a ~300 MB
NASS bulk export, so it is a one-time script and is not in the image. Counties
whose production NASS withholds for disclosure reasons are excluded, and each
row records how many that was.

A state-scale representative point is a modelling choice, not a measurement. It
describes where the corn is, not any particular field, and a stratum point is
still an average over a stratum. Crop-reporting districts would be the natural
refinement.

## Determinism

Deterministic given `date`, the region set and the Open-Meteo data **as of
retrieval** -- the same category as a market-data pull. There is no randomness
and no wall-clock dependence beyond the `date` default and the `retrieved_at`
stamp.

The newest ~21 days per region are forecast or preliminary and ERA5 revises them
later, so re-running an old `date` can shift those days. Every run re-fetches
and recomputes the whole window with the current code, so the series always
reflects current data and current code. `is_forecast` marks every row that was
not yet reanalysis at retrieval, and `retrieved_at` records when.

## What comes next: NASA POWER

NASA POWER is the agromet standard for driving WOFOST and DSSAT, and PCSE ships
a provider for it. It serves the crop-model variables natively -- 2 m wind and
agricultural radiation -- which would remove two of the conversions above and
the judgement calls in them, and it carries more weight with an agronomic
audience than a general-purpose weather API.

What it cannot do is forecast, and it lags several days behind. So the likely
evolution is a split: NASA POWER for the observed leg, Open-Meteo retained for
the forecast leg, behind the same output contract. `fetch_window` is the only
place that knows where the weather comes from, so a second provider slots in
there without touching the conversions, the agromet columns or the schema.

## Validation

```bash
uv run --no-project --python 3.12 --with pcse==6.0.13 --with numpy \
    python check_weather.py ../run/crop_weather_daily.output.json
```

PCSE is a development dependency only and never appears in the Dockerfile.
`check_weather.py` covers the unit conversions against PCSE's own helpers, the
GDD and stress arithmetic against hand-worked examples, the region table, the
shape of the output, and -- the decisive one -- loading the output into a real
`WeatherDataProvider` and running WOFOST maize to a finished yield.

The maize runs three times, on whichever region in the output saw the least
rain between 1 May and 30 September, since that is where water actually binds:
potential production, water-limited rainfed, and water-limited with irrigation
triggered when root-zone soil moisture falls through 0.25. Three assertions
follow, and each can fail:

- irrigating the water-limited run reproduces the potential yield to within 1
  percent, which is what pins down what the published figure means;
- potential is at least rainfed, within 1 percent -- the tolerance is there
  because a mild deficit lowers LAI and WOFOST's partitioning can repay a
  little of that, so rainfed occasionally edges above potential;
- the water balance actually applied irrigation.

## Files

```
crop-weather/
  Modelfile.toml      two JSON outputs; semantic annotations
  Dockerfile          python:3.12-slim, no pip layer (standard library only)
  runner.py           the model
  regions.csv         twelve regions: point, stratum, weight, method, source
  build_regions.py    one-time region build from NASS + Census (not in the image)
  check_weather.py    validation, including the WOFOST run (not in the image)
  sample_input.json   2026-09-15, Iowa / Illinois / irrigated Nebraska
  README.md
```

## Licence and attribution

MIT (see the repository `LICENSE`). Weather data by
[Open-Meteo.com](https://open-meteo.com/), CC BY 4.0. Region weights from the
USDA NASS 2022 Census of Agriculture; county coordinates from the US Census
Bureau 2023 Gazetteer. Unit conventions and the Angstrom estimator follow
[PCSE](https://github.com/ajwdewit/pcse) 6.0.13 (no PCSE code is vendored here).
