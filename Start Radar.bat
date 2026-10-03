@echo off
rem 30 Jahre Radar: Doppelklick startet alles. Beim ersten Mal wird alles
rem installiert (ein paar Minuten). Dieses Fenster offen lassen.
cd /d "%~dp0"
set "PY=python"
where py >nul 2>nul && set "PY=py -3"
if exist .git (git pull --ff-only -q >nul 2>nul || echo Update uebersprungen, starte vorhandene Version.)
if not exist .venv\Scripts\python.exe (
  echo Einmalige Einrichtung ...
  %PY% -m venv .venv || goto :fail
)
fc /b requirements.txt .venv\requirements.stamp >nul 2>nul || (
  echo Installiere Pakete ...
  .venv\Scripts\python -m pip install -q --disable-pip-version-check -r requirements.txt || goto :fail
  copy /y requirements.txt .venv\requirements.stamp >nul
)
if not exist .venv\chromium.stamp (
  echo Installiere unsichtbaren Chrome fuer MediaMarkt, Mueller und Co. ...
  .venv\Scripts\python -m playwright install chromium && echo ok> .venv\chromium.stamp
  if not exist .venv\chromium.stamp echo Chrome Installation fehlgeschlagen, grosse Haendler fehlen bis zum naechsten Start.
)
echo Starte 30 Jahre Radar ...
.venv\Scripts\python -m pokemon_preorder_bot.live || goto :fail
exit /b 0
:fail
echo.
echo Etwas ist schiefgelaufen, siehe oben. Python 3.9+ von python.org noetig.
pause
exit /b 1
