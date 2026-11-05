#!/usr/bin/env python3
"""Privat-Daten: liest die Privatfirma (Ordner "01 Projekte/Bereitschaft") und schreibt daten.json für die App „Privatfirma“.

Ohne KI, ohne Netz, nur Standardbibliothek. Vorbild: leitstand_daten.py (Leitstand-Sync).
Liest NUR diese Dateien in "01 Projekte/Bereitschaft": Einsatzbuch.md, Berater-Vorschläge.md,
Personal Bereitschaft.md und den Ordner Übergaben/; vom Ordner Rollen/ nur, welche Dateien es gibt.
Für den Melder (melder.py) zusätzlich, ohne Text daraus auszugeben: die Ampel des obersten Eintrags in
"03 Wissen/Sicherheitsberichte.md" und die Anzahl 🔴 unter „Offene Funde“ in "01 Projekte/Prüf-Zelle/Befund-Buch.md".
Andere Notizen (z. B. das private Notizbuch) liest es nie, symbolische Links werden übersprungen. Alles Ausgegebene wird HTML-maskiert (Felder "html")
und auf private Angaben (Mailadressen, Heimnetz, ts.net-Namen, Wörter aus PRIVAT_FILTER) gefiltert.
Format: DATENFORMAT.md im Repository.

Aufruf: python3 -I privat_daten.py <vault-ordner> <ziel/daten.json>
"""
import datetime as dt
import hashlib
import html
import json
import os
import re
import sys
from pathlib import Path

# python3 -I nimmt den Skriptordner nicht in den Suchpfad auf; melder.py liegt daneben (im Image)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import melder  # noqa: E402

PRIVAT = [
    re.compile(r"[\w.+-]+@(icloud\.com|me\.com|mac\.com|bundeswehr\.org|privaterelay\.appleid\.com)", re.I),
    re.compile(r"\b(?:192\.168|10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b"),
    re.compile(r"\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b"),
    re.compile(r"[\w-]+\.tail[0-9a-f]+\.ts\.net", re.I),
]
# Weitere private Wörter (z. B. Straße, Postleitzahl) kommen nur aus der Umgebung, nie aus dem Code:
# PRIVAT_FILTER="Wort1,Wort2"
PRIVAT += [re.compile(re.escape(w.strip()) + r"\w*\.?\s*\d*", re.I)
           for w in os.environ.get("PRIVAT_FILTER", "").split(",") if w.strip()]

# Kernrollen der Privatfirma (dürfen nie fehlen): Bezeichnung → mögliche Dateinamen in Rollen/ (ohne .md,
# Groß-/Kleinschreibung egal). Eine Kernrolle gilt als vorhanden, wenn einer der Namen existiert.
KERNROLLEN = {
    "Einsatzleiter": ["Einsatzleiter"],
    "Assistent": ["Bereitschafts-Assistent"],
    "Personalchef": ["Bereitschafts-Personalchef"],
    "Controller": ["Bereitschafts-Controller"],
    "Sicherheit": ["Bereitschafts-Sicherheit"],
    "Aufsichtsrat": ["Bereitschafts-Aufsichtsrat"],
    "Qualitätsprüfung": ["Bereitschafts-Prüfer"],
    "Schlichtung": ["Bereitschafts-Schlichter"],
    "Finanzen und Recht": ["Bereitschafts-Finanzreferent"],
}
AMPEL = {"🔴": "rot", "🟡": "gelb", "🟢": "gruen"}

ARTEN = {"💡": "Vorschlag", "🔔": "Meldung", "⏰": "Frist", "🧪": "Ausprobieren", "⚠️": "Warnung"}


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
    aus, liste, tabelle_ = [], False, []

    def tab_ende():
        nonlocal tabelle_
        if tabelle_:
            zeilen = [z for z in tabelle_ if not re.match(r"^\|[\s:|-]+\|$", z)]
            h = "<table>"
            for i, z in enumerate(zeilen):
                zellen = [c.strip() for c in z.strip("|").split("|")]
                tag = "th" if i == 0 else "td"
                h += "<tr>" + "".join(f"<{tag}>{inline_html(c)}</{tag}>" for c in zellen) + "</tr>"
            aus.append(h + "</table>")
            tabelle_ = []

    for roh in md.splitlines():
        z = roh.rstrip()
        if z.startswith("|"):
            tabelle_.append(z)
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
        elif z.strip() and not z.strip().startswith("```") and not z.strip().startswith("<!--"):
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


def lies(p: Path) -> str:
    # Symbolische Links nie folgen: sie könnten aus dem Ordner herauszeigen
    if p.is_symlink() or not p.is_file():
        return ""
    return p.read_text(encoding="utf-8")


def einsaetze(t: str):
    """Zeilen „- E-Nr · Datum Uhrzeit · Auftrag · Triage · Mitarbeiter · Ergebnis · übergeben“."""
    erg = []
    for z in suche(abschnitte(t), "Einsätze"):
        if not re.match(r"^- E-\d+", z):
            continue
        p = [x.strip() for x in z[2:].split(" · ")]
        p += [""] * (7 - len(p))
        ergebnis = " · ".join(p[5:-1]) if len(p) > 7 else p[5]
        erg.append({"nr": text(p[0]), "datum": text(p[1]), "auftrag": text(p[2]), "triage": text(p[3]),
                    "mitarbeiter": text(p[4]), "ergebnis": text(ergebnis), "uebergeben": text(p[-1])})
    return erg


def berater(t: str):
    """Einträge mit ID B-xxx unter „## Einträge“; eingerückte Zeilen darunter sind Details."""
    erg, lauf = [], ""
    for z in suche(abschnitte(t), "Einträge"):
        if z.startswith("### "):
            lauf = text(z[4:])
            continue
        m = re.match(r"^- (.*\bB-\d{3,}\b.*)$", z)
        if m:
            inhalt = m.group(1)
            art = next((name for zeichen, name in ARTEN.items() if zeichen in inhalt), "")
            erg.append({"id": re.search(r"\bB-\d{3,}\b", inhalt).group(), "art": art,
                        "text": text(inhalt), "details": [], "lauf": lauf})
        elif erg and re.match(r"^\s+\S", z):
            erg[-1]["details"].append(text(re.sub(r"^\s*[-*]\s*", "", z)))
    return erg


def personal(t: str):
    teile = abschnitte(t)
    aktiv = [{"rolle": r.get("Rolle", ""), "version": r.get("Version", ""), "ebene": r.get("Ebene", ""),
              "kuendbar": r.get("kündbar", ""), "einsaetze": r.get("Einsätze", ""),
              "quote": r.get("Abnahmequote", ""), "letzter": r.get("letzter Einsatz", "")}
             for r in tabelle(suche(teile, "Aktiv"))]
    ehemalige = [text(z[2:]) for z in suche(teile, "Ehemalige") if z.startswith("- ")]
    return aktiv, ehemalige


def wochen_update(ordner: Path):
    """Neueste Datei „Wochen-Update JJJJ-WW.md“; Übergabe-Text zwischen den Markierungen, unverändert (nur gefiltert)."""
    dateien = sorted(p for p in ordner.glob("Wochen-Update *.md") if not p.is_symlink())
    if not dateien:
        return None
    p = dateien[-1]
    fm, rest = frontmatter(lies(p))
    m = re.search(r"<!-- übergabe-start -->\s*\n(.*?)\n?\s*<!-- übergabe-ende -->", rest, re.S)
    funde = re.match(r"^\d+$", fm.get("pruefung_funde", ""))
    return {"datei": p.stem, "woche": fm.get("woche", ""), "erstellt": fm.get("erstellt", ""),
            "pruefung": fm.get("pruefung", "").strip().lower(),
            "pruefung_funde": int(funde.group()) if funde else None,
            "geprueft": fm.get("geprueft", ""), "text": sauber(m.group(1).strip()) if m else ""}


def uebergaben(ordner: Path):
    erg = []
    for p in sorted(ordner.glob("*.md")):
        if p.is_symlink() or p.name.startswith(("Wochen-Update ", "Vorlage ")):
            continue
        fm, rest = frontmatter(lies(p))
        rest = re.sub(r"^# .*\n", "", rest.strip() + "\n", count=1)
        erg.append({"datei": p.stem, "erstellt": fm.get("erstellt", ""), "html": md_html(rest)})
    return sorted(erg, key=lambda u: (u["erstellt"], u["datei"]), reverse=True)


def signale(vault: Path, ordner: Path) -> dict:
    """Nur Zustände für den Melder, keine Texte: fehlende Kernrollen, Ampel, Anzahl roter Funde, Abteilungs-Status."""
    rollen = ordner / "Rollen"
    da = {p.stem.lower() for p in rollen.glob("*.md") if not p.is_symlink()} if rollen.is_dir() else set()
    fehlt = [k for k, namen in KERNROLLEN.items() if not any(n.lower() in da for n in namen)]

    ampel, kennung = "", ""
    for z in lies(vault / "03 Wissen" / "Sicherheitsberichte.md").splitlines():
        if z.startswith("## "):
            ampel = next((w for zeichen, w in AMPEL.items() if zeichen in z), "")
            kennung = hashlib.sha256(z.encode("utf-8")).hexdigest()[:16]
            break

    befunde = abschnitte(lies(vault / "01 Projekte" / "Prüf-Zelle" / "Befund-Buch.md"))
    rot = sum(z.count("🔴") for z in suche(befunde, "Offene Funde"))

    abt = {r.get("Abteilung", ""): r.get("Status", "").lower()
           for r in tabelle(suche(abschnitte(lies(ordner / "Personal Bereitschaft.md")), "Abteilungen"))}
    return {"kern_fehlt": fehlt, "sicherheit": {"ampel": ampel, "kennung": kennung}, "pruef_rot": rot,
            "abteilungen": {k: v for k, v in abt.items() if k}}


def main(vault: Path, ziel: Path):
    ordner = vault / "01 Projekte" / "Bereitschaft"
    buch = lies(ordner / "Einsatzbuch.md")
    nr = re.search(r"Nächste Einsatz-Nr\.:\*?\*?\s*(E-\d+)", buch)
    aktiv, ehemalige = personal(lies(ordner / "Personal Bereitschaft.md"))
    daten = {
        "erzeugt": dt.datetime.now().strftime("%d.%m.%Y %H:%M"),
        "einsaetze": einsaetze(buch),
        "naechste_nr": nr.group(1) if nr else "",
        "berater": berater(lies(ordner / "Berater-Vorschläge.md")),
        "personal": aktiv,
        "ehemalige": ehemalige,
        "uebergaben": uebergaben(ordner / "Übergaben"),
        "wochen_update": wochen_update(ordner / "Übergaben"),
    }
    roh = sauber(json.dumps(daten, ensure_ascii=False, separators=(",", ":")))
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text(roh, encoding="utf-8")
    # Melder: Zustand im Ausgabeordner, auf Wunsch woanders (MELDER_ZUSTAND)
    zustand = Path(os.environ.get("MELDER_ZUSTAND") or ziel.parent / "melder_zustand.json")
    try:
        gesendet = melder.melden(daten, signale(vault, ordner), zustand)
    except Exception as fehler:  # der Melder darf daten.json nie verhindern
        print(f"Melder-Fehler: {type(fehler).__name__}")
        gesendet = []
    print(f"OK: {len(daten['einsaetze'])} Einsätze, {len(daten['berater'])} Berater-Einträge, "
          f"{len(aktiv)} Rollen, {len(daten['uebergaben'])} Übergaben, {len(gesendet)} Meldungen → {ziel}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    main(Path(sys.argv[1]), Path(sys.argv[2]))
