"""Jellyfin-Bibliotheken verwalten (über die Jellyfin-API mit eigenem API-Schlüssel)."""
from __future__ import annotations

import json
import posixpath
import urllib.error
import urllib.parse
import urllib.request

from .config import Settings
from .unraid import UnraidFehler

TYPEN = {"movies": "Filme", "tvshows": "Serien", "music": "Musik", "books": "Bücher/Hörbücher",
         "homevideos": "Videos und Fotos", "musicvideos": "Musikvideos", "mixed": "Gemischt"}


def _req(s: Settings, methode: str, pfad: str, body=None):
    if not (s.jellyfin_url and s.jellyfin_api_key):
        raise UnraidFehler("Jellyfin ist nicht eingerichtet (JELLYFIN_URL und JELLYFIN_API_KEY fehlen).")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        s.jellyfin_url.rstrip("/") + pfad, data=data, method=methode,
        headers={"X-Emby-Token": s.jellyfin_api_key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=s.timeout) as r:
            roh = r.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise UnraidFehler("Jellyfin lehnt den API-Schlüssel ab.") from e
        raise UnraidFehler(f"Jellyfin antwortet mit HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise UnraidFehler(f"Jellyfin nicht erreichbar: {e.reason}") from e
    return json.loads(roh) if roh.strip() else {}


def bibliotheken(s: Settings) -> list[dict]:
    return [{"name": b.get("Name"), "typ": b.get("CollectionType") or "mixed",
             "ordner_im_container": b.get("Locations")} for b in _req(s, "GET", "/Library/VirtualFolders")]


def host_zu_container(mounts: list[dict], host_pfad: str) -> str | None:
    """Übersetzt /mnt/user/daten/Filme in den Pfad, den Jellyfin im Container sieht."""
    h = posixpath.normpath(host_pfad)
    for m in sorted(mounts, key=lambda m: -len(m.get("Source") or "")):
        quelle = posixpath.normpath(m.get("Source") or "")
        if quelle and (h == quelle or h.startswith(quelle + "/")):
            return posixpath.normpath(m["Destination"] + h[len(quelle):])
    return None


def bibliothek_anlegen(s: Settings, name: str, typ: str, ordner_im_container: str) -> dict:
    if typ not in TYPEN:
        raise UnraidFehler(f"Typ „{typ}“ unbekannt; erlaubt: {', '.join(TYPEN)}")
    if any((b.get("name") or "").lower() == name.lower() for b in bibliotheken(s)):
        raise UnraidFehler(f"Bibliothek „{name}“ gibt es schon.")
    q = urllib.parse.urlencode({"name": name, "collectionType": typ, "refreshLibrary": "true"})
    _req(s, "POST", f"/Library/VirtualFolders?{q}", {"LibraryOptions": {
        "PathInfos": [{"Path": ordner_im_container}], "EnableRealtimeMonitor": True,
        "PreferredMetadataLanguage": "de", "MetadataCountryCode": "DE"}})
    return {"bibliothek": name, "typ": TYPEN[typ], "ordner": ordner_im_container,
            "hinweis": "Neue Dateien in diesem Ordner erkennt Jellyfin automatisch (Echtzeit-Überwachung)."}


def scannen(s: Settings) -> dict:
    _req(s, "POST", "/Library/Refresh")
    return {"ergebnis": "Scan aller Bibliotheken gestartet"}
