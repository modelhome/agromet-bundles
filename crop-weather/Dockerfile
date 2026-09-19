# to build and run from your local machine (build context is this crop-weather/
# folder, which is also what Model Home uses when the model is added from
# github.com/modelhome/agromet-bundles/tree/main/crop-weather):
#   cd crop-weather && docker build -t agromet-crop-weather:local .
#   docker run --rm agromet-crop-weather:local          # uses sample_input.json
#   mkdir -p run && echo '{}' > run/crop_weather_request.json
#   docker run --rm -v "$PWD/run:/run" agromet-crop-weather:local \
#       /run/crop_weather_request.json /run/crop_weather_summary.output.json \
#       > run/crop_weather_daily.output.json
# The model fetches weather from Open-Meteo, so the container needs network access.

FROM python:3.12-slim

WORKDIR /app

# No pip layer: the runner is standard library only. PCSE belongs to the
# downstream crop model, and this bundle deliberately does not import it -- it
# only has to conform to PCSE's format. check_weather.py, which does use PCSE,
# is not copied into the image.

COPY runner.py regions.csv sample_input.json ./

ENTRYPOINT ["python", "runner.py"]

# irl the input/output paths come from the Modelfile; for local testing we
# default to the bundled sample input.
CMD ["sample_input.json"]
