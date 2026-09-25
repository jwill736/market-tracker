# Plumbline private dashboard. One process on purpose: the live-price hub lives in memory and
# keeps a single upstream connection per data source.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MT_DB_PATH=/data/market_tracker.db REQUIRE_LOGIN=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY market_tracker ./market_tracker
RUN pip install --no-cache-dir . && mkdir -p /data && useradd --create-home app && chown app /data
USER app
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn market_tracker.api:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'"]
