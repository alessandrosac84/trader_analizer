@echo off
:: ============================================================
::  COLETA COMPLETA do fluxo do Profit (WIN + WDO, varios contratos)
::  Roda TUDO numa vez: converte o que ja existe + puxa os contratos
::  anteriores, agregando em barras de 1 min. Deixe rodando (demora).
::  Pode fechar o profit_history antigo; use SO este.
:: ============================================================
cd /d "%~dp0"
if exist "venv\Scripts\activate.bat" ( call venv\Scripts\activate.bat ) else if exist ".venv\Scripts\activate.bat" ( call .venv\Scripts\activate.bat )
echo Coleta COMPLETA do fluxo WIN+WDO (varios contratos)...
python coletar_profit_completo.py --win 12 --wdo 18
pause
