"""Konfiguration aus Umgebungsvariablen. Geheimnisse kommen nie aus dem Code."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _req(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Umgebungsvariable {name} fehlt")
    return value


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "ja")


def _liste(name: str) -> tuple[str, ...]:
    return tuple(x.strip() for x in os.environ.get(name, "").split(",") if x.strip())


@dataclass(frozen=True)
class Settings:
    unraid_url: str               # z. B. http://192.168.2.113/graphql
    unraid_api_key: str           # API-Schlüssel aus Unraid (Einstellungen → Management Access → API Keys)
    access_token: str             # geheimer Bearer-Token für Claude
    arbeit_dir: str = "/arbeit"   # Claudes Arbeitsordner (lesen + schreiben)
    lese_dirs: tuple[str, ...] = ("/daten",)   # nur lesend eingebundene Ordner
    container_steuern: bool = True             # Start/Stopp/Neustart von Containern erlauben
    geschuetzte_container: tuple[str, ...] = ("unraid-mcp",)  # nie anhalten (sonst sägt Claude den Ast ab)
    verwaltung: bool = False                   # Container anlegen/ändern (braucht Docker-Socket)
    docker_socket: str = "/var/run/docker.sock"
    vorlagen_dir: str = "/vorlagen"            # = /boot/config/plugins/dockerMan/templates-user
    erlaubte_images: tuple[str, ...] = (
        "lscr.io/linuxserver/", "linuxserver/", "jellyfin/", "ghcr.io/jellyfin/", "ghcr.io/bestoug/",
        "library/")
    erlaubte_freigaben: tuple[str, ...] = ("daten", "appdata", "claude")  # andere nur mit Freigabe
    jellyfin_url: str = ""
    jellyfin_api_key: str = ""
    jellyfin_container: str = "Jellyfin"
    verify_ssl: bool = True
    timeout: float = 20.0
    host: str = "0.0.0.0"
    port: int = 8000

    @classmethod
    def from_env(cls) -> "Settings":
        token = _req("MCP_ACCESS_TOKEN")
        if len(token) < 32:
            raise RuntimeError("MCP_ACCESS_TOKEN muss mindestens 32 Zeichen lang sein")
        url = _req("UNRAID_URL").rstrip("/")
        if not url.endswith("/graphql"):
            url += "/graphql"
        lese = tuple(p.strip() for p in os.environ.get("LESE_ORDNER", "/daten").split(",") if p.strip())
        geschuetzt = {"unraid-mcp"} | {
            n.strip().lower() for n in os.environ.get("GESCHUETZTE_CONTAINER", "").split(",") if n.strip()
        }
        return cls(
            unraid_url=url,
            unraid_api_key=_req("UNRAID_API_KEY"),
            access_token=token,
            arbeit_dir=os.environ.get("ARBEIT_ORDNER", "/arbeit"),
            lese_dirs=lese,
            container_steuern=not _flag("CONTAINER_NUR_LESEN"),
            geschuetzte_container=tuple(sorted(geschuetzt)),
            verwaltung=_flag("VERWALTUNG"),
            docker_socket=os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock"),
            vorlagen_dir=os.environ.get("VORLAGEN_ORDNER", "/vorlagen"),
            erlaubte_images=cls.erlaubte_images + _liste("ERLAUBTE_IMAGES"),
            erlaubte_freigaben=cls.erlaubte_freigaben + _liste("ERLAUBTE_FREIGABEN"),
            jellyfin_url=os.environ.get("JELLYFIN_URL", "").strip(),
            jellyfin_api_key=os.environ.get("JELLYFIN_API_KEY", "").strip(),
            jellyfin_container=os.environ.get("JELLYFIN_CONTAINER", "Jellyfin").strip(),
            verify_ssl=not _flag("UNRAID_SSL_IGNORIEREN"),
            port=int(os.environ.get("PORT", "8000")),
        )
