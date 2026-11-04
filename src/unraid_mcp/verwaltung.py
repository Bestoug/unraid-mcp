"""Container-Verwaltung: anlegen, ändern, aktualisieren – mit harten Sicherheitsregeln.

Braucht den Docker-Socket (= Vollzugriff auf den Server). Deshalb prüft dieser Code jede
Anfrage gegen feste Regeln, die auch eine präparierte Anfrage nicht umgehen kann:

* nie privileged, Host-Netzwerk, zusätzliche Rechte (CapAdd) oder Geräte außer /dev/dri
* Ordner nur unter /mnt/user/<freigabe>/…; nie /boot, system, ganz /mnt/user oder fremde appdata
* Images nur aus bekannten Quellen, sonst nur mit ausdrücklicher Freigabe von Philipp
* Tailscale/Funnel, Code-Nachlade-Variablen und nicht freigegebene Freigaben nur mit Freigabe
* unraid-mcp verändert sich nie selbst
* jede Änderung: Sicherung (Container-Einstellungen + Unraid-Vorlage) und Protokoll mit Rückweg

Die Unraid-Vorlage (my-<name>.xml) wird immer mitgepflegt, damit Unraids „Edit“ und
„Force Update“ dieselben Einstellungen verwenden.
"""
from __future__ import annotations

import copy
import datetime as dt
import http.client
import json
import os
import posixpath
import re
import socket
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET

from .config import Settings
from .unraid import UnraidFehler

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
GEHEIM_RE = re.compile(r"(PASS|PWD|KEY|TOKEN|SECRET|CRED|AUTH|DSN|COOKIE)", re.I)
# Variablen, mit denen ein erlaubtes Image fremden Code nachladen könnte
CODE_VARIABLEN = re.compile(r"^(DOCKER_MODS|INSTALL_\w*|LD_\w*|PATH|PYTHONPATH|PYTHONSTARTUP|NODE_OPTIONS|PERL5OPT|BASH_ENV|ENV)$", re.I)
STEUERZEICHEN = re.compile(r"[\x00-\x1f\x7f]")
SPERRE = threading.Lock()
ERLAUBTE_GERAETE = {"/dev/dri"}
GESPERRTE_FREIGABEN = {"system"}
SELBST = "unraid-mcp"


# ---------------------------------------------------------------- Docker-Client

class _UnixConn(http.client.HTTPConnection):
    def __init__(self, pfad: str, timeout: float):
        super().__init__("localhost", timeout=timeout)
        self._pfad = pfad

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._pfad)


class Docker:
    def __init__(self, sock: str):
        self.sock = sock

    def _req(self, methode: str, pfad: str, body=None, timeout: float = 60, roh: bool = False):
        conn = _UnixConn(self.sock, timeout)
        headers = {"Content-Type": "application/json"} if body is not None else {}
        try:
            conn.request(methode, pfad, body=json.dumps(body) if body is not None else None,
                         headers=headers)
            r = conn.getresponse()
            data = r.read()
        except OSError as e:
            raise UnraidFehler(f"Docker nicht erreichbar ({e}). Ist /var/run/docker.sock eingebunden?") from e
        finally:
            conn.close()
        if r.status >= 400:
            try:
                msg = json.loads(data).get("message", "")
            except ValueError:
                msg = data[:300].decode(errors="replace")
            raise UnraidFehler(f"Docker meldet {r.status}: {msg}")
        if roh:
            return data
        return json.loads(data) if data.strip() else {}

    def alle(self) -> list[dict]:
        return self._req("GET", "/containers/json?all=1")

    def inspect(self, name: str) -> dict:
        return self._req("GET", f"/containers/{urllib.parse.quote(name)}/json")

    def pull(self, image: str) -> None:
        repo, tag = teile_image(image)
        q = urllib.parse.urlencode({"fromImage": repo, "tag": tag})
        roh = self._req("POST", f"/images/create?{q}", timeout=900, roh=True)
        for zeile in roh.decode(errors="replace").splitlines():
            try:
                d = json.loads(zeile)
            except ValueError:
                continue
            if d.get("error"):
                raise UnraidFehler(f"Image „{image}“ konnte nicht geladen werden: {d['error']}")

    def create(self, name: str, body: dict) -> str:
        return self._req("POST", f"/containers/create?name={urllib.parse.quote(name)}", body)["Id"]

    def start(self, cid: str) -> None:
        self._req("POST", f"/containers/{cid}/start")

    def stop(self, cid: str, t: int = 30) -> None:
        try:
            self._req("POST", f"/containers/{cid}/stop?t={t}", timeout=t + 30)
        except UnraidFehler as e:
            if "304" not in str(e):
                raise

    def rename(self, cid: str, neu: str) -> None:
        self._req("POST", f"/containers/{cid}/rename?name={urllib.parse.quote(neu)}")

    def remove(self, cid: str) -> None:
        # v=0: benannte und anonyme Volumes bleiben erhalten
        self._req("DELETE", f"/containers/{cid}?v=0&force=1")


# ---------------------------------------------------------------- Prüfungen

def teile_image(image: str) -> tuple[str, str]:
    image = image.strip()
    if "@" in image:
        repo, digest = image.split("@", 1)
        if ":" in repo.rsplit("/", 1)[-1]:
            repo = repo.rsplit(":", 1)[0]
        return repo, digest
    letzter = image.rsplit("/", 1)[-1]
    if ":" in letzter:
        repo, tag = image.rsplit(":", 1)
        return repo, tag
    return image, "latest"


def image_bekannt(s: Settings, image: str) -> bool:
    repo, _ = teile_image(image)
    repo = repo.lower()
    if repo.startswith("docker.io/"):
        repo = repo[len("docker.io/"):]
    if "/" not in repo:            # offizielle Docker-Images (nginx, postgres …)
        return True
    return any(repo.startswith(p.lower()) for p in s.erlaubte_images)


def pruefe_name(name: str) -> str:
    if not NAME_RE.fullmatch(name or ""):
        raise UnraidFehler(f"Ungültiger Container-Name „{name}“ (Buchstaben, Ziffern, - _ .)")
    return name


def pruefe_host_pfad(s: Settings, pfad: str, container: str, freigabe: bool) -> str:
    p = posixpath.normpath(pfad.strip())
    if p == "/dev/dri":
        return p
    if not p.startswith("/mnt/user/"):
        raise UnraidFehler(f"Ordner „{pfad}“ abgelehnt: nur Ordner unter /mnt/user/<Freigabe>/ sind erlaubt.")
    teile = p.split("/")[3:]
    share = teile[0].lower()
    if share in GESPERRTE_FREIGABEN:
        raise UnraidFehler(f"Freigabe „{share}“ ist gesperrt (Unraid-Systemdateien).")
    if share == "appdata":
        if len(teile) < 2 or teile[1].lower() != container.lower():
            raise UnraidFehler(
                f"„{p}“ abgelehnt: Ein Container bekommt nur seinen eigenen Ordner /mnt/user/appdata/{container}.")
    if share not in {f.lower() for f in s.erlaubte_freigaben} and not freigabe:
        raise UnraidFehler(
            f"Freigabe „{share}“ ist nicht für Container freigegeben (erlaubt: {', '.join(s.erlaubte_freigaben)}); "
            "nur mit ausdrücklicher Freigabe von Philipp.")
    return p


def pruefe_ziel(ziel: str) -> str:
    z = posixpath.normpath("/" + ziel.strip().lstrip("/")) if ziel.strip().startswith("/") else ziel.strip()
    if not z.startswith("/") or z == "/" or z.split("/")[1] in ("proc", "sys", "dev", "etc", "bin", "usr", "lib"):
        raise UnraidFehler(f"Ziel im Container „{ziel}“ ist nicht erlaubt.")
    return z


def pruefe_text(wert, was: str) -> str:
    wert = str(wert)
    if STEUERZEICHEN.search(wert):
        raise UnraidFehler(f"{was} enthält Steuerzeichen oder Zeilenumbrüche.")
    return wert


def pruefe_variablen(variablen: dict, freigabe: bool) -> dict:
    aus = {}
    for k, wert in (variablen or {}).items():
        pruefe_variable(k, freigabe)
        aus[k] = pruefe_text(wert, f"Wert von {k}")
    return aus


def pruefe_variable(key: str, freigabe: bool) -> None:
    if not VAR_RE.fullmatch(key):
        raise UnraidFehler(f"Ungültiger Variablenname „{key}“")
    if CODE_VARIABLEN.match(key) and not freigabe:
        raise UnraidFehler(f"„{key}“ kann fremden Code nachladen: nur mit Philipps Freigabe.")
    if (key.upper().startswith("TS_") or "TAILSCALE" in key.upper()) and not freigabe:
        raise UnraidFehler("Tailscale/Funnel macht den Container von außen erreichbar: nur mit Philipps Freigabe.")


def belegte_ports(d: Docker) -> set[int]:
    """Host-Ports aller Container, auch gestoppter (aus den Einstellungen, nicht nur laufend)."""
    belegt = set()
    for c in d.alle():
        for p in c.get("Ports") or []:
            if p.get("PublicPort"):
                belegt.add(int(p["PublicPort"]))
        try:
            hc = d.inspect(c["Id"]).get("HostConfig") or {} if c.get("Id") else {}
        except UnraidFehler:
            continue
        for bindungen in (hc.get("PortBindings") or {}).values():
            for b in bindungen or []:
                if str(b.get("HostPort") or "").isdigit():
                    belegt.add(int(b["HostPort"]))
    return belegt


def pruefe_ports(ports: list[dict], belegt: set[int]) -> list[dict]:
    aus = []
    for p in ports:
        host, cont = int(p["host"]), int(p["container"])
        proto = (p.get("protokoll") or "tcp").lower()
        if proto not in ("tcp", "udp"):
            raise UnraidFehler(f"Protokoll „{proto}“ unbekannt (tcp/udp)")
        if not 1025 <= host <= 65535 or not 1 <= cont <= 65535:
            raise UnraidFehler(f"Port {host} nicht erlaubt (Host-Ports 1025–65535)")
        if host in belegt:
            raise UnraidFehler(f"Port {host} ist schon belegt")
        belegt.add(host)
        aus.append({"host": host, "container": cont, "protokoll": proto})
    return aus


def pruefe_ordner(s: Settings, ordner: list[dict], container: str, freigabe: bool) -> list[dict]:
    aus = []
    for o in ordner:
        pruefe_text(o["host"], "Ordner")
        pruefe_text(o["container"], "Ziel im Container")
        host = pruefe_host_pfad(s, o["host"], container, freigabe)
        if host == "/dev/dri":
            raise UnraidFehler("/dev/dri gehört unter „geraete“, nicht unter „ordner“.")
        aus.append({"host": host, "container": pruefe_ziel(o["container"]),
                    "nur_lesen": bool(o.get("nur_lesen", False))})
    return aus


def pruefe_geraete(geraete: list[str]) -> list[str]:
    for g in geraete:
        if posixpath.normpath(g) not in ERLAUBTE_GERAETE:
            raise UnraidFehler(f"Gerät „{g}“ nicht erlaubt; erlaubt ist nur /dev/dri (Hardware-Transcoding).")
    return [posixpath.normpath(g) for g in geraete]


# ---------------------------------------------------------------- Unraid-Vorlagen

def vorlagen_pfad(s: Settings, name: str) -> str | None:
    try:
        for f in os.listdir(s.vorlagen_dir):
            if f.lower() == f"my-{name.lower()}.xml":
                return os.path.join(s.vorlagen_dir, f)
    except FileNotFoundError:
        return None
    return None


def _config(parent, name, target, typ, wert, mode="", maske=False, beschreibung=""):
    el = ET.SubElement(parent, "Config", {
        "Name": name, "Target": str(target), "Default": "", "Mode": mode,
        "Description": beschreibung, "Type": typ, "Display": "always", "Required": "false",
        "Mask": "true" if maske else "false"})
    el.text = str(wert)
    return el


def vorlage_neu(name: str, image: str, ports, ordner, variablen, geraete, webui_port, beschreibung) -> str:
    root = ET.Element("Container", {"version": "2"})
    felder = {"Name": name, "Repository": image, "Registry": "", "Network": "bridge", "MyIP": "",
              "Shell": "sh", "Privileged": "false", "Support": "", "Project": "",
              "Overview": beschreibung or "Von Claude angelegt (unraid-mcp)", "Category": "",
              "WebUI": f"http://[IP]:[PORT:{webui_port}]/" if webui_port else "", "TemplateURL": "",
              "Icon": "", "ExtraParams": "", "PostArgs": "", "CPUset": "",
              "DateInstalled": str(int(time.time())), "DonateText": "", "DonateLink": "", "Requires": ""}
    for k, v in felder.items():
        ET.SubElement(root, k).text = v
    for p in ports:
        _config(root, f"Port {p['container']}", p["container"], "Port", p["host"], p["protokoll"])
    for o in ordner:
        _config(root, o["container"], o["container"], "Path", o["host"], "ro" if o["nur_lesen"] else "rw")
    for k, v in variablen.items():
        _config(root, k, k, "Variable", v, maske=bool(GEHEIM_RE.search(k)))
    for g in geraete:
        _config(root, g, "", "Device", g)
    ET.indent(root)
    return '<?xml version="1.0"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def vorlage_ergaenzen(xml: str, ports, ordner, variablen, geraete) -> str:
    root = ET.fromstring(xml)
    configs = root.findall("Config")
    for k, v in variablen.items():
        vorhanden = [c for c in configs if c.get("Type") == "Variable" and c.get("Target") == k]
        if vorhanden:
            vorhanden[0].text = str(v)
        else:
            _config(root, k, k, "Variable", v, maske=bool(GEHEIM_RE.search(k)))
    for o in ordner:
        _config(root, o["container"], o["container"], "Path", o["host"], "ro" if o["nur_lesen"] else "rw")
    for p in ports:
        _config(root, f"Port {p['container']}", p["container"], "Port", p["host"], p["protokoll"])
    for g in geraete:
        _config(root, g, "", "Device", g)
    ET.indent(root)
    return '<?xml version="1.0"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


# ---------------------------------------------------------------- Sicherung und Protokoll

def _jetzt() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def _schreiben(pfad: str, inhalt: str, anhaengen: bool = False) -> None:
    os.makedirs(os.path.dirname(pfad), exist_ok=True)
    with open(pfad, "a" if anhaengen else "w", encoding="utf-8") as f:
        f.write(inhalt)
    try:
        os.chown(pfad, 99, 100)
        os.chown(os.path.dirname(pfad), 99, 100)
    except OSError:
        pass


def protokoll_ordner(s: Settings) -> str:
    return os.path.join(s.arbeit_dir, "protokoll")


def protokollieren(s: Settings, aktion: str, name: str, text: str) -> None:
    zeile = f"- {dt.datetime.now():%d.%m.%Y %H:%M} **{aktion}** `{name}`: {text}\n"
    _schreiben(os.path.join(protokoll_ordner(s), "verwaltung.md"), zeile, anhaengen=True)


def sichern(s: Settings, name: str, insp: dict | None, vorlage: str | None) -> str:
    ordner = os.path.join(protokoll_ordner(s), "sicherung")
    stempel = _jetzt()
    if insp is not None:
        roh = copy.deepcopy(insp)
        roh.get("Config", {}).pop("Env", None)   # keine Geheimnisse im Arbeitsordner
        _schreiben(os.path.join(ordner, f"{name}-{stempel}.json"), json.dumps(roh, indent=2))
    if vorlage and os.path.exists(vorlage):
        # Vorlage enthält Geheimnisse im Klartext: Sicherung bleibt auf dem USB-Stick,
        # außerhalb von Claudes lesbaren Ordnern.
        ziel = os.path.join(s.vorlagen_dir, "claude-sicherung")
        os.makedirs(ziel, exist_ok=True)
        with open(vorlage, encoding="utf-8") as f, \
                open(os.path.join(ziel, f"{os.path.basename(vorlage)}.{stempel}"), "w", encoding="utf-8") as g:
            g.write(f.read())
    return f"Einstellungen: {s.arbeit_dir}/protokoll/sicherung, Unraid-Vorlage: templates-user/claude-sicherung (Stand {stempel})"


# ---------------------------------------------------------------- Container-Konfiguration

def _bind(o: dict) -> str:
    return f"{o['host']}:{o['container']}:{'ro' if o['nur_lesen'] else 'rw'}"


def neuer_body(image, ports, ordner, variablen, geraete, webui_port, name) -> dict:
    exposed, bindings = {}, {}
    for p in ports:
        key = f"{p['container']}/{p['protokoll']}"
        exposed[key] = {}
        bindings.setdefault(key, []).append({"HostIp": "", "HostPort": str(p["host"])})
    labels = {"net.unraid.docker.managed": "dockerman", "angelegt-von": "claude-unraid-mcp"}
    if webui_port:
        labels["net.unraid.docker.webui"] = f"http://[IP]:[PORT:{webui_port}]/"
    return {
        "Image": image, "Env": [f"{k}={v}" for k, v in variablen.items()],
        "ExposedPorts": exposed, "Labels": labels, "Hostname": name,
        "HostConfig": {
            "Binds": [_bind(o) for o in ordner], "PortBindings": bindings,
            "NetworkMode": "bridge", "Privileged": False,
            "RestartPolicy": {"Name": "unless-stopped"},
            "Devices": [{"PathOnHost": g, "PathInContainer": g, "CgroupPermissions": "rwm"} for g in geraete],
            "LogConfig": {"Type": "json-file", "Config": {"max-size": "50m", "max-file": "1"}},
        },
    }


KOPIER_FELDER = ("Image", "Env", "Cmd", "Entrypoint", "Labels", "ExposedPorts", "WorkingDir", "User",
                 "Tty", "OpenStdin", "StdinOnce", "Healthcheck", "StopSignal", "StopTimeout",
                 "Volumes", "Domainname")


def nur_abweichungen(cfg: dict, img: dict | None) -> dict:
    """Nur was der Container gegenüber den Standardwerten seines Images selbst gesetzt hat.
    So bekommt ein aktualisiertes Image seine neuen Standardwerte (Env, Entrypoint, Labels …)."""
    if img is None:
        return {k: copy.deepcopy(cfg[k]) for k in KOPIER_FELDER if cfg.get(k) is not None}
    body = {"Image": cfg.get("Image")}
    env = [e for e in cfg.get("Env") or [] if e not in set(img.get("Env") or [])]
    if env:
        body["Env"] = env
    labels = {k: v for k, v in (cfg.get("Labels") or {}).items() if (img.get("Labels") or {}).get(k) != v}
    if labels:
        body["Labels"] = labels
    for k in ("ExposedPorts", "Volumes"):
        extra = {x: {} for x in (cfg.get(k) or {}) if x not in (img.get(k) or {})}
        if extra:
            body[k] = extra
    for k in ("Cmd", "Entrypoint", "WorkingDir", "User", "Healthcheck", "StopSignal", "StopTimeout",
              "Domainname"):
        if cfg.get(k) not in (None, "", []) and cfg.get(k) != img.get(k):
            body[k] = copy.deepcopy(cfg[k])
    for k in ("Tty", "OpenStdin", "StdinOnce"):
        if cfg.get(k):
            body[k] = True
    return body


def body_aus_inspect(insp: dict, ports=(), ordner=(), variablen=None, geraete=(), image_cfg=None) -> dict:
    """Baut die Einstellungen eines bestehenden Containers nach und fügt Änderungen hinzu."""
    cfg, hc = insp.get("Config") or {}, copy.deepcopy(insp.get("HostConfig") or {})
    body = nur_abweichungen(cfg, image_cfg)
    if cfg.get("Hostname") and not (insp.get("Id") or "").startswith(cfg["Hostname"]):
        body["Hostname"] = cfg["Hostname"]
    binds = list(hc.get("Binds") or [])
    ziele = {b.split(":")[1] for b in binds if ":" in b} | {
        m.get("Target") for m in hc.get("Mounts") or []}
    for m in insp.get("Mounts") or []:   # anonyme Volumes (z. B. Jellyfin /cache) mitnehmen
        if m.get("Type") == "volume" and m.get("Name") and m.get("Destination") not in ziele:
            binds.append(f"{m['Name']}:{m['Destination']}")
            ziele.add(m["Destination"])
    for o in ordner:
        if o["container"] in ziele:
            raise UnraidFehler(f"Im Container ist „{o['container']}“ schon belegt.")
        binds.append(_bind(o))
    hc["Binds"] = binds
    env = list(body.get("Env") or [])
    for k, v in (variablen or {}).items():
        env = [e for e in env if not e.startswith(f"{k}=")] + [f"{k}={v}"]
    body["Env"] = env
    if ports:
        body.setdefault("ExposedPorts", {})
        hc.setdefault("PortBindings", {})
        hc["PortBindings"] = hc["PortBindings"] or {}
        for p in ports:
            key = f"{p['container']}/{p['protokoll']}"
            body["ExposedPorts"][key] = {}
            hc["PortBindings"].setdefault(key, []).append({"HostIp": "", "HostPort": str(p["host"])})
    if geraete:
        hc["Devices"] = (hc.get("Devices") or []) + [
            {"PathOnHost": g, "PathInContainer": g, "CgroupPermissions": "rwm"} for g in geraete]
    body["HostConfig"] = hc
    mode = hc.get("NetworkMode") or "bridge"
    if mode not in ("bridge", "host", "none", "default") and not mode.startswith("container:"):
        ep = ((insp.get("NetworkSettings") or {}).get("Networks") or {}).get(mode) or {}
        body["NetworkingConfig"] = {"EndpointsConfig": {
            mode: {k: ep[k] for k in ("IPAMConfig", "Aliases") if ep.get(k)}}}
    return body


def _ohne_geheimnisse(variablen: dict) -> dict:
    return {k: ("***" if GEHEIM_RE.search(k) else v) for k, v in variablen.items()}


# ---------------------------------------------------------------- Aktionen

def _docker(s: Settings) -> Docker:
    if not s.verwaltung:
        raise UnraidFehler(
            "Container-Verwaltung ist nicht eingeschaltet (VERWALTUNG=1, Docker-Socket und "
            "Vorlagen-Ordner einbinden; Skript unraid/verwaltung-aktivieren.sh).")
    return Docker(s.docker_socket)


STABIL_SEKUNDEN = 5


def _warte_laeuft(d: Docker, cid: str, sekunden: int = 30) -> dict:
    """Wartet, bis der Container einige Sekunden stabil läuft."""
    beginn, laeuft_seit = time.time(), None
    while True:
        insp = d.inspect(cid)
        st = insp.get("State") or {}
        if st.get("Running") and not st.get("Restarting") and not insp.get("RestartCount"):
            laeuft_seit = laeuft_seit or time.time()
            if time.time() - laeuft_seit >= STABIL_SEKUNDEN:
                return insp
        else:
            laeuft_seit = None
            if st.get("Status") in ("exited", "dead"):
                raise UnraidFehler(f"Container startet nicht (Status {st.get('Status')}, Exit {st.get('ExitCode')}).")
        if st.get("Restarting") or insp.get("RestartCount"):
            raise UnraidFehler("Container startet ständig neu (Absturzschleife).")
        if time.time() - beginn > sekunden:
            raise UnraidFehler(f"Container läuft nach {sekunden} s nicht (Status {st.get('Status')}).")
        time.sleep(1)


def _anlegen(s: Settings, name: str, image: str, ports: list[dict], ordner: list[dict],
            variablen: dict, geraete: list[str], webui_port: int | None, beschreibung: str,
            freigabe: bool) -> dict:
    d = _docker(s)
    pruefe_name(name)
    if name.lower() == SELBST:
        raise UnraidFehler("Der Name unraid-mcp ist reserviert.")
    if any(name.lower() in {n.lstrip("/").lower() for n in c.get("Names") or []} for c in d.alle()):
        raise UnraidFehler(f"Container „{name}“ gibt es schon; zum Ändern container_aendern verwenden.")
    if vorlagen_pfad(s, name):
        raise UnraidFehler(f"Eine Unraid-Vorlage für „{name}“ gibt es schon; anderen Namen wählen.")
    if not image_bekannt(s, image) and not freigabe:
        raise UnraidFehler(
            f"Image „{image}“ stammt aus keiner bekannten Quelle ({', '.join(s.erlaubte_images)}). "
            "Erst prüfen und Philipp fragen; nur mit seiner ausdrücklichen Freigabe anlegen.")
    variablen = pruefe_variablen(variablen, freigabe)
    beschreibung = pruefe_text(beschreibung, "Beschreibung")
    image = pruefe_text(image, "Image")
    ports = pruefe_ports(ports, belegte_ports(d))
    ordner = pruefe_ordner(s, ordner, name, freigabe)
    geraete = pruefe_geraete(geraete)

    d.pull(image)
    vorlage = os.path.join(s.vorlagen_dir, f"my-{name}.xml")
    xml = vorlage_neu(name, image, ports, ordner, variablen, geraete, webui_port, beschreibung)
    cid = d.create(name, neuer_body(image, ports, ordner, variablen, geraete, webui_port, name))
    try:
        d.start(cid)
        insp = _warte_laeuft(d, cid)
    except Exception as e:  # noqa: BLE001
        try:
            d.remove(cid)   # neuer, leerer Container: keine Daten betroffen
        except Exception as x:  # noqa: BLE001
            raise UnraidFehler(f"Container „{name}“ startet nicht ({e}) und ließ sich nicht entfernen ({x}); "
                               "bitte in Unraid entfernen.") from e
        raise UnraidFehler(f"Container „{name}“ wurde nicht angelegt: {e}") from e
    _schreiben(vorlage, xml)
    protokollieren(s, "angelegt", name,
                   f"Image {image}, Ports {[p['host'] for p in ports]}, Ordner {[o['host'] for o in ordner]}. "
                   f"Rückweg: Container stoppen und in Unraid entfernen (Daten in appdata bleiben).")
    return {"container": name, "status": (insp.get("State") or {}).get("Status"), "image": image,
            "ports": ports, "ordner": ordner, "variablen": _ohne_geheimnisse(variablen),
            "vorlage": f"my-{name}.xml", "webui": f"http://192.168.2.113:{ports[0]['host']}" if ports else None}


def neu_erstellen(s: Settings, d: Docker, insp: dict, body: dict) -> dict:
    """Ersetzt einen Container durch einen neuen mit `body`; bei jedem Fehler zurück zum alten.
    Ein gestoppter Container bleibt gestoppt."""
    name = (insp.get("Name") or "").lstrip("/")
    alt_id = insp["Id"]
    lief = bool((insp.get("State") or {}).get("Running"))
    zwischen = f"{name}-alt-{_jetzt()}"
    schritte = []          # was schon passiert ist, für den Rückweg
    neu_id = None
    try:
        if lief:
            schritte.append("gestoppt")   # vorher eintragen: auch ein halb geglückter Stopp wird zurückgenommen
            d.stop(alt_id)
        d.rename(alt_id, zwischen)
        schritte.append("umbenannt")
        neu_id = d.create(name, body)
        if lief:
            d.start(neu_id)
            neu = _warte_laeuft(d, neu_id)
        else:
            neu = d.inspect(neu_id)
    except Exception as e:   # noqa: BLE001 – jeder Fehler führt zum Rückweg
        probleme = []
        if neu_id:
            try:
                d.remove(neu_id)
            except Exception as x:  # noqa: BLE001
                probleme.append(f"neuen Container entfernen: {x}")
        if "umbenannt" in schritte:
            try:
                d.rename(alt_id, name)
            except Exception as x:  # noqa: BLE001
                probleme.append(f"zurückbenennen: {x}")
        if "gestoppt" in schritte:
            try:
                d.start(alt_id)
            except Exception as x:  # noqa: BLE001
                probleme.append(f"alten Container starten: {x}")
        if probleme:
            raise UnraidFehler(
                f"Änderung fehlgeschlagen ({e}) UND Rückweg unvollständig: {'; '.join(probleme)}. "
                f"Der alte Container heißt evtl. „{zwischen}“: in Unraid in „{name}“ umbenennen und starten."
            ) from e
        raise UnraidFehler(f"Änderung fehlgeschlagen, alter Zustand wiederhergestellt: {e}") from e
    d.remove(alt_id)   # nur der Container; Volumes und Ordner bleiben
    return neu


def _pruefe_geschuetzt(s: Settings, name: str) -> None:
    if name.lower() == SELBST or name.lower() in {g.lower() for g in s.geschuetzte_container}:
        raise UnraidFehler(f"„{name}“ ist geschützt und wird von Claude nicht verändert; das macht Philipp in Unraid.")


def _image_cfg(d: Docker, insp: dict) -> dict | None:
    try:
        cfg = d._req("GET", f"/images/{insp.get('Image')}/json")
        return (cfg.get("Config") or {}) if isinstance(cfg, dict) else None
    except Exception:  # noqa: BLE001 – ohne Image-Infos alles übernehmen (sicherer Rückfall)
        return None


def _aendern(s: Settings, name: str, ports: list[dict], ordner: list[dict], variablen: dict,
            geraete: list[str], freigabe: bool) -> dict:
    d = _docker(s)
    insp = d.inspect(name)
    echt = (insp.get("Name") or "").lstrip("/")
    _pruefe_geschuetzt(s, echt)
    if not (ports or ordner or variablen or geraete):
        raise UnraidFehler("Keine Änderung angegeben.")
    variablen = pruefe_variablen(variablen, freigabe)
    ports = pruefe_ports(ports, belegte_ports(d))   # inkl. der eigenen bisherigen Ports
    ordner = pruefe_ordner(s, ordner, echt, freigabe)
    geraete = pruefe_geraete(geraete)

    vorlage = vorlagen_pfad(s, echt)
    sicherung = sichern(s, echt, insp, vorlage)
    body = body_aus_inspect(insp, ports, ordner, variablen, geraete, _image_cfg(d, insp))
    neu = neu_erstellen(s, d, insp, body)
    if vorlage:
        with open(vorlage, encoding="utf-8") as f:
            xml = f.read()
        _schreiben(vorlage, vorlage_ergaenzen(xml, ports, ordner, variablen, geraete))
    protokollieren(s, "geändert", echt,
                   f"neue Ordner {[o['host'] + '→' + o['container'] for o in ordner]}, "
                   f"Ports {[p['host'] for p in ports]}, Variablen {list(variablen)}, Geräte {geraete}. "
                   f"Rückweg: Sicherung in {sicherung}.")
    return {"container": echt, "status": (neu.get("State") or {}).get("Status"),
            "neue_ordner": ordner, "neue_ports": ports, "variablen": _ohne_geheimnisse(variablen),
            "geraete": geraete, "unraid_vorlage": "aktualisiert" if vorlage else
            "keine Vorlage gefunden – Unraids „Edit“ kennt die Änderung nicht",
            "sicherung": sicherung}


def _aktualisieren(s: Settings, name: str) -> dict:
    d = _docker(s)
    insp = d.inspect(name)
    echt = (insp.get("Name") or "").lstrip("/")
    _pruefe_geschuetzt(s, echt)
    image = (insp.get("Config") or {}).get("Image")
    alt_image_id = insp.get("Image")
    d.pull(image)
    neu_image_id = d._req("GET", f"/images/{urllib.parse.quote(image, safe='/:')}/json").get("Id")
    if neu_image_id == alt_image_id:
        return {"container": echt, "ergebnis": "schon aktuell", "image": image}
    alt_cfg = _image_cfg(d, insp)   # Standardwerte des ALTEN Images, damit die neuen greifen
    if alt_cfg is None:
        raise UnraidFehler(
            "Die Einstellungen des bisherigen Images sind nicht mehr lesbar (Image gelöscht?). "
            "Sicherer Weg: in Unraid bei diesem Container „force update“.")
    sicherung = sichern(s, echt, insp, vorlagen_pfad(s, echt))
    neu = neu_erstellen(s, d, insp, body_aus_inspect(insp, image_cfg=alt_cfg))
    protokollieren(s, "aktualisiert", echt, f"Image {image} neu geladen. Sicherung: {sicherung}.")
    return {"container": echt, "ergebnis": "aktualisiert", "image": image,
            "status": (neu.get("State") or {}).get("Status")}


def details(s: Settings, name: str) -> dict:
    d = _docker(s)
    insp = d.inspect(name)
    hc = insp.get("HostConfig") or {}
    env = {}
    for e in (insp.get("Config") or {}).get("Env") or []:
        k, _, v = e.partition("=")
        env[k] = v
    return {
        "name": (insp.get("Name") or "").lstrip("/"), "image": (insp.get("Config") or {}).get("Image"),
        "status": (insp.get("State") or {}).get("Status"),
        "ordner": [{"host": m.get("Source") or m.get("Name"), "container": m.get("Destination"),
                    "nur_lesen": not m.get("RW", True), "typ": m.get("Type")} for m in insp.get("Mounts") or []],
        "ports": hc.get("PortBindings"), "netzwerk": hc.get("NetworkMode"),
        "variablen": _ohne_geheimnisse(env),
        "unraid_vorlage": os.path.basename(vorlagen_pfad(s, (insp.get("Name") or "").lstrip("/")) or "") or None,
    }


def anlegen(*a, **k):
    with SPERRE:
        return _anlegen(*a, **k)


def aendern(*a, **k):
    with SPERRE:
        return _aendern(*a, **k)


def aktualisieren(*a, **k):
    with SPERRE:
        return _aktualisieren(*a, **k)
