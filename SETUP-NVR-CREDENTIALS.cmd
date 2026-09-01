@echo off
setlocal EnableExtensions
chcp 65001 >nul
title Factory Monitor NVR Credential Setup

set "PROJECT_DIR=%~dp0"
set "PYTHON=%PROJECT_DIR%.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
  echo [FAILED] Project Python was not found. Run INSTALL-WINDOWS.cmd first.
  pause
  exit /b 1
)

echo.
echo This setup does not require access to the Huang Wei work group.
echo Obtain the four read-only NVR connection items through a company-approved secure channel.
echo Passwords are entered invisibly and stored only in this Windows user's Credential Manager.
echo Do not send passwords in chat and do not save them in the project directory.
echo.

"%PYTHON%" "%PROJECT_DIR%connector\gate_nvr_service.py" --setup-credentials
if errorlevel 1 (
  echo [FAILED] NVR credential setup did not complete.
  pause
  exit /b 1
)

"%PYTHON%" "%PROJECT_DIR%connector\gate_nvr_service.py" --credential-status
if errorlevel 1 (
  echo [FAILED] The four local NVR connection items are incomplete.
  pause
  exit /b 1
)

echo.
echo [PASS] Local NVR credentials are ready. The Huang Wei work group is not required.
echo Live video still requires the company network or approved VPN and NVR read-only authorization.
pause
exit /b 0
