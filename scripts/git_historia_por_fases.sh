#!/usr/bin/env bash
# Reconstruye historial Git por fases (ver docs/HISTORIA_FASES.md).
# Equivalente Bash de scripts/git_historia_por_fases.ps1
set -euo pipefail

DRY_RUN=0
FROM_PHASE=""
ONLY_PHASE=""
SKIP_DATES=0
FORCE=0
INTERACTIVE=0
PREPARE_FIXTURES_ONLY=0

usage() {
  cat <<'EOF'
Uso: ./scripts/git_historia_por_fases.sh [opciones]

Opciones:
  --dry-run                 Vista previa sin commits
  --from-phase SLUG         Empezar en esta fase (ej. fase-4)
  --only-phase SLUG         Solo una fase
  --skip-dates              Fechas actuales
  --force                   Permitir aunque ya haya commits
  --interactive             Pausa entre fases
  --prepare-fixtures-only   Solo copiar fixtures
  -h, --help                Ayuda

Ejemplos:
  ./scripts/git_historia_por_fases.sh --dry-run
  ./scripts/git_historia_por_fases.sh
  ./scripts/git_historia_por_fases.sh --from-phase fase-6
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --from-phase) FROM_PHASE="$2"; shift ;;
    --only-phase) ONLY_PHASE="$2"; shift ;;
    --skip-dates) SKIP_DATES=1 ;;
    --force) FORCE=1 ;;
    --interactive) INTERACTIVE=1 ;;
    --prepare-fixtures-only) PREPARE_FIXTURES_ONLY=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Opcion desconocida: $1" >&2; usage; exit 1 ;;
  esac
  shift
done

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$ROOT" ]]; then
  echo "ERROR: no estas en un repo Git (git init primero)" >&2
  exit 1
fi
cd "$ROOT"

MANIFEST="$ROOT/scripts/git_historia_manifest.json"
if [[ ! -f "$MANIFEST" ]]; then
  echo "ERROR: manifiesto no encontrado: $MANIFEST" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1 && ! command -v python >/dev/null 2>&1; then
  echo "ERROR: se necesita python para leer el manifiesto JSON" >&2
  exit 1
fi
PY="$(command -v python3 2>/dev/null || command -v python)"

prepare_fixtures() {
  echo ""
  echo "==> Preparando fixtures de test y assets"
  "$PY" - <<'PY' "$ROOT" "$DRY_RUN"
import json, shutil, sys
from pathlib import Path

root = Path(sys.argv[1])
dry = sys.argv[2] == "1"
manifest = json.loads((root / "scripts/git_historia_manifest.json").read_text(encoding="utf-8"))

for item in manifest.get("prepare_fixtures", []):
    dest = root / item["dest"]
    if dry:
        print(f"  [dry-run] dest={dest}")
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)

    src = None
    for cand in item["sources"]:
        p = root / cand
        if p.exists():
            src = p
            break
    if src is None:
        print(f"WARN: sin fuente para {item['dest']}", file=sys.stderr)
        continue
    if dry:
        print(f"  [dry-run] copy {src} -> {dest}")
    else:
        shutil.copy2(src, dest)
        print(f"OK:  Copiado {item['dest']}")
PY
}

if [[ "$PREPARE_FIXTURES_ONLY" -eq 1 ]]; then
  prepare_fixtures
  exit 0
fi

COMMIT_COUNT="$(git rev-list --count HEAD 2>/dev/null || echo 0)"
if [[ "$COMMIT_COUNT" -gt 0 && "$FORCE" -eq 0 ]]; then
  echo "ERROR: el repo ya tiene commits. Usa --force o repo vacio." >&2
  exit 1
fi

if [[ "$FORCE" -eq 0 && "$DRY_RUN" -eq 0 ]]; then
  echo ""
  echo "Reconstruccion de historial SINTETICO por fases."
  read -r -p "Enter para continuar o Ctrl+C para cancelar..."
fi

run_phases() {
  "$PY" - <<'PY' "$ROOT" "$DRY_RUN" "$SKIP_DATES" "$FROM_PHASE" "$ONLY_PHASE" "$INTERACTIVE"
import json, os, subprocess, sys
from pathlib import Path

root = Path(sys.argv[1])
dry = sys.argv[2] == "1"
skip_dates = sys.argv[3] == "1"
from_phase = sys.argv[4]
only_phase = sys.argv[5]
interactive = sys.argv[6] == "1"

manifest = json.loads((root / "scripts/git_historia_manifest.json").read_text(encoding="utf-8"))
phases = manifest["phases"]

if only_phase:
    phases = [p for p in phases if p["slug"] == only_phase]
    if not phases:
        slugs = ", ".join(p["slug"] for p in manifest["phases"])
        raise SystemExit(f"Fase desconocida: {only_phase}. Validos: {slugs}")
    started = True
else:
    started = not from_phase

def prepare():
    for item in manifest.get("prepare_fixtures", []):
        dest = root / item["dest"]
        if not dry:
            dest.parent.mkdir(parents=True, exist_ok=True)
        src = next((root / c for c in item["sources"] if (root / c).exists()), None)
        if src is None:
            print(f"WARN: sin fuente para {item['dest']}", file=sys.stderr)
            continue
        if dry:
            print(f"  [dry-run] copy {src} -> {dest}")
        else:
            import shutil
            shutil.copy2(src, dest)
            print(f"OK:  Copiado {item['dest']}")

for phase in phases:
    if not started:
        if phase["slug"] == from_phase:
            started = True
        else:
            continue

    print("")
    print(f"==> [{phase['id']}] {phase['name']}")

    if phase.get("prepare_fixtures"):
        prepare()

    missing = []
    for rel in phase["paths"]:
        p = root / rel
        if rel.endswith("/"):
            if not p.exists():
                missing.append(rel)
        elif not p.exists():
            missing.append(rel)
    if missing:
        raise SystemExit(f"Fase {phase['slug']}: faltan rutas:\n  - " + "\n  - ".join(missing))

    if dry:
        print(f"  [dry-run] git add -- {' '.join(phase['paths'])}")
        print(f"  [dry-run] git commit -m {phase['message']!r}")
        if not skip_dates:
            print(f"  [dry-run] fecha: {phase.get('date')}")
        continue

    subprocess.run(["git", "add", "--", *phase["paths"]], cwd=root, check=True)
    env = os.environ.copy()
    if not skip_dates and phase.get("date"):
        env["GIT_AUTHOR_DATE"] = phase["date"]
        env["GIT_COMMITTER_DATE"] = phase["date"]
    subprocess.run(["git", "commit", "-m", phase["message"]], cwd=root, check=True, env=env)
    short = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=root, text=True).strip()
    print(f"OK:  Commit {short} — {phase['slug']}")

    if interactive:
        input("Enter para siguiente fase...")
PY
}

run_phases

echo ""
echo "==> Resumen"
if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "Dry-run completado."
else
  git log --oneline --decorate
  echo ""
  echo "OK:  Listo. Revisa git log y haz push cuando quieras."
fi
