@echo off
setlocal
set "APP=%~dp0app"
rem Fast path: the venv already holds exactly this requirements.txt, so every setup check is skipped.
if not exist "%APP%\.venv\Scripts\python.exe" goto setup
fc /b "%APP%\requirements.txt" "%APP%\.venv\requirements.installed" >nul 2>&1 || goto setup
pushd "%APP%"
"%APP%\.venv\Scripts\python.exe" -m audio_stems.desktop
set "CODE=%errorlevel%"
popd
rem Exit code 3 means a dependency is missing: run the full setup, which reinstalls it.
if "%CODE%"=="3" (
  del "%APP%\.venv\requirements.installed" >nul 2>&1
  goto setup
)
if not "%CODE%"=="0" pause
exit /b %CODE%

:setup
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%APP%\Start-Audio-Stems.ps1"
if errorlevel 1 pause
