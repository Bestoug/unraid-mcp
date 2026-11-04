import pytest

from unraid_mcp import unraid
from unraid_mcp.config import Settings

S = Settings(unraid_url="http://server/graphql", unraid_api_key="k", access_token="t" * 40)

CONT = {"docker": {"containers": [
    {"id": "abc:1", "names": ["/jellyfin"], "image": "jellyfin/jellyfin", "state": "RUNNING", "status": "Up", "autoStart": True},
    {"id": "abc:2", "names": ["/unraid-mcp"], "image": "ghcr.io/bestoug/unraid-mcp", "state": "RUNNING", "status": "Up", "autoStart": True},
    {"id": "abc:3", "names": ["/icloud-mcp"], "image": "ghcr.io/bestoug/icloud-mcp", "state": "EXITED", "status": "Exited", "autoStart": True},
]}}


@pytest.mark.parametrize("q", [
    "mutation { docker { stop(id: \"x\") } }",
    "  MUTATION{array{stop}}",
    "query { online } mutation { array { stop } }",
    "subscription { cpu { percentTotal } }",
    ",mutation { docker { stop(id: \"x\") } }",
    "query { online },mutation { docker { stop(id: \"x\") } }",
    "\ufeffmutation { docker { stop(id: \"x\") } }",
    "query { online }\tmutation{array{stop}}",
])
def test_mutationen_abgelehnt(q):
    assert not unraid.ist_nur_lesend(q)
    with pytest.raises(unraid.UnraidFehler):
        unraid.query(S, q)


@pytest.mark.parametrize("q", [
    "query { online }",
    "{ info { os { hostname } } }",
    'query { shares { name comment } } # mutation im Kommentar',
    'query { notifications { list(filter: {type: UNREAD}) { title } } }',
    'query { x(name: "mutation { y }") }',
])
def test_lesende_abfragen_erlaubt(q):
    assert unraid.ist_nur_lesend(q)


def fake_post(antworten, aufrufe):
    def _f(s, q, v=None):
        aufrufe.append((q, v))
        for key, val in antworten.items():
            if key in q:
                if isinstance(val, Exception):
                    raise val
                return val
        return {}
    return _f


def test_container_liste_und_suche(monkeypatch):
    monkeypatch.setattr(unraid, "_post", fake_post({"containers": CONT}, []))
    namen = [c["name"] for c in unraid.containers(S)]
    assert namen == ["jellyfin", "unraid-mcp", "icloud-mcp"]
    assert unraid.finde_container(S, "Jelly")["id"] == "abc:1"
    with pytest.raises(unraid.UnraidFehler, match="nicht eindeutig"):
        unraid.finde_container(S, "mcp")
    with pytest.raises(unraid.UnraidFehler, match="nicht gefunden"):
        unraid.finde_container(S, "plex")


def test_container_start(monkeypatch):
    aufrufe = []
    monkeypatch.setattr(unraid, "_post", fake_post({
        "containers": CONT,
        "start(id": {"docker": {"start": {"id": "abc:3", "state": "RUNNING", "status": "Up 1 s"}}},
    }, aufrufe))
    r = unraid.container_aktion(S, "icloud-mcp", "start")
    assert r == {"container": "icloud-mcp", "aktion": "start", "vorher": "EXITED",
                 "nachher": "RUNNING", "status": "Up 1 s"}
    assert aufrufe[-1][1] == {"id": "abc:3"}


def test_geschuetzter_container(monkeypatch):
    monkeypatch.setattr(unraid, "_post", fake_post({"containers": CONT}, []))
    with pytest.raises(unraid.UnraidFehler, match="geschützt"):
        unraid.container_aktion(S, "unraid-mcp", "stop")


def test_restart_fallback(monkeypatch):
    aufrufe = []
    monkeypatch.setattr(unraid, "_post", fake_post({
        "containers": CONT,
        "restart(id": unraid.UnraidFehler("GraphQL-Fehler: Cannot query field \"restart\""),
        "stop(id": {"docker": {"stop": {"state": "EXITED"}}},
        "start(id": {"docker": {"start": {"state": "RUNNING", "status": "Up"}}},
    }, aufrufe))
    r = unraid.container_aktion(S, "jellyfin", "restart")
    assert r["nachher"] == "RUNNING"
    assert [q.split("{")[2].split("(")[0].strip() for q, _ in aufrufe[1:]] == ["restart", "stop", "start"]


def test_nur_lesen_modus(monkeypatch):
    s = Settings(unraid_url="u", unraid_api_key="k", access_token="t" * 40, container_steuern=False)
    with pytest.raises(unraid.UnraidFehler, match="ausgeschaltet"):
        unraid.container_aktion(s, "jellyfin", "start")


def test_settings(monkeypatch):
    monkeypatch.setenv("MCP_ACCESS_TOKEN", "x" * 40)
    monkeypatch.setenv("UNRAID_URL", "http://192.168.2.113/")
    monkeypatch.setenv("UNRAID_API_KEY", "k")
    monkeypatch.setenv("GESCHUETZTE_CONTAINER", "Jellyfin, icloud-mcp")
    s = Settings.from_env()
    assert s.unraid_url == "http://192.168.2.113/graphql"
    assert s.geschuetzte_container == ("icloud-mcp", "jellyfin", "unraid-mcp")
    monkeypatch.setenv("MCP_ACCESS_TOKEN", "kurz")
    with pytest.raises(RuntimeError):
        Settings.from_env()
