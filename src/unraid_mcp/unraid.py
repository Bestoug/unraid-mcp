"""Schlanker Client für die offizielle Unraid-GraphQL-API (ab Unraid 7.2 eingebaut).

Nur Standardbibliothek, damit das Image klein bleibt. Lesende Abfragen laufen über
`query`, Änderungen nur über die fest verdrahteten Funktionen unten.
"""
from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.request

from .config import Settings


class UnraidFehler(RuntimeError):
    pass


def _post(s: Settings, query: str, variables: dict | None = None) -> dict:
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        s.unraid_url, data=body, method="POST",
        headers={"Content-Type": "application/json", "x-api-key": s.unraid_api_key},
    )
    ctx = None if s.verify_ssl else ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(req, timeout=s.timeout, context=ctx) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise UnraidFehler("Unraid lehnt den API-Schlüssel ab (401/403). Schlüssel und Rechte prüfen.") from e
        raise UnraidFehler(f"Unraid-API antwortet mit HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise UnraidFehler(f"Unraid-API nicht erreichbar: {e.reason}") from e
    if data.get("errors"):
        msgs = "; ".join(str(err.get("message", err)) for err in data["errors"])
        raise UnraidFehler(f"GraphQL-Fehler: {msgs}")
    return data.get("data") or {}


_MUTATION = re.compile(r"^\s*(mutation|subscription)\b", re.I)


def ist_nur_lesend(query: str) -> bool:
    """True, wenn die Abfrage keine Mutation/Subscription enthält (auch nicht als zweite Operation)."""
    ohne_strings = re.sub(r'"(?:\\.|[^"\\])*"', '""', query)
    ohne_kommentare = re.sub(r"#[^\n]*", "", ohne_strings)
    return not re.search(r"(^|[}\s])(mutation|subscription)\b", ohne_kommentare, re.I) \
        and not _MUTATION.match(ohne_kommentare)


def query(s: Settings, q: str, variables: dict | None = None) -> dict:
    if not ist_nur_lesend(q):
        raise UnraidFehler("Nur lesende Abfragen erlaubt; Änderungen nur über die eigenen Werkzeuge.")
    return _post(s, q, variables)


# ---------- feste Abfragen ----------

SYSTEM = """query {
  info {
    os { hostname uptime release }
    cpu { brand cores threads }
    versions { core { unraid api } }
  }
  metrics {
    memory { total used available percentTotal }
    cpu { percentTotal }
  }
}"""

ARRAY = """query {
  array {
    state
    capacity { kilobytes { free used total } }
    parities { name size status temp numErrors }
    disks { name size status temp numErrors fsFree fsUsed }
    caches { name size status temp fsFree fsUsed }
  }
}"""

CONTAINERS = """query {
  docker { containers { id names image state status autoStart } }
}"""

SHARES = """query { shares { name free used comment } }"""

NOTIFICATIONS = """query {
  notifications { list(filter: {type: UNREAD, offset: 0, limit: 50}) {
    id title subject description importance timestamp } }
}"""


def _vereinfachen(c: dict) -> dict:
    names = c.get("names") or []
    name = (names[0] if names else c.get("id", "")).lstrip("/")
    return {"id": c.get("id"), "name": name, "image": c.get("image"), "zustand": c.get("state"),
            "status": c.get("status"), "autostart": c.get("autoStart")}


def containers(s: Settings) -> list[dict]:
    data = _post(s, CONTAINERS)
    return [_vereinfachen(c) for c in (data.get("docker") or {}).get("containers") or []]


def finde_container(s: Settings, name_oder_id: str) -> dict:
    ziel = name_oder_id.strip().lstrip("/").lower()
    alle = containers(s)
    for c in alle:
        if (c["id"] or "").lower() == ziel or c["name"].lower() == ziel:
            return c
    treffer = [c for c in alle if ziel in c["name"].lower()]
    if len(treffer) == 1:
        return treffer[0]
    namen = ", ".join(sorted(c["name"] for c in alle))
    if treffer:
        raise UnraidFehler(f"„{name_oder_id}“ ist nicht eindeutig: {', '.join(c['name'] for c in treffer)}")
    raise UnraidFehler(f"Container „{name_oder_id}“ nicht gefunden. Vorhanden: {namen}")


AKTIONEN = {"start", "stop", "restart"}


def container_aktion(s: Settings, name_oder_id: str, aktion: str) -> dict:
    if not s.container_steuern:
        raise UnraidFehler("Container-Steuerung ist ausgeschaltet (CONTAINER_NUR_LESEN=1).")
    if aktion not in AKTIONEN:
        raise UnraidFehler(f"Unbekannte Aktion „{aktion}“; erlaubt: start, stop, restart")
    c = finde_container(s, name_oder_id)
    if aktion in ("stop", "restart") and c["name"].lower() in s.geschuetzte_container:
        raise UnraidFehler(f"„{c['name']}“ ist geschützt und wird nicht angehalten.")
    neu = _mutation(s, aktion, c["id"])
    return {"container": c["name"], "aktion": aktion, "vorher": c["zustand"],
            "nachher": neu.get("state"), "status": neu.get("status")}


def _mutation(s: Settings, aktion: str, cid: str) -> dict:
    m = f"mutation($id: PrefixedID!) {{ docker {{ {aktion}(id: $id) {{ id names state status }} }} }}"
    try:
        data = _post(s, m, {"id": cid})
    except UnraidFehler as e:
        # Ältere API-Versionen kennen kein natives restart: dann stop + start.
        if aktion == "restart" and "restart" in str(e).lower():
            _mutation(s, "stop", cid)
            return _mutation(s, "start", cid)
        raise
    return (data.get("docker") or {}).get(aktion) or {}
