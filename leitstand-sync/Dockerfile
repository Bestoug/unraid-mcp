# Leitstand-Sync: holt alle 10 Minuten nur den Ordner „01 Firma“ aus dem Firmen-Vault (Nur-Lese-Schlüssel)
# und schreibt daten.json für den Leitstand. Kein KI-Aufruf, kein Webserver, kein offener Port.
FROM python:3.12-alpine
LABEL org.opencontainers.image.source=https://github.com/Bestoug/unraid-mcp
LABEL org.opencontainers.image.description="Leitstand-Sync fuer die Agenten-Firma: liest nur 01 Firma aus firma-vault (nur lesen)"
RUN apk add --no-cache git openssh-client tzdata
ENV TZ=Europe/Berlin PYTHONDONTWRITEBYTECODE=1
COPY leitstand_daten.py sync.sh /opt/leitstand/
COPY web/ /opt/leitstand/web/
RUN chmod 755 /opt/leitstand/sync.sh
CMD ["/opt/leitstand/sync.sh"]
