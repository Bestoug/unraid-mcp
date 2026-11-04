FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Berlin \
    PORT=8000

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd --create-home --uid 99 --gid 100 --no-user-group mcp

# Startet als root, damit Unraids Tailscale-Integration laufen kann;
# der Server selbst gibt die Rechte beim Start an den Benutzer mcp ab.
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/health',timeout=4)" || exit 1

CMD ["unraid-mcp"]
