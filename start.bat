@echo off
REM Market Lab - starts the research server and opens the app in its own window.
cd /d "%~dp0backend"
if not exist "..\frontend\dist\index.html" (
  echo Building the web interface...
  pushd ..\frontend && call npm install && call npm run build && popd
)
REM Start the server minimised (if it is already running, this second copy simply exits).
start "Market Lab server" /min "..\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8777
REM Wait until the server answers (up to 60 seconds).
powershell -NoProfile -Command "for($i=0;$i -lt 60;$i++){try{Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8777/api/meta -TimeoutSec 2 | Out-Null; exit 0}catch{Start-Sleep 1}}"
REM Open as an app window (no address bar) in Chrome; fall back to the default browser.
set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%LocalAppData%\Google\Chrome\Application\chrome.exe"
if exist "%CHROME%" (
  start "" "%CHROME%" --app=http://localhost:8777
) else (
  start "" http://localhost:8777
)
