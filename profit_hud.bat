@echo off
:: Trade AI HUD — Atalho de inicializacao
:: Duplo clique para abrir o painel flutuante sobre o Profit Chart

cd /d "%~dp0"

:: Tenta ativar venv se existir
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) else if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
)

echo Iniciando Trade AI HUD...
python profit_hud.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Erro ao iniciar o HUD. Verifique se o Python esta instalado.
    pause
)
