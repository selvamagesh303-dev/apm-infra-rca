# One parametrized image for all microservices.
# Build with: --build-arg SERVICE=<orders|catalog|profiles|sessions|search|gateway>
FROM python:3.12-slim
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common ./common
ARG SERVICE
COPY ${SERVICE} ./${SERVICE}
ENV SERVICE=${SERVICE}

EXPOSE 8000
# opentelemetry-instrument auto-instruments FastAPI + the DB client and exports
# traces over OTLP (endpoint/service name set via OTEL_* env in docker-compose).
CMD ["sh", "-c", "opentelemetry-instrument uvicorn ${SERVICE}.main:app --host 0.0.0.0 --port 8000"]
