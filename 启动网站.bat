@echo off
chcp 65001 >nul
title Travel Planner Launcher

echo ================================================
echo   Travel Planner - starting services...
echo ================================================

cd /d "%~dp0backend"
start "TP-Backend(8000)" cmd /k ""C:\Users\i\.workbuddy\binaries\python\envs\default\Scripts\python.exe" -m uvicorn app.main:app --port 8000"

cd /d "%~dp0frontend"
start "TP-Frontend(5173)" cmd /k ""C:\Users\i\.workbuddy\binaries\node\versions\22.22.2-2\node.exe" "C:\Users\i\.workbuddy\binaries\node\versions\22.22.2-2\node_modules\npm\bin\npm-cli.js" run dev"

timeout /t 6 /nobreak >nul
start http://localhost:5173

echo ================================================
echo   Done! Browser will open at localhost:5173
echo   Keep the two black windows OPEN while using.
echo   Close them = shut down the website.
echo ================================================
pause
