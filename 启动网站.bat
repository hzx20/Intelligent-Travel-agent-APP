@echo off
chcp 65001 >nul
title Travel Planner Launcher

echo ================================================
echo   Travel Planner - starting services...
echo ================================================

cd /d "%~dp0backend"
start "TP-Backend(8000)" cmd /k ""C:\Users\i\.workbuddy\binaries\python\envs\default\Scripts\python.exe" -m uvicorn app.main:app --port 8000"

cd /d "%~dp0frontend"

rem —— 自动定位当前 Node 版本（WorkBuddy 升级会更换版本目录，写死路径会失效）——
rem 规则：扫描版本目录，取"node.exe 和 npm 都齐全"的最新一个；找不到就报错提示
set "NODE_BASE=C:\Users\i\.workbuddy\binaries\node\versions"
set "NODE_VER="
for /f "delims=" %%i in ('dir /b /ad /o-n "%NODE_BASE%"') do if not defined NODE_VER if exist "%NODE_BASE%\%%i\node.exe" if exist "%NODE_BASE%\%%i\node_modules\npm\bin\npm-cli.js" set "NODE_VER=%%i"
if not defined NODE_VER (
  echo [错误] 没找到可用的 Node，请告诉 AI 排查。
  pause
  exit /b 1
)

start "TP-Frontend(5173)" cmd /k ""%NODE_BASE%\%NODE_VER%\node.exe" "%NODE_BASE%\%NODE_VER%\node_modules\npm\bin\npm-cli.js" run dev"

timeout /t 6 /nobreak >nul
start http://localhost:5173

echo ================================================
echo   Done! Browser will open at localhost:5173
echo   Keep the two black windows OPEN while using.
echo   Close them = shut down the website.
echo ================================================
pause
