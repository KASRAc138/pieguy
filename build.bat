@echo off
title PieGuy - build the portable app
cd /d "%~dp0"
echo.
echo   Building PieGuy.exe (portable)... first time takes a few minutes.
echo.
set "PY=python"
where py >nul 2>nul && set "PY=py -3"

rem --- close a running PieGuy.exe so its files are not locked
taskkill /IM PieGuy.exe /F >nul 2>nul

%PY% -m pip install --upgrade pip >nul
%PY% -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :fail
%PY% -m PyInstaller --noconfirm --clean PieGuy.spec
if errorlevel 1 goto :fail
if not exist "dist\PieGuy\PieGuy.exe" goto :fail

rem --- carry your settings over (pies, chats, flows, recordings)
if exist "data\config.json" if not exist "dist\PieGuy\data\config.json" xcopy "data" "dist\PieGuy\data\" /E /I /Y >nul

rem --- Desktop + Start menu shortcuts
powershell -NoProfile -ExecutionPolicy Bypass -Command "$w = New-Object -ComObject WScript.Shell; $exe = '%~dp0dist\PieGuy\PieGuy.exe'; foreach ($d in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) { $s = $w.CreateShortcut((Join-Path $d 'PieGuy.lnk')); $s.TargetPath = $exe; $s.WorkingDirectory = (Split-Path $exe); $s.IconLocation = $exe + ',0'; $s.Save() }"

echo.
echo   Done!  Your app:  %~dp0dist\PieGuy\PieGuy.exe
echo   The whole "dist\PieGuy" folder is portable - move it anywhere.
echo.
rem --- start it: takes over from a running script version + turns on Start-with-Windows
start "" "%~dp0dist\PieGuy\PieGuy.exe" --replace --enable-startup
timeout /t 4 >nul
exit /b 0

:fail
echo.
echo   Build failed - scroll up for the error. (Python 3.10+ must be installed.)
pause
exit /b 1
