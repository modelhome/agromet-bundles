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
| `crop-weather/` | *(in progress, brief [0001](./docs/features/0001-crop-weather.md))* Daily weather per US corn-growing region, season to date plus a forecast horizon, in the variables and units PCSE/WOFOST consumes | optional date, regions, season start, forecast horizon and GDD parameters (empty for today's defaults) -> per region per day: the PCSE weather series, plus accumulated growing degree days and frost / heat-stress flags |

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
