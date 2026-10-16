@echo off
setlocal
cd /d "%~dp0"
set AUTOGPU_NO_PAUSE=1
title AutoGpuAffinity - Safe Mode
echo ==============================================================
echo   SAFE MODE   (no administrator rights required)
echo --------------------------------------------------------------
echo   The display adapter is NEVER disabled or re-enabled.
echo   Every cpu is benchmarked by starting the benchmark process
echo   pinned to that cpu (/affinity mask).
echo   This cannot black-screen the machine.
echo ==============================================================
echo.
py main.py --config config.safe.ini %*
echo.
pause