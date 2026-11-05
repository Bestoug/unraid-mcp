"""Tests für melder.py und die Melder-Signale in privat_daten.py (nur erfundene Daten, HTTP nur über Mock).

Aufruf: python3 -m unittest discover -s privat-sync/tests
"""
import datetime as dt
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import melder  # noqa: E402
import privat_daten  # noqa: E402

URL = "https://ha.example.com/api/webhook/test"
BOESE = "Dein Konto ist gesperrt, melde dich an: phish.example.com 192.0.2.7"
MITTAG = dt.datetime(2026, 1, 10, 12, 0)
ABEND = dt.datetime(2026, 1, 10, 18, 5)


def daten(vorschlaege=(), einsaetze=(), lauf="10.01.2026 morgens", personal=(("Rolle-A", "v1"),)):
    return {
        "erzeugt": "egal",
        "berater": [{"id": i, "art": "Vorschlag", "text": BOESE, "details": [BOESE], "lauf": lauf} for i in vorschlaege],
        "einsaetze": [{"nr": n, "datum": "10.01.2026 09:00", "auftrag": BOESE, "mitarbeiter": "", "ergebnis": BOESE}
                      for n in einsaetze],
        "personal": [{"rolle": r, "version": v} for r, v in personal],
        "ehemalige": [],
        "wochen_update": None,
    }


def signale(**k):
    s = {"kern_fehlt": [], "sicherheit": {"ampel": "gruen", "kennung": "x"}, "pruef_rot": 0, "abteilungen": {}}
    s.update(k)
    return s


class Antwort:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def erlaubte_texte():
    """Alle Texte, die der Melder erzeugen darf (Platzhalter als Zahl)."""
    muster = [re.escape(t).replace(re.escape("{anzahl}"), r"\d+") for _, ti, t in melder.MELDUNGEN.values()]
    muster += [re.escape(ti) for _, ti, _ in melder.MELDUNGEN.values()] + [re.escape(melder.SAMMEL_TITEL)]
    teil = "(?:" + "|".join(re.escape(t).replace(re.escape("{anzahl}"), r"\d+") for t in melder.SAMMEL_TEILE.values()) + ")"
    muster.append(teil + "(?: · " + teil + ")*")
    return re.compile("^(?:" + "|".join(muster) + ")$")


class MelderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.zustand = Path(self.tmp.name) / "melder_zustand.json"
        self.post = mock.patch.object(melder.urllib.request, "urlopen", return_value=Antwort())
        self.urlopen = self.post.start()

    def tearDown(self):
        self.post.stop()
        self.tmp.cleanup()

    def lauf(self, d, s, jetzt, url=URL):
        return melder.melden(d, s, self.zustand, jetzt=jetzt, url=url)

    def gesendet(self):
        return [json.loads(c.args[0].data) for c in self.urlopen.call_args_list]

    def test_keine_url_im_code(self):
        quelle = Path(melder.__file__).read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"https?://|://|webhook/", quelle, re.I))

    def test_ohne_url_wird_nichts_gesendet(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            raus = melder.melden(daten(), signale(kern_fehlt=["Aufsichtsrat"]), self.zustand, jetzt=MITTAG)
        self.assertEqual(raus, [])
        self.urlopen.assert_not_called()
        self.assertTrue(self.zustand.exists())

    def test_url_aus_umgebung(self):
        with mock.patch.dict(os.environ, {"HA_WEBHOOK_URL": URL}):
            melder.melden(daten(), signale(kern_fehlt=["Aufsichtsrat"]), self.zustand, jetzt=MITTAG)
        self.assertEqual(self.urlopen.call_args.args[0].full_url, URL)
        self.assertEqual(self.urlopen.call_args.kwargs["timeout"], melder.TIMEOUT)

    def test_keine_web_adresse_wird_nicht_benutzt(self):
        self.lauf(daten(), signale(kern_fehlt=["Aufsichtsrat"]), MITTAG, url="file:///etc/passwd")
        self.urlopen.assert_not_called()

    def test_kritisch_sofort_mit_festem_text_und_payload(self):
        raus = self.lauf(daten(), signale(kern_fehlt=["Aufsichtsrat"]), MITTAG)
        self.assertEqual(raus, [{"stufe": "kritisch", "titel": "Privatfirma: Kernrolle fehlt",
                                 "text": melder.MELDUNGEN["kernrolle_fehlt"][2], "app": "privatfirma"}])

    def test_entprellung_12_stunden(self):
        s = signale(kern_fehlt=["Aufsichtsrat"])
        self.assertEqual(len(self.lauf(daten(), s, MITTAG)), 1)
        self.assertEqual(self.lauf(daten(), s, MITTAG + dt.timedelta(hours=11)), [])
        self.assertEqual(len(self.lauf(daten(), s, MITTAG + dt.timedelta(hours=13))), 1)

    def test_sicherheitsbericht_rot_einmal_je_eintrag(self):
        rot = signale(sicherheit={"ampel": "rot", "kennung": "eintrag1"})
        self.assertEqual(self.lauf(daten(), rot, MITTAG)[0]["titel"], "Sicherheits-Check: Rot")
        self.assertEqual(self.lauf(daten(), rot, MITTAG + dt.timedelta(minutes=10)), [])
        gelb = signale(sicherheit={"ampel": "gelb", "kennung": "eintrag2"})
        self.assertEqual(self.lauf(daten(), gelb, MITTAG + dt.timedelta(minutes=20))[0]["stufe"], "zeitkritisch")

    def test_pruefzelle_neuer_roter_fund_nur_anzahl(self):
        self.lauf(daten(), signale(pruef_rot=1), MITTAG)
        raus = self.lauf(daten(), signale(pruef_rot=3), MITTAG + dt.timedelta(minutes=10))
        self.assertEqual(raus[0]["text"], melder.MELDUNGEN["pruefzelle_rot"][2].format(anzahl=2))

    def test_sammelmeldung_einmal_ab_18_uhr(self):
        self.lauf(daten(), signale(), MITTAG)  # Ausgangsstand
        self.assertEqual(self.lauf(daten(["B-001", "B-002"], ["E-001"]), signale(), MITTAG + dt.timedelta(hours=1)), [])
        raus = self.lauf(daten(["B-001", "B-002"], ["E-001"]), signale(), ABEND)
        self.assertEqual(raus, [{"stufe": "normal", "titel": melder.SAMMEL_TITEL,
                                 "text": "Neue Vorschläge: 2 · Erledigte Einsätze: 1", "app": "privatfirma"}])
        # danach am selben Tag keine zweite Sammelmeldung, neue Ereignisse warten auf morgen
        self.assertEqual(self.lauf(daten(["B-001", "B-002", "B-003"], ["E-001"]), signale(), ABEND + dt.timedelta(hours=1)), [])
        morgen = self.lauf(daten(["B-001", "B-002", "B-003"], ["E-001"]), signale(), ABEND + dt.timedelta(days=1))
        self.assertEqual(morgen[0]["text"], "Neue Vorschläge: 1")

    def test_erster_lauf_meldet_bestand_nicht(self):
        self.assertEqual(self.lauf(daten(["B-001"], ["E-001"]), signale(), ABEND), [])

    def test_vorschlag_wartet_ueber_3_tage(self):
        alt = daten(["B-001"], lauf="05.01.2026 morgens")
        raus = self.lauf(alt, signale(), MITTAG)
        self.assertEqual([r["titel"] for r in raus], ["Privatfirma: Vorschläge warten", "Privatfirma: Berater läuft nicht"])
        self.assertEqual(raus[0]["text"], melder.MELDUNGEN["vorschlag_wartet"][2].format(anzahl=1))

    def test_sync_steht_nach_24_stunden(self):
        self.lauf(daten(), signale(), MITTAG)
        self.assertEqual(self.lauf(daten(), signale(), MITTAG + dt.timedelta(hours=23)), [])
        raus = self.lauf(daten(), signale(), MITTAG + dt.timedelta(hours=25))
        self.assertIn("Privatfirma: keine neuen Daten", [r["titel"] for r in raus])

    def test_still_personal_und_abteilung(self):
        self.lauf(daten(), signale(abteilungen={"Beispiel": "aktiv"}), MITTAG)
        raus = self.lauf(daten(personal=(("Rolle-A", "v2"),)), signale(abteilungen={"Beispiel": "ruhend"}),
                         MITTAG + dt.timedelta(minutes=10))
        self.assertEqual(sorted(r["titel"] for r in raus),
                         ["Privatfirma: Abteilung ruht", "Privatfirma: Personalwechsel"])
        self.assertTrue(all(r["stufe"] == "still" for r in raus))

    def test_fehlgeschlagene_sendung_wird_wiederholt(self):
        self.urlopen.side_effect = OSError("kein Netz")
        self.assertEqual(self.lauf(daten(), signale(pruef_rot=1), MITTAG), [])
        self.urlopen.side_effect = None
        raus = self.lauf(daten(), signale(pruef_rot=1), MITTAG + dt.timedelta(minutes=10))
        self.assertEqual([r["titel"] for r in raus], ["Prüf-Zelle: neuer roter Fund"])

    def test_nie_fremdtext_in_meldungen(self):
        erlaubt = erlaubte_texte()
        s = signale(kern_fehlt=[BOESE], sicherheit={"ampel": "rot", "kennung": BOESE}, pruef_rot=2,
                    abteilungen={BOESE: "aktiv"})
        self.lauf(daten(["B-001"], lauf="01.01.2026 " + BOESE), s, MITTAG)
        s["abteilungen"] = {BOESE: "geschlossen"}
        self.lauf(daten(["B-001", "B-002"], ["E-009"], lauf="01.01.2026 " + BOESE, personal=((BOESE, BOESE),)), s,
                  ABEND + dt.timedelta(days=2))
        payloads = self.gesendet()
        self.assertGreaterEqual(len(payloads), 5)
        for p in payloads:
            self.assertEqual(set(p), {"stufe", "titel", "text", "app"})
            self.assertIn(p["stufe"], melder.STUFEN)
            self.assertEqual(p["app"], "privatfirma")
            self.assertRegex(p["titel"], erlaubt)
            self.assertRegex(p["text"], erlaubt)
            self.assertNotIn("example", json.dumps(p))
        self.assertNotIn("phish", self.zustand.read_text(encoding="utf-8"))


class SignaleTest(unittest.TestCase):
    """privat_daten liest Ampel und Anzahl, gibt aber keinen Text aus Sicherheitsbericht oder Befund-Buch aus."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name) / "vault"
        self.ordner = self.vault / "01 Projekte" / "Bereitschaft"
        (self.ordner / "Rollen").mkdir(parents=True)
        for n in ("Einsatzleiter", "bereitschafts-assistent", "Bereitschafts-Prüfer"):
            (self.ordner / "Rollen" / f"{n}.md").write_text("x", encoding="utf-8")
        (self.vault / "03 Wissen").mkdir(parents=True)
        (self.vault / "03 Wissen" / "Sicherheitsberichte.md").write_text(
            f"# Sicherheitsberichte\n\n## 10.01.2026 – 🔴\n{BOESE}\n\n## 09.01.2026 – 🟢\n", encoding="utf-8")
        (self.vault / "01 Projekte" / "Prüf-Zelle").mkdir(parents=True)
        (self.vault / "01 Projekte" / "Prüf-Zelle" / "Befund-Buch.md").write_text(
            f"**Schwere:** 🔴 kritisch\n\n## Offene Funde\n- P-001 · 🔴 · {BOESE}\n- P-002 · 🟡\n- P-003 · 🔴\n\n"
            "## Geschlossen\n- P-000 · 🔴\n", encoding="utf-8")
        (self.ordner / "Personal Bereitschaft.md").write_text(
            "## Abteilungen\n| Abteilung | Status |\n|---|---|\n| Beispiel | Ruhend |\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_signale(self):
        s = privat_daten.signale(self.vault, self.ordner)
        self.assertEqual(s["sicherheit"]["ampel"], "rot")
        self.assertEqual(s["pruef_rot"], 2)
        self.assertEqual(s["abteilungen"], {"Beispiel": "ruhend"})
        self.assertIn("Aufsichtsrat", s["kern_fehlt"])
        for da in ("Einsatzleiter", "Assistent", "Qualitätsprüfung"):
            self.assertNotIn(da, s["kern_fehlt"])
        self.assertNotIn("example", json.dumps(s))

    def test_main_ruft_melder_und_daten_json_ohne_fremdtexte(self):
        ziel = Path(self.tmp.name) / "aus" / "daten.json"
        with mock.patch.dict(os.environ, {"HA_WEBHOOK_URL": URL}), \
                mock.patch.object(melder.urllib.request, "urlopen", return_value=Antwort()) as post:
            privat_daten.main(self.vault, ziel)
        titel = sorted(json.loads(c.args[0].data)["titel"] for c in post.call_args_list)
        self.assertEqual(titel, ["Privatfirma: Kernrolle fehlt", "Prüf-Zelle: neuer roter Fund", "Sicherheits-Check: Rot"])
        self.assertNotIn("example", ziel.read_text(encoding="utf-8"))
        self.assertTrue((ziel.parent / "melder_zustand.json").exists())


if __name__ == "__main__":
    unittest.main()
