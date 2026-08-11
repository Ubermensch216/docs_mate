@echo off
chcp 949 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

title 업무기억관

echo ============================================
echo   업무기억관 (Work Memory)
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [오류] Python을 찾을 수 없습니다.
    echo         https://www.python.org 에서 Python 3.11 이상을 설치한 뒤
    echo         "Add python.exe to PATH"에 체크하고 다시 실행하세요.
    echo.
    pause
    exit /b 1
)

set VENV_DIR=.venv
set VENV_PY=%VENV_DIR%\Scripts\python.exe

if not exist "%VENV_PY%" (
    echo 처음 실행이라 실행 환경을 준비합니다. 잠시만 기다려주세요.
    python -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [오류] 실행 환경을 만들지 못했습니다.
        pause
        exit /b 1
    )
)

set STAMP=%VENV_DIR%\.requirements.stamp
set NEED_INSTALL=0

if not exist "%STAMP%" set NEED_INSTALL=1
if exist "%STAMP%" (
    fc /b "requirements.txt" "%STAMP%" >nul 2>nul
    if errorlevel 1 set NEED_INSTALL=1
)

if "!NEED_INSTALL!"=="1" (
    echo 필요한 구성 요소를 설치합니다. 처음에는 몇 분 걸릴 수 있습니다.
    echo   인터넷 연결이 필요합니다
    echo.
    "%VENV_PY%" -m pip install --upgrade pip --quiet
    "%VENV_PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo [오류] 설치 중 문제가 발생했습니다. 인터넷 연결을 확인하세요.
        pause
        exit /b 1
    )
    copy /y "requirements.txt" "%STAMP%" >nul
    echo.
    echo 설치가 끝났습니다.
    echo.
)

echo 업무기억관을 시작합니다.
echo.
"%VENV_PY%" -m app.main %*

if errorlevel 1 (
    echo.
    echo ------------------------------------------------
    echo 프로그램이 오류로 종료되었습니다. 위 내용을 확인하세요.
    echo ------------------------------------------------
    pause
)

endlocal
