import json
import xml.etree.ElementTree as ET

import pytest

from unraid_mcp import jellyfin, verwaltung as v
from unraid_mcp.config import Settings
from unraid_mcp.unraid import UnraidFehler


def S(tmp_path, **kw):
    (tmp_path / "vorlagen").mkdir(exist_ok=True)
    (tmp_path / "arbeit").mkdir(exist_ok=True)
    return Settings(unraid_url="http://x/graphql", unraid_api_key="k", access_token="t" * 40,
                    verwaltung=True, vorlagen_dir=str(tmp_path / "vorlagen"),
                    arbeit_dir=str(tmp_path / "arbeit"), private_freigaben=("dokumente",), **kw)


# ---------- Regeln ----------

@pytest.mark.parametrize("pfad", ["/boot/config", "/mnt/user", "/mnt/user/system/docker",
                                  "/mnt/user/appdata", "/mnt/user/appdata/icloud-mcp",
                                  "/var/run/docker.sock", "/mnt/disk1/daten", "/mnt/user/../../etc",
                                  "/mnt/user/dokumente/steuer"])
def test_gesperrte_ordner(tmp_path, pfad):
    with pytest.raises(UnraidFehler):
        v.pruefe_host_pfad(S(tmp_path), pfad, "jellyfin", freigabe=False)


@pytest.mark.parametrize("pfad", ["/mnt/user/appdata/jellyfin", "/mnt/user/appdata/Jellyfin/cache",
                                  "/mnt/user/daten/Filme", "/mnt/user/claude"])
def test_erlaubte_ordner(tmp_path, pfad):
    assert v.pruefe_host_pfad(S(tmp_path), pfad, "Jellyfin", freigabe=False)


def test_private_freigabe_nur_mit_freigabe(tmp_path):
    assert v.pruefe_host_pfad(S(tmp_path), "/mnt/user/dokumente", "x", freigabe=True)


@pytest.mark.parametrize("image,ok", [
    ("lscr.io/linuxserver/audiobookshelf:latest", True), ("jellyfin/jellyfin", True),
    ("nginx:alpine", True), ("docker.io/library/postgres:16", True), ("ghcr.io/bestoug/x", True),
    ("evil/miner:latest", False), ("ghcr.io/fremd/tool", False), ("docker.io/evil/x", False)])
def test_images(tmp_path, image, ok):
    assert v.image_bekannt(S(tmp_path), image) is ok


def test_ports_und_geraete():
    assert v.pruefe_ports([{"host": 13378, "container": 80}], {8096})[0]["protokoll"] == "tcp"
    for p in ({"host": 80, "container": 80}, {"host": 8096, "container": 8096},
              {"host": 9000, "container": 1, "protokoll": "sctp"}):
        with pytest.raises(UnraidFehler):
            v.pruefe_ports([p], {8096})
    assert v.pruefe_geraete(["/dev/dri"]) == ["/dev/dri"]
    with pytest.raises(UnraidFehler):
        v.pruefe_geraete(["/dev/sda"])


def test_tailscale_nur_mit_freigabe():
    with pytest.raises(UnraidFehler):
        v.pruefe_variable("TAILSCALE_USE_FUNNEL", False)
    v.pruefe_variable("TAILSCALE_USE_FUNNEL", True)
    v.pruefe_variable("PUID", False)


def test_ziel_im_container():
    for z in ("/", "/etc/x", "/proc", "data"):
        with pytest.raises(UnraidFehler):
            v.pruefe_ziel(z)
    assert v.pruefe_ziel("/data/music/") == "/data/music"


# ---------- Vorlagen ----------

def test_vorlage_neu_und_ergaenzen():
    xml = v.vorlage_neu("abs", "lscr.io/linuxserver/audiobookshelf", [{"host": 13378, "container": 80, "protokoll": "tcp"}],
                        [{"host": "/mnt/user/appdata/abs", "container": "/config", "nur_lesen": False}],
                        {"TZ": "Europe/Berlin", "API_KEY": "geheim"}, [], 80, "Hörbücher")
    root = ET.fromstring(xml)
    assert root.findtext("Name") == "abs" and root.findtext("Privileged") == "false"
    assert root.findtext("Network") == "bridge"
    masken = {c.get("Target"): c.get("Mask") for c in root.findall("Config")}
    assert masken["API_KEY"] == "true" and masken["TZ"] == "false"
    neu = ET.fromstring(v.vorlage_ergaenzen(xml, [], [{"host": "/mnt/user/daten/Hörbücher", "container": "/audiobooks",
                                                        "nur_lesen": True}], {"TZ": "UTC"}, []))
    pfade = {c.get("Target"): (c.text, c.get("Mode")) for c in neu.findall("Config") if c.get("Type") == "Path"}
    assert pfade["/audiobooks"] == ("/mnt/user/daten/Hörbücher", "ro")
    assert [c.text for c in neu.findall("Config") if c.get("Target") == "TZ"] == ["UTC"]


# ---------- Neu erstellen ----------

JELLYFIN = {
    "Id": "aaa111", "Name": "/Jellyfin", "Image": "sha256:alt", "State": {"Running": True},
    "Config": {"Image": "jellyfin/jellyfin:latest", "Hostname": "aaa111", "Env": ["TZ=Europe/Berlin"],
               "Labels": {"net.unraid.docker.managed": "dockerman"}, "ExposedPorts": {"8096/tcp": {}}},
    "HostConfig": {"Binds": ["/mnt/user/appdata/Jellyfin:/config:rw", "/mnt/user/daten/Filme:/data/movies:rw"],
                   "PortBindings": {"8096/tcp": [{"HostIp": "", "HostPort": "8096"}]},
                   "NetworkMode": "bridge", "Privileged": False},
    "Mounts": [{"Type": "bind", "Source": "/mnt/user/daten/Filme", "Destination": "/data/movies", "RW": True},
               {"Type": "volume", "Name": "9842abc", "Destination": "/cache", "RW": True}],
    "NetworkSettings": {"Networks": {"bridge": {}}},
}


def test_body_aus_inspect_behaelt_alles():
    body = v.body_aus_inspect(JELLYFIN, ordner=[{"host": "/mnt/user/daten/Musik", "container": "/data/music",
                                                 "nur_lesen": True}], variablen={"TZ": "UTC"})
    binds = body["HostConfig"]["Binds"]
    assert "9842abc:/cache" in binds                      # anonymes Volume bleibt erhalten
    assert "/mnt/user/daten/Musik:/data/music:ro" in binds
    assert "/mnt/user/appdata/Jellyfin:/config:rw" in binds
    assert body["Env"] == ["TZ=UTC"] and "Hostname" not in body
    assert body["HostConfig"]["PortBindings"]["8096/tcp"][0]["HostPort"] == "8096"
    with pytest.raises(UnraidFehler):
        v.body_aus_inspect(JELLYFIN, ordner=[{"host": "/mnt/user/x", "container": "/config", "nur_lesen": False}])


class FakeDocker:
    def __init__(self, start_fehler=False):
        self.calls, self.start_fehler, self.neu = [], start_fehler, False

    def alle(self):
        return [{"Names": ["/Jellyfin"], "Ports": [{"PublicPort": 8096}]}]

    def inspect(self, name):
        if name == "neu":
            return {"Id": "neu", "Name": "/Jellyfin", "State": {"Running": not self.start_fehler,
                                                                  "Status": "exited" if self.start_fehler else "running"}}
        return json.loads(json.dumps(JELLYFIN))

    def __getattr__(self, n):
        def f(*a, **k):
            self.calls.append((n, a))
            if n == "create":
                return "neu"
            if n == "start" and a[0] == "neu" and self.start_fehler:
                raise UnraidFehler("kaputt")
        return f


def test_aendern_ok(tmp_path, monkeypatch):
    s = S(tmp_path)
    (tmp_path / "vorlagen" / "my-Jellyfin.xml").write_text(
        '<?xml version="1.0"?><Container version="2"><Name>Jellyfin</Name></Container>')
    d = FakeDocker()
    monkeypatch.setattr(v, "_docker", lambda s: d)
    monkeypatch.setattr(v, "STABIL_SEKUNDEN", 0)
    r = v.aendern(s, "Jellyfin", [], [{"host": "/mnt/user/daten/Musik", "container": "/data/music",
                                       "nur_lesen": True}], {}, ["/dev/dri"], False)
    assert r["unraid_vorlage"] == "aktualisiert"
    namen = [c[0] for c in d.calls]
    assert namen == ["stop", "rename", "create", "start", "remove"]
    assert d.calls[-1][1][0] == "aaa111"                       # alter Container entfernt, nicht der neue
    xml = (tmp_path / "vorlagen" / "my-Jellyfin.xml").read_text()
    assert "/mnt/user/daten/Musik" in xml and "/dev/dri" in xml
    assert any(p.name.startswith("my-Jellyfin.xml.") for p in (tmp_path / "arbeit/protokoll/sicherung").iterdir())
    assert "TZ=" not in "".join(p.read_text() for p in (tmp_path / "arbeit/protokoll/sicherung").glob("*.json"))
    assert "geändert" in (tmp_path / "arbeit/protokoll/verwaltung.md").read_text()


def test_aendern_fehler_stellt_alten_zustand_her(tmp_path, monkeypatch):
    d = FakeDocker(start_fehler=True)
    monkeypatch.setattr(v, "_docker", lambda s: d)
    with pytest.raises(UnraidFehler, match="wiederhergestellt"):
        v.aendern(S(tmp_path), "Jellyfin", [], [], {"X": "1"}, [], False)
    namen = [(c[0], c[1][0]) for c in d.calls]
    assert ("remove", "neu") in namen and ("rename", "aaa111") in namen and namen[-1] == ("start", "aaa111")
    assert ("remove", "aaa111") not in namen


def test_selbst_und_ohne_verwaltung(tmp_path, monkeypatch):
    aus = Settings(unraid_url="http://x/graphql", unraid_api_key="k", access_token="t" * 40)
    with pytest.raises(UnraidFehler, match="nicht eingeschaltet"):
        v._docker(aus)
    d = FakeDocker()
    d.inspect = lambda n: {"Id": "x", "Name": "/unraid-mcp"}
    monkeypatch.setattr(v, "_docker", lambda s: d)
    with pytest.raises(UnraidFehler):
        v.aendern(S(tmp_path), "unraid-mcp", [], [], {"A": "1"}, [], False)
    with pytest.raises(UnraidFehler):
        v.aktualisieren(S(tmp_path), "unraid-mcp")


def test_anlegen_regeln(tmp_path, monkeypatch):
    d = FakeDocker()
    monkeypatch.setattr(v, "_docker", lambda s: d)
    monkeypatch.setattr(v, "STABIL_SEKUNDEN", 0)
    s = S(tmp_path)
    with pytest.raises(UnraidFehler, match="bekannten Quelle"):
        v.anlegen(s, "x", "evil/miner", [], [], {}, [], None, "", False)
    with pytest.raises(UnraidFehler, match="gibt es schon"):
        v.anlegen(s, "jellyfin", "jellyfin/jellyfin", [], [], {}, [], None, "", False)
    with pytest.raises(UnraidFehler):
        v.anlegen(s, "abs", "lscr.io/linuxserver/audiobookshelf", [], [
            {"host": "/mnt/user/appdata/Jellyfin", "container": "/config"}], {}, [], None, "", False)
    r = v.anlegen(s, "abs", "lscr.io/linuxserver/audiobookshelf", [{"host": 13378, "container": 80}],
                  [{"host": "/mnt/user/appdata/abs", "container": "/config"}], {"API_KEY": "geheim"}, [], 80, "", False)
    assert r["variablen"] == {"API_KEY": "***"} and (tmp_path / "vorlagen" / "my-abs.xml").exists()
    body = [c for c in d.calls if c[0] == "create"][0][1][1]
    assert body["HostConfig"]["Privileged"] is False and body["HostConfig"]["NetworkMode"] == "bridge"


# ---------- Jellyfin ----------

def test_host_zu_container():
    mounts = [{"Source": "/mnt/user/daten/Filme", "Destination": "/data/movies"},
              {"Source": "/mnt/user/daten", "Destination": "/media"}]
    assert jellyfin.host_zu_container(mounts, "/mnt/user/daten/Filme/Neu") == "/data/movies/Neu"
    assert jellyfin.host_zu_container(mounts, "/mnt/user/daten/Musik") == "/media/Musik"
    assert jellyfin.host_zu_container(mounts, "/mnt/user/andere") is None
