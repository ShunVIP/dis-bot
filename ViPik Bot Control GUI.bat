@echo off
setlocal
cd /d "%~dp0"

set "PYSCRIPT=%~dp0scripts\bot_control_gui.py"
set "PYW="

if exist "%~dp0.venv\Scripts\pythonw.exe" set "PYW=%~dp0.venv\Scripts\pythonw.exe"
if not defined PYW if exist "D:\Python\python 3.12\pythonw.exe" set "PYW=D:\Python\python 3.12\pythonw.exe"

if not exist "%PYSCRIPT%" (
    echo GUI script file not found:
    echo %PYSCRIPT%
    echo.
    pause
    exit /b 1
)

if not defined PYW (
    echo pythonw.exe not found. Install Python or create .venv first.
    echo.
    pause
    exit /b 1
)

start "" "%PYW%" "%PYSCRIPT%"
exit /b 0
