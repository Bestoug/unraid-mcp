#!/bin/bash
# Schaltet in unraid-mcp die Container-Verwaltung und die Jellyfin-Werkzeuge ein.
# Ergänzt die Unraid-Vorlage von unraid-mcp um:
#   - Docker-Socket (Container anlegen/ändern/aktualisieren)
#   - Unraid-Vorlagen-Ordner (damit „Edit“ und „Force Update“ in Unraid die Änderungen kennen)
#   - VERWALTUNG=1, JELLYFIN_URL, JELLYFIN_API_KEY (wird abgefragt, steht nicht in diesem Skript)
# Danach in Unraid bei unraid-mcp „force update“ klicken.
# Aufruf im Unraid-Terminal:
#   bash <(curl -fsSL https://raw.githubusercontent.com/Bestoug/unraid-mcp/main/unraid/verwaltung-aktivieren.sh)
set -euo pipefail

D=/boot/config/plugins/dockerMan/templates-user
V=$(ls "$D"/my-unraid-mcp.xml 2>/dev/null | head -1 || true)
if [ -z "$V" ]; then
  echo "FEHLER: Vorlage my-unraid-mcp.xml nicht gefunden in $D"
  exit 1
fi
if grep -q 'Target="VERWALTUNG"' "$V"; then
  echo "Verwaltung ist schon eingetragen – nichts geändert."
  exit 0
fi

JF_URL="http://192.168.2.113:8096"
echo "Jellyfin-API-Schlüssel einfügen (Jellyfin → Dashboard → API-Schlüssel → +, Name claude)."
echo "Die Eingabe ist unsichtbar. Leer lassen = Jellyfin-Werkzeuge später einrichten."
read -rs -p "Schlüssel: " JF_KEY
echo
case "$JF_KEY" in *[!A-Za-z0-9]*) echo "FEHLER: Schlüssel enthält unerwartete Zeichen."; exit 1;; esac

cp "$V" "$V.vor-verwaltung.$(date +%Y%m%d-%H%M%S)"

zeile() {  # Name Ziel Typ Modus Wert Maske Beschreibung
  printf '  <Config Name="%s" Target="%s" Default="" Mode="%s" Description="%s" Type="%s" Display="always" Required="false" Mask="%s">%s</Config>\n' \
    "$1" "$2" "$4" "$7" "$3" "$6" "$5"
}

NEU="$(zeile 'Docker-Socket' /var/run/docker.sock Path rw /var/run/docker.sock false 'Container anlegen/ändern (Regeln im Code)')
$(zeile 'Unraid-Vorlagen' /vorlagen Path rw "$D" false 'Unraid-Vorlagen mitpflegen')
$(zeile VERWALTUNG VERWALTUNG Variable '' 1 false 'Container-Verwaltung einschalten')
$(zeile JELLYFIN_URL JELLYFIN_URL Variable '' "$JF_URL" false 'Jellyfin im Heimnetz')
$(zeile JELLYFIN_API_KEY JELLYFIN_API_KEY Variable '' "$JF_KEY" true 'Jellyfin-API-Schlüssel claude')
"
awk -v z="$NEU" '/<\/Container>/ { printf "%s", z } { print }' "$V" > "$V.tmp"
grep -q 'Target="VERWALTUNG"' "$V.tmp" || { rm -f "$V.tmp"; echo "FEHLER: Vorlage hat unerwartetes Format."; exit 1; }
mv "$V.tmp" "$V"
echo "Fertig. Jetzt: Docker → Advanced View → bei unraid-mcp auf „force update“ klicken."
