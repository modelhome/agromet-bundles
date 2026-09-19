# agromet-bundles

Standalone [Model Home](https://modelhome.run) model bundles for agricultural
meteorology: the daily weather layer that crop models run on. Each subfolder is
a self-contained model: a `Modelfile.toml`, a `Dockerfile`, a `runner.py`, and
sample inputs.

Nothing upstream is vendored here. Libraries these bundles depend on are
installed as pinned pip packages inside each bundle's image.

## Bundles

| Bundle | Model | Inputs -> Outputs |
|---|---|---|
| [`crop-weather/`](./crop-weather) | US corn crop weather: the daily series a WOFOST run consumes, for the ten biggest corn states, 1 January to today plus a two-week forecast, from Open-Meteo | optional date, regions, season start, forecast horizon and crop thresholds (empty for today) -> per region per day: TMIN, TMAX, IRRAD, VAP, WIND and RAIN in PCSE's own units, plus accumulated growing degree days and frost / heat-stress days; and the same grouped per region |

## Quick start

Each bundle builds and runs from its own folder, which is also the build context
Model Home uses:

```bash
cd crop-weather
docker build -t agromet-crop-weather:local .
docker run --rm agromet-crop-weather:local   # needs network access
```

Or, when creating a new model on Model Home, paste the bundle folder's GitHub
URL (for example
`https://github.com/modelhome/agromet-bundles/tree/main/crop-weather`) into the
"classic import" option.

Each bundle's README covers its inputs, outputs, data sources, unit conversions
and assumptions. See [`CLAUDE.md`](./CLAUDE.md) for the design notes, the
conventions every bundle follows, the verified PCSE weather contract, and how
features are briefed, planned and built.

## Licence

MIT. See [`LICENSE`](./LICENSE). Weather data comes from
[Open-Meteo](https://open-meteo.com/) (CC BY 4.0); each bundle's README carries
the full attribution.
