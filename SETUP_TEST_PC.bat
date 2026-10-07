@echo off
rem ===========================================================================
rem  CityLand 9 - one-time setup of a TEST installation on this PC.
rem  Guide: CITYLAND9_TEST_PC_MIGRATION.md
rem
rem  Before running it, put the two files you received next to this file:
rem      cityland9_testpkg_<kind>_<date>.sql.gz   and   its .manifest.json
rem  (this folder, its parent folder, or database\test_packages also work).
rem
rem  It does, in order (a step that is already done is skipped, so it can be run again):
rem    1. finds Python 3.10+, creates .venv and installs the Python packages (internet the first time)
rem    2. creates .env for a test installation (new secret key, database cityland9_test, email off)
rem    3. creates this PC's own MariaDB database (XAMPP's programs, port 3307, this PC only)
rem    4. loads the data file (checksum checked; accounts are never in it)
rem    5. asks you to create this PC's Superadmin, then creates tester accounts (shown once)
rem    6. checks the installation is independent and local, then offers to start CityLand 9
rem  Needs: Python (python.org, "Add python.exe to PATH") and XAMPP (C:\xampp). Not Node.js.
rem ===========================================================================
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title CityLand 9 - test PC setup
set "PY=.venv\Scripts\python.exe"

echo.
echo  CITYLAND 9 - TEST PC SETUP
echo  ==========================
echo.

rem --- 1. Python and packages -----------------------------------------------------
if not exist "%PY%" (
    call :find_python
    if not defined BOOT (
        echo [X] Python 3.10 or newer is not installed.
        echo     Install it from https://www.python.org/downloads/ and tick
        echo     "Add python.exe to PATH" on the first screen of the installer, then run this again.
        goto :fail
    )
    echo Creating the Python environment ^(.venv^)...
    !BOOT! -m venv .venv
    if errorlevel 1 (
        if exist .venv rmdir /s /q .venv
        echo [X] Could not create the Python environment.
        goto :fail
    )
)
copy /b requirements.txt "%TEMP%\cl9-req.txt" >nul
fc /b "%TEMP%\cl9-req.txt" ".venv\cl9-requirements.installed" >nul 2>nul
if errorlevel 1 (
    echo Installing Python packages ^(needs internet the first time; takes a few minutes^)...
    "%PY%" -m pip install --disable-pip-version-check -q -r requirements.txt
    if errorlevel 1 (
        echo [X] Package installation failed. Check the internet connection and run this again.
        goto :fail
    )
    copy /y "%TEMP%\cl9-req.txt" ".venv\cl9-requirements.installed" >nul
) else (
    echo Python packages are already installed.
)

rem --- 2. Configuration -----------------------------------------------------------
"%PY%" database\test_pc.py make-env
if errorlevel 1 goto :fail

rem --- 3-6. Database, data, accounts, check (a new process: it reads the new .env) ---
"%PY%" database\test_pc.py setup
if errorlevel 1 (
    echo.
    echo [X] Setup did not finish. Read the message above, fix it, and run SETUP_TEST_PC.bat again.
    goto :fail
)

echo.
echo  SETUP COMPLETE. Write down the tester accounts shown above: they are not shown again.
echo  From now on, start CityLand 9 with START_WINDOWS.bat and stop it with STOP_WINDOWS.bat.
echo.
choice /c YN /m "Start CityLand 9 now"
if errorlevel 2 goto :end
call START_WINDOWS.bat
goto :end

:fail
echo.
pause
exit /b 1

:end
endlocal
exit /b 0

rem Sets BOOT to a working Python 3.10+ launcher, or leaves it empty (same test as START_WINDOWS.bat).
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
