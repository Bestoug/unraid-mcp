"""Tests für privat_daten.py mit kleinen, künstlichen Beispieldateien (keine echten Daten).

Aufruf: python3 -m unittest discover -s privat-sync/tests
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import privat_daten  # noqa: E402

EINSATZBUCH = """---
erstellt: 2026-01-01
---
# Einsatzbuch

**Nächste Einsatz-Nr.: E-003**

## Einsätze
- E-002 · 02.01.2026 10:00 · Beispielauftrag mit <b>HTML</b> · 🟢 · keine Unteragenten · erledigt · Teil zwei · ja
- E-001 · 01.01.2026 09:00 · Testauftrag an rechner.tail0000.ts.net und 10.1.2.3 · 🔴 · Beispiel-Rolle · [[Beispielnotiz]] · nein
"""

BERATER = """# Berater-Vorschläge

- „B-001 ja“ → steht in der Anleitung und ist kein Eintrag

## Einträge
### 01.01.2026 morgens
- 💡 **B-002** Beispielvorschlag
  - warum jetzt: Beispielgrund
- ⏰ **B-001** Beispielfrist, Rückfrage an beispiel@icloud.com
"""

PERSONAL = """# Personal Bereitschaft

## Aktiv
| Rolle | Version | Ebene | kündbar | Einsätze | Abnahmequote | letzter Einsatz |
|---|---|---|---|---|---|---|
| [[Beispiel-Rolle]] | v2 | 1 | nein | 3 | 100 % | 01.01.2026 |

## Ehemalige
- Beispiel-Rolle v1 · abgelöst 01.01.2026
"""


def wochen_update(woche, pruefung, funde, text):
    return f"""---
erstellt: 2026-01-05
woche: {woche}
pruefung: {pruefung}
pruefung_funde: {funde}
geprueft: 2026-01-05 18:30
---
# Wochen-Update {woche}

Kopf, der nicht kopiert wird.

<!-- übergabe-start -->
{text}
<!-- übergabe-ende -->

Fuß, der nicht kopiert wird.
"""


class PrivatDatenTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name) / "vault"
        self.ordner = self.vault / "01 Projekte" / "Bereitschaft"
        (self.ordner / "Übergaben").mkdir(parents=True)
        (self.ordner / "Einsatzbuch.md").write_text(EINSATZBUCH, encoding="utf-8")
        (self.ordner / "Berater-Vorschläge.md").write_text(BERATER, encoding="utf-8")
        (self.ordner / "Personal Bereitschaft.md").write_text(PERSONAL, encoding="utf-8")
        (self.ordner / "Übergaben" / "Wochen-Update 2026-01.md").write_text(
            wochen_update("2026-01", "nicht bestanden", 2, "Alter Text"), encoding="utf-8")
        (self.ordner / "Übergaben" / "Wochen-Update 2026-02.md").write_text(
            wochen_update("2026-02", "bestanden", 0, "Zeile eins\n- **Punkt** zwei"), encoding="utf-8")
        (self.ordner / "Übergaben" / "Übergabe Beispiel.md").write_text(
            "---\nerstellt: 2026-01-03\n---\n# Titel\n\nText mit <script>alert(1)</script>\n", encoding="utf-8")
        # Notiz außerhalb des Ordners darf nie gelesen werden
        andere = self.vault / "01 Projekte" / "Andere"
        andere.mkdir()
        (andere / "Geheim.md").write_text("GEHEIMNIS-AUSSERHALB", encoding="utf-8")
        self.ziel = Path(self.tmp.name) / "aus" / "daten.json"

    def tearDown(self):
        self.tmp.cleanup()

    def laufen(self):
        privat_daten.main(self.vault, self.ziel)
        roh = self.ziel.read_text(encoding="utf-8")
        return roh, json.loads(roh)

    def test_einsatzbuch(self):
        _, d = self.laufen()
        self.assertEqual(d["naechste_nr"], "E-003")
        self.assertEqual([e["nr"] for e in d["einsaetze"]], ["E-002", "E-001"])
        e2 = d["einsaetze"][0]
        self.assertEqual(e2["triage"], "🟢")
        self.assertEqual(e2["ergebnis"], "erledigt · Teil zwei")
        self.assertEqual(e2["uebergeben"], "ja")
        self.assertEqual(d["einsaetze"][1]["ergebnis"], "Beispielnotiz")

    def test_berater(self):
        _, d = self.laufen()
        self.assertEqual([b["id"] for b in d["berater"]], ["B-002", "B-001"])
        self.assertEqual(d["berater"][0]["art"], "Vorschlag")
        self.assertEqual(d["berater"][0]["details"], ["warum jetzt: Beispielgrund"])
        self.assertEqual(d["berater"][0]["lauf"], "01.01.2026 morgens")
        self.assertEqual(d["berater"][1]["art"], "Frist")

    def test_personal(self):
        _, d = self.laufen()
        self.assertEqual(d["personal"], [{"rolle": "Beispiel-Rolle", "version": "v2", "ebene": "1",
                                          "kuendbar": "nein", "einsaetze": "3", "quote": "100 %",
                                          "letzter": "01.01.2026"}])
        self.assertEqual(d["ehemalige"], ["Beispiel-Rolle v1 · abgelöst 01.01.2026"])

    def test_wochen_update_neueste_datei_und_text(self):
        _, d = self.laufen()
        w = d["wochen_update"]
        self.assertEqual(w["woche"], "2026-02")
        self.assertEqual(w["pruefung"], "bestanden")
        self.assertEqual(w["pruefung_funde"], 0)
        self.assertEqual(w["geprueft"], "2026-01-05 18:30")
        self.assertEqual(w["text"], "Zeile eins\n- **Punkt** zwei")

    def test_wochen_update_fehlt(self):
        for p in (self.ordner / "Übergaben").glob("Wochen-Update *.md"):
            p.unlink()
        _, d = self.laufen()
        self.assertIsNone(d["wochen_update"])

    def test_uebergaben_ohne_wochen_update_und_maskiert(self):
        _, d = self.laufen()
        self.assertEqual([u["datei"] for u in d["uebergaben"]], ["Übergabe Beispiel"])
        self.assertIn("&lt;script&gt;", d["uebergaben"][0]["html"])
        self.assertNotIn("<script>", d["uebergaben"][0]["html"])

    def test_filter_und_nur_eigener_ordner(self):
        roh, _ = self.laufen()
        for verboten in ("icloud.com", "10.1.2.3", "ts.net", "GEHEIMNIS-AUSSERHALB", "Alter Text"):
            self.assertNotIn(verboten, roh)
        self.assertIn("[entfernt]", roh)

    @unittest.skipUnless(hasattr(os, "symlink"), "keine symbolischen Links")
    def test_symlink_wird_nicht_gelesen(self):
        link = self.ordner / "Übergaben" / "Verweis.md"
        link.symlink_to(self.vault / "01 Projekte" / "Andere" / "Geheim.md")
        roh, d = self.laufen()
        self.assertNotIn("GEHEIMNIS-AUSSERHALB", roh)
        self.assertNotIn("Verweis", [u["datei"] for u in d["uebergaben"]])


if __name__ == "__main__":
    unittest.main()
