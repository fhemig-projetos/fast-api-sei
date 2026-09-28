@echo off
REM ==========================================================================
REM  Empacota a interface grafica (interface_app.py) em um executavel .exe
REM
REM  Como usar (Prompt de Comando ou PowerShell, dentro da pasta do projeto):
REM      build_exe.bat
REM
REM  O .exe gerado NAO contem usuario nem senha: cada pessoa usa o proprio
REM  login do SEI, digitado na tela. Nao distribua o arquivo .env do projeto.
REM ==========================================================================

setlocal enabledelayedexpansion

REM --- Onde estamos ------------------------------------------------------
cd /d "%~dp0"

REM --- Python do ambiente virtual (o mesmo do projeto) --------------------
if exist "venv\Scripts\python.exe" (
    set "PY=venv\Scripts\python.exe"
) else (
    echo [ERRO] venv\Scripts\python.exe nao encontrado.
    echo        Rode antes:  python -m venv venv
    echo                    venv\Scripts\pip install -r requirements.txt
    exit /b 1
)

echo.
echo === Conferindo as dependencias da interface ===========================
"%PY%" -c "import customtkinter" 2>nul
if errorlevel 1 (
    echo [ERRO] customtkinter ausente. Instalando...
    "%PY%" -m pip install customtkinter
    if errorlevel 1 exit /b 1
)

REM --- O Tcl/Tk precisa existir DENTRO do venv -----------------------------
REM  O tkinter procura a pasta tcl8.6 ao lado do python.exe. Num venv do
REM  Windows ela nao vem: sem ela o customtkinter importa mas a janela NAO
REM  abre, e o PyInstaller avisa "tkinter installation is broken" e deixa o
REM  tkinter FORA do .exe (que quebra com "No module named 'tkinter'").
REM  Copiamos do Python base. Confira se a sua versao tambem precisa:
REM      "%PY%" -c "import tkinter; tkinter.Tk()"
if not exist "venv\tcl\tcl8.6" (
    echo.
    echo === Copiando a pasta Tcl/Tk para dentro do venv ======================
    for /f "delims=" %%P in ('"%PY%" -c "import sys,os;print(os.path.join(sys.base_prefix,'tcl'))"') do set "TCLBASE=%%P"
    if not exist "!TCLBASE!" (
        echo [ERRO] Nao achei a pasta tcl do Python base em "!TCLBASE!"
        echo        Instale o Python com Tcl/Tk e tente de novo.
        exit /b 1
    )
    echo     origem: !TCLBASE!
    xcopy /E /I /Y "!TCLBASE!" "venv\tcl" >nul
)

REM --- Teste final: a interface realmente abre? ---------------------------
"%PY%" -c "import customtkinter, tkinter; tkinter.Tk().destroy()" >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERRO] O tkinter nao abre neste venv - o .exe sairia quebrado.
    echo        See a secao "Interface grafica" do README.md.
    exit /b 1
)
echo [OK] Interface e tkinter verificados.

echo.
echo === Limpando builds anteriores =========================================
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist
if exist AutomacaoSEI.spec del /q AutomacaoSEI.spec

echo.
echo === Compilando (pode levar 1 a 3 minutos) =============================
REM  --onefile  : tudo em um unico .exe
REM  --windowed : sem a janela preta do console
REM  --collect-all customtkinter + --add-data : os temas/fontes/icones do
REM    customtkinter sao arquivos separados; sem embutir-los o .exe abre em
REM    branco ou acusa modulo faltando.
REM  --add-data venv\tcl : o Tcl/Tk e necessario para a JANELA existir.
"%PY%" -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onefile ^
    --windowed ^
    --name AutomacaoSEI ^
    --paths "venv\Lib\site-packages" ^
    --collect-all customtkinter ^
    --add-data "venv\Lib\site-packages\customtkinter;customtkinter" ^
    --add-data "venv\tcl;_tcl_data" ^
    --hidden-import customtkinter ^
    --hidden-import PIL ^
    interface_app.py

if errorlevel 1 (
    echo.
    echo [ERRO] Falha na compilacao. Veja a mensagem acima.
    exit /b 1
)

REM --- Conferindo se o .exe realmente saiu -------------------------------
if not exist "dist\AutomacaoSEI.exe" (
    echo.
    echo [ERRO] A compilacao terminou sem gerar dist\AutomacaoSEI.exe.
    echo        O arquivo AutomacaoSEI.spec que aparece na pasta e so a
    echo        "receita" do build - NAO e o programa. O programa e o .exe.
    echo        Leia as mensagens acima para ver a causa.
    exit /b 1
)

echo.
echo === Pronto =============================================================
echo.
echo    >>> PROGRAMA GERADO:  dist\AutomacaoSEI.exe  <<<
echo.
echo    (o arquivo AutomacaoSEI.spec na pasta e so a receita do build;
echo     ignore-o. Para usar o programa, de dois cliques no .exe acima)
echo.
echo    Distribuicao:
echo      - envie APENAS o dist\AutomacaoSEI.exe
echo      - NUNCA envie a pasta venv nem o arquivo .env do projeto
echo      - quem for usar digita o proprio usuario e senha do SEI na tela
echo.
echo    Dica: --onefile descompacta em %%TEMP%% a cada execucao e leva cerca
echo    de 1 minuto para abrir. Para uso diario, comente a linha --onefile
echo    e descomente a versao --onedir abaixo: abre instantaneamente, ao
echo    custo de enviar uma pasta em vez de um arquivo.
echo.

REM --- Versao alternativa: --onedir (recomendada para uso frequente) -------
REM "%PY%" -m PyInstaller ^
REM     --noconfirm ^
REM     --clean ^
REM     --windowed ^
REM     --name AutomacaoSEI ^
REM     --paths "venv\Lib\site-packages" ^
REM     --collect-all customtkinter ^
REM     --add-data "venv\Lib\site-packages\customtkinter;customtkinter" ^
REM     --hidden-import customtkinter ^
REM     interface_app.py

endlocal
