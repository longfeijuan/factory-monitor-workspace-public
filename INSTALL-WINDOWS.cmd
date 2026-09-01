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

echo [3/4] Installing dependencies and running Windows quality gates...
set "INSTALL_ARGS="
if /I "%FACTORY_MONITOR_SKIP_OPEN%"=="1" set "INSTALL_ARGS=-NoCodexLaunch"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_DIR%\scripts\install-windows.ps1" %INSTALL_ARGS%
if errorlevel 1 (
  echo [FAILED] Installation or quality gates failed. Send the full console output to the project maintainer.
  if /I not "!FACTORY_MONITOR_NONINTERACTIVE!"=="1" pause
  exit /b 1
)

echo [4/4] Complete.
echo Project directory: %PROJECT_DIR%
echo Offline quality gates passed. Live video still requires the user's authorized login, company network, and NVR read-only permission.
if /I not "%FACTORY_MONITOR_SKIP_OPEN%"=="1" echo Codex opened the factory monitor project and is ready for questions.
if /I not "%FACTORY_MONITOR_NONINTERACTIVE%"=="1" pause
exit /b 0
