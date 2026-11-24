@echo off
chcp 65001 >nul
setlocal
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

rem LRS_084 multi-speech EXPERIMENTAL launcher.
rem Default start.bat and the default three-choice speech are untouched.
rem Only heuristic AI is available here (research model is not adapted).

py -3.12 -c "import pygame" >nul 2>nul
if not errorlevel 1 (
  py -3.12 werewolf_gui.py --speech-mode multi %*
  goto done
)

python -c "import pygame" >nul 2>nul
if not errorlevel 1 (
  python werewolf_gui.py --speech-mode multi %*
  goto done
)

echo Python with pygame not found. Use the default start.bat instead.
:done
echo.
pause
