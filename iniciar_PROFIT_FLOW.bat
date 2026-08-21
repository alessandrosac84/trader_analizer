@echo off
:: ============================================================
::  PROFIT FLOW - Coletor de Agressao (Times&Trades) do WIN via ProfitDLL
::  Roda em PROCESSO SEPARADO (nao e o app). Grava data/profit_aggression.json
::  que o Opening Engine le para o fator AGRESSAO.
::
::  PRE-REQUISITOS (ver topo do profit_flow.py):
::    - ProfitDLL.dll da Nelogica (caminho em PROFIT_DLL_PATH no .env)
::    - profit_sdk/profitTypes.py (do exemplo gratuito da Nelogica)
::    - PROFIT_ACTIVATION / PROFIT_USER / PROFIT_PASS no .env
::  Deixe o Profit ABERTO/logado antes de rodar.
:: ============================================================
cd /d "%~dp0"

if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) else if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
)

echo Iniciando ProfitFlow (coletor de agressao do WIN)...
python profit_flow.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Erro ao iniciar o ProfitFlow. Veja as mensagens acima.
    pause
)
