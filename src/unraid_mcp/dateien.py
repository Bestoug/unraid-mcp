"""Dateizugriff, eingesperrt auf freigegebene Ordner.

Schreiben nur im Arbeitsordner, Lesen zusätzlich in den nur-lesend eingebundenen Ordnern.
Pfade, die per `..` oder Symlink aus dem Ordner herausführen, werden abgelehnt.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .config import Settings

MAX_LESEN = 512 * 1024      # 512 KB Text pro Lesezugriff
MAX_SCHREIBEN = 2 * 1024 * 1024
MAX_EINTRAEGE = 500


class DateiFehler(RuntimeError):
    pass


def _wurzeln(s: Settings, schreiben: bool) -> list[Path]:
    dirs = [s.arbeit_dir] + ([] if schreiben else list(s.lese_dirs))
    return [Path(d).resolve() for d in dirs]


def aufloesen(s: Settings, pfad: str, schreiben: bool = False) -> Path:
    """Relative Pfade gelten im Arbeitsordner; absolute müssen in einer Wurzel liegen."""
    roh = Path(pfad.strip() or ".")
    kandidat = roh if roh.is_absolute() else Path(s.arbeit_dir) / roh
    echt = kandidat.resolve()
    for w in _wurzeln(s, schreiben):
        if echt == w or w in echt.parents:
            return echt
    erlaubt = s.arbeit_dir if schreiben else ", ".join([s.arbeit_dir, *s.lese_dirs])
    raise DateiFehler(f"Pfad „{pfad}“ liegt außerhalb der freigegebenen Ordner ({erlaubt}).")


def auflisten(s: Settings, pfad: str = ".") -> dict:
    p = aufloesen(s, pfad)
    if not p.is_dir():
        raise DateiFehler(f"„{pfad}“ ist kein Ordner.")
    eintraege = []
    for i, e in enumerate(sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))):
        if i >= MAX_EINTRAEGE:
            break
        st = e.stat()
        eintraege.append({"name": e.name, "typ": "ordner" if e.is_dir() else "datei",
                          "bytes": None if e.is_dir() else st.st_size})
    return {"pfad": str(p), "eintraege": eintraege,
            "abgeschnitten": len(eintraege) >= MAX_EINTRAEGE}


def lesen(s: Settings, pfad: str) -> dict:
    p = aufloesen(s, pfad)
    if not p.is_file():
        raise DateiFehler(f"„{pfad}“ ist keine Datei.")
    with p.open("rb") as f:
        roh = f.read(MAX_LESEN + 1)
    if b"\x00" in roh[:8192]:
        raise DateiFehler("Binärdatei – wird nicht als Text gelesen.")
    return {"pfad": str(p), "inhalt": roh[:MAX_LESEN].decode("utf-8", errors="replace"),
            "abgeschnitten": len(roh) > MAX_LESEN}


def schreiben(s: Settings, pfad: str, inhalt: str, anhaengen: bool = False) -> dict:
    daten = inhalt.encode("utf-8")
    if len(daten) > MAX_SCHREIBEN:
        raise DateiFehler("Inhalt zu groß (max. 2 MB).")
    p = aufloesen(s, pfad, schreiben=True)
    if p.is_dir():
        raise DateiFehler(f"„{pfad}“ ist ein Ordner.")
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("ab" if anhaengen else "wb") as f:
        f.write(daten)
    return {"pfad": str(p), "bytes": len(daten), "angehaengt": anhaengen}


def ordner_anlegen(s: Settings, pfad: str) -> dict:
    p = aufloesen(s, pfad, schreiben=True)
    p.mkdir(parents=True, exist_ok=True)
    return {"pfad": str(p)}


def verschieben(s: Settings, von: str, nach: str) -> dict:
    a = aufloesen(s, von, schreiben=True)
    b = aufloesen(s, nach, schreiben=True)
    if not a.exists():
        raise DateiFehler(f"„{von}“ gibt es nicht.")
    if b.exists():
        raise DateiFehler(f"„{nach}“ gibt es schon – nichts wird überschrieben.")
    b.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(a), str(b))
    return {"von": str(a), "nach": str(b)}


def papierkorb(s: Settings, pfad: str) -> dict:
    """Statt zu löschen: in den Ordner `.papierkorb` im Arbeitsordner verschieben."""
    a = aufloesen(s, pfad, schreiben=True)
    wurzel = Path(s.arbeit_dir).resolve()
    if a == wurzel:
        raise DateiFehler("Der Arbeitsordner selbst kann nicht weggeräumt werden.")
    korb = wurzel / ".papierkorb"
    korb.mkdir(exist_ok=True)
    ziel = korb / a.name
    n = 1
    while ziel.exists():
        ziel = korb / f"{a.stem}-{n}{a.suffix}"
        n += 1
    shutil.move(str(a), str(ziel))
    return {"verschoben_nach": str(ziel)}


def speicherplatz(s: Settings) -> list[dict]:
    out = []
    for w in _wurzeln(s, False):
        if w.exists():
            u = shutil.disk_usage(w)
            out.append({"ordner": str(w), "frei_gb": round(u.free / 1e9, 1),
                        "gesamt_gb": round(u.total / 1e9, 1)})
    return out


def ist_lesend(s: Settings, pfad: str) -> bool:
    """Für Tests/Diagnose: liegt der Pfad nur in einem nur-lesend freigegebenen Ordner?"""
    try:
        aufloesen(s, pfad, schreiben=True)
        return False
    except DateiFehler:
        aufloesen(s, pfad)
        return True


__all__ = ["DateiFehler", "auflisten", "lesen", "schreiben", "ordner_anlegen",
           "verschieben", "papierkorb", "speicherplatz", "aufloesen", "ist_lesend"]
