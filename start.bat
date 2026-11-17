@echo off
rem LifeOps for Windows: double-click this file to start LifeOps.
rem The first run sets everything up (about a minute); after that it starts in seconds.
rem Close this window (or press Ctrl+C) to stop LifeOps. Your data stays in the "data" folder.
setlocal
cd /d "%~dp0"

rem Prefer the "py" launcher that comes with Python from python.org
set "PY=python"
where py >nul 2>nul && set "PY=py -3"

if exist ".venv\Scripts\python.exe" goto start

%PY% -c "import sys; sys.exit(sys.version_info < (3, 12))" >nul 2>nul || goto nopython
echo Setting up LifeOps for the first time (about a minute)...
%PY% -m venv .venv || goto nopython
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --quiet -r requirements.lock || goto failed

:start
echo.
echo LifeOps is starting at http://127.0.0.1:8780
echo Close this window (or press Ctrl+C) to stop it.
echo.
rem Open the browser a few seconds from now, once the server is up
start "" /min powershell -NoProfile -Command "Start-Sleep -Seconds 3; Start-Process 'http://127.0.0.1:8780'"
".venv\Scripts\python.exe" -m uvicorn --factory app.main:create_app --host 127.0.0.1 --port 8780
goto end

:nopython
echo.
echo LifeOps needs Python 3.12 or newer.
echo Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH"),
echo then double-click start.bat again.
pause
goto end

:failed
echo.
echo Setup didn't finish. Check the internet connection, then double-click start.bat again.
if exist ".venv" rmdir /s /q ".venv"
pause

:end
endlocal
