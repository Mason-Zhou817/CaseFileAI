@echo off
setlocal
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows.ps1" -Action Start %*
set "CASEFILE_EXIT=%ERRORLEVEL%"
if not "%CASEFILE_EXIT%"=="0" if not defined CASEFILE_NO_PAUSE pause
exit /b %CASEFILE_EXIT%
