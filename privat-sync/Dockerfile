# Privat-Sync: holt alle 10 Minuten nur den Ordner „01 Projekte/Bereitschaft“ (dazu zwei Dateien für den Melder)
# aus dem privaten Vault (Nur-Lese-Schlüssel) und schreibt daten.json für die App „Privatfirma“.
# Kein KI-Aufruf, kein Webserver, kein offener Port.
FROM python:3.12-alpine
LABEL org.opencontainers.image.source=https://github.com/Bestoug/unraid-mcp
LABEL org.opencontainers.image.description="Privat-Sync fuer die App Privatfirma (nur lesen)"
RUN apk add --no-cache git openssh-client tzdata
ENV TZ=Europe/Berlin PYTHONDONTWRITEBYTECODE=1
COPY privat_daten.py melder.py sync.sh /opt/privat/
RUN chmod 755 /opt/privat/sync.sh
CMD ["/opt/privat/sync.sh"]
