#!/bin/bash
# 30 Jahre Radar: Doppelklick startet alles.
# Beim ersten Mal werden Python Pakete und ein unsichtbarer Chrome für
# MediaMarkt & Co. installiert (ein paar Minuten), danach startet es sofort.
# Dieses Fenster offen lassen, solange das Radar laufen soll.

cd "$(dirname "$0")" || exit 1

fail() {
  echo
  echo "⚠️  $1"
  read -r -p "Enter drücken zum Schließen …"
  exit 1
}

PY=""
for candidate in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
  if command -v "$candidate" >/dev/null 2>&1 \
    && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 9))' >/dev/null 2>&1; then
    PY="$candidate"
    break
  fi
done
if [ -z "$PY" ]; then
  open "https://www.python.org/downloads/" 2>/dev/null
  fail "Python 3.9 oder neuer fehlt. Bitte von python.org installieren und danach erneut doppelklicken."
fi

# Neueste Version holen, falls das ein Git Ordner ist. Klappt das nicht
# (offline, eigene Änderungen), läuft einfach die vorhandene Version.
if command -v git >/dev/null 2>&1 && [ -d .git ]; then
  git pull --ff-only -q >/dev/null 2>&1 || echo "Update übersprungen, starte vorhandene Version."
fi

if [ ! -x .venv/bin/python ]; then
  echo "Einmalige Einrichtung …"
  "$PY" -m venv .venv || fail "Konnte keine Python Umgebung anlegen."
fi

if ! cmp -s requirements.txt .venv/requirements.stamp; then
  echo "Installiere Pakete …"
  .venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt \
    || fail "Paketinstallation fehlgeschlagen (Internet da?)."
  cp requirements.txt .venv/requirements.stamp
fi

PW_VERSION=$(.venv/bin/python -c "import importlib.metadata as m; print(m.version('playwright'))" 2>/dev/null)
if [ "$(cat .venv/chromium.stamp 2>/dev/null)" != "$PW_VERSION" ]; then
  echo "Installiere unsichtbaren Chrome für MediaMarkt, Müller & Co. …"
  if .venv/bin/python -m playwright install chromium; then
    echo "$PW_VERSION" > .venv/chromium.stamp
  else
    echo "⚠️  Chrome Installation fehlgeschlagen, nächster Start versucht es erneut."
    echo "   Bis dahin fehlen nur die großen Händler, alle anderen Shops laufen."
  fi
fi

echo "Starte 30 Jahre Radar …"
.venv/bin/python -m pokemon_preorder_bot.live || fail "Das Radar wurde mit einem Fehler beendet, siehe oben."
