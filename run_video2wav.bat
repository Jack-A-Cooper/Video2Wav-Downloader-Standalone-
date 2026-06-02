@echo off
cd /d "%~dp0"
if exist "%~dp0Video2WAV.exe" (
    "%~dp0Video2WAV.exe"
    goto end
)
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" video2wav.py
    goto end
)
echo Video2WAV executable and virtual environment were not found.
echo Run install_video2wav.bat, then try again.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\write_crashlog.ps1" -Context "legacy_launcher_missing_runtime" -Message "Video2WAV executable and virtual environment were not found." -Details "Launcher: run_video2wav.bat"
:end
pause
