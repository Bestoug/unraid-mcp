import os

import pytest

from unraid_mcp import dateien
from unraid_mcp.config import Settings


@pytest.fixture
def s(tmp_path):
    arbeit = tmp_path / "arbeit"
    daten = tmp_path / "daten"
    arbeit.mkdir()
    daten.mkdir()
    (daten / "film.txt").write_text("Interstellar")
    (tmp_path / "geheim.txt").write_text("nein")
    return Settings(unraid_url="u", unraid_api_key="k", access_token="t" * 40,
                    arbeit_dir=str(arbeit), lese_dirs=(str(daten),))


def test_schreiben_lesen_auflisten(s):
    dateien.schreiben(s, "projekt/notiz.md", "Hallo")
    dateien.schreiben(s, "projekt/notiz.md", " Kiel", anhaengen=True)
    assert dateien.lesen(s, "projekt/notiz.md")["inhalt"] == "Hallo Kiel"
    namen = [e["name"] for e in dateien.auflisten(s, ".")["eintraege"]]
    assert namen == ["projekt"]


def test_lese_ordner_nur_lesen(s):
    film = os.path.join(s.lese_dirs[0], "film.txt")
    assert dateien.lesen(s, film)["inhalt"] == "Interstellar"
    assert dateien.ist_lesend(s, film)
    with pytest.raises(dateien.DateiFehler):
        dateien.schreiben(s, film, "kaputt")
    with pytest.raises(dateien.DateiFehler):
        dateien.papierkorb(s, film)


@pytest.mark.parametrize("pfad", ["../geheim.txt", "/etc/passwd", "projekt/../../geheim.txt"])
def test_ausbruch_verhindert(s, pfad):
    with pytest.raises(dateien.DateiFehler):
        dateien.lesen(s, pfad)
    with pytest.raises(dateien.DateiFehler):
        dateien.schreiben(s, pfad, "x")


def test_symlink_ausbruch(s, tmp_path):
    os.symlink(tmp_path / "geheim.txt", os.path.join(s.arbeit_dir, "link.txt"))
    with pytest.raises(dateien.DateiFehler):
        dateien.lesen(s, "link.txt")


def test_verschieben_ueberschreibt_nicht(s):
    dateien.schreiben(s, "a.txt", "1")
    dateien.schreiben(s, "b.txt", "2")
    with pytest.raises(dateien.DateiFehler):
        dateien.verschieben(s, "a.txt", "b.txt")
    dateien.verschieben(s, "a.txt", "ordner/c.txt")
    assert dateien.lesen(s, "ordner/c.txt")["inhalt"] == "1"


def test_papierkorb(s):
    dateien.schreiben(s, "alt.txt", "1")
    dateien.papierkorb(s, "alt.txt")
    dateien.schreiben(s, "alt.txt", "2")
    r = dateien.papierkorb(s, "alt.txt")
    assert r["verschoben_nach"].endswith(".papierkorb/alt-1.txt")
    with pytest.raises(dateien.DateiFehler):
        dateien.papierkorb(s, ".")


def test_binaer_und_groesse(s):
    with open(os.path.join(s.arbeit_dir, "bild.bin"), "wb") as f:
        f.write(b"\x89PNG\x00\x00")
    with pytest.raises(dateien.DateiFehler, match="Binär"):
        dateien.lesen(s, "bild.bin")
    with pytest.raises(dateien.DateiFehler, match="zu groß"):
        dateien.schreiben(s, "gross.txt", "x" * (3 * 1024 * 1024))
