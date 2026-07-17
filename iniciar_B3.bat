@echo off
:: ============================================================
::  TRADE AI - Instancia B3 (XP)  -  porta 5000
::  Usa as credenciais MT5_* do .env (conta XP / B3).
::  Abra o terminal MT5 da XP ANTES de rodar este atalho.
::  Depois acesse:  http://localhost:5000/newdashboard
:: ============================================================
cd /d "%~dp0"

if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) else if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
)

set MT5_PROFILE=b3
set PORT=5000

echo Iniciando instancia B3 (XP) na porta 5000...
python app.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Erro ao iniciar. Verifique o Python / venv.
    pause
)
