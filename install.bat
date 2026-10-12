@echo off
chcp 65001 >nul
rem SLAPrep kurulumu: sanal ortam + paketler + masaustu kisayolu. Klasor tasindiysa tekrar calistirin.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Sanal ortam olusturuluyor...
    py -3.13 -m venv .venv 2>nul || py -3 -m venv .venv || python -m venv .venv
)
if not exist ".venv\Scripts\python.exe" (
    echo Python bulunamadi. python.org'dan Python 3.11+ kurun ve tekrar deneyin.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 (
    echo Paket kurulumu basarisiz.
    pause
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\create_shortcut.ps1"
echo.
echo Kurulum tamam. Masaustundeki "SLAPrep" kisayolunu kullanin
echo (kit klasorunu kisayolun veya pencerenin ustune surukleyip birakabilirsiniz).
pause
