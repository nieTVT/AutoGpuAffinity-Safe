@echo off
setlocal
cd /d "%~dp0"
set AUTOGPU_NO_PAUSE=1
title AutoGpuAffinity - Original Mode

echo ==============================================================
echo   ORIGINAL MODE   *** DANGEROUS ***   (requires admin)
echo --------------------------------------------------------------
echo   This is the ORIGINAL upstream behaviour: EVERY present,
echo   non-virtual display adapter is disabled and re-enabled once
echo   per benchmarked cpu.
echo.
echo   If one of those adapters drives your display you can end up
echo   with a black screen until you reboot.
echo.
echo   Recovery:  py main.py --restore-display   (as administrator)
echo ==============================================================
echo.
set /p CONFIRM=Type YES to continue: 
if /i not "%CONFIRM%"=="YES" (
    echo aborted.
    pause
    exit /b 1
)

net session >nul 2>&1
if errorlevel 1 (
    echo Requesting administrator rights...
    powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0'"
    exit /b
)

py main.py --config config.original.ini %*
echo.
pause