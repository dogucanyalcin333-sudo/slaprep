@echo off
chcp 65001 >nul
rem SLAPrep hizli mod: kit klasorunu bu dosyanin ustune surukleyip birakin.
rem Varsayilan (veya kitte kayitli slaprep.yaml) ayarlarla hazirlar, bitince raporu acar.
rem Ayar secmek icin "SLAPrep Uygulama.bat" veya masaustu kisayolunu kullanin.
setlocal
set "HERE=%~dp0"
if "%~1"=="" (
    echo Kullanim: kit klasorunu slaprep.bat dosyasinin ustune surukleyip birakin.
    echo   veya:   slaprep.bat "E:\3d\STL Modeller\Kit"
    pause
    exit /b 1
)
set PYTHONIOENCODING=utf-8
"%HERE%.venv\Scripts\python.exe" -m slaprep prep "%~1"
if errorlevel 1 (
    echo.
    echo Hata olustu. Yukaridaki mesaji kontrol edin.
)
pause
