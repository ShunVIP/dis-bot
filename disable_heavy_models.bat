@echo off
setlocal
cd /d "%~dp0"

powershell -ExecutionPolicy Bypass -File ".\scripts\disable_remote_models.ps1"
