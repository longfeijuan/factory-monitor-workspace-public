@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
title Factory Monitor Windows Installer

set "REPOSITORY_URL=https://github.com/longfeijuan/factory-monitor-workspace-public.git"
if defined FACTORY_MONITOR_INSTALL_ROOT (
  set "INSTALL_ROOT=%FACTORY_MONITOR_INSTALL_ROOT%"
) else (
  set "INSTALL_ROOT=%USERPROFILE%\Codex"
)
set "PROJECT_DIR=%INSTALL_ROOT%\factory-monitor-workspace-public"

echo.
echo [1/4] Checking Git...
where git >nul 2>nul
if errorlevel 1 (
  where winget >nul 2>nul
  if errorlevel 1 (
    echo [FAILED] Git and winget were not found. Install App Installer from Microsoft Store first.
    if /I not "!FACTORY_MONITOR_NONINTERACTIVE!"=="1" pause
    exit /b 1
  )
  winget install --id Git.Git --exact --source winget --accept-package-agreements --accept-source-agreements --disable-interactivity
  set "PATH=%PATH%;C:\Program Files\Git\cmd"
)
where git >nul 2>nul
if errorlevel 1 (
  echo [FAILED] Git is still unavailable. Restart Windows and run this installer again.
  if /I not "!FACTORY_MONITOR_NONINTERACTIVE!"=="1" pause
  exit /b 1
)

echo [2/4] Downloading the factory monitor project...
if exist "%PROJECT_DIR%\.git" (
  set "CURRENT_REMOTE="
  set "DIRTY_TREE="
  for /f "delims=" %%R in ('git -C "%PROJECT_DIR%" remote get-url origin 2^>nul') do set "CURRENT_REMOTE=%%R"
  if /I not "!CURRENT_REMOTE!"=="%REPOSITORY_URL%" (
    echo [FAILED] The target directory is already another Git repository: %PROJECT_DIR%
    if /I not "!FACTORY_MONITOR_NONINTERACTIVE!"=="1" pause
    exit /b 1
  )
  for /f "delims=" %%S in ('git -C "%PROJECT_DIR%" status --porcelain') do set "DIRTY_TREE=1"
  if defined DIRTY_TREE (
    echo [FAILED] The existing project has uncommitted changes. The installer will not overwrite them.
    if /I not "!FACTORY_MONITOR_NONINTERACTIVE!"=="1" pause
    exit /b 1
  )
  git -C "%PROJECT_DIR%" pull --ff-only origin main
) else (
  if not exist "%INSTALL_ROOT%" mkdir "%INSTALL_ROOT%"
  git clone --branch main --single-branch "%REPOSITORY_URL%" "%PROJECT_DIR%"
)
if errorlevel 1 (
  echo [FAILED] The public repository is unavailable. Check the network connection to GitHub and run the installer again.
  if /I not "!FACTORY_MONITOR_NONINTERACTIVE!"=="1" pause
  exit /b 1
)

echo [3/4] Installing dependencies, running quality gates, and preparing local NVR access...
set "INSTALL_ARGS="
if /I "%FACTORY_MONITOR_SKIP_OPEN%"=="1" set "INSTALL_ARGS=!INSTALL_ARGS! -NoCodexLaunch"
if /I "%FACTORY_MONITOR_SKIP_CREDENTIAL_SETUP%"=="1" set "INSTALL_ARGS=!INSTALL_ARGS! -SkipCredentialSetup"
if /I "%FACTORY_MONITOR_NONINTERACTIVE%"=="1" set "INSTALL_ARGS=!INSTALL_ARGS! -SkipCredentialSetup"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_DIR%\scripts\install-windows.ps1" %INSTALL_ARGS%
if errorlevel 1 (
  echo [FAILED] Installation or quality gates failed. Send the full console output to the project maintainer.
  if /I not "!FACTORY_MONITOR_NONINTERACTIVE!"=="1" pause
  exit /b 1
)

echo [4/4] Complete.
echo Project directory: %PROJECT_DIR%
echo The reviewed camera package is built in. Ordinary users do not need access to the Huang Wei work group.
echo Live video requires four locally stored read-only NVR connection items and the company network or approved VPN.
echo If credential setup was skipped, run SETUP-NVR-CREDENTIALS.cmd in the project directory later.
if /I not "%FACTORY_MONITOR_SKIP_OPEN%"=="1" echo Codex opened the factory monitor project and is ready for questions.
if /I not "%FACTORY_MONITOR_NONINTERACTIVE%"=="1" pause
exit /b 0
