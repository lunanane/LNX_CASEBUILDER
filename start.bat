@echo off
rem ---------------------------------------------------------------------------
rem  hwcase -- start the editor.
rem
rem    start.bat            first free port from 8487, opens a browser
rem    start.bat 9000       pick the port yourself
rem    start.bat 9000 bare  no auto-reload, no browser
rem
rem  Everything lives in .venv next to this file, so this never touches your
rem  system Python and is safe to delete and re-run.
rem ---------------------------------------------------------------------------
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "VENV=%CD%\.venv"
set "PY=%VENV%\Scripts\python.exe"
set "REQ=%CD%\backend\requirements.txt"
set "STAMP=%VENV%\requirements.stamp"
set "MODE=%~2"

title hwcase editor

rem --- 1. find a Python to bootstrap from ------------------------------------
if exist "%PY%" goto :have_venv

set "BOOT="
py -3 -c "import sys" >nul 2>&1 && set "BOOT=py -3"
if not defined BOOT python -c "import sys" >nul 2>&1 && set "BOOT=python"
if not defined BOOT (
  echo.
  echo   [x] No Python found on PATH.
  echo       Install Python 3.10+ from https://www.python.org/downloads/
  echo       and tick "Add python.exe to PATH", then run this again.
  goto :fail
)

echo   [setup] creating virtual environment in .venv ...
%BOOT% -m venv "%VENV%"
if errorlevel 1 goto :fail
if not exist "%PY%" (
  echo   [x] venv creation reported success but %PY% is missing.
  goto :fail
)

:have_venv

rem --- 2. install dependencies, but only when they actually changed ----------
set "NEED=1"
if exist "%STAMP%" (
  fc /b "%REQ%" "%STAMP%" >nul 2>&1
  if not errorlevel 1 set "NEED=0"
)
if "%NEED%"=="1" (
  echo   [setup] installing dependencies ...
  "%PY%" -m pip install --quiet --upgrade pip
  "%PY%" -m pip install --quiet -r "%REQ%"
  if errorlevel 1 (
    echo   [x] pip install failed -- see the output above.
    goto :fail
  )
  copy /y "%REQ%" "%STAMP%" >nul
)

rem --- 3. sanity check: can we import the engine at all? ---------------------
pushd backend
"%PY%" -c "import hwcase, hwcase.api" >nul 2>&1
if errorlevel 1 (
  echo.
  echo   [x] the hwcase package failed to import. Full error:
  echo.
  "%PY%" -c "import hwcase, hwcase.api"
  popd
  goto :fail
)
popd

rem --- 4. pick a port that is actually free ----------------------------------
set "PORT=%~1"
rem 8487 is ours. It used to be 8765, until another app on this machine took
rem that port and the editor's bookmark quietly started opening the wrong
rem program -- the scan-upward logic kept the server running, on a port
rem nobody was looking at. Distinctive beats conventional here.
if not defined PORT set "PORT=8487"
set /a "TRY=0"
:portloop
netstat -ano | findstr /r /c:":%PORT% .*LISTENING" >nul 2>&1
if not errorlevel 1 (
  set /a "PORT+=1"
  set /a "TRY+=1"
  if !TRY! lss 20 goto :portloop
  echo   [x] no free port between %~1 and !PORT!.
  goto :fail
)

rem --- 5. go ----------------------------------------------------------------
set "URL=http://127.0.0.1:%PORT%/"
echo.
echo   hwcase editor
echo   ---------------------------------------------
echo   url      %URL%
echo   parts    backend\parts\*.yaml
echo   scenes   backend\scenes\*.yaml
echo   stop     Ctrl+C in this window
echo.

set "EXTRA=--reload --reload-dir hwcase --reload-include *.yaml"
if /i "%MODE%"=="bare" set "EXTRA="
if /i not "%MODE%"=="bare" start "" /b cmd /c "timeout /t 3 >nul & start "" "%URL%""

pushd backend
"%PY%" -m uvicorn hwcase.api:app --host 127.0.0.1 --port %PORT% %EXTRA%
set "RC=%ERRORLEVEL%"
popd

if not "%RC%"=="0" (
  echo.
  echo   [x] the server exited with code %RC%.
  goto :fail
)
echo.
echo   server stopped.
endlocal
exit /b 0

:fail
echo.
pause
endlocal
exit /b 1
