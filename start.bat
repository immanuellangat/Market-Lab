@echo off
REM Market Lab - starts the research server and opens the app in your browser.
cd /d "%~dp0backend"
if not exist "..\frontend\dist\index.html" (
  echo Building the web interface...
  pushd ..\frontend && call npm install && call npm run build && popd
)
start "" http://127.0.0.1:8777
"..\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8777
