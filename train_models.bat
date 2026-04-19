@echo off
setlocal
cd /d "%~dp0"

echo 1. Train all users
echo 2. Train one user
set /p mode=Choose option ^(1/2^): 

if "%mode%"=="1" (
  powershell -ExecutionPolicy Bypass -File ".\scripts\train_local.ps1" -All -Modes all
  goto :eof
)

if "%mode%"=="2" (
  set /p userid=Enter Discord user id: 
  powershell -ExecutionPolicy Bypass -File ".\scripts\train_local.ps1" -UserId "%userid%" -Modes all
  goto :eof
)

echo Unknown option
exit /b 1
