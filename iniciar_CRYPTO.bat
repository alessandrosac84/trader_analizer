@echo off
:: ============================================================
::  TRADE AI - Instancia CRYPTO (ICMarkets)  -  porta 5001
::  Usa as credenciais MT5_CRYPTO_* do .env (conta ICMarkets).
::  Abra o terminal MT5 da ICMarkets ANTES de rodar este atalho.
::  Depois acesse:  http://localhost:5001/newdashboard  (menu Monitor Crypto)
:: ============================================================
cd /d "%~dp0"

if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) else if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
)

set MT5_PROFILE=crypto
set PORT=5001

echo Iniciando instancia CRYPTO (ICMarkets) na porta 5001...
python app.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Erro ao iniciar. Verifique o Python / venv.
    pause
)
