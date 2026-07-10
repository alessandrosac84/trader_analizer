@echo off
chcp 65001 >nul
setlocal
REM ============================================================
REM  Copia o projeto para fora do OneDrive, preservando o Git.
REM  Origem : C:\Users\aless\OneDrive\Documentos\GIT_PESSOAL\trader_analizer
REM  Destino: C:\Projetos\trader_analizer
REM  A pasta .git (vinculo com o GitHub) e copiada junto.
REM ============================================================

set "ORIG=C:\Users\aless\OneDrive\Documentos\GIT_PESSOAL\trader_analizer"
set "DEST=C:\Projetos\trader_analizer"

echo.
echo ========================================================
echo   COPIA SEGURA DO PROJETO (preserva Git/GitHub)
echo ========================================================
echo   De : %ORIG%
echo   Para: %DEST%
echo.
echo   A copia inclui a pasta .git, entao o vinculo com o
echo   GitHub e mantido. Nada e apagado da pasta original.
echo.
pause

if not exist "C:\Projetos" mkdir "C:\Projetos"

echo.
echo Copiando... (pode levar alguns minutos)
echo.
REM /E = subpastas (inclui .git)  /COPY:DAT = dados+atributos+datas
REM Exclui apenas caches regeneraveis. NAO exclui .git.
robocopy "%ORIG%" "%DEST%" /E /COPY:DAT /R:1 /W:1 /XD "__pycache__" ".pytest_cache" /NFL /NDL /NP

set RC=%ERRORLEVEL%
echo.
if %RC% GEQ 8 (
  echo [ERRO] robocopy retornou codigo %RC% - verifique acima.
  goto :fim
)
echo [OK] Copia concluida (codigo robocopy %RC%).

echo.
echo ========================================================
echo   Verificando o Git na NOVA pasta...
echo ========================================================
cd /d "%DEST%"
where git >nul 2>nul
if errorlevel 1 (
  echo   Git nao encontrado no PATH - abra o Git Bash na pasta
  echo   %DEST% e rode:  git status ^&^& git remote -v
) else (
  echo.
  echo --- git remote -v (deve mostrar o GitHub) ---
  git remote -v
  echo.
  echo --- git status ---
  git status -s -b
)

echo.
echo ========================================================
echo   PRONTO.
echo.
echo   Proximos passos:
echo   1) Passe a rodar a aplicacao a partir de:
echo      %DEST%
echo   2) So depois de confirmar que tudo funciona la,
echo      apague a pasta antiga dentro do OneDrive.
echo   3) Reconecte esta nova pasta no Cowork.
echo ========================================================
:fim
echo.
pause
endlocal
