[CmdletBinding()]
param(
    [switch]$SkipSystemDependencies,
    [switch]$SkipValidation,
    [switch]$NoCodexLaunch
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$previousLocation = Get-Location
$venvDirectory = Join-Path $projectRoot '.venv'
$venvPython = Join-Path $venvDirectory 'Scripts\python.exe'

[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

function Refresh-ProcessPath {
    $processPath = $env:Path
    $machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $extraPaths = @(
        'C:\Program Files\Git\cmd',
        'C:\Program Files\nodejs',
        (Join-Path $env:APPDATA 'npm'),
        (Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps')
    )
    $env:Path = (@($processPath, $machinePath, $userPath) + $extraPaths | Where-Object { $_ }) -join ';'
}

function Get-AvailableCommand {
    param([Parameter(Mandatory = $true)][string[]]$Names)

    foreach ($name in $Names) {
        $candidate = Get-Command $name -ErrorAction SilentlyContinue
        if ($candidate) {
            return $candidate.Source
        }
    }
    return $null
}

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [Parameter(Mandatory = $true)][string]$Description
    )

    Write-Host "[RUN] $Description" -ForegroundColor Cyan
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE"
    }
}

function Invoke-Winget {
    param(
        [Parameter(Mandatory = $true)][ValidateSet('install', 'upgrade')][string]$Operation,
        [Parameter(Mandatory = $true)][string]$PackageId,
        [string]$Source = 'winget'
    )

    $winget = Get-AvailableCommand -Names @('winget.exe', 'winget')
    if (-not $winget) {
        throw "winget is required to install $PackageId. Install App Installer from Microsoft Store first."
    }
    $arguments = @(
        $Operation,
        '--id', $PackageId,
        '--exact',
        '--source', $Source,
        '--accept-package-agreements',
        '--accept-source-agreements',
        '--disable-interactivity'
    )
    & $winget @arguments
    Refresh-ProcessPath
}

function Get-NodeVersion {
    $node = Get-AvailableCommand -Names @('node.exe', 'node')
    if (-not $node) {
        return $null
    }
    $raw = & $node --version
    if ($LASTEXITCODE -ne 0 -or -not $raw) {
        return $null
    }
    return [version](($raw | Select-Object -First 1).Trim().TrimStart('v'))
}

function Set-BootstrapPython {
    $launcher = Get-AvailableCommand -Names @('py.exe', 'py')
    if ($launcher) {
        & $launcher -3.11 -c 'import sys; print(sys.executable)' *> $null
        if ($LASTEXITCODE -eq 0) {
            $script:bootstrapPython = $launcher
            $script:bootstrapPythonPrefix = @('-3.11')
            return $true
        }
    }

    foreach ($name in @('python.exe', 'python', 'python3.exe', 'python3')) {
        $candidate = Get-AvailableCommand -Names @($name)
        if (-not $candidate) {
            continue
        }
        $versionText = & $candidate -c 'import sys; print(str(sys.version_info.major) + "." + str(sys.version_info.minor))'
        if ($LASTEXITCODE -eq 0 -and [version]$versionText -ge [version]'3.11') {
            $script:bootstrapPython = $candidate
            $script:bootstrapPythonPrefix = @()
            return $true
        }
    }
    return $false
}

function Invoke-BootstrapPython {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Description
    )

    Invoke-Native -FilePath $script:bootstrapPython -Arguments @($script:bootstrapPythonPrefix + $Arguments) -Description $Description
}

try {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw 'This installer only supports Windows.'
    }

    Set-Location -LiteralPath $projectRoot
    Refresh-ProcessPath
    Write-Host "Factory monitor project: $projectRoot" -ForegroundColor Green

    $nodeVersion = Get-NodeVersion
    if (-not $nodeVersion -or $nodeVersion -lt [version]'22.13.0') {
        if ($SkipSystemDependencies) {
            throw 'Node.js 22.13.0 or newer is required.'
        }
        Invoke-Winget -Operation install -PackageId 'OpenJS.NodeJS.LTS'
        $nodeVersion = Get-NodeVersion
        if (-not $nodeVersion -or $nodeVersion -lt [version]'22.13.0') {
            Invoke-Winget -Operation upgrade -PackageId 'OpenJS.NodeJS.LTS'
            $nodeVersion = Get-NodeVersion
        }
    }
    if (-not $nodeVersion -or $nodeVersion -lt [version]'22.13.0') {
        throw 'Node.js is still older than 22.13.0. Restart Windows and run the installer again.'
    }
    Write-Host "Node.js: $nodeVersion"

    if (-not (Set-BootstrapPython)) {
        if ($SkipSystemDependencies) {
            throw 'Python 3.11 or newer is required.'
        }
        Invoke-Winget -Operation install -PackageId 'Python.Python.3.11'
        if (-not (Set-BootstrapPython)) {
            throw 'Python 3.11 is still unavailable. Restart Windows and run the installer again.'
        }
    }

    $pnpm = Get-AvailableCommand -Names @('pnpm.cmd', 'pnpm')
    if (-not $pnpm) {
        $npm = Get-AvailableCommand -Names @('npm.cmd', 'npm')
        if (-not $npm) {
            throw 'pnpm is unavailable and npm could not be found to install it.'
        }
        Invoke-Native -FilePath $npm -Arguments @('install', '--global', 'pnpm@11.19.0') -Description 'Install pnpm 11.19.0'
        Refresh-ProcessPath
        $pnpm = Get-AvailableCommand -Names @('pnpm.cmd', 'pnpm')
    }
    if (-not $pnpm) {
        throw 'pnpm is still unavailable after installation.'
    }

    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        Invoke-BootstrapPython -Arguments @('-m', 'venv', $venvDirectory) -Description 'Create the project Python virtual environment'
    }
    Invoke-Native -FilePath $venvPython -Arguments @('-m', 'pip', 'install', '--disable-pip-version-check', '-r', 'requirements-monitor.txt') -Description 'Install monitor Python dependencies'
    Invoke-Native -FilePath $pnpm -Arguments @('install', '--frozen-lockfile') -Description 'Install Node.js dependencies'

    if (-not $SkipValidation) {
        & (Join-Path $PSScriptRoot 'verify-windows.ps1')
        if ($LASTEXITCODE -ne 0) {
            throw "Windows quality gates failed with exit code $LASTEXITCODE"
        }
    }

    $dws = Get-AvailableCommand -Names @('dws.exe', 'dws')
    if ($dws) {
        Write-Host 'DWS_STATUS=INSTALLED; live video still requires the user login, company network, and read-only permission.' -ForegroundColor Yellow
    }
    else {
        Write-Host 'DWS_STATUS=AUTH_REQUIRED; offline setup is ready, but live video requires an authorized local DWS login.' -ForegroundColor Yellow
    }

    Write-Host 'FACTORY_MONITOR_INSTALL_STATUS=PASS' -ForegroundColor Green

    if (-not $NoCodexLaunch) {
        $codexApp = Get-AppxPackage -Name 'OpenAI.Codex' -ErrorAction SilentlyContinue
        if (-not $codexApp -and -not $SkipSystemDependencies) {
            Invoke-Winget -Operation install -PackageId '9PLM9XGG6VKS' -Source 'msstore'
        }
        Refresh-ProcessPath
        $codex = Get-AvailableCommand -Names @('codex.exe', 'codex.cmd', 'codex')
        if (-not $codex) {
            $npm = Get-AvailableCommand -Names @('npm.cmd', 'npm')
            if (-not $npm) {
                throw 'Codex and npm are both unavailable. Install the Codex Windows app and run this installer again.'
            }
            Invoke-Native -FilePath $npm -Arguments @('install', '--global', '@openai/codex') -Description 'Install the Codex launcher'
            Refresh-ProcessPath
            $codex = Get-AvailableCommand -Names @('codex.exe', 'codex.cmd', 'codex')
        }
        if (-not $codex) {
            throw 'The project is installed, but the Codex launcher was not found. Open Codex and use Ctrl+O to select this project directory.'
        }
        Invoke-Native -FilePath $codex -Arguments @('app', $projectRoot) -Description 'Open the factory monitor project in Codex'
    }
}
finally {
    Set-Location -LiteralPath $previousLocation
}
