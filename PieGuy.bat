@echo off
cd /d "%~dp0"
where pyw >nul 2>nul && (start "" pyw -3 "%~dp0PieGuy.pyw" & exit /b)
start "" pythonw "%~dp0PieGuy.pyw"
