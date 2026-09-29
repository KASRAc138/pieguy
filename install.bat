@echo off
title PieGuy setup
cd /d "%~dp0"
echo.
echo   PieGuy - installing dependencies (PySide6, uiautomation)...
echo.
set "PY=python"
where py >nul 2>nul && set "PY=py -3"
%PY% -m pip install --upgrade pip
%PY% -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo   Install failed. Make sure Python 3.10+ is installed and on PATH.
  pause
  exit /b 1
)
echo.
echo   Done! Starting PieGuy...
call "%~dp0PieGuy.bat"
timeout /t 2 >nul
