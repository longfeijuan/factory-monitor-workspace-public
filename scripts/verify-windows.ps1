[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$statusDirectory = Join-Path $projectRoot 'runtime\onboarding'
$statusPath = Join-Path $statusDirectory 'windows-ready.json'
$previousLocation = Get-Location

[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

function Get-RequiredCommand {
    param([Parameter(Mandatory = $true)][string[]]$Names)

    foreach ($name in $Names) {
        $candidate = Get-Command $name -ErrorAction SilentlyContinue
        if ($candidate) {
            return $candidate.Source
        }
    }
    throw "Required command not found: $($Names -join ', ')"
}

function Invoke-NativeStep {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @()
    )

    Write-Host "[RUN] $Name" -ForegroundColor Cyan
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE"
    }
}

$startedAt = [DateTimeOffset]::Now
$result = [ordered]@{
    schema_version = 1
    status = 'RUNNING'
    project = $projectRoot
    started_at = $startedAt.ToString('o')
    completed_at = $null
    git_commit = $null
    quality_gates = [ordered]@{}
    error = $null
}

New-Item -ItemType Directory -Path $statusDirectory -Force | Out-Null

try {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw 'This validation entry point only supports Windows.'
    }

    $venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        throw '.venv was not found. Run INSTALL-WINDOWS.cmd first.'
    }
    $pnpm = Get-RequiredCommand -Names @('pnpm.cmd', 'pnpm')
    $git = Get-RequiredCommand -Names @('git.exe', 'git')

    Set-Location -LiteralPath $projectRoot

    Invoke-NativeStep -Name 'Repository self-check' -FilePath $venvPython -Arguments @('scripts/monitor_self_check.py')
    $result.quality_gates['monitor_self_check'] = 'PASS'

    Invoke-NativeStep -Name 'Camera catalog consistency' -FilePath $venvPython -Arguments @('scripts/sync_camera_catalog.py', '--check')
    $result.quality_gates['camera_catalog'] = 'PASS'

    Invoke-NativeStep -Name 'Connector and monitor contract tests' -FilePath $pnpm -Arguments @('run', 'test:connector')
    $result.quality_gates['connector_tests'] = 'PASS'

    Invoke-NativeStep -Name 'Web build and rendering tests' -FilePath $pnpm -Arguments @('test')
    $result.quality_gates['rendered_tests'] = 'PASS'

    $commit = & $git rev-parse HEAD
    if ($LASTEXITCODE -ne 0) {
        throw 'Unable to read the Git commit.'
    }
    $result.git_commit = ($commit | Select-Object -First 1).Trim()
    $result.status = 'PASS'
    Write-Host 'WINDOWS_QUALITY_GATES=PASS' -ForegroundColor Green
}
catch {
    $result.status = 'FAIL'
    $result.error = $_.Exception.Message
    Write-Host "WINDOWS_QUALITY_GATES=FAIL: $($result.error)" -ForegroundColor Red
    throw
}
finally {
    $result.completed_at = [DateTimeOffset]::Now.ToString('o')
    $result | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $statusPath -Encoding utf8
    Set-Location -LiteralPath $previousLocation
    Write-Host "Validation certificate: $statusPath"
}
