"""Melder der Privatfirma: schickt bei neuen Ereignissen eine Meldung an einen Home-Assistant-Webhook.

Wird von privat_daten.py nach dem Schreiben der daten.json aufgerufen. Nur Standardbibliothek.

SICHERHEIT
- Titel und Text jeder Meldung sind FESTE Texte aus MELDUNGEN, ausgewählt nur über Ereignistyp und Stufe.
  Nie Text aus dem Vault: eine manipulierte Schicht kann so keine eigenen Texte (z. B. Phishing) aufs Handy schicken.
  Eingesetzt werden höchstens Anzahlen (int).
- Die Webhook-Adresse kommt nur aus der Umgebungsvariable HA_WEBHOOK_URL; fehlt sie, wird nichts gesendet.

REGELN
- kritisch, zeitkritisch und still: sofort; normal: eine Sammelmeldung pro Tag ab 18:00 (Europe/Berlin)
- gleiche Meldung (Typ + interne Kennung) höchstens einmal in 12 Stunden
- Zustand (letzter Stand, gesendete Meldungen, Sammelliste) in einer JSON-Datei; sie enthält keine Vault-Texte,
  nur IDs (B-/E-Nummern), Anzahlen, Zeitpunkte und Hashes

Ereigniskatalog und Erkennung: siehe DATENFORMAT.md, Abschnitt „Melder“.
"""
import datetime as dt
import hashlib
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

STUFEN = ("kritisch", "zeitkritisch", "normal", "still")
ENTPRELLUNG = dt.timedelta(hours=12)
SAMMEL_STUNDE = 18
TIMEOUT = 10

# Typ → (Stufe, Titel, Text). Platzhalter nur {anzahl}, gefüllt ausschließlich mit int.
MELDUNGEN = {
    "kernrolle_fehlt": ("kritisch", "Privatfirma: Kernrolle fehlt",
                        "Eine Kernrolle der Privatfirma fehlt. Bitte My App öffnen."),
    "sicherheit_rot": ("kritisch", "Sicherheits-Check: Rot",
                       "Der Sicherheits-Check meldet Rot. Bitte den Sicherheitsbericht im Vault lesen."),
    "pruefzelle_rot": ("kritisch", "Prüf-Zelle: neuer roter Fund",
                       "Die Prüf-Zelle meldet neue rote Funde: {anzahl}. Bitte das Befund-Buch im Vault lesen."),
    "vorschlag_wartet": ("zeitkritisch", "Privatfirma: Vorschläge warten",
                         "Vorschläge warten seit über 3 Tagen auf deine Antwort: {anzahl}."),
    "berater_ohne_lauf": ("zeitkritisch", "Privatfirma: Berater läuft nicht",
                          "Der Berater hat seit über 2 Tagen keinen Lauf eingetragen."),
    "aufsichtsrat_ohne_lauf": ("zeitkritisch", "Privatfirma: Aufsichtsrat läuft nicht",
                               "Der Aufsichtsrat der Privatfirma hat 2 Wochen in Folge nicht getagt."),
    "sync_steht": ("zeitkritisch", "Privatfirma: keine neuen Daten",
                   "Seit über 24 Stunden kamen keine neuen Daten aus dem Vault."),
    "sicherheit_gelb": ("zeitkritisch", "Sicherheits-Check: Gelb",
                        "Der Sicherheits-Check empfiehlt eine Handlung. Bitte den Sicherheitsbericht lesen."),
    "abteilung_ruht": ("still", "Privatfirma: Abteilung ruht", "Eine Abteilung der Privatfirma ruht jetzt."),
    "abteilung_geschlossen": ("still", "Privatfirma: Abteilung geschlossen",
                              "Eine Abteilung der Privatfirma wurde geschlossen."),
    "abteilung_wiedereroeffnet": ("still", "Privatfirma: Abteilung wiedereröffnet",
                                  "Eine Abteilung der Privatfirma arbeitet wieder."),
    "personalwechsel": ("still", "Privatfirma: Personalwechsel", "Im Personal der Privatfirma hat sich etwas geändert."),
    "schlankheit_fertig": ("still", "Privatfirma: Schlankheits-Check fertig",
                           "Der Schlankheits-Check ist fertig. Vorschläge stehen in der App."),
}
# Normale Ereignisse: Bausteine der Sammelmeldung (eine pro Tag)
SAMMEL_TITEL = "Privatfirma: Tagesübersicht"
SAMMEL_TEILE = {
    "neue_vorschlaege": "Neue Vorschläge: {anzahl}",
    "einsatz_erledigt": "Erledigte Einsätze: {anzahl}",
    "wochen_update": "Wochen-Update bereit zum Kopieren",
}

# Erledigte Berater-Vorschläge erkennt der Melder an diesen Zeichen/Wörtern im Eintrag
ERLEDIGT = ("✅", "❌", "erledigt", "abgelehnt", "beantwortet")
DATUM = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")


def _jetzt() -> dt.datetime:
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo("Europe/Berlin"))
    except Exception:  # ohne Zeitzonendaten: Ortszeit des Containers (TZ=Europe/Berlin)
        return dt.datetime.now().astimezone()


def _datum(t: str):
    m = DATUM.search(t or "")
    if not m:
        return None
    try:
        return dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def _kennung(*teile) -> str:
    """Interne Kennung für die Entprellung (Hash, wird nie gesendet)."""
    return hashlib.sha256(json.dumps(teile, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def _text(vorlage: str, anzahl) -> str:
    return vorlage.format(anzahl=int(anzahl or 0))


def payload(typ: str, anzahl: int = 0) -> dict:
    stufe, titel, vorlage = MELDUNGEN[typ]
    return {"stufe": stufe, "titel": titel, "text": _text(vorlage, anzahl), "app": "privatfirma"}


def sammel_payload(sammel: dict) -> dict:
    teile = [_text(SAMMEL_TEILE[k], n) for k, n in sammel.items() if k in SAMMEL_TEILE and n]
    return {"stufe": "normal", "titel": SAMMEL_TITEL, "text": " · ".join(teile), "app": "privatfirma"}


def _status(s: str) -> str:
    s = (s or "").lower()
    return "ruht" if "ruh" in s else "geschlossen" if "geschlossen" in s else "aktiv" if "aktiv" in s else ""


def stand_aus(daten: dict, signale: dict) -> dict:
    """Vergleichbarer Stand ohne Texte: nur IDs, Anzahlen, Status und Hashes."""
    ohne_zeit = {k: v for k, v in daten.items() if k != "erzeugt"}
    wu = daten.get("wochen_update") or {}
    return {
        "vorschlaege": sorted(b["id"] for b in daten.get("berater", []) if b.get("art") == "Vorschlag"),
        "einsaetze": sorted(e["nr"] for e in daten.get("einsaetze", [])),
        "schlankheit": sorted(e["nr"] for e in daten.get("einsaetze", [])
                              if "schlankheit" in (e.get("auftrag", "") + e.get("ergebnis", "")).lower()),
        "personal": sorted(_kennung(p.get("rolle", ""), p.get("version", "")) for p in daten.get("personal", [])),
        "ehemalige": len(daten.get("ehemalige", [])),
        "wochen_update": wu.get("datei", "") if wu.get("pruefung") == "bestanden" and wu.get("text") else "",
        "abteilungen": {_kennung(k): _status(v) for k, v in (signale.get("abteilungen") or {}).items()},
        "pruef_rot": int(signale.get("pruef_rot") or 0),
        "hash": _kennung(ohne_zeit),
    }


def _zustandsereignisse(daten: dict, signale: dict, heute: dt.date):
    """Ereignisse, die gelten, solange der Zustand besteht (Wiederholung frühestens nach 12 h)."""
    erg = []
    fehlt = signale.get("kern_fehlt") or []
    if fehlt:
        erg.append(("kernrolle_fehlt", 0, _kennung(sorted(fehlt))))
    sich = signale.get("sicherheit") or {}
    if sich.get("ampel") in ("rot", "gelb"):
        erg.append(("sicherheit_" + sich["ampel"], 0, _kennung(sich.get("kennung", ""))))

    wartend = []
    for b in daten.get("berater", []):
        d = _datum(b.get("lauf", ""))
        roh = (b.get("text", "") + " " + " ".join(b.get("details", []))).lower()
        if b.get("art") == "Vorschlag" and d and 3 < (heute - d).days <= 14 and not any(w in roh for w in ERLEDIGT):
            wartend.append(b.get("id", ""))
    if wartend:
        erg.append(("vorschlag_wartet", len(wartend), _kennung(sorted(wartend), heute.isoformat())))

    laeufe = [d for d in (_datum(b.get("lauf", "")) for b in daten.get("berater", [])) if d]
    if laeufe and (heute - max(laeufe)).days > 2:
        erg.append(("berater_ohne_lauf", 0, max(laeufe).isoformat()))
    ar = [d for d in (_datum(e.get("datum", "")) for e in daten.get("einsaetze", [])
                      if "aufsichtsrat" in (e.get("auftrag", "") + " " + e.get("mitarbeiter", "")).lower()) if d]
    if ar and (heute - max(ar)).days > 14:
        erg.append(("aufsichtsrat_ohne_lauf", 0, max(ar).isoformat()))
    return erg


def _aenderungen(alt: dict, neu: dict):
    """Ereignisse aus dem Vergleich mit dem letzten Stand. Liefert (sofort, sammel)."""
    sofort, sammel = [], {}
    if neu["pruef_rot"] > alt.get("pruef_rot", 0):
        sofort.append(("pruefzelle_rot", neu["pruef_rot"] - alt.get("pruef_rot", 0), str(neu["pruef_rot"])))
    neue_v = set(neu["vorschlaege"]) - set(alt.get("vorschlaege", []))
    if neue_v:
        sammel["neue_vorschlaege"] = len(neue_v)
    neue_e = set(neu["einsaetze"]) - set(alt.get("einsaetze", []))
    if neue_e:
        sammel["einsatz_erledigt"] = len(neue_e)
    if neu["wochen_update"] and neu["wochen_update"] != alt.get("wochen_update", ""):
        sammel["wochen_update"] = 1
    if set(neu["schlankheit"]) - set(alt.get("schlankheit", [])):
        sofort.append(("schlankheit_fertig", 0, _kennung(neu["schlankheit"])))
    if neu["personal"] != alt.get("personal", neu["personal"]) or neu["ehemalige"] > alt.get("ehemalige", neu["ehemalige"]):
        sofort.append(("personalwechsel", 0, _kennung(neu["personal"], neu["ehemalige"])))
    for name, status in neu["abteilungen"].items():
        vorher = alt.get("abteilungen", {}).get(name)
        if vorher is None or vorher == status:
            continue
        if status == "ruht":
            typ = "abteilung_ruht"
        elif status == "geschlossen":
            typ = "abteilung_geschlossen"
        elif status == "aktiv" and vorher in ("ruht", "geschlossen"):
            typ = "abteilung_wiedereroeffnet"
        else:
            continue
        sofort.append((typ, 0, _kennung(name, status)))
    return sofort, sammel


def _post(url: str, daten: dict) -> bool:
    anfrage = urllib.request.Request(url, data=json.dumps(daten, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(anfrage, timeout=TIMEOUT) as antwort:
            return 200 <= antwort.status < 300
    except Exception as fehler:  # Netzfehler: nicht abbrechen, beim nächsten Lauf erneut
        print(f"Melder: Senden fehlgeschlagen ({type(fehler).__name__})")
        return False


def melden(daten: dict, signale: dict, zustand_pfad: Path, jetzt: dt.datetime = None, url: str = None) -> list:
    """Vergleicht mit dem letzten Stand, sendet neue Meldungen und schreibt den Zustand. Gibt die gesendeten Payloads zurück."""
    jetzt = jetzt or _jetzt()
    url = (url if url is not None else os.environ.get("HA_WEBHOOK_URL", "")).strip()
    if url and urllib.parse.urlsplit(url).scheme.lower() not in ("http", "https"):
        print("Melder: HA_WEBHOOK_URL ist keine Web-Adresse, es wird nichts gesendet")
        url = ""
    try:
        zustand = json.loads(zustand_pfad.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        zustand = {}
    alt = zustand.get("stand")
    neu = stand_aus(daten, signale)
    gesendet = {k: v for k, v in zustand.get("gesendet", {}).items()
                if jetzt - dt.datetime.fromisoformat(v) < dt.timedelta(days=2)}
    offen = list(zustand.get("offen", []))  # fehlgeschlagene Sendungen: [typ, anzahl, kennung]
    sammel = dict(zustand.get("sammel", {}))

    # Sync steht: gleicher Datenstand seit über 24 h
    seit = zustand.get("hash_seit", jetzt.isoformat()) if alt and alt.get("hash") == neu["hash"] else jetzt.isoformat()
    ereignisse = _zustandsereignisse(daten, signale, jetzt.date())
    if jetzt - dt.datetime.fromisoformat(seit) > dt.timedelta(hours=24):
        ereignisse.append(("sync_steht", 0, neu["hash"]))
    if alt is None:  # erster Lauf: nur Ausgangsstand merken, Änderungen nicht melden (außer roten Prüf-Zellen-Funden)
        alt = {k: v for k, v in neu.items() if k != "pruef_rot"}
    sofort, neu_sammel = _aenderungen(alt, neu)
    ereignisse += sofort
    for k, n in neu_sammel.items():
        sammel[k] = sammel.get(k, 0) + n

    raus = []
    neu_offen = []
    for typ, anzahl, kennung in [tuple(o) for o in offen] + ereignisse:
        schluessel = f"{typ}:{kennung}"
        letzte = gesendet.get(schluessel)
        if letzte and jetzt - dt.datetime.fromisoformat(letzte) < ENTPRELLUNG:
            continue
        if not url:
            continue
        p = payload(typ, anzahl)
        if _post(url, p):
            gesendet[schluessel] = jetzt.isoformat()
            raus.append(p)
        elif [typ, anzahl, kennung] not in neu_offen:
            neu_offen.append([typ, anzahl, kennung])

    heute = jetzt.date().isoformat()
    if jetzt.hour >= SAMMEL_STUNDE and zustand.get("sammel_tag") != heute:
        if sammel and url:
            p = sammel_payload(sammel)
            if _post(url, p):
                raus.append(p)
                sammel, zustand["sammel_tag"] = {}, heute
        else:
            sammel, zustand["sammel_tag"] = {}, heute

    zustand.update({"stand": neu, "hash_seit": seit, "gesendet": gesendet, "offen": neu_offen[:20], "sammel": sammel})
    zustand_pfad.parent.mkdir(parents=True, exist_ok=True)
    tmp = zustand_pfad.with_suffix(".tmp")
    tmp.write_text(json.dumps(zustand, ensure_ascii=False), encoding="utf-8")
    tmp.replace(zustand_pfad)
    return raus
