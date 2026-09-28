@echo off
setlocal
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows.ps1" -Action Models %*
set "CASEFILE_EXIT=%ERRORLEVEL%"
if not defined CASEFILE_NO_PAUSE pause
exit /b %CASEFILE_EXIT%
