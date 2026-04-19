@echo off
setlocal
cd /d "%~dp0"

set /p tsip=Enter your PC Tailscale IP: 
set /p token=Enter bridge token: 

powershell -ExecutionPolicy Bypass -File ".\scripts\enable_remote_models.ps1" -TailscaleIp "%tsip%" -Token "%token%"
