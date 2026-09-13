@echo off
rem Open geo in the browser, starting the server first if it is not running.
rem The server runs in a minimized window titled "geo server"; close that
rem window to stop it.
cd /d "%~dp0"
set URL=http://127.0.0.1:8770

rem Any HTTP answer means the server is up (even 401/403), so no -f here.
curl -s -o nul %URL%/api/health
if not errorlevel 1 goto open

start "geo server" /min C:\ms2env\Scripts\python.exe -m uvicorn api:app --host 127.0.0.1 --port 8770
rem A cold start imports rasterio and the satellite-mvp modules; allow 60 s.
for /l %%i in (1,1,60) do (
  timeout /t 1 /nobreak >nul
  curl -s -o nul %URL%/api/health && goto open
)
echo geo did not start within 60 seconds. Check the "geo server" window.
pause
exit /b 1

:open
start "" %URL%/
