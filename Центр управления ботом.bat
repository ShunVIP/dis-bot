@echo off
setlocal
cd /d "%~dp0"

set "SCRIPT=%~dp0scripts\control_center.ps1"

if not exist "%SCRIPT%" (
    echo Script file not found:
    echo %SCRIPT%
    echo.
    pause
    exit /b 1
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%"
set "ERR=%ERRORLEVEL%"

if not "%ERR%"=="0" (
    echo.
    echo Control center exited with error code: %ERR%
    pause
)

exit /b %ERR%
