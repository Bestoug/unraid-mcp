"""Tests für leitstand_daten.py (nur erfundene Daten).

Aufruf: python3 -m unittest discover -s leitstand-sync -p "test_*.py"
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import leitstand_daten  # noqa: E402

LAGEBILD = """---
erstellt: 2026-01-01
---
# Lagebild

**Stand:** 01.01.2026 · **Firma:** aktiv

## ⚠️ Sicherheit
- 🔴 Beispielfund in der **Werkstatt** → [[Beispielnotiz]]
- 🟡 Beispielhinweis
- Zeile ohne Stufe wird ignoriert

## ✋ Wartet auf dich
- Beispiel
"""

PROFIL = """# Chef-Profil

## Wie der Chef Ergebnisse will
- Immer **kurz** und gut scanbar

## Aus der Wochen-Übergabe (bestätigt)
- (noch nichts)

## Gelernt (zur Bestätigung)
- Vermutung, die nicht erscheinen darf

## Rückmeldungen von ihm
- 01.01.2026: Beispielrückmeldung
"""

EINGANG = """# Eingang

## Eingefügt
<!-- übergabe-start -->
- Text aus dem Update, der nie gelesen wird
<!-- übergabe-ende -->

## Übernommen
| Datum | Woche | Prüfung | Übernommen ins Chef-Profil |
|---|---|---|---|
| – | – | – | – |
| 04.01.2026 | 2026-01 | bestanden | Beispielvorliebe übernommen |
"""


class LeitstandDatenTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        (self.vault / "01 Firma").mkdir()
        (self.vault / "00 Eingang" / "Schleuse").mkdir(parents=True)
        self.ziel = self.vault / "aus" / "daten.json"

    def tearDown(self):
        self.tmp.cleanup()

    def laufen(self, lagebild=LAGEBILD, profil=PROFIL, eingang=EINGANG):
        for pfad, inhalt in (("01 Firma/Lagebild.md", lagebild), ("01 Firma/Chef-Profil.md", profil),
                             ("00 Eingang/Schleuse/Eingang.md", eingang)):
            if inhalt is not None:
                (self.vault / pfad).write_text(inhalt, encoding="utf-8")
        leitstand_daten.main(self.vault, self.ziel)
        roh = self.ziel.read_text(encoding="utf-8")
        return roh, json.loads(roh)

    def test_sicherheit_lage(self):
        _, d = self.laufen()
        self.assertEqual(d["sicherheit_lage"], [
            {"stufe": "rot", "text": "Beispielfund in der Werkstatt → Beispielnotiz"},
            {"stufe": "gelb", "text": "Beispielhinweis"},
        ])

    def test_sicherheit_keine(self):
        _, d = self.laufen(lagebild="## ⚠️ Sicherheit\n- keine\n")
        self.assertEqual(d["sicherheit_lage"], [])

    def test_gelernt(self):
        roh, d = self.laufen()
        self.assertEqual(d["gelernt"], [
            {"titel": "Rückmeldung", "text": "01.01.2026: Beispielrückmeldung"},
            {"titel": "So will der Chef Ergebnisse", "text": "Immer kurz und gut scanbar"},
            {"titel": "Wochen-Übergabe 2026-01", "text": "Beispielvorliebe übernommen"},
        ])
        self.assertNotIn("nie gelesen", roh)
        self.assertNotIn("Vermutung", roh)

    def test_ohne_dateien_leere_listen(self):
        _, d = self.laufen(lagebild=None, profil=None, eingang=None)
        self.assertEqual(d["sicherheit_lage"], [])
        self.assertEqual(d["gelernt"], [])


if __name__ == "__main__":
    unittest.main()
