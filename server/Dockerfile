FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FARM_DATA_DIR=/data

WORKDIR /app
COPY server/requirements.txt /app/server/requirements.txt
# nmcli is only the client; it talks to the host NetworkManager over D-Bus
# when compose.hotspot.yaml mounts the system bus and FARM_HOTSPOT_APPLY=1.
RUN apt-get update \
    && apt-get install -y --no-install-recommends network-manager \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r /app/server/requirements.txt \
    && useradd --system --uid 10001 --create-home farm

COPY server /app/server
RUN mkdir -p /data && chown -R farm:farm /data /app
USER farm

EXPOSE 8000
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"

CMD ["uvicorn", "server.farm_server.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
