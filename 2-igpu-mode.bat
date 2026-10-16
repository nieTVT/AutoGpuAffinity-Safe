@echo off
setlocal
cd /d "%~dp0"
set AUTOGPU_NO_PAUSE=1
title AutoGpuAffinity - iGPU Mode

echo ==============================================================
echo   IGPU MODE   (requires administrator rights)
echo --------------------------------------------------------------
echo   Restarts ONLY the NVIDIA adapter (PCI\VEN_10DE) to apply a
echo   real gpu interrupt affinity policy.
echo   ONLY use this when the monitor is plugged into the INTEGRATED
echo   gpu, otherwise the restart can leave you with a black screen.
echo ==============================================================
echo.

net session >nul 2>&1
if errorlevel 1 (
    echo Requesting administrator rights...
    powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0'"
    exit /b
)

py main.py --config config.igpu.ini %*
echo.
pause