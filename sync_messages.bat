@echo off
setlocal
cd /d "%~dp0"

powershell -ExecutionPolicy Bypass -File ".\scripts\sync_messages_from_vps.ps1"
