@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title NunchiCoach

echo ============================================
echo   NunchiCoach launcher
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found on PATH.
    echo         Install Python 3.11+ from https://www.python.org
    echo         and check "Add python.exe to PATH" during setup.
    echo.
    pause
    exit /b 1
)

set VENV_DIR=.venv
set VENV_PY=%VENV_DIR%\Scripts\python.exe

if not exist "%VENV_PY%" (
    echo First run detected. Setting up the environment, please wait...
    python -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [ERROR] Failed to create the virtual environment.
        echo         If a Microsoft Store window opened, Python is not
        echo         really installed yet even though "python" was found.
        pause
        exit /b 1
    )
)

"%VENV_PY%" --version >nul 2>nul
if errorlevel 1 (
    echo [ERROR] The Python environment looks broken.
    echo         Delete the ".venv" folder next to this script and try again.
    pause
    exit /b 1
)

set STAMP=%VENV_DIR%\.requirements.stamp
set NEED_INSTALL=0

if not exist "%STAMP%" set NEED_INSTALL=1
if exist "%STAMP%" (
    fc /b "requirements.txt" "%STAMP%" >nul 2>nul
    if errorlevel 1 set NEED_INSTALL=1
)

if "!NEED_INSTALL!"=="1" (
    echo Installing required packages. This can take several minutes
    echo the first time ^(internet connection required^)...
    echo.
    REM Do not run "pip install --upgrade pip" here: on Windows this can
    REM fail with a file-lock error ^(WinError 32^) while pip tries to
    REM replace its own running exe, and a failed upgrade can remove the
    REM old pip without installing the new one - leaving no pip at all.
    REM The pip bundled with a fresh venv is new enough for our packages.
    "%VENV_PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo [ERROR] Package installation failed. Check your internet connection.
        pause
        exit /b 1
    )
    copy /y "requirements.txt" "%STAMP%" >nul
    echo.
    echo Installation complete.
    echo.
)

echo Starting NunchiCoach now.
echo The window may take a few seconds to appear - please wait.
echo.
"%VENV_PY%" -m app.main %*
set APP_EXIT=%ERRORLEVEL%

if not "%APP_EXIT%"=="0" (
    echo.
    echo ------------------------------------------------
    echo The program exited with an error ^(code %APP_EXIT%^).
    echo Please check the messages above.
    echo ------------------------------------------------
    pause
)

endlocal
