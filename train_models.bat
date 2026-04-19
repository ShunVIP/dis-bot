@echo off
setlocal
cd /d "%~dp0"

echo Сначала скачать свежую messages.db с VPS?
echo 1. Да
echo 2. Нет
set /p syncmode=Выбери вариант ^(1/2^): 
if "%syncmode%"=="1" (
  powershell -ExecutionPolicy Bypass -File ".\scripts\sync_messages_from_vps.ps1"
)

echo.
echo Что обучать?
echo 1. Только GPT
echo 2. Только Markov и Persona
echo 3. Всё вместе ^(GPT + Markov + Persona^)
set /p trainkind=Выбери вариант ^(1/2/3^): 

if "%trainkind%"=="1" set "modes=gpt"
if "%trainkind%"=="2" set "modes=markovify"
if "%trainkind%"=="3" set "modes=all"

if not defined modes (
  echo Неизвестный вариант обучения
  exit /b 1
)

echo.
echo Для кого обучать?
echo 1. Для всех пользователей
echo 2. Для одного пользователя
set /p mode=Выбери вариант ^(1/2^): 

if "%mode%"=="1" (
  powershell -ExecutionPolicy Bypass -File ".\scripts\train_local.ps1" -All -Modes %modes%
  goto :eof
)

if "%mode%"=="2" (
  set /p userid=Введи Discord user id: 
  powershell -ExecutionPolicy Bypass -File ".\scripts\train_local.ps1" -UserId "%userid%" -Modes %modes%
  goto :eof
)

echo Неизвестный вариант
exit /b 1
