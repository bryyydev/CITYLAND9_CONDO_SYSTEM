@echo off
rem ===========================================================================
rem  CityLand 9 Condo Management System - start the server for LAN use.
rem
rem    START_WINDOWS.bat            normal start
rem    START_WINDOWS.bat --rebuild  rebuild the React app first (after updating the code)
rem
rem  What it does (docs\run-guide\README.md explains each step):
rem    1. checks Python (and Node.js only when the React app must be built); on a new PC
rem       (no .env yet) runs scripts\first_run_setup.py: configuration, database, demo data
rem    2. creates .venv and installs Python packages only when requirements change,
rem       so the server PC can start without internet
rem    3. builds the React app (frontend\dist) when it is missing, older than the code
rem       (compared by content, scripts\frontend_build.py) or --rebuild is given
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

rem --- 1. Python ---------------------------------------------------------------
rem A .venv copied from another PC (e.g. inside a zip) points to that PC's Python: rebuild it.
if exist "%PY%" (
    "%PY%" -c "import sys" >nul 2>nul
    if errorlevel 1 (
        echo [1/5] The Python environment ^(.venv^) was made on another PC. Rebuilding it...
        rmdir /s /q .venv
    )
)
if not exist "%PY%" (
    call :find_python
    if not defined BOOT (
        echo [X] Python 3.10 or newer is not installed.
        echo     Install it from https://www.python.org/downloads/ and tick
        echo     "Add python.exe to PATH" on the first screen of the installer, then run this again.
        goto :fail
    )
    rem Python packages have deep folders; with Windows' 260-character path limit a long folder path fails.
    !BOOT! -c "import os, sys; sys.exit(1 if len(os.getcwd()) > 120 else 0)" >nul 2>nul
    if errorlevel 1 (
        echo [X] This folder's path is too long for Windows ^(more than 120 characters^):
        echo     !CD!
        echo     Move the CITYLAND9 folder somewhere short, for example C:\CITYLAND9 or your Desktop,
        echo     then run START_WINDOWS.bat again.
        goto :fail
    )
    echo [1/5] Creating the Python environment ^(.venv^) with !BOOT!...
    !BOOT! -m venv .venv
    if errorlevel 1 (
        if exist .venv rmdir /s /q .venv
        echo [X] Could not create the Python environment.
        echo     - Extract the zip first ^(don't run START_WINDOWS.bat from inside the zip^).
        echo     - Use a short folder path, e.g. C:\CITYLAND9 ^(Windows limits paths to 260 characters^).
        goto :fail
    )
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

rem --- First run on this PC: configuration, database, demo data, Superadmin ------
rem (A downloaded copy has no .env: it holds secrets and is never shared.)
if not exist ".env" (
    echo.
    "%PY%" scripts\first_run_setup.py
    if errorlevel 1 goto :fail
    if not exist ".env" goto :fail
    for /f "usebackq tokens=1,* delims==" %%A in (`findstr /b /i "FLASK_PORT=" .env 2^>nul`) do set "PORT=%%B"
)

rem --- 3. React app -----------------------------------------------------------
rem BUILD=required: no usable build (missing, or a prototype/mock build) or --rebuild was given.
rem BUILD=outdated: the code changed since the last build. Rebuilt when Node.js is installed;
rem without Node.js the existing build is used (offline start) and a warning is shown.
set "BUILD="
"%PY%" scripts\frontend_build.py status
set "BUILD_STATUS=%errorlevel%"
if "%BUILD_STATUS%"=="1" set "BUILD=outdated"
if "%BUILD_STATUS%"=="2" set "BUILD=required"
if "%BUILD_STATUS%"=="3" set "BUILD=required"
if /i "%~1"=="--rebuild" set "BUILD=required"
if defined BUILD (
    where npm >nul 2>nul
    if errorlevel 1 (
        if "!BUILD!"=="outdated" (
            echo [!] The React app is older than the code, and Node.js is not installed to rebuild it.
            echo     Starting with the existing build. Screens changed since then will look old.
            echo     To update: install Node.js 20.19+ ^(LTS^) and run  START_WINDOWS.bat --rebuild
            set "BUILD="
        ) else (
            echo [X] The React app must be built, but Node.js is not installed.
            echo     Install Node.js 20.19 or newer ^(LTS^) from nodejs.org, then run this again.
            goto :fail
        )
    )
)
if defined BUILD (
    echo [3/5] Building the React app ^(frontend\dist^)...
    pushd frontend
    if not exist "node_modules" call npm ci --no-audit --no-fund
    if errorlevel 1 (popd & echo [X] npm ci failed ^(needs internet the first time^). The system was not started. & goto :fail)
    call npm run build
    if errorlevel 1 (popd & echo [X] The React build failed. The error is shown above; the old build was NOT used. & goto :fail)
    popd
    "%PY%" scripts\frontend_build.py stamp
    if errorlevel 1 (echo [X] The new build could not be verified as the live system. & goto :fail)
    echo       React app: built.
) else (
    if "%BUILD_STATUS%"=="0" echo [3/5] React app is up to date.
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

rem Sets BOOT to a working Python 3.10+ launcher, or leaves it empty. "python" on a PC without
rem Python is often the Microsoft Store shortcut, which only opens the Store: it is tested, not trusted.
:find_python
set "BOOT="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 (
    set "BOOT=py -3"
    exit /b 0
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 set "BOOT=python"
exit /b 0
