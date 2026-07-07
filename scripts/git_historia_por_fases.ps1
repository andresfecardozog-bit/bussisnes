#Requires -Version 5.1
<#
.SYNOPSIS
  Reconstruye historial Git por fases segun AGENTS.md / git_historia_manifest.json.

.DESCRIPTION
  NO hace push. Solo git add + commit por fase, con fechas opcionales del manifiesto.
  Ejecutar desde la raiz del repo cuando todo el codigo ya existe en working tree.

.PARAMETER DryRun
  Muestra que haria sin ejecutar git commit.

.PARAMETER FromPhase
  Slug de fase inicial (ej. fase-4, fase-7a). Incluye fases posteriores.

.PARAMETER OnlyPhase
  Ejecuta una sola fase por slug.

.PARAMETER SkipDates
  Usa fecha/hora actual en lugar de las fechas del manifiesto.

.PARAMETER Force
  Permite ejecutar aunque ya existan commits (advertencia).

.PARAMETER Interactive
  Pausa y pide Enter entre fases.

.PARAMETER PrepareFixturesOnly
  Solo copia fixtures (PRE CORTE, homologacion, logo) y sale.

.EXAMPLE
  .\scripts\git_historia_por_fases.ps1 -DryRun
.EXAMPLE
  .\scripts\git_historia_por_fases.ps1
.EXAMPLE
  .\scripts\git_historia_por_fases.ps1 -FromPhase fase-6 -SkipDates
#>
[CmdletBinding()]
param(
    [switch] $DryRun,
    [string] $FromPhase = "",
    [string] $OnlyPhase = "",
    [switch] $SkipDates,
    [switch] $Force,
    [switch] $Interactive,
    [switch] $PrepareFixturesOnly,
    [switch] $Yes
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step([string] $Text) {
    Write-Host ""
    Write-Host "==> $Text" -ForegroundColor Cyan
}

function Write-Warn([string] $Text) {
    Write-Host "WARN: $Text" -ForegroundColor Yellow
}

function Write-Ok([string] $Text) {
    Write-Host "OK:  $Text" -ForegroundColor Green
}

function Get-RepoRoot {
    $root = git rev-parse --show-toplevel 2>$null
    if (-not $root) {
        throw "No estas dentro de un repositorio Git. Ejecuta 'git init' en la raiz del proyecto."
    }
    return (Resolve-Path $root).Path
}

function Read-Manifest([string] $Path) {
    if (-not (Test-Path $Path)) {
        throw "Manifiesto no encontrado: $Path"
    }
    return Get-Content -Raw -Path $Path | ConvertFrom-Json
}

function Invoke-PrepareFixtures {
    param(
        [object] $Manifest,
        [string] $Root,
        [switch] $DryRunMode
    )

    Write-Step "Preparando fixtures de test y assets"
    foreach ($item in $Manifest.prepare_fixtures) {
        $dest = Join-Path $Root $item.dest
        $destDir = Split-Path $dest -Parent
        if (-not (Test-Path $destDir)) {
            if ($DryRunMode) {
                Write-Host "  [dry-run] mkdir $destDir"
            } else {
                New-Item -ItemType Directory -Path $destDir -Force | Out-Null
            }
        }

        $sourcePath = $null
        foreach ($candidate in $item.sources) {
            $full = Join-Path $Root $candidate
            if (Test-Path $full) {
                $sourcePath = $full
                break
            }
        }

        if (-not $sourcePath) {
            Write-Warn "Sin fuente para '$($item.dest)'. Candidatos: $($item.sources -join ', ')"
            continue
        }

        if ($DryRunMode) {
            Write-Host "  [dry-run] copy '$sourcePath' -> '$dest'"
        } else {
            Copy-Item -Path $sourcePath -Destination $dest -Force
            Write-Ok "Copiado $($item.dest)"
        }
    }
}

function Test-PhasePaths {
    param(
        [object] $Phase,
        [string] $Root
    )

    $missing = @()
    foreach ($rel in $Phase.paths) {
        $full = Join-Path $Root $rel
        if ($rel.EndsWith("/") -or $rel.EndsWith("\")) {
            if (-not (Test-Path $full)) { $missing += $rel }
            continue
        }
        if (-not (Test-Path $full)) {
            $missing += $rel
        }
    }
    return @($missing)
}

function Invoke-PhaseCommit {
    param(
        [object] $Phase,
        [string] $Root,
        [switch] $DryRunMode,
        [switch] $SkipDatesMode
    )

    Write-Step "[$($Phase.id)] $($Phase.name)"

    if ($Phase.PSObject.Properties.Name -contains "prepare_fixtures" -and $Phase.prepare_fixtures -eq $true) {
        Invoke-PrepareFixtures -Manifest $script:Manifest -Root $Root -DryRunMode:$DryRunMode
    }

    $missing = Test-PhasePaths -Phase $Phase -Root $Root
    if (@($missing).Count -gt 0) {
        throw "Fase $($Phase.slug): faltan rutas:`n  - $($missing -join "`n  - ")"
    }

    if ($DryRunMode) {
        Write-Host "  [dry-run] git add -- $($Phase.paths -join ', ')"
        Write-Host "  [dry-run] git commit -m '$($Phase.message)'"
        if (-not $SkipDatesMode) {
            Write-Host "  [dry-run] fecha: $($Phase.date)"
        }
        return
    }

    foreach ($rel in $Phase.paths) {
        git -C $Root add -- $rel
    }

    $env:GIT_AUTHOR_DATE = $null
    $env:GIT_COMMITTER_DATE = $null
    if (-not $SkipDatesMode -and $Phase.date) {
        $env:GIT_AUTHOR_DATE = $Phase.date
        $env:GIT_COMMITTER_DATE = $Phase.date
    }

    git -C $Root commit -m $Phase.message
    Remove-Item Env:GIT_AUTHOR_DATE -ErrorAction SilentlyContinue
    Remove-Item Env:GIT_COMMITTER_DATE -ErrorAction SilentlyContinue

    $hash = git -C $Root rev-parse --short HEAD
    Write-Ok "Commit $hash - $($Phase.slug)"
}

# --- main ---
$Root = Get-RepoRoot
Set-Location $Root

$manifestPath = Join-Path $Root "scripts/git_historia_manifest.json"
$script:Manifest = Read-Manifest $manifestPath

if ($PrepareFixturesOnly) {
    Invoke-PrepareFixtures -Manifest $script:Manifest -Root $Root -DryRunMode:$DryRun
    exit 0
}

$commitCount = 0
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
$rawCount = git -C $Root rev-list --count HEAD 2>$null
$ErrorActionPreference = $prevEap
if ($LASTEXITCODE -eq 0 -and $rawCount) {
    $commitCount = [int]$rawCount
}
if ($commitCount -gt 0 -and -not $Force) {
    throw @"
El repositorio ya tiene $commitCount commit(s).
Usa -Force si quieres continuar de todos modos, o clona/en init un repo vacio.
Para anadir fases sueltas: -OnlyPhase fase-X -Force
"@
}

if (-not $Force -and -not $DryRun -and -not $Yes) {
    Write-Host ""
    Write-Host "Reconstruccion de historial SINTETICO por fases (ver docs/HISTORIA_FASES.md)." -ForegroundColor Yellow
    Write-Host "Presiona Enter para continuar o Ctrl+C para cancelar..."
    [void][System.Console]::ReadLine()
}

$phases = @($script:Manifest.phases)
$started = [string]::IsNullOrWhiteSpace($FromPhase)

if ($OnlyPhase) {
    $match = $phases | Where-Object { $_.slug -eq $OnlyPhase }
    if (-not $match) {
        $slugs = ($phases | ForEach-Object { $_.slug }) -join ", "
        throw "Fase desconocida: $OnlyPhase. Slugs validos: $slugs"
    }
    $phases = @($match)
    $started = $true
}

foreach ($phase in $phases) {
    if (-not $started) {
        if ($phase.slug -eq $FromPhase) { $started = $true }
        else { continue }
    }

    Invoke-PhaseCommit -Phase $phase -Root $Root -DryRunMode:$DryRun -SkipDatesMode:$SkipDates

    if ($Interactive -and -not $DryRun) {
        Write-Host "Enter para siguiente fase..."
        [void][System.Console]::ReadLine()
    }
}

Write-Step "Resumen"
if ($DryRun) {
    Write-Host "Dry-run completado. Ejecuta sin -DryRun para crear commits."
} else {
    git -C $Root log --oneline --decorate
    Write-Host ""
    Write-Ok "Listo. Revisa con 'git log' y luego 'git remote add origin ...' + 'git push -u origin master'"
}
