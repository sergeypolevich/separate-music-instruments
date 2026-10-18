@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\Start-Audio-Stems.ps1"
if errorlevel 1 pause
