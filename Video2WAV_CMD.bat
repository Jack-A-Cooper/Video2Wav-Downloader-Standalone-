@echo off
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\python.exe" goto missing_venv
"%~dp0.venv\Scripts\python.exe" -c "import sys" >nul 2>nul
if errorlevel 1 goto missing_venv
"%~dp0.venv\Scripts\python.exe" video2wav.py
pause
exit /b
:missing_venv
echo Video2WAV virtual environment is missing or broken.
echo Run install_video2wav.bat, then try again.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\write_crashlog.ps1" -Context "cmd_launcher_missing_or_broken_venv" -Message "Video2WAV virtual environment is missing or broken." -Details "Launcher: Video2WAV_CMD.bat"
pause

