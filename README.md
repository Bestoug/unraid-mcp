# unraid-mcp

Kleiner MCP-Server, der Claude Zugriff auf einen **Unraid-Server** gibt. Läuft als Docker-Container auf dem Server selbst und wird in claude.ai als eigener Connector eingebunden. Gleiches Muster wie [icloud-mcp](https://github.com/Bestoug/icloud-mcp).

## Was Claude damit kann
| Bereich | Werkzeuge |
|---|---|
| Status | Server (Version, Laufzeit, CPU, RAM), Array und Platten, Freigaben, ungelesene Benachrichtigungen, eigene **lesende** GraphQL-Abfragen |
| Docker | Container auflisten, **starten, stoppen, neu starten** |
| Dateien | im Arbeitsordner lesen, schreiben, Ordner anlegen, verschieben, wegräumen; weitere Ordner **nur lesen** |

**Nichts wird endgültig gelöscht:** „Wegräumen“ verschiebt nach `.papierkorb` im Arbeitsordner. Verschieben überschreibt nie.

## Sicherheit
- Zugriff auf Unraid über einen eigenen **API-Schlüssel** der offiziellen Unraid-API (ab Unraid 7.2 eingebaut), jederzeit widerrufbar
- Eigene Abfragen sind auf **lesend** beschränkt; Änderungen gehen nur über die fest eingebauten Werkzeuge
- Kein Docker-Socket, kein `privileged`, kein Zugriff auf das Host-Dateisystem außer den eingebundenen Ordnern; Pfade mit `..` oder Symlinks nach draußen werden abgelehnt
- Der Container `unraid-mcp` selbst lässt sich nicht anhalten; weitere mit `GESCHUETZTE_CONTAINER`
- Der Server nimmt nur Anfragen mit dem geheimen Token `MCP_ACCESS_TOKEN` an (Header `Authorization: Bearer …`)
- Jede ändernde Aktion wird mit `AKTION …` ins Container-Log geschrieben
- Läuft als Benutzer `99:100` (nobody:users), damit Dateien im Arbeitsordner Unraid-üblich gehören

## Umgebungsvariablen
| Variable | Bedeutung |
|---|---|
| `UNRAID_URL` | Adresse der API, z. B. `http://192.168.2.113/graphql` |
| `UNRAID_API_KEY` | API-Schlüssel (Unraid → Einstellungen → Management Access → API Keys) |
| `MCP_ACCESS_TOKEN` | langer Zufallswert (mind. 32 Zeichen), z. B. `openssl rand -hex 32` |
| `ARBEIT_ORDNER` | Arbeitsordner im Container, Standard `/arbeit` |
| `LESE_ORDNER` | nur lesend eingebundene Ordner, kommagetrennt, Standard `/daten` |
| `CONTAINER_NUR_LESEN` | `1` schaltet Start/Stopp ab |
| `GESCHUETZTE_CONTAINER` | weitere Container, die nie angehalten werden, kommagetrennt |
| `UNRAID_SSL_IGNORIEREN` | `1` bei selbstsigniertem Zertifikat |
| `PORT` | Port im Container, Standard `8000` |

## Image
`ghcr.io/bestoug/unraid-mcp:latest` wird bei jedem Push auf `main` getestet und gebaut.

## Endpunkte
- `/mcp`: MCP (Streamable HTTP), braucht den Token
- `/health`: Lebenszeichen ohne Token
