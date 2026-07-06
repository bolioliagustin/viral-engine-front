# Deploy en un solo comando (Windows PowerShell)
#
# Uso:
#   .\deploy\push-deploy.ps1
#   .\deploy\push-deploy.ps1 -Message "fix: algo"
#
# Requiere:
#   - git configurado
#   - SSH a ubuntu@51.79.50.95 sin password (clave en ~/.ssh)
#
# Si tenés GitHub Actions configurado, con solo push a main alcanza
# (este script hace push; el VPS se actualiza solo vía Actions).

param(
    [string]$Message = "",
    [string]$Branch = "",
    [string]$VpsHost = "51.79.50.95",
    [string]$VpsUser = "ubuntu",
    [switch]$SkipPush,
    [switch]$SkipSsh
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

if (-not $Branch) {
    $Branch = (git rev-parse --abbrev-ref HEAD).Trim()
}

Write-Host "==> Branch: $Branch" -ForegroundColor Cyan

if (-not $SkipPush) {
  $status = git status --porcelain
  if ($status) {
    if (-not $Message) { $Message = "chore: deploy $(Get-Date -Format 'yyyy-MM-dd HH:mm')" }
    Write-Host "==> Commit: $Message" -ForegroundColor Yellow
    git add -A
    git commit -m $Message
  }
  Write-Host "==> Pushing to origin/$Branch..." -ForegroundColor Yellow
  git push -u origin $Branch
  if ($Branch -ne "main") {
    Write-Host ""
    Write-Host "NOTA: GitHub Actions solo deploya desde 'main'." -ForegroundColor Yellow
    Write-Host "      Mergeá a main o corré deploy manual con -SkipPush en el VPS." -ForegroundColor Yellow
    Write-Host ""
  }
}

if (-not $SkipSsh) {
  if ($Branch -eq "main") {
    Write-Host "==> Si GitHub Actions está configurado, el VPS se actualiza solo." -ForegroundColor Green
    Write-Host "    Deploy manual por SSH (por si Actions no está listo):" -ForegroundColor Gray
  }
  Write-Host "==> SSH deploy to ${VpsUser}@${VpsHost}..." -ForegroundColor Yellow
  ssh "${VpsUser}@${VpsHost}" "cd ~/viralengine && git fetch origin main && git reset --hard origin/main && bash deploy/deploy-worker.sh"
}

Write-Host "==> Done." -ForegroundColor Green
