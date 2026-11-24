@echo off
chcp 65001 >nul
setlocal
set PYTHONUTF8=1
cd /d "%~dp0"

set "PYEXE="
where python >nul 2>&1 && set "PYEXE=python"
if not defined PYEXE (
    where py >nul 2>&1 && set "PYEXE=py"
)

if not defined PYEXE (
    echo Python not found. Install Python 3.10+ with pygame.
    pause
    exit /b 1
)

%PYEXE% -c "import pygame" 2>nul
if errorlevel 1 (
    echo [INFO] Installing pygame...
    %PYEXE% -m pip install pygame
)

%PYEXE% dice_gui.py %*
pause
