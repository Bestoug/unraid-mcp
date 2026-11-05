#!/bin/sh
# Leitstand-Sync (Claude, 05.11.2026)
# Ordner: /schluessel (appdata, geheimer Schlüssel + Klon), /status (öffentlicher Schlüssel + Status), /ausgabe (daten.json)
# Code kommt nur aus diesem Image, nie aus GitHub-Daten. Aus dem Firmen-Vault (firma-vault) werden nur der Ordner „01 Firma“ und „00 Eingang/Schleuse/Eingang.md“ geholt.
set -u
REPO_URL="${LEITSTAND_REPO:-git@github.com:Bestoug/firma-vault.git}"
INTERVALL="${LEITSTAND_INTERVALL:-600}"
REPO=/schluessel/vault
STATUS=/status/status.json
# Teil-Klon: nur „01 Firma“ und die Eingangs-Notiz der Schleuse (davon liest das Skript nur die Tabelle „Übernommen“)
teile() { git -C "$REPO" sparse-checkout set --no-cone '/01 Firma/' '/00 Eingang/Schleuse/Eingang.md'; }
mkdir -p /schluessel /status /ausgabe
chmod 700 /schluessel
[ -f /schluessel/id_ed25519 ] || ssh-keygen -t ed25519 -N "" -C "leitstand-nur-lesen" -f /schluessel/id_ed25519 -q
chmod 600 /schluessel/id_ed25519
# Offizieller Hostschlüssel von GitHub, fest eingetragen (schützt vor gefälschtem Server)
echo "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl" > /schluessel/known_hosts
cp /schluessel/id_ed25519.pub /status/oeffentlicher-schluessel.txt
export GIT_SSH_COMMAND="ssh -i /schluessel/id_ed25519 -o IdentitiesOnly=yes -o UserKnownHostsFile=/schluessel/known_hosts -o StrictHostKeyChecking=yes -o BatchMode=yes"

# Webseite (App-Code) kommt aus dem Image und wird bei jedem Start ausgeliefert
for f in index.html manifest.json icon.svg; do cp "/opt/leitstand/web/$f" "/ausgabe/$f.neu" && chmod 644 "/ausgabe/$f.neu" && mv "/ausgabe/$f.neu" "/ausgabe/$f"; done

melde() {
  printf '{"zeit":"%s","ok":%s,"meldung":"%s","stand":"%s"}\n' "$(date '+%d.%m.%Y %H:%M')" "$1" "$2" "$3" > "$STATUS.tmp"
  mv "$STATUS.tmp" "$STATUS"
}

while true; do
  # Klon eines anderen Repos (z. B. noch vom alten Vault) verwerfen und neu holen
  if [ -d "$REPO/.git" ] && [ "$(git -C "$REPO" remote get-url origin 2>/dev/null)" != "$REPO_URL" ]; then rm -rf "$REPO"; fi
  if [ ! -d "$REPO/.git" ]; then
    rm -rf "$REPO"
    if git clone -q --depth 1 --filter=blob:none --sparse "$REPO_URL" "$REPO" 2>/tmp/git.err; then
      teile 2>>/tmp/git.err
    fi
  else
    git -C "$REPO" fetch -q --depth 1 origin main 2>/tmp/git.err && git -C "$REPO" reset -q --hard origin/main 2>>/tmp/git.err
    # Teil-Klon-Liste bei jedem Lauf setzen (ein älterer Klon hat sonst noch die alte Liste)
    teile 2>>/tmp/git.err
  fi
  if [ ! -d "$REPO/.git" ]; then
    melde false "Kein Zugriff auf GitHub: Schluessel noch nicht freigeschaltet oder kein Netz" ""
  else
    STAND=$(git -C "$REPO" log -1 --format='%h %cd' --date=format:'%d.%m.%Y %H:%M')
    if python3 -I /opt/leitstand/leitstand_daten.py "$REPO" /tmp/daten.json >/tmp/skript.log 2>&1; then
      cp /tmp/daten.json /ausgabe/daten.json.neu && chmod 644 /ausgabe/daten.json.neu && mv /ausgabe/daten.json.neu /ausgabe/daten.json
      melde true "aktualisiert" "$STAND"
    else
      melde false "Skript-Fehler: $(tail -1 /tmp/skript.log | tr -d '\"')" "$STAND"
    fi
  fi
  sleep "$INTERVALL"
done
