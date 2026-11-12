# syntax=docker/dockerfile:1
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    STACKTRIAGE_ENV=production \
    STACKTRIAGE_DB=/data/stacktriage.db \
    STACKTRIAGE_STATIC_DIR=/app/frontend/dist
WORKDIR /app
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
# python:3.12-slim bundles pip and setuptools releases with known advisories (pip-audit and Trivy
# found them in the built image); upgrade both before installing the app.
RUN pip install --no-cache-dir --upgrade "pip>=26.2" "setuptools>=84.1.0" && pip install --no-cache-dir ".[api,ml]"
COPY --from=web /web/dist ./frontend/dist
RUN useradd --create-home --uid 10001 app && mkdir /data && chown app /data
USER app
VOLUME /data
EXPOSE 8000
# STACKTRIAGE_API_KEY (>= 16 chars) is REQUIRED at runtime in production mode; the app refuses to start without it.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status==200 else 1)"
CMD ["stacktriage", "serve", "--host", "0.0.0.0", "--port", "8000"]
