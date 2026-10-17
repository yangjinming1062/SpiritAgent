@echo off
REM Windows development wrapper: forward all arguments to install.ps1.

echo.
echo  SpiritAgent Agent Installer
echo  Launching PowerShell installer from %~dp0install.ps1 ...
echo.

powershell -ExecutionPolicy ByPass -NoProfile -File "%~dp0install.ps1" %*
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  Installation failed. See %LOCALAPPDATA%\SpiritAgent\logs\ for details.
    echo.
    pause
    exit /b 1
)
