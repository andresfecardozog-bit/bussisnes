# Smoke E2E manual contra API local (Fase 7A).
# Uso: levantar backend con run_backend.bat, luego:
#   .\_smoke_e2e_curl.ps1
# Requiere: servidor en http://127.0.0.1:8000

$ErrorActionPreference = "Stop"
$Base = "http://127.0.0.1:8000"

function Step([string]$Label) { Write-Host "`n==> $Label" -ForegroundColor Cyan }

Step "GET /health"
$health = Invoke-RestMethod -Uri "$Base/health" -Method Get
Write-Host ($health | ConvertTo-Json -Compress)

Step "POST /batches"
$batch = Invoke-RestMethod -Uri "$Base/batches" -Method Post -ContentType "application/json" -Body '{"nombre":"smoke-e2e"}'
$batchId = $batch.id
Write-Host "batch_id=$batchId status=$($batch.status)"

Step "GET /batches/$batchId"
$detail = Invoke-RestMethod -Uri "$Base/batches/$batchId" -Method Get
Write-Host "pre_cortes=$($detail.pre_cortes.Count) flash=$([bool]$detail.flash)"

Write-Host "`nOK: smoke basico completado. Sube archivos desde el wizard Angular o curl multipart." -ForegroundColor Green
