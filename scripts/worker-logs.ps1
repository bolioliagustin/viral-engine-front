# Worker log viewer (Windows / local dev)
# Usage:
#   .\scripts\worker-logs.ps1
#   .\scripts\worker-logs.ps1 tail 200
#   .\scripts\worker-logs.ps1 job abc123
#   .\scripts\worker-logs.ps1 errors

param(
    [string]$Command = "tail",
    [string]$Arg = ""
)

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$LogDir = if ($env:WORKER_LOG_DIR) { $env:WORKER_LOG_DIR } else { Join-Path $Root "worker-logs" }
$LogFile = Join-Path $LogDir "worker.log"
$ErrFile = Join-Path $LogDir "worker-error.log"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

switch ($Command) {
    "path" { Write-Output $LogFile }
    "tail" {
        if (-not (Test-Path $LogFile)) {
            Write-Error "Log file not found: $LogFile"
            exit 1
        }
        if ($Arg -match '^\d+$') {
            Get-Content $LogFile -Tail ([int]$Arg)
        } else {
            Get-Content $LogFile -Wait -Tail 50
        }
    }
    "job" {
        if (-not $Arg) { Write-Error "Usage: .\scripts\worker-logs.ps1 job <id-prefix>"; exit 1 }
        Select-String -Path $LogFile -Pattern "job=$Arg" -SimpleMatch | Select-Object -Last 200
    }
    "edit" {
        if (-not $Arg) { Write-Error "Usage: .\scripts\worker-logs.ps1 edit <id-prefix>"; exit 1 }
        Select-String -Path $LogFile -Pattern "edit=$Arg" -SimpleMatch | Select-Object -Last 200
    }
    "errors" {
        $n = if ($Arg -match '^\d+$') { [int]$Arg } else { 80 }
        if (Test-Path $ErrFile) {
            Get-Content $ErrFile -Tail $n
        } else {
            Select-String -Path $LogFile -Pattern 'ERROR|WARNING|❌|falló|failed' | Select-Object -Last $n
        }
    }
    "grep" {
        if (-not $Arg) { Write-Error "Usage: .\scripts\worker-logs.ps1 grep <pattern>"; exit 1 }
        Select-String -Path $LogFile -Pattern $Arg | Select-Object -Last 150
    }
    "docker" {
        $tail = if ($Arg -match '^\d+$') { $Arg } else { "100" }
        docker compose -f (Join-Path $Root "docker-compose.worker.yml") logs -f --tail $tail worker
    }
    default {
        Write-Error "Unknown command: $Command. Use: tail, job, edit, errors, grep, docker, path"
        exit 1
    }
}
