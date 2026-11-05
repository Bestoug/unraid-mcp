# Leitstand-Sync

Kleiner Begleit-Container für Philipps Leitstand (Übersicht der Agenten-Firma). Er holt alle 10 Minuten **nur lesend** den Firmenordner aus dem privaten Vault und erzeugt daraus `daten.json`. Die Webseite selbst liefert ein getrennter, gehärteter nginx-Container aus.

## Sicherheit
- Nur-Lese-Deploy-Key, nur für ein Repository; der geheime Teil verlässt `/schluessel` nie
- Teil-Klon (`--filter=blob:none --sparse`): Auf dem Server landen nur der Firmenordner und das Befund-Buch, keine anderen Notizen
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
