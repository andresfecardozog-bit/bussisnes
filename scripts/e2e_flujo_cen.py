"""E2E del flujo completo de la plataforma contra el backend local.

Simula exactamente lo que hace una analista en el frontend:
1. Sube CEN + SAP con un brief en lenguaje natural (los agentes Gemini
   REALES analizan y proponen).
2. Lee el chat: preguntas de los agentes.
3. Responde las preguntas (respuestas de negocio realistas).
4. Aprueba el profile (debe fallar si hay bloqueantes; responder primero).
5. Genera entregables (Excel + PBIP zip).
6. Descarga y verifica los archivos.

Uso: venv\\Scripts\\python.exe scripts/e2e_flujo_cen.py
Requiere el backend corriendo en 127.0.0.1:8000 y GEMINI_API_KEY en .env.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEFT = ROOT / "tests" / "fixtures" / "cen" / "cen_junio_muestra.xlsx"
DEFAULT_RIGHT = ROOT / "tests" / "fixtures" / "cen" / "sap_junio_muestra.xlsx"

BRIEF = (
    "Necesito medir el nivel de servicio de las ordenes de compra que "
    "llegan por la plataforma CEN contra lo que realmente entregamos "
    "segun SAP. El primer archivo es el acumulado de ordenes CEN del mes "
    "(todas las ordenes, entregadas o no). El segundo es el reporte de "
    "ventas de SAP del mismo mes (solo lo facturado; ojo que trae TODAS "
    "las ventas de la compania, no solo las de CEN, y tambien "
    "devoluciones). Quiero saber cuantos pedidos se entregaron completos, "
    "parciales y no entregados, en unidades y porcentaje, desglosado por "
    "material, distrito y cliente, y aparte los motivos de devolucion."
)

RESPUESTA_GENERICA = (
    "Confirmado: el grano en CEN es linea de producto por orden (una orden "
    "puede repetirse en varias lineas). Para reconstruir y cruzar se usa "
    "numero_orden + codigo_material, no texto libre. En SAP, el numero de "
    "orden del cliente viene en la columna 56 (no en la 1). El material es "
    "columna 40 y la cantidad entregada es columna 42. El join correcto es "
    "CEN 'Numero de la Orden de compra' vs SAP col56, y CEN 'Codigo item "
    "proveedor' vs SAP col40 (normalizando espacios y ceros a la "
    "izquierda). IMPORTANTE: las ordenes CEN vienen en varios formatos "
    "(con guion como '003-0023901' y tambien numericas como '0020018102'); "
    "NO filtrar por un formato especifico de orden. Solo descartar "
    "placeholders no numericos escritos a mano ('SIN DC', 'SIN ORDEN', "
    "'sin oc', '*', vacios); el cruce outer ya deja fuera lo que no "
    "coincida. Para nivel de servicio, usar como real_column la cantidad "
    "entregada de SAP (suma de col42), sin reemplazarla por una columna "
    "neta en 0. Las devoluciones son SOLO las filas con tipo de operacion "
    "DEVOLUCIONES (columna 13), con motivo en columna 62: van en KPI y "
    "breakdown aparte, excluidas de la entrega, y no deben contarse como "
    "el total entregado. El archivo CEN es un periodo (~mes) y puede traer "
    "dias sueltos del mes anterior/siguiente en 'F. Documento O/C'; se "
    "cruza el archivo completo sin filtrar por fecha. Distrito principal: "
    "columna 11 SAP. Cliente: razon social compradora en CEN y cliente SAP "
    "para breakdowns."
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="E2E completo contra API de perfiles (chat + approve + generate)."
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="Base URL del backend FastAPI.",
    )
    parser.add_argument(
        "--left",
        default=str(DEFAULT_LEFT),
        help="Ruta del archivo izquierdo (CEN).",
    )
    parser.add_argument(
        "--right",
        default=str(DEFAULT_RIGHT),
        help="Ruta del archivo derecho (SAP).",
    )
    parser.add_argument(
        "--homologacion",
        default="",
        help="Ruta opcional del archivo de homologacion.",
    )
    parser.add_argument(
        "--profile-prefix",
        default="e2e_cen",
        help="Prefijo del profile_id generado.",
    )
    parser.add_argument(
        "--refine-intentos",
        type=int,
        default=3,
        help="Numero maximo de intentos para /refine.",
    )
    return parser.parse_args()


def _resolve_path(raw_path: str) -> Path:
    p = Path(raw_path)
    if not p.is_absolute():
        p = ROOT / p
    return p.resolve()


def main() -> int:
    args = _parse_args()
    base = args.base_url.rstrip("/")
    profile_id = f"{args.profile_prefix}_{int(time.time())}"
    cen = _resolve_path(args.left)
    sap = _resolve_path(args.right)
    homolog = _resolve_path(args.homologacion) if args.homologacion else None
    if not cen.exists():
        print(f"ERROR: no existe archivo left: {cen}")
        return 2
    if not sap.exists():
        print(f"ERROR: no existe archivo right: {sap}")
        return 2
    if homolog and not homolog.exists():
        print(f"ERROR: no existe archivo homologacion: {homolog}")
        return 2

    client = httpx.Client(timeout=httpx.Timeout(1800.0, connect=30.0))

    print(f"[1] Health check...")
    r = client.get(f"{base}/health")
    r.raise_for_status()
    print(f"    base_url: {base}")
    print(f"    profile_id: {profile_id}")
    print(f"    left: {cen}")
    print(f"    right: {sap}")
    if homolog:
        print(f"    homologacion: {homolog}")

    print(f"[2] POST /profiles/draft (agentes Gemini reales analizando)...")
    t0 = time.monotonic()
    with cen.open("rb") as f1, sap.open("rb") as f2:
        files = {
            "left_file": (cen.name, f1, "application/vnd.ms-excel"),
            "right_file": (sap.name, f2, "application/vnd.ms-excel"),
        }
        if homolog:
            with homolog.open("rb") as f3:
                files["homologacion_file"] = (
                    homolog.name,
                    f3,
                    "application/vnd.ms-excel",
                )
                r = client.post(
                    f"{base}/profiles/draft",
                    data={"profile_id": profile_id, "brief": BRIEF},
                    files=files,
                )
        else:
            r = client.post(
                f"{base}/profiles/draft",
                data={"profile_id": profile_id, "brief": BRIEF},
                files=files,
            )
    dur = time.monotonic() - t0
    if r.status_code != 200:
        print(f"    FALLO {r.status_code}: {r.text[:2000]}")
        return 1
    draft = r.json()
    print(f"    OK en {dur:.1f}s")
    print(f"    status: {draft['status']}")
    print(f"    resumen left: {draft['resumen_fuentes']['left'][:200]}")
    print(f"    resumen right: {draft['resumen_fuentes']['right'][:200]}")
    print(f"    justificacion mapping: {draft['justificaciones']['mapping'][:300]}")

    print(f"[3] GET /chat (preguntas de los agentes)...")
    chat = client.get(f"{base}/profiles/{profile_id}/chat").json()
    preguntas = [m for m in chat if m["tipo"] == "pregunta" and m["estado"] == "abierta"]
    print(f"    {len(preguntas)} preguntas abiertas:")
    for q in preguntas:
        marca = "BLOQUEANTE" if q["bloqueante"] else "no bloqueante"
        print(f"    - [{marca}] ({q['autor']}) {q['contenido'][:220]}")
        print(f"      hipotesis: {(q.get('hipotesis') or '')[:180]}")

    print(f"[4] Respondiendo preguntas...")
    for q in preguntas:
        r = client.post(
            f"{base}/profiles/{profile_id}/chat",
            json={"mensaje": RESPUESTA_GENERICA, "question_id": q["question_id"]},
        )
        r.raise_for_status()
    status = client.get(f"{base}/profiles/{profile_id}/proposal").json()["status"]
    print(f"    status tras responder: {status}")

    print(f"[5] POST /refine (re-propuesta con las respuestas)...")
    t0 = time.monotonic()
    refined = None
    for intento in range(args.refine_intentos):
        r = client.post(f"{base}/profiles/{profile_id}/refine")
        if r.status_code == 200:
            refined = r.json()
            break
        print(f"    intento {intento + 1} fallo {r.status_code}: {r.text[:400]}")
    if refined is None:
        return 1
    print(f"    OK en {time.monotonic() - t0:.1f}s -> version {refined['version']}")
    print(f"    status: {refined['status']}")

    # responder lo que haya surgido nuevo (no repetido gracias a la memoria)
    chat = client.get(f"{base}/profiles/{profile_id}/chat").json()
    nuevas = [m for m in chat if m["tipo"] == "pregunta" and m["estado"] == "abierta"]
    if nuevas:
        print(f"    {len(nuevas)} preguntas nuevas tras el refine; respondiendo...")
        for q in nuevas:
            client.post(
                f"{base}/profiles/{profile_id}/chat",
                json={"mensaje": RESPUESTA_GENERICA, "question_id": q["question_id"]},
            )

    print(f"[5b] POST /approve...")
    status = client.get(f"{base}/profiles/{profile_id}/proposal").json()["status"]
    if not status.get("listo_para_aprobar"):
        print(f"    BLOQUEADO: {status['preguntas_bloqueantes']} preguntas bloqueantes abiertas")
        return 1
    r = client.post(
        f"{base}/profiles/{profile_id}/approve", json={"aprobado_por": "e2e"}
    )
    if r.status_code != 200:
        print(f"    FALLO {r.status_code}: {r.text[:1000]}")
        return 1
    print(f"    aprobado: {r.json()}")

    print(f"[6] POST /generate (motor deterministico + renderers)...")
    t0 = time.monotonic()
    r = client.post(f"{base}/profiles/{profile_id}/generate", json={})
    dur = time.monotonic() - t0
    if r.status_code != 200:
        print(f"    FALLO {r.status_code}: {r.text[:2000]}")
        return 1
    gen = r.json()
    print(f"    OK en {dur:.1f}s")
    print(f"    summary: {gen['summary']}")
    kpis_plano = {k: v for k, v in gen["kpis"].items() if not isinstance(v, dict)}
    print(f"    kpis: {kpis_plano}")
    if "service_level" in gen["kpis"] and "pedidos" in gen["kpis"]["service_level"]:
        print(f"    pedidos: {gen['kpis']['service_level']['pedidos']}")
    print(f"    archivos: {gen['archivos']}")

    print(f"[7] Descargando y verificando...")
    files = client.get(f"{base}/profiles/{profile_id}/downloads").json()
    for f in files:
        r = client.get(f"{base}/profiles/{profile_id}/downloads/{f['filename']}")
        r.raise_for_status()
        ok = len(r.content) == f["size_bytes"]
        print(f"    {f['filename']} ({f['kind']}, {f['size_bytes']:,} bytes) descarga {'OK' if ok else 'TAMANO NO CUADRA'}")

    print(f"[8] Telemetria...")
    tele = client.get(f"{base}/profiles/{profile_id}/telemetry").json()
    print(f"    {tele}")

    print("\nE2E COMPLETO OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
