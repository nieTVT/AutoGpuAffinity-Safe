@echo off
setlocal
cd /d "%~dp0"
set AUTOGPU_NO_PAUSE=1
title AutoGpuAffinity - Safe Mode
echo ==============================================================
echo   SAFE MODE   -   the display adapter is never touched
echo --------------------------------------------------------------
echo   Every cpu is benchmarked by starting the benchmark process
echo   pinned to that cpu with an /affinity mask.
echo   The display adapter is NEVER disabled or re-enabled, so this
echo   cannot black-screen the machine.
echo.
echo   Administrator rights are still requested below, because
echo   PresentMon needs them to start an ETW trace session for the
echo   frametime capture.  Elevation on its own is harmless here:
echo   nothing in this mode touches the display device or the gpu
echo   driver state.
echo ==============================================================
echo.

net session >nul 2>&1
if errorlevel 1 (
    echo Requesting administrator rights...
    powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0'"
    exit /b
)

py main.py --config config.safe.ini %*
echo.
pause