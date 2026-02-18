"""Validadores anti-perdida y anti-modificacion silenciosa.

Cada validador retorna `(ok: bool, detalle: dict)` y NUNCA modifica los
datos. La consolidacion en `run_all_validations` produce un checklist que
Streamlit renderiza y que Power Automate/FastAPI usan para decidir si se
puede continuar con la persistencia.

Filosofia: los validadores deben ser INDEPENDIENTES de los loaders para
poder detectar bugs introducidos por un refactor futuro. Por ejemplo, si
alguien cambia `pd.read_csv(..., decimal=',')` para "arreglar" algo, el
validador `validate_sum_preserved` releera el archivo como strings puros y
sumara de forma independiente, revelando la discrepancia.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import openpyxl
import pandas as pd

from app.core.resumen_parser import load_resumen

if TYPE_CHECKING:
    from app.core.matcher import MatchResult


@dataclass
class Validation:
    """Resultado de una validacion individual."""

    nombre: str
    ok: bool
    detalle: dict[str, Any] = field(default_factory=dict)


def validate_row_count(
    filas_original: int,
    filas_procesado: int,
    tolerancia: int = 0,
    nombre: str = "row_count",
) -> Validation:
    diff = abs(filas_original - filas_procesado)
    return Validation(
        nombre=nombre,
        ok=diff <= tolerancia,
        detalle={
            "filas_original": filas_original,
            "filas_procesado": filas_procesado,
            "diferencia": diff,
            "tolerancia": tolerancia,
        },
    )


def _sum_from_raw_strings(path: Path, original_column_name: str) -> float | None:
    """Suma independiente calculada leyendo el archivo como strings puros.

    Elimina espacios, comas de miles y simbolos de moneda pero NO interpreta
    comas como decimales. Sirve como fuente de verdad alternativa al loader.
    """
    if path.suffix.lower() in (".xlsx", ".xlsm", ".xls"):
        raw = pd.read_excel(path, dtype=object)
    elif path.suffix.lower() == ".csv":
        raw = pd.read_csv(path, dtype=object)
    else:
        return None
    if original_column_name not in raw.columns:
        return None
    total = 0.0
    for v in raw[original_column_name]:
        if pd.isna(v):
            continue
        s = str(v).strip().replace(",", "").replace("$", "").strip()
        if not s or s in ("-", ".", "-."):
            continue
        try:
            total += float(s)
        except ValueError:
            continue
    return total


def validate_sum_preserved(
    path: str | Path,
    df_procesado: pd.DataFrame,
    original_col: str,
    processed_col: str,
    tolerancia: float = 0.01,
    nombre: str | None = None,
) -> Validation:
    """Compara la suma leida como strings puros vs la suma post-cleaning."""
    path = Path(path)
    expected = _sum_from_raw_strings(path, original_col)
    nombre = nombre or f"sum_preserved:{original_col}"

    if expected is None:
        return Validation(
            nombre=nombre,
            ok=False,
            detalle={"error": f"Columna original '{original_col}' no encontrada en {path.name}"},
        )
    if processed_col not in df_procesado.columns:
        return Validation(
            nombre=nombre,
            ok=False,
            detalle={"error": f"Columna procesada '{processed_col}' no existe"},
        )
    actual = float(df_procesado[processed_col].sum())
    diff = abs(expected - actual)
    return Validation(
        nombre=nombre,
        ok=diff <= tolerancia,
        detalle={
            "suma_leida_raw": round(expected, 4),
            "suma_procesada": round(actual, 4),
            "diferencia_absoluta": round(diff, 4),
            "tolerancia": tolerancia,
        },
    )


def validate_all_materials_accounted(
    pre_corte_df: pd.DataFrame,
    match_result: "MatchResult",
) -> Validation:
    """N_pre_corte == matched + solo_pre_corte (ninguna fila del PRE CORTE se descarta)."""
    total_pre = len(pre_corte_df)
    contabilizado = len(match_result.matched) + len(match_result.solo_pre_corte)
    return Validation(
        nombre="all_materials_accounted",
        ok=total_pre == contabilizado,
        detalle={
            "pre_corte_filas": total_pre,
            "matched": len(match_result.matched),
            "solo_pre_corte": len(match_result.solo_pre_corte),
            "solo_flash": len(match_result.solo_flash),
            "no_cruzados_total": len(match_result.no_cruzados),
        },
    )


def validate_no_duplicates(
    df: pd.DataFrame,
    keys: list[str],
    nombre: str = "no_duplicates",
) -> Validation:
    """Alerta si hay claves duplicadas que puedan estar inflando o corrompiendo joins."""
    missing_cols = [c for c in keys if c not in df.columns]
    if missing_cols:
        return Validation(
            nombre=nombre,
            ok=False,
            detalle={"error": f"Columnas ausentes: {missing_cols}"},
        )
    duplicados = df.duplicated(subset=keys, keep=False)
    n_dup = int(duplicados.sum())
    ejemplos = df.loc[duplicados, keys].head(5).to_dict(orient="records")
    return Validation(
        nombre=nombre,
        ok=n_dup == 0,
        detalle={
            "claves": keys,
            "filas_duplicadas": n_dup,
            "ejemplos": ejemplos,
        },
    )


def validate_resumen_total_preserved(
    path: str | Path,
    pre_corte_df: pd.DataFrame,
    tolerancia: float = 0.5,
) -> Validation:
    """La suma de `notificado` del df procesado coincide con el total unidades del RESUMEN."""
    path = Path(path)
    try:
        _, resumen_meta = load_resumen(path)
    except Exception as exc:
        return Validation(
            nombre="pre_corte_resumen_total_preserved",
            ok=False,
            detalle={"error": f"load_resumen fallo: {exc}"},
        )
    expected = float(resumen_meta.get("total_unidades", 0.0))
    actual = float(pre_corte_df.get("notificado", pd.Series(dtype=float)).sum())
    diff = abs(expected - actual)
    return Validation(
        nombre="pre_corte_resumen_total_preserved",
        ok=diff <= tolerancia,
        detalle={
            "resumen_total_unidades": expected,
            "df_notificado_sum": actual,
            "diferencia_absoluta": round(diff, 4),
            "tolerancia": tolerancia,
            "filas_procesadas": len(pre_corte_df),
            "filas_sin_sap": int(pre_corte_df.attrs.get("filas_sin_sap", 0)),
        },
    )


def validate_resumen_vs_notificacion(
    path: str | Path,
    tolerancia: float = 0.5,
) -> Validation:
    """Cuando ambas hojas estan presentes, sus totales de unidades deben coincidir.

    Si NOTIFICACION no esta presente en el xlsx, devuelve ok=True con motivo
    'notificacion_ausente' (no aplica).
    """
    path = Path(path)
    try:
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    except Exception as exc:
        return Validation(
            nombre="resumen_vs_notificacion",
            ok=False,
            detalle={"error": f"No se pudo abrir el xlsx: {exc}"},
        )
    try:
        if "NOTIFICACION" not in wb.sheetnames:
            return Validation(
                nombre="resumen_vs_notificacion",
                ok=True,
                detalle={"motivo": "notificacion_ausente"},
            )
    finally:
        wb.close()

    from app.core.loaders import load_notificacion

    try:
        r_df, r_meta = load_resumen(path)
        n_df, n_meta = load_notificacion(path)
    except Exception as exc:
        return Validation(
            nombre="resumen_vs_notificacion",
            ok=False,
            detalle={"error": f"Fallo al cargar hojas: {exc}"},
        )

    resumen_total = float(r_meta.get("total_unidades", 0.0))
    notif_total = float(n_meta.get("total_necesidad_unidades", 0.0))
    diff = abs(resumen_total - notif_total)
    return Validation(
        nombre="resumen_vs_notificacion",
        ok=diff <= tolerancia,
        detalle={
            "resumen_total_unidades": resumen_total,
            "notificacion_total_unidades": notif_total,
            "diferencia_absoluta": round(diff, 4),
            "tolerancia": tolerancia,
        },
    )


def validate_catalog_coverage(
    pre_corte_meta: dict[str, Any],
    umbral_pct: float = 95.0,
) -> Validation:
    """Alerta si menos del `umbral_pct` de filas del RESUMEN resolvio a SAP."""
    total = int(pre_corte_meta.get("num_filas_original", 0))
    procesadas = int(pre_corte_meta.get("num_filas_procesadas", 0))
    sin_sap = int(pre_corte_meta.get("num_filas_sin_sap", 0))
    coverage_pct = (procesadas / total * 100.0) if total > 0 else 0.0
    return Validation(
        nombre="catalog_coverage",
        ok=coverage_pct >= umbral_pct,
        detalle={
            "filas_resumen": total,
            "filas_con_sap": procesadas,
            "filas_sin_sap": sin_sap,
            "cobertura_pct": round(coverage_pct, 2),
            "umbral_pct": umbral_pct,
            "sin_sap_detalle": pre_corte_meta.get("sin_sap_detalle", []),
        },
    )


def run_all_validations(
    pre_corte_path: str | Path,
    pre_corte_df: pd.DataFrame,
    pre_corte_meta: dict[str, Any],
    flash_path: str | Path,
    flash_df: pd.DataFrame,
    flash_meta: dict[str, Any],
    match_result: "MatchResult",
) -> list[Validation]:
    """Ejecuta todos los validadores relevantes para un run completo."""
    return [
        validate_row_count(
            pre_corte_meta["num_filas_original"],
            pre_corte_meta["num_filas_procesadas"] + pre_corte_meta.get("num_filas_sin_sap", 0),
            nombre="pre_corte_row_count",
        ),
        validate_row_count(
            flash_meta["num_filas_original"],
            flash_meta["num_filas_procesadas"],
            nombre="flash_row_count",
        ),
        validate_resumen_total_preserved(pre_corte_path, pre_corte_df),
        validate_resumen_vs_notificacion(pre_corte_path),
        validate_catalog_coverage(pre_corte_meta),
        validate_sum_preserved(
            flash_path,
            flash_df,
            original_col="Cantidad Neta",
            processed_col="cantidad_neta",
            nombre="flash_sum_cantidad_neta",
        ),
        validate_no_duplicates(
            pre_corte_df,
            keys=["material"],
            nombre="pre_corte_material_unico",
        ),
        validate_all_materials_accounted(pre_corte_df, match_result),
    ]


def all_ok(validaciones: list[Validation]) -> bool:
    return all(v.ok for v in validaciones)


def as_dict_list(validaciones: list[Validation]) -> list[dict[str, Any]]:
    """Serializa la lista para exponer via API/JSON."""
    return [
        {"nombre": v.nombre, "ok": v.ok, "detalle": v.detalle}
        for v in validaciones
    ]
