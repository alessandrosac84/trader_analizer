@echo off
:: Coleta o HISTORICO de fluxo (Times&Trades) do WIN/WDO via ProfitDLL.
:: Rode durante a licenca de 7 dias. Deixe o Profit logado.
cd /d "%~dp0"
if exist "venv\Scripts\activate.bat" ( call venv\Scripts\activate.bat ) else if exist ".venv\Scripts\activate.bat" ( call .venv\Scripts\activate.bat )
echo Coletando historico de fluxo do WIN/WDO (ultimos 30 pregoes)...
python profit_history.py --dias 30
pause
