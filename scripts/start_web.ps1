[CmdletBinding()]
param(
    [switch]$BuildBackend,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$composeFiles = @(
    '-f', (Join-Path $repo 'docker-compose.yml'),
    '-f', (Join-Path $repo 'docker-compose.gpu.yml')
)
$services = @('backend', 'frontend', 'renderer')

function Invoke-Compose {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    & docker compose @composeFiles @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose failed with exit code $LASTEXITCODE."
    }
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker was not found in PATH. Start Docker Desktop and try again.'
}

Push-Location $repo
try {
    & docker info *> $null
    if ($LASTEXITCODE -ne 0) {
        throw 'Docker Desktop is not ready. Start Docker Desktop and try again.'
    }

    if ($BuildBackend) {
        Write-Host 'Building the GPU backend image...' -ForegroundColor Cyan
        Invoke-Compose @('build', 'backend')
        Write-Host 'Starting the rebuilt backend...' -ForegroundColor Cyan
        Invoke-Compose @('up', '-d', 'backend')
    }

    $running = @(
        (& docker compose @composeFiles ps --status running --services 2>$null) |
            ForEach-Object { $_.Trim() } |
            Where-Object { $_ }
    )
    $missing = @($services | Where-Object { $running -notcontains $_ })

    if ($missing.Count -gt 0) {
        Write-Host ("Starting: " + ($missing -join ', ')) -ForegroundColor Cyan
        Invoke-Compose (@('up', '-d') + $missing)
    }
    else {
        Write-Host 'OpenShorts is already running; no containers were restarted.' -ForegroundColor DarkGreen
    }

    $healthDeadline = (Get-Date).AddSeconds(30)
    $healthy = $false
    do {
        try {
            $health = Invoke-RestMethod -Uri 'http://localhost:8000/health' -TimeoutSec 3
            if ($health.status -eq 'ok') {
                $healthy = $true
                break
            }
        }
        catch {
            Start-Sleep -Seconds 1
        }
    } while ((Get-Date) -lt $healthDeadline)

    if (-not $healthy) {
        throw 'Backend did not become healthy within 30 seconds.'
    }

    Write-Host ''
    Write-Host 'OpenShorts is ready.' -ForegroundColor Green
    Write-Host 'Web:     http://localhost:5175'
    Write-Host 'Backend: http://localhost:8000/health'
    Write-Host 'GPU:     backend uses the GPU Compose overlay'
    Write-Host ''
    Invoke-Compose @('ps')

    if (-not $NoBrowser) {
        Start-Process 'http://localhost:5175'
    }
}
finally {
    Pop-Location
}
