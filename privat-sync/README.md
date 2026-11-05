# Privat-Sync

Begleit-Container für die App „Privatfirma“. Er holt alle 10 Minuten **nur lesend** den Ordner `01 Projekte/Bereitschaft` aus dem privaten Vault (dazu für den Melder nur die Ampel aus den Sicherheitsberichten und die Anzahl roter Funde aus dem Befund-Buch) und erzeugt daraus `daten.json`. Ausgeliefert wird die Datei von einem getrennten, gehärteten nginx-Container nur im Heimnetz/Tailnet.

## Sicherheit
- Nur-Lese-Deploy-Key, nur für ein Repository; der geheime Teil verlässt `/schluessel` nie
- Teil-Klon (`--filter=blob:none --sparse`): auf dem Server landen nur die genannten Teile
- Der ausgeführte Code steckt im Image, nie in den geholten Daten
- `daten.json` wird auf Mailadressen, private IP-Adressen, `ts.net`-Namen und Wörter aus `PRIVAT_FILTER` gefiltert
- Melder: Titel und Text sind **feste Texte** aus `melder.py`, nie Text aus dem Vault. Webhook nur über die Variable `HA_WEBHOOK_URL`; fehlt sie, wird nichts gesendet
- GitHub-Hostschlüssel fest eingetragen; kein offener Port, kein Webserver, keine KI

## Ordner
| Container | Zweck |
|---|---|
| `/schluessel` | geheimer Schlüssel und Klon (eigener appdata-Ordner) |
| `/status` | öffentlicher Schlüssel, `status.json`, Zustand des Melders |
| `/ausgabe` | `daten.json` für die App |

## Variablen
`PRIVAT_INTERVALL` (Sekunden, Standard 600), `PRIVAT_FILTER` (Wörter, kommagetrennt), `HA_WEBHOOK_URL` (optional), `PRIVAT_REPO` (Standard: privater Vault)

## Testen
`python3 -m unittest discover -s privat-sync/tests` (nur erfundene Daten)
