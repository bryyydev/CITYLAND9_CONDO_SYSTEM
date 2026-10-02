@echo off
rem ===========================================================================
rem  CityLand 9 Condo Management System - start the server for LAN use.
rem
rem    START_WINDOWS.bat            normal start
rem    START_WINDOWS.bat --rebuild  rebuild the React app first (after updating the code)
rem
rem  What it does (docs\run-guide\README.md explains each step):
rem    1. checks Python (and Node.js only when the React app must be built)
rem    2. creates .venv and installs Python packages only when requirements change,
rem       so the server PC can start without internet
rem    3. builds the React app (frontend\dist) if it is missing or --rebuild is given
rem    4. starts CityLand's own MariaDB and checks the database
rem    5. starts the Waitress server on 0.0.0.0:5000 and shows the LAN address
rem  Stop the server: press Ctrl+C in this window, or close it (or run STOP_WINDOWS.bat).
rem  Set CL9_NO_BROWSER=1 to start without opening the browser.
rem ===========================================================================
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title CityLand 9 Condo System
set "PY=.venv\Scripts\python.exe"
set "PORT=5000"
for /f "usebackq tokens=1,* delims==" %%A in (`findstr /b /i "FLASK_PORT=" .env 2^>nul`) do set "PORT=%%B"

echo.
echo  CITYLAND 9 CONDO MANAGEMENT SYSTEM
echo  ==================================
echo.

rem --- 1. Configuration -------------------------------------------------------
if not exist ".env" (
    echo [X] The .env configuration file is missing.
    echo     First-time setup: see docs\run-guide\01-PREREQUISITES_AND_ENV.md
    echo     and docs\run-guide\02-DATABASE_SETUP.md
    goto :fail
)

rem --- 2. Python and packages -------------------------------------------------
if not exist "%PY%" (
    where py >nul 2>nul
    if errorlevel 1 (
        where python >nul 2>nul
        if errorlevel 1 (
            echo [X] Python is not installed. Install Python 3.11 or newer from python.org
            echo     and tick "Add python.exe to PATH" during setup.
            goto :fail
        )
        set "BOOT=python"
    ) else (
        set "BOOT=py -3"
    )
    echo [1/5] Creating the Python environment ^(.venv^)...
    !BOOT! -m venv .venv
    if errorlevel 1 goto :fail
)
echo [1/5] Python:
"%PY%" --version

rem Install packages only when requirements changed since the last install.
copy /b requirements.txt "%TEMP%\cl9-req.txt" >nul
fc /b "%TEMP%\cl9-req.txt" ".venv\cl9-requirements.installed" >nul 2>nul
if errorlevel 1 (
    echo [2/5] Installing Python packages ^(needs internet the first time^)...
    "%PY%" -m pip install --disable-pip-version-check -q -r requirements.txt
    if errorlevel 1 (
        echo [X] Package installation failed. Check the internet connection and try again.
        goto :fail
    )
    copy /y "%TEMP%\cl9-req.txt" ".venv\cl9-requirements.installed" >nul
) else (
    echo [2/5] Python packages are up to date.
)

rem --- 3. React app -----------------------------------------------------------
set "BUILD="
if not exist "frontend\dist\index.html" set "BUILD=1"
if /i "%~1"=="--rebuild" set "BUILD=1"
if defined BUILD (
    where npm >nul 2>nul
    if errorlevel 1 (
        echo [X] The React app needs to be built, but Node.js is not installed.
        echo     Install Node.js 20.19 or newer ^(LTS^) from nodejs.org, then run this again.
        goto :fail
    )
    echo [3/5] Building the React app ^(frontend\dist^)...
    pushd frontend
    if not exist "node_modules" call npm ci --no-audit --no-fund
    if errorlevel 1 (popd & echo [X] npm ci failed. & goto :fail)
    call npm run build
    if errorlevel 1 (popd & echo [X] The React build failed. The error is shown above. & goto :fail)
    popd
) else (
    echo [3/5] React app is built ^(use --rebuild after updating the code^).
)

rem --- 4. Port check and database ---------------------------------------------
netstat -ano | findstr /r /c:":%PORT% .*LISTENING" >nul
if not errorlevel 1 (
    echo [X] Port %PORT% is already in use. CityLand 9 may already be running in another window.
    echo     Close that window ^(or run STOP_WINDOWS.bat^) and try again.
    echo     See docs\run-guide\05-TROUBLESHOOTING.md, "Port 5000 is already in use".
    goto :fail
)
echo [4/5] Starting the database and checking it...
"%PY%" database\db_migrate.py check
if errorlevel 1 (
    echo [X] The database must be upgraded before this version can start. See the message above.
    goto :fail
)
rem Going through run.py starts CityLand's own MariaDB before the app connects to it.
"%PY%" -c "import sys; sys.path.insert(0, 'backend'); import run; run.init_db(); print('      Database: OK')"
if errorlevel 1 (
    echo [X] The database check failed. The error above shows the exact problem.
    echo     See docs\run-guide\05-TROUBLESHOOTING.md
    goto :fail
)

rem --- 5. Start the server ------------------------------------------------------
echo [5/5] Starting the server...
echo.
echo  ------------------------------------------------------------------
echo   This PC:        http://127.0.0.1:%PORT%
for /f "tokens=2 delims=:" %%A in ('ipconfig ^| findstr /r /c:"IPv4 Address"') do (
    set "IP=%%A"
    echo   Other PCs:      http://!IP: =!:%PORT%
)
echo.
echo   Keep this window open while the system is in use.
echo   To stop the server: press Ctrl+C here, or close this window.
echo  ------------------------------------------------------------------
echo.
if not defined CL9_NO_BROWSER start "" "http://127.0.0.1:%PORT%"
"%PY%" backend\run.py --lan
exit /b %errorlevel%

:fail
echo.
echo  The system was NOT started.
pause
exit /b 1
