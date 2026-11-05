# unraid-mcp

Kleiner MCP-Server, der Claude Zugriff auf einen **Unraid-Server** gibt. Läuft als Docker-Container auf dem Server selbst und wird in claude.ai als eigener Connector eingebunden. Gleiches Muster wie [icloud-mcp](https://github.com/Bestoug/icloud-mcp).

## Was Claude damit kann
| Bereich | Werkzeuge |
|---|---|
| Status | Server (Version, Laufzeit, CPU, RAM), Array und Platten, Freigaben, ungelesene Benachrichtigungen, eigene **lesende** GraphQL-Abfragen |
| Docker | Container auflisten, **starten, stoppen, neu starten** |
| Dateien | im Arbeitsordner lesen, schreiben, Ordner anlegen, verschieben, wegräumen; weitere Ordner **nur lesen** |
| Verwaltung (optional) | Container **anlegen**, **ändern** (Ordner, Ports, Variablen, /dev/dri hinzufügen), **aktualisieren**, Details anzeigen |
| Jellyfin (optional) | Bibliotheken anzeigen, **anlegen** (mit Echtzeit-Überwachung), Scan starten |

**Nichts wird endgültig gelöscht:** „Wegräumen“ verschiebt nach `.papierkorb` im Arbeitsordner. Verschieben überschreibt nie.

## Sicherheit
- Zugriff auf Unraid über einen eigenen **API-Schlüssel** der offiziellen Unraid-API (ab Unraid 7.2 eingebaut), jederzeit widerrufbar
- Eigene Abfragen sind auf **lesend** beschränkt; Änderungen gehen nur über die fest eingebauten Werkzeuge
- Ohne `VERWALTUNG=1`: kein Docker-Socket, kein `privileged`, kein Zugriff auf das Host-Dateisystem außer den eingebundenen Ordnern; Pfade mit `..` oder Symlinks nach draußen werden abgelehnt
- Mit `VERWALTUNG=1` (Docker-Socket = Vollzugriff, daher feste Regeln im Code):
  - neue/geänderte Container nie `privileged`, nie Host-Netz, keine zusätzlichen Rechte, Geräte nur `/dev/dri`
  - Ordner nur unter `/mnt/user/<Freigabe>/`; gesperrt: `/boot`, `system`, ganz `/mnt/user`, fremde `appdata`-Ordner; andere Freigaben als `daten`, `appdata` (nur eigener Ordner), `claude` und `ERLAUBTE_FREIGABEN` nur mit Freigabe
  - Images nur aus bekannten Quellen (linuxserver, jellyfin, ghcr.io/bestoug, offizielle Images, `ERLAUBTE_IMAGES`), sonst nur mit Freigabe
  - Tailscale/Funnel (Erreichbarkeit aus dem Internet) und Variablen, die Code nachladen (`DOCKER_MODS`, `INSTALL_*`, `LD_*`, `PATH` …), nur mit Freigabe; keine Steuerzeichen in Werten
  - Container ändern startet nichts, was vorher gestoppt war; eine Absturzschleife gilt als Fehler → alter Zustand zurück
  - geschützte Container (`GESCHUETZTE_CONTAINER`) werden nicht geändert oder aktualisiert
  - unraid-mcp verändert sich nie selbst
  - vor jeder Änderung Sicherung: Einstellungen ohne Geheimnisse in `protokoll/sicherung`, Unraid-Vorlage (mit Geheimnissen) nur auf dem USB-Stick unter `templates-user/claude-sicherung`, Protokoll in `protokoll/verwaltung.md`; schlägt eine Änderung fehl, wird der alte Container wiederhergestellt
  - die Unraid-Vorlage (`my-<name>.xml`) wird mitgepflegt, damit „Edit“ und „Force Update“ dieselben Einstellungen nutzen
  - läuft dann als root (Docker-Socket, Vorlagen auf dem USB-Stick); neue Dateien im Arbeitsordner gehören trotzdem `nobody:users`
- Der Container `unraid-mcp` selbst lässt sich nicht anhalten; weitere mit `GESCHUETZTE_CONTAINER`
- Der Server nimmt nur Anfragen mit dem geheimen Token `MCP_ACCESS_TOKEN` an (Header `Authorization: Bearer …`)
- Jede ändernde Aktion wird mit `AKTION …` ins Container-Log geschrieben
- Läuft als Benutzer `99:100` (nobody:users), damit Dateien im Arbeitsordner Unraid-üblich gehören

## Umgebungsvariablen
| Variable | Bedeutung |
|---|---|
| `UNRAID_URL` | Adresse der API, z. B. `http://<Heimnetz-Adresse des Servers>/graphql` |
| `UNRAID_API_KEY` | API-Schlüssel (Unraid → Einstellungen → Management Access → API Keys) |
| `MCP_ACCESS_TOKEN` | langer Zufallswert (mind. 32 Zeichen), z. B. `openssl rand -hex 32` |
| `ARBEIT_ORDNER` | Arbeitsordner im Container, Standard `/arbeit` |
| `LESE_ORDNER` | nur lesend eingebundene Ordner, kommagetrennt, Standard `/daten` |
| `CONTAINER_NUR_LESEN` | `1` schaltet Start/Stopp ab |
| `GESCHUETZTE_CONTAINER` | weitere Container, die nie angehalten werden, kommagetrennt |
| `UNRAID_SSL_IGNORIEREN` | `1` bei selbstsigniertem Zertifikat |
| `PORT` | Port im Container, Standard `8000` |
| `VERWALTUNG` | `1` schaltet Container-Verwaltung ein (braucht `/var/run/docker.sock` und Vorlagen-Ordner unter `/vorlagen`) |
| `ERLAUBTE_IMAGES` | weitere bekannte Image-Quellen (Präfixe), kommagetrennt |
| `ERLAUBTE_FREIGABEN` | weitere Freigaben, die Container ohne Rückfrage bekommen dürfen (Standard: daten, appdata, claude) |
| `JELLYFIN_URL` / `JELLYFIN_API_KEY` | Jellyfin-Adresse und API-Schlüssel |
| `JELLYFIN_CONTAINER` | Name des Jellyfin-Containers, Standard `Jellyfin` |

Einschalten auf Unraid: `bash <(curl -fsSL https://raw.githubusercontent.com/Bestoug/unraid-mcp/main/unraid/verwaltung-aktivieren.sh)`, danach Force Update.

## Image
`ghcr.io/bestoug/unraid-mcp:latest` wird bei jedem Push auf `main` getestet und gebaut.

## Endpunkte
- `/mcp`: MCP (Streamable HTTP), braucht den Token
- `/health`: Lebenszeichen ohne Token
