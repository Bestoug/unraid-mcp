# Leitstand-Sync

Kleiner Begleit-Container für Philipps Leitstand (Übersicht der Agenten-Firma). Er holt alle 10 Minuten **nur lesend** den Ordner `01 Firma` aus dem Firmen-Vault (`firma-vault`) und erzeugt daraus `daten.json`. Beim Start legt er außerdem die Webseite (`web/`) in den Ausgabeordner; ausgeliefert wird sie von einem getrennten, gehärteten nginx-Container. App-Updates: hier ändern, pushen, Container neu laden.

## Sicherheit
- Nur-Lese-Deploy-Key, nur für ein Repository; der geheime Teil verlässt `/schluessel` nie
- Teil-Klon (`--filter=blob:none --sparse`): Auf dem Server landet nur der Ordner `01 Firma`, keine anderen Notizen. Das Befund-Buch der Prüf-Zelle bleibt im privaten Vault; in `daten.json` ist `sicherheit` deshalb `null`
- Der ausgeführte Code steckt im Image, nie in den geholten Daten
- GitHub-Hostschlüssel fest eingetragen
- Kein offener Port, kein Webserver, keine KI
- Private Wörter für den Filter (Straße, PLZ …) nur über die Variable `LEITSTAND_PRIVAT`, nie im Code

## Ordner
| Im Container | Zweck |
|---|---|
| `/schluessel` | geheimer Schlüssel und Klon (eigener appdata-Ordner) |
| `/status` | öffentlicher Schlüssel und `status.json` |
| `/ausgabe` | `daten.json` für die Webseite |

## Umzug auf den Firmen-Vault
- Standard-Repo ist jetzt `firma-vault` (änderbar über `LEITSTAND_REPO`). Ein vorhandener Klon eines anderen Repos wird beim Start verworfen und neu geholt.
- GitHub erlaubt einen Deploy-Key nur in einem Repository: den öffentlichen Schlüssel aus `/status/oeffentlicher-schluessel.txt` beim alten Repo entfernen und bei `firma-vault` als Nur-Lese-Deploy-Key eintragen.
- Datenformat: wie bisher, nur `sicherheit` ist `null` (Befund-Buch nicht mehr im Firmen-Vault).
