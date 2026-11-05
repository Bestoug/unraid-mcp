#!/usr/bin/env python3
"""Leitstand-Daten: liest den Firmenordner der Agenten-Firma und schreibt daten.json für den Leitstand.

Gehört zu [[Leitstand]]. Ohne KI, ohne Netz, nur Standardbibliothek.
Liest NUR "01 Projekte/Agenten-Firma" und vom Befund-Buch der Prüf-Zelle nur die Anzahl offener Funde.
Den Ordner "01 Projekte/Bereitschaft" liest es nie. Alles Ausgegebene wird HTML-maskiert und
auf private Angaben (Mailadressen, Heimnetz, Wohnadresse) gefiltert.

Aufruf: python3 -I leitstand_daten.py <vault-ordner> <ziel/daten.json>
Läuft automatisch im Container leitstand-sync auf Unraid (Image ghcr.io/bestoug/leitstand-sync).
"""
import datetime as dt
import html
import json
import os
import re
import sys
from pathlib import Path

PRIVAT = [
    re.compile(r"[\w.+-]+@(icloud\.com|me\.com|mac\.com|bundeswehr\.org|privaterelay\.appleid\.com)", re.I),
    re.compile(r"\b(?:192\.168|10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b"),
    re.compile(r"\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b"),
    re.compile(r"[\w-]+\.tail[0-9a-f]+\.ts\.net", re.I),
]
# Weitere private Wörter (z. B. Straße, Postleitzahl) kommen nur aus der Umgebung, nie aus dem Code:
# LEITSTAND_PRIVAT="Wort1,Wort2" – so bleibt der Code öffentlich teilbar, ohne private Angaben zu verraten.
PRIVAT += [re.compile(re.escape(w.strip()) + r"\w*\.?\s*\d*", re.I)
           for w in os.environ.get("LEITSTAND_PRIVAT", "").split(",") if w.strip()]


def sauber(t: str) -> str:
    for m in PRIVAT:
        t = m.sub("[entfernt]", t)
    return t


def ohne_links(t: str) -> str:
    t = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", t)
    t = re.sub(r"\[\[([^\]]+)\]\]", r"\1", t)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1", t)
    return sauber(t)


def text(t: str) -> str:
    """Klartext ohne Markdown-Zeichen."""
    t = ohne_links(t)
    return re.sub(r"[*`]", "", t).strip()


def inline_html(t: str) -> str:
    t = html.escape(ohne_links(t), quote=False)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"`(.+?)`", r"<code>\1</code>", t)
    t = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"<i>\1</i>", t)
    return t


def md_html(md: str) -> str:
    """Sehr kleines Markdown → HTML (Überschriften, Listen, Tabellen, Absätze). Alles maskiert."""
    aus, liste, tabelle = [], False, []

    def tab_ende():
        nonlocal tabelle
        if tabelle:
            zeilen = [z for z in tabelle if not re.match(r"^\|[\s:|-]+\|$", z)]
            h = "<table>"
            for i, z in enumerate(zeilen):
                zellen = [c.strip() for c in z.strip("|").split("|")]
                tag = "th" if i == 0 else "td"
                h += "<tr>" + "".join(f"<{tag}>{inline_html(c)}</{tag}>" for c in zellen) + "</tr>"
            aus.append(h + "</table>")
            tabelle = []

    for roh in md.splitlines():
        z = roh.rstrip()
        if z.startswith("|"):
            tabelle.append(z)
            continue
        tab_ende()
        if re.match(r"^\s*[-*] ", z) or re.match(r"^\s*\d+\. ", z):
            if not liste:
                aus.append("<ul>")
                liste = True
            inhalt = re.sub(r"^\s*(?:[-*]|\d+\.) (\[[ x]\] )?", "", z)
            aus.append(f"<li>{inline_html(inhalt)}</li>")
            continue
        if liste:
            aus.append("</ul>")
            liste = False
        if z.startswith("#"):
            stufe = min(len(z) - len(z.lstrip("#")) + 1, 4)
            aus.append(f"<h{stufe}>{inline_html(z.lstrip('#').strip())}</h{stufe}>")
        elif z.strip() and not z.strip().startswith("```"):
            aus.append(f"<p>{inline_html(z.strip())}</p>")
    tab_ende()
    if liste:
        aus.append("</ul>")
    return "\n".join(aus)


def frontmatter(t: str):
    fm, rest = {}, t
    if t.startswith("---"):
        teile = t.split("---", 2)
        if len(teile) == 3:
            for z in teile[1].splitlines():
                if ":" in z:
                    k, v = z.split(":", 1)
                    fm[k.strip()] = text(v.strip())
            rest = teile[2]
    return fm, rest


def abschnitte(t: str, stufe: str = "## "):
    erg, titel = {}, None
    for z in t.splitlines():
        if z.startswith(stufe):
            titel = z[len(stufe):].strip()
            erg[titel] = []
        elif titel is not None:
            erg[titel].append(z)
    return erg


def tabelle(zeilen):
    zeilen = [ohne_links(z) for z in zeilen if z.strip().startswith("|")]
    if len(zeilen) < 2:
        return []
    kopf = [text(c) for c in zeilen[0].strip().strip("|").split("|")]
    daten = []
    for z in zeilen[2:]:
        zellen = [text(c) for c in z.strip().strip("|").split("|")]
        daten.append(dict(zip(kopf, zellen)))
    return daten


def suche(abschn: dict, wort: str):
    for k, v in abschn.items():
        if wort.lower() in k.lower():
            return v
    return []


def zahl(s: str) -> float:
    s = s.replace(".", "").replace(",", ".")
    m = re.search(r"-?\d+(?:\.\d+)?", s.replace("−", "-"))
    return float(m.group()) if m else 0.0


def auftraege(t: str):
    erg = []
    for name, zeilen in abschnitte(t).items():
        spalte = re.sub(r"^[^\wÄÖÜäöü]+", "", name).strip()
        for z in zeilen:
            m = re.match(r"^- (?:\[[ x]\] )?\*\*([AV]-\d+(?:\.\d+)?)\*\*(.*)$", z)
            if not m:
                continue
            rest = m.group(2)
            tm = re.search(r"\*\*(.+?)\*\*\s*[–-]?\s*(.*)", rest)
            titel = text(tm.group(1)) if tm else ""
            beschr = text(tm.group(2)) if tm else text(rest)
            meta = text(rest[: tm.start()] if tm else "")
            felder = [f.strip() for f in meta.split("·") if f.strip()]
            vm = re.search(r"Vorstellung: \[\[([^\]|]+)", rest)
            erg.append({
                "id": m.group(1), "spalte": spalte, "titel": titel,
                "beschreibung": beschr[:320], "meta": felder,
                "von": next((f[4:].strip() for f in felder if f.startswith("von:")), ""),
                "prio": next((f[5:].strip() for f in felder if f.startswith("Prio:")), ""),
                "vorstellung": vm.group(1) if vm else "",
            })
    return erg


def schichten(t: str):
    erg = []
    for name, zeilen in abschnitte(t).items():
        inhalt = "\n".join(zeilen)
        sm = re.search(r"Subagent-Starts:\s*(\d+)", inhalt)
        wm = re.search(r"Woche:\s*(\d+)\s*von\s*(\d+)", inhalt)
        qm = re.search(r"Abnahmequote der Schicht:\*?\*?\s*(\d+)\s*/\s*(\d+)", inhalt)
        dm = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", text(name))
        erg.append({
            "titel": text(name),
            "datum": f"{dm.group(3)}-{dm.group(2)}-{dm.group(1)}" if dm else "",
            "starts": int(sm.group(1)) if sm else 0,
            "woche": int(wm.group(1)) if wm else None,
            "woche_max": int(wm.group(2)) if wm else 30,
            "abgenommen": [int(qm.group(1)), int(qm.group(2))] if qm else None,
            "punkte": [text(re.sub(r"^- ", "", z)) for z in zeilen if z.startswith("- ")][:8],
        })
    return erg


def main(vault: Path, ziel: Path):
    firma = vault / "01 Projekte" / "Agenten-Firma"
    lies = lambda p: (p.read_text(encoding="utf-8") if p.exists() else "")

    personal = abschnitte(lies(firma / "Personal.md"))
    aktive = tabelle(suche(personal, "Aktive Belegschaft"))
    steck = {s.get("Rolle", ""): s for s in tabelle(suche(personal, "Steckbriefe"))}
    team = []
    for a in aktive:
        rolle = re.sub(r"\s*\(.*\)$", "", a.get("Rolle", "")).strip()
        s = steck.get(rolle, {})
        team.append({
            "agent": a.get("Agent", ""), "rolle": rolle, "ebene": a.get("Ebene", ""),
            "status": a.get("Status", ""), "eingestellt": a.get("Eingestellt", ""),
            "erledigt": a.get("Erledigt", ""), "quote": a.get("Abnahmequote", ""),
            "note": a.get("Note", ""), "letzter": a.get("Letzter Einsatz", ""),
            "name": s.get("Name", rolle), "kuerzel": s.get("Kürzel", rolle[:2].upper()),
            "abteilung": s.get("Abteilung", "Sonstige"), "staerken": s.get("Stärken", ""),
            "eigenart": s.get("Eigenart", ""),
        })

    lage_t = lies(firma / "Lagebild.md")
    stand = re.search(r"\*\*Stand:\*\*(.+)", lage_t)
    lage = {k: [text(re.sub(r"^- ", "", z)) for z in v if z.startswith("- ")]
            for k, v in abschnitte(lage_t).items()}

    vorst = []
    for p in sorted((firma / "Vorstellungen").glob("*.md")):
        fm, rest = frontmatter(lies(p))
        rest = re.sub(r"^# .*\n", "", rest.strip() + "\n", count=1)
        vorst.append({"datei": p.stem, "fm": fm, "html": md_html(rest)})

    ergebnisse = []
    for p in sorted((firma / "Ergebnisse").glob("*.md")):
        fm, rest = frontmatter(lies(p))
        va = suche(abschnitte(rest), "Verstanden als")
        ergebnisse.append({"titel": p.stem, "erstellt": fm.get("erstellt", ""),
                           "kurz": text(" ".join(z.lstrip("- ") for z in va if z.strip()))[:400],
                           "gliederung": [text(z.lstrip("#")) for z in rest.splitlines() if re.match(r"^#{2,3} ", z)][:30]})

    fin = abschnitte(lies(firma / "Finanzen" / "Finanzübersicht.md"))
    summen = {r.get("Posten", ""): zahl(r.get("Betrag", "0")) for r in tabelle(suche(fin, "Summen"))}
    grenzen = tabelle(suche(fin, "Grenzwerte"))
    fristen = tabelle(lies(firma / "Finanzen" / "Fristenkalender.md").splitlines())

    ziele = []
    for z in suche(abschnitte(lies(firma / "Ziele.md")), "Langfristig"):
        m = re.match(r"^\d+\.\s+\*\*(.+?)\*\*\s*(.*)", z)
        if m:
            ziele.append({"titel": text(m.group(1)).rstrip(":"), "text": text(m.group(2)).split("→")[0].strip(" :")})

    befund = abschnitte(lies(vault / "01 Projekte" / "Prüf-Zelle" / "Befund-Buch.md"))
    offen = "\n".join(suche(befund, "Offene Funde"))
    sicherheit = {"rot": offen.count("🔴"), "gelb": offen.count("🟡"), "gruen": offen.count("🟢"),
                  "laeufe": len([z for z in suche(befund, "Läufe") if re.match(r"^- \d", z)])}

    abteilungen = tabelle(suche(personal, "Abteilungen"))
    stamm = tabelle(suche(personal, "Stammbelegschaft"))
    ehemalige = tabelle(suche(personal, "Ehemalige"))
    auftr = auftraege(lies(firma / "Auftragsbuch.md"))
    sch = schichten(lies(firma / "Leistungsberichte.md"))

    heute = dt.date.today()
    kw_jetzt = heute.isocalendar()[:2]
    wochen = {}
    for x in sch:
        if not x["datum"]:
            continue
        d = dt.date.fromisoformat(x["datum"])
        kw = d.isocalendar()[:2]
        w = wochen.setdefault(kw, {"kw": f"KW {kw[1]}", "starts": 0, "abgenommen": 0, "geprueft": 0, "schichten": 0})
        w["starts"] += x["starts"]
        w["schichten"] += 1 if x["starts"] else 0
        if x["abgenommen"]:
            w["abgenommen"] += x["abgenommen"][0]
            w["geprueft"] += x["abgenommen"][1]
    woche = wochen.get(kw_jetzt, {}).get("starts", 0)
    wochenvergleich = [wochen[k] for k in sorted(wochen)][-8:]

    # Fristen mit Datum für den Zeitstrahl
    for f in fristen:
        fm_ = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", f.get("Wann", ""))
        if fm_:
            d = dt.date(int(fm_.group(3)), int(fm_.group(2)), int(fm_.group(1)))
            f["datum"] = d.isoformat()
            f["tage"] = (d - heute).days

    # Verkäufe aus der Kennzahlen-Brücke (nur geprüfte Zahlen), falls vorhanden
    verkauf = []
    kz = lies(firma / "Kennzahlen.md")
    for abschn in abschnitte(kz).values():
        for r in tabelle(abschn):
            monat = r.get("Monat") or r.get("Zeitraum") or ""
            if monat and ("Verkäufe" in r or "Umsatz" in r):
                verkauf.append({"monat": monat, "verkaeufe": zahl(r.get("Verkäufe", "0")),
                                "umsatz": zahl(r.get("Umsatz", "0"))})
    daten = {
        "erzeugt": dt.datetime.now().strftime("%d.%m.%Y %H:%M"),
        "stand": text(stand.group(1)) if stand else "",
        "lage": lage, "team": team, "abteilungen": abteilungen, "stamm": stamm,
        "ehemalige": ehemalige, "auftraege": auftr, "vorstellungen": vorst,
        "ergebnisse": ergebnisse, "schichten": sch, "ziele": ziele,
        "finanzen": {"summen": summen, "grenzen": grenzen, "fristen": fristen},
        "sicherheit": sicherheit,
        "kontingent": {"woche": woche, "woche_max": 30, "schicht_max": 8},
        "wochen": wochenvergleich, "verkauf": verkauf[-12:],
    }
    roh = json.dumps(daten, ensure_ascii=False, separators=(",", ":"))
    roh = sauber(roh)
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text(roh, encoding="utf-8")
    print(f"OK: {len(team)} Mitarbeiter, {len(auftr)} Aufträge/Vorschläge, {len(vorst)} Vorstellungen → {ziel}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    main(Path(sys.argv[1]), Path(sys.argv[2]))
