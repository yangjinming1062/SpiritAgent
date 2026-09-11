@echo off
REM SpiritAgent 安装器（CMD 包装）：转发到同目录下 install.ps1。
REM 仅用于只有 install.cmd 可用的 CMD 环境；能直接用 PowerShell 跑 install.ps1 时优先走 install.ps1。
REM 所有参数原样透传给 install.ps1。

setlocal
set SCRIPT_DIR=%~dp0

echo.
echo  SpiritAgent Agent Installer
echo  Launching PowerShell installer from %SCRIPT_DIR%install.ps1 ...
echo.

powershell -ExecutionPolicy ByPass -NoProfile -File "%SCRIPT_DIR%install.ps1" %*
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  Installation failed. See %%LOCALAPPDATA%%\spiritagent\logs\ for details.
    echo.
    pause
    exit /b 1
)
endlocal
