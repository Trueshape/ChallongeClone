@echo off
setlocal enabledelayedexpansion
title ChallongeClone
cd /d "%~dp0"

set "PYCMD="

where python >nul 2>nul
if %errorlevel%==0 (
    set "PYCMD=python"
) else (
    where py >nul 2>nul
    if %errorlevel%==0 (
        set "PYCMD=py"
    )
)

if "!PYCMD!"=="" (
    echo.
    echo  Python non e' installato ^(o non e' nel PATH^).
    echo.
    set /p INSTALLA="Vuoi che lo installi ora automaticamente? (S/N): "
    if /i "!INSTALLA!"=="S" call :install_python

    where python >nul 2>nul && set "PYCMD=python"
    if "!PYCMD!"=="" (
        for /d %%D in ("%LocalAppData%\Programs\Python\Python3*") do (
            if exist "%%D\python.exe" set "PYCMD=%%D\python.exe"
        )
    )

    if "!PYCMD!"=="" (
        echo.
        echo  Non sono riuscito a installarlo automaticamente ^(o hai risposto N^).
        echo  Scaricalo da: https://www.python.org/downloads/
        echo  Durante l'installazione spunta "Add python.exe to PATH".
        echo  Poi rilancia questo file.
        echo.
        pause
        exit /b 1
    )
)

"!PYCMD!" -c "import webview" >nul 2>nul
if not !errorlevel!==0 (
    echo Installo la libreria "pywebview" ^(serve una sola volta^)...
    "!PYCMD!" -m pip install --quiet pywebview
    if not !errorlevel!==0 (
        echo.
        echo  Installazione di "pywebview" non riuscita. Prova a lanciare a mano:
        echo    !PYCMD! -m pip install pywebview
        echo.
        pause
        exit /b 1
    )
)

"!PYCMD!" "%~dp0challonge_clone.py" %*
if not !errorlevel!==0 pause
exit /b

:install_python
where winget >nul 2>nul
if %errorlevel%==0 (
    echo Installo Python con winget, un momento...
    winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
    exit /b
)
echo winget non disponibile: scarico l'installer da python.org...
set "PYEXE=%TEMP%\python-installer.exe"
powershell -NoProfile -Command "try { Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.6/python-3.12.6-amd64.exe' -OutFile '%PYEXE%' } catch { exit 1 }"
if not exist "%PYEXE%" (
    echo Download non riuscito.
    exit /b
)
echo Installo Python ^(nessun clic necessario^)...
"%PYEXE%" /passive InstallAllUsers=0 PrependPath=1 Include_launcher=1
del "%PYEXE%" >nul 2>nul
exit /b
