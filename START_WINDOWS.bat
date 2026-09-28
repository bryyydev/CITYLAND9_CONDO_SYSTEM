@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -m venv .venv
    if errorlevel 1 goto :error
)
echo Checking/installing dependencies...
.venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 goto :piperror
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto :piperror

echo Verifying application startup...
.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, 'backend'); import legacy_app; print('Application import: OK')"
if errorlevel 1 goto :apperror

echo Initializing/checking the database...
.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, 'backend'); import legacy_app; legacy_app.init_db(); print('Database initialization: OK')"
if errorlevel 1 goto :dberror

echo.
echo Starting CityLand 9 Condo Web System for LAN use...
echo.
for /f "tokens=2 delims=:" %%A in ('ipconfig ^| findstr /R /C:"IPv4 Address"') do echo Server IP: %%A
echo.
echo Other PCs can open: http://SERVER-IP:5000
echo Example: http://192.168.1.25:5000
echo.
echo Opening the Condo System in your default browser...
start "" "http://127.0.0.1:5000"
echo.
.venv\Scripts\python.exe backend\run.py --lan
exit /b 0
:piperror
echo.
echo Dependency installation failed.
echo Please check your internet connection and Python installation.
pause
exit /b 1
:apperror
echo.
echo backend\legacy_app.py could not be loaded. The error above shows the exact problem.
pause
exit /b 1
:dberror
echo.
echo Database initialization failed. The error above shows the exact problem.
pause
exit /b 1
:error
echo.
echo Setup failed. Check that Python is installed and internet access is available for pip.
pause
exit /b 1
