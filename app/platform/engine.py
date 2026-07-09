"""Motor generico de cruce: ejecuta un MatchProfile aprobado.

Deterministico, sin LLM. Todas las operaciones provienen de la whitelist
del contrato (profile.py); no hay eval de formulas libres.

Garantia de cero perdida: para cada fuente,
    filas_procesadas == filas_matched + filas_no_cruzadas(origen)
verificado en `GenericMatchResult.verify_accounting()` que el pipeline
llama SIEMPRE antes de persistir.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.platform.loader import load_source
from app.platform.profile import (
    AggFn,
    BreakdownSpec,
    ComputedColumn,
    FilterEquals,
    FilterNotEquals,
    FilterRegexMatch,
    GroupByAggregate,
    JoinSpec,
    KeyNormalizer,
    KpiSpec,
    MatchProfile,
    SelectRename,
    ServiceLevelSpec,
    SourceSpec,
    Transform,
    Unpivot,
)

_LEFT_SUFFIX = "_left"
_RIGHT_SUFFIX = "_right"


@dataclass
class GenericMatchResult:
    profile_id: str
    profile_version: int
    parameters: dict[str, Any]
    matched: pd.DataFrame
    solo_left: pd.DataFrame
    solo_right: pd.DataFrame
    no_cruzados: pd.DataFrame
    kpis: dict[str, Any]
    left_meta: dict[str, Any] = field(default_factory=dict)
    right_meta: dict[str, Any] = field(default_factory=dict)
    service_level: dict[str, Any] | None = None
    breakdowns: dict[str, pd.DataFrame] = field(default_factory=dict)

    def summary(self) -> dict[str, int]:
        return {
            "matched": len(self.matched),
            "solo_left": len(self.solo_left),
            "solo_right": len(self.solo_right),
            "no_cruzados": len(self.no_cruzados),
        }

    def verify_accounting(self, left_rows: int, right_rows: int) -> None:
        """Cero perdida: cada fila de entrada (post-transform) termina en
        matched o en su particion solo_*. Lanza si no cuadra."""
        got_left = len(self.matched) + len(self.solo_left)
        got_right = len(self.matched) + len(self.solo_right)
        if got_left != left_rows:
            raise AssertionError(
                f"Perdida de filas en fuente izquierda: entrada={left_rows}, "
                f"contabilizadas={got_left}"
            )
        if got_right != right_rows:
            raise AssertionError(
                f"Perdida de filas en fuente derecha: entrada={right_rows}, "
                f"contabilizadas={got_right}"
            )
        esperadas = len(self.solo_left) + len(self.solo_right)
        if len(self.no_cruzados) != esperadas:
            raise AssertionError(
                f"no_cruzados incompleto: {len(self.no_cruzados)} != {esperadas}"
            )


# ---------------------------------------------------------------------------
# Normalizadores de keys
# ---------------------------------------------------------------------------

def _apply_normalizers(series: pd.Series, normalizers: list[KeyNormalizer]) -> pd.Series:
    out = series
    for norm in normalizers:
        if norm == KeyNormalizer.STRIP:
            out = out.astype("string").str.strip()
        elif norm == KeyNormalizer.UPPER:
            out = out.astype("string").str.upper()
        elif norm == KeyNormalizer.LSTRIP_ZEROS:
            out = out.astype("string").str.strip().str.lstrip("0").replace("", "0")
        elif norm == KeyNormalizer.DIGITS_ONLY:
            out = out.astype("string").str.replace(r"\D", "", regex=True)
        elif norm == KeyNormalizer.TO_INT:
            out = pd.to_numeric(out, errors="coerce").astype("Int64")
        elif norm == KeyNormalizer.TO_STR:
            out = out.astype("string")
        else:
            raise ValueError(f"Normalizador no soportado: {norm}")
    return out


# ---------------------------------------------------------------------------
# Transformaciones
# ---------------------------------------------------------------------------

def _resolve_param_value(value: Any, parameters: dict[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("$"):
        name = value[1:]
        if name not in parameters:
            raise ValueError(f"Parametro runtime faltante: {name}")
        return parameters[name]
    return value


def _apply_transform(
    df: pd.DataFrame, transform: Transform, parameters: dict[str, Any]
) -> pd.DataFrame:
    if isinstance(transform, FilterEquals):
        if transform.column not in df.columns:
            raise ValueError(f"filter_equals: columna inexistente '{transform.column}'")
        value = _resolve_param_value(transform.value, parameters)
        return df[df[transform.column] == value].copy()

    if isinstance(transform, FilterNotEquals):
        if transform.column not in df.columns:
            raise ValueError(
                f"filter_not_equals: columna inexistente '{transform.column}'"
            )
        value = _resolve_param_value(transform.value, parameters)
        return df[df[transform.column] != value].copy()

    if isinstance(transform, FilterRegexMatch):
        if transform.column not in df.columns:
            raise ValueError(
                f"filter_regex_match: columna inexistente '{transform.column}'"
            )
        mask = (
            df[transform.column]
            .astype("string")
            .str.match(transform.pattern, na=False)
        )
        if transform.keep == "not_matched":
            mask = ~mask
        return df[mask].copy()

    if isinstance(transform, GroupByAggregate):
        missing = [c for c in transform.by if c not in df.columns]
        if missing:
            raise ValueError(f"group_by_aggregate: columnas by inexistentes {missing}")
        if df.empty:
            cols = list(transform.by) + [a.target for a in transform.aggregations]
            return pd.DataFrame(columns=cols)
        named: dict[str, tuple[str, str]] = {}
        for agg in transform.aggregations:
            if agg.source not in df.columns:
                raise ValueError(
                    f"group_by_aggregate: columna source inexistente '{agg.source}'"
                )
            fn = "count" if agg.fn == AggFn.COUNT else agg.fn.value
            named[agg.target] = (agg.source, fn)
        out = df.groupby(transform.by, as_index=False, dropna=False).agg(**named)
        return out

    if isinstance(transform, SelectRename):
        missing = [c for c in transform.mapping if c not in df.columns]
        if missing:
            raise ValueError(f"select_rename: columnas inexistentes {missing}")
        return df[list(transform.mapping.keys())].rename(columns=transform.mapping)

    if isinstance(transform, Unpivot):
        missing = [c for c in transform.id_vars if c not in df.columns]
        if missing:
            raise ValueError(f"unpivot: id_vars inexistentes {missing}")
        value_vars = transform.value_vars
        if value_vars is not None:
            missing_v = [c for c in value_vars if c not in df.columns]
            if missing_v:
                raise ValueError(f"unpivot: value_vars inexistentes {missing_v}")
        out = df.melt(
            id_vars=transform.id_vars,
            value_vars=value_vars,
            var_name=transform.var_name,
            value_name=transform.value_name,
        )
        if transform.drop_null_values:
            out = out[out[transform.value_name].notna()]
        return out.reset_index(drop=True)

    raise ValueError(f"Transform no soportado: {type(transform).__name__}")


def prepare_source(
    path: str | Path, source: SourceSpec, parameters: dict[str, Any]
) -> tuple[pd.DataFrame, dict[str, Any]]:
    df, meta = load_source(path, source.loader)
    for transform in source.transforms:
        df = _apply_transform(df, transform, parameters)
    meta["num_filas_post_transform"] = len(df)
    return df.reset_index(drop=True), meta


# ---------------------------------------------------------------------------
# Join + computed + KPIs
# ---------------------------------------------------------------------------

class GranoNoResueltoError(ValueError):
    """Las keys de join se repiten en una fuente: el grano de la tabla no
    coincide con el grano del cruce. Un outer join con keys duplicadas
    produce filas cartesianas y rompe la garantia de cero perdida.

    Solucion: agregar un transform `group_by_aggregate` a esa fuente (los
    agentes lo proponen tras confirmar el grano con el usuario)."""


def _check_grano(df: pd.DataFrame, key_cols: list[str], lado: str) -> None:
    dupes = df.duplicated(subset=key_cols)
    if dupes.any():
        ejemplos = [
            "|".join(str(v) for v in row)
            for row in df.loc[dupes, key_cols].head(3).itertuples(index=False)
        ]
        raise GranoNoResueltoError(
            f"La fuente {lado} tiene {int(dupes.sum())} filas con keys de join "
            f"repetidas (ej: {ejemplos}). El grano de la tabla no coincide con "
            f"el grano del cruce: agregar un transform group_by_aggregate."
        )


def _prenormalize_join_keys(
    df: pd.DataFrame, keys: list[tuple[str, list[KeyNormalizer]]]
) -> pd.DataFrame:
    """Aplica los normalizadores del join a las columnas de llave ANTES de
    los transforms de la fuente.

    Motivo: si el grano se resuelve con group_by_aggregate sobre la llave
    CRUDA pero el join normaliza (digits_only, lstrip_zeros, upper...), dos
    llaves crudas distintas ('412' y '0412', 'BA123' y '123') colapsan a la
    misma llave normalizada DESPUES del group_by, produciendo duplicados en
    el cruce (GranoNoResueltoError falso). Normalizando antes, el group_by
    agrupa sobre la llave ya normalizada y el grano queda consistente con el
    cruce.

    Se normaliza hasta PUNTO FIJO: la cadena de normalizadores no siempre es
    idempotente (ej. 'SIN DC' -> digits_only -> '' -> lstrip_zeros -> '0'),
    y el _join la reaplica una vez mas. Iterar hasta estabilizar garantiza
    que la llave del group_by sea identica a la que produce el join.
    """
    df = df.copy()
    for col, normalizers in keys:
        if normalizers and col in df.columns:
            serie = df[col]
            for _ in range(4):
                nueva = _apply_normalizers(serie, normalizers)
                if nueva.astype("string").equals(serie.astype("string")):
                    break
                serie = nueva
            df[col] = serie
    return df


def _join(
    left: pd.DataFrame, right: pd.DataFrame, join: JoinSpec
) -> tuple[pd.DataFrame, list[str]]:
    left = left.copy()
    right = right.copy()
    key_cols: list[str] = []
    for i, key in enumerate(join.keys):
        if key.left not in left.columns:
            raise ValueError(f"Join key inexistente en fuente izquierda: '{key.left}'")
        if key.right not in right.columns:
            raise ValueError(f"Join key inexistente en fuente derecha: '{key.right}'")
        kcol = f"_k{i}"
        left[kcol] = _apply_normalizers(left[key.left], key.normalizers)
        right[kcol] = _apply_normalizers(right[key.right], key.normalizers)
        key_cols.append(kcol)

    _check_grano(left, key_cols, "izquierda")
    _check_grano(right, key_cols, "derecha")

    merged = left.merge(
        right,
        on=key_cols,
        how="outer",
        indicator=True,
        suffixes=(_LEFT_SUFFIX, _RIGHT_SUFFIX),
    )
    return merged, key_cols


def _apply_computed(matched: pd.DataFrame, computed: list[ComputedColumn]) -> pd.DataFrame:
    matched = matched.copy()
    for spec in computed:
        for col in (spec.left, spec.right):
            if col not in matched.columns:
                raise ValueError(f"computed '{spec.name}': columna inexistente '{col}'")
        left_vals = pd.to_numeric(matched[spec.left], errors="coerce")
        right_vals = pd.to_numeric(matched[spec.right], errors="coerce")
        if spec.op == "subtract":
            result = left_vals - right_vals
        elif spec.op == "ratio_pct":
            with np.errstate(divide="ignore", invalid="ignore"):
                result = pd.Series(
                    np.where(right_vals > 0, left_vals / right_vals * 100.0, np.nan),
                    index=matched.index,
                )
        else:
            raise ValueError(f"computed op no soportada: {spec.op}")
        if spec.round is not None:
            result = result.round(spec.round)
        matched[spec.name] = result
    return matched


def _compute_kpis(matched: pd.DataFrame, kpis: list[KpiSpec]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for kpi in kpis:
        if kpi.op == "count":
            out[kpi.id] = int(len(matched))
            continue
        if kpi.numerator not in matched.columns:
            raise ValueError(f"KPI '{kpi.id}': columna inexistente '{kpi.numerator}'")
        num = pd.to_numeric(matched[kpi.numerator], errors="coerce").sum()
        if kpi.op == "sum":
            out[kpi.id] = float(num)
            continue
        if kpi.denominator not in matched.columns:
            raise ValueError(f"KPI '{kpi.id}': columna inexistente '{kpi.denominator}'")
        den = pd.to_numeric(matched[kpi.denominator], errors="coerce").sum()
        out[kpi.id] = round(float(num) / float(den) * 100.0, 2) if den > 0 else None
    return out


def _resolve_col(df: pd.DataFrame, name: str) -> str | None:
    """Columnas presentes en ambas fuentes quedan sufijadas tras el merge:
    resuelve 'pedido' a 'pedido_left'/'pedido_right' si hace falta."""
    for candidato in (name, f"{name}{_LEFT_SUFFIX}", f"{name}{_RIGHT_SUFFIX}"):
        if candidato in df.columns:
            return candidato
    return None


def _classify_service_level(
    matched: pd.DataFrame,
    solo_left: pd.DataFrame,
    solo_right: pd.DataFrame,
    spec: ServiceLevelSpec,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Clasifica lineas y pedidos en completo/parcial/no_entregado/sin_pedido.

    Retorna (matched con columna nivel_servicio, bloque service_level para
    los KPIs) con conteos, unidades y porcentajes por clase.
    """
    matched = matched.copy()
    plan_col = _resolve_col(matched, spec.plan_column) or spec.plan_column
    real_col = _resolve_col(matched, spec.real_column) or spec.real_column
    for col in (plan_col, real_col):
        if col not in matched.columns and not matched.empty:
            raise ValueError(f"service_level: columna inexistente '{col}'")

    if matched.empty:
        matched["nivel_servicio"] = pd.Series(dtype="string")
        plan_m = pd.Series(dtype=float)
        real_m = pd.Series(dtype=float)
    else:
        plan_m = pd.to_numeric(matched[plan_col], errors="coerce").fillna(0)
        real_m = pd.to_numeric(matched[real_col], errors="coerce").fillna(0)
        umbral = plan_m * (1 - spec.tolerancia_pct / 100.0)
        matched["nivel_servicio"] = np.select(
            [real_m <= 0, real_m >= umbral],
            ["no_entregado", "completo"],
            default="parcial",
        )

    plan_col_l = _resolve_col(solo_left, spec.plan_column)
    plan_solo_left = (
        pd.to_numeric(solo_left[plan_col_l], errors="coerce").fillna(0).sum()
        if plan_col_l
        else 0.0
    )
    real_col_r = _resolve_col(solo_right, spec.real_column)
    real_solo_right = (
        pd.to_numeric(solo_right[real_col_r], errors="coerce").fillna(0).sum()
        if real_col_r
        else 0.0
    )

    def _bucket(clase: str) -> dict[str, Any]:
        mask = matched["nivel_servicio"] == clase
        return {
            "lineas": int(mask.sum()),
            "unidades_plan": float(plan_m[mask].sum()) if len(plan_m) else 0.0,
            "unidades_real": float(real_m[mask].sum()) if len(real_m) else 0.0,
        }

    por_clase = {c: _bucket(c) for c in ("completo", "parcial", "no_entregado")}
    # solo_left son pedidos sin ninguna entrega: se suman a no_entregado
    por_clase["no_entregado"]["lineas"] += len(solo_left)
    por_clase["no_entregado"]["unidades_plan"] += float(plan_solo_left)
    por_clase["sin_pedido"] = {
        "lineas": len(solo_right),
        "unidades_plan": 0.0,
        "unidades_real": float(real_solo_right),
    }

    total_lineas_pedido = sum(
        por_clase[c]["lineas"] for c in ("completo", "parcial", "no_entregado")
    )
    total_plan = sum(
        por_clase[c]["unidades_plan"] for c in ("completo", "parcial", "no_entregado")
    )
    for c in ("completo", "parcial", "no_entregado"):
        b = por_clase[c]
        b["pct_lineas"] = (
            round(b["lineas"] / total_lineas_pedido * 100, 2) if total_lineas_pedido else None
        )
        b["pct_unidades_plan"] = (
            round(b["unidades_plan"] / total_plan * 100, 2) if total_plan else None
        )

    bloque: dict[str, Any] = {
        "nivel": "linea",
        "total_lineas_pedido": total_lineas_pedido,
        "total_unidades_plan": float(total_plan),
        "clases": por_clase,
    }

    # Nivel pedido: un pedido es completo si TODAS sus lineas son completas,
    # no_entregado si ninguna linea recibio nada, parcial en el resto.
    if spec.pedido_key:
        key_m = _resolve_col(matched, spec.pedido_key)
        pedidos: dict[str, str] = {}
        if key_m and not matched.empty:
            for pid, grupo in matched.groupby(key_m, dropna=False):
                niveles = set(grupo["nivel_servicio"])
                if niveles <= {"completo"}:
                    pedidos[str(pid)] = "completo"
                elif niveles <= {"no_entregado"}:
                    pedidos[str(pid)] = "no_entregado"
                else:
                    pedidos[str(pid)] = "parcial"
        key_l = _resolve_col(solo_left, spec.pedido_key)
        if key_l:
            for pid in solo_left[key_l].dropna().astype(str):
                actual = pedidos.get(pid)
                # lineas sin entrega degradan un pedido completo a parcial
                if actual is None:
                    pedidos[pid] = "no_entregado"
                elif actual == "completo":
                    pedidos[pid] = "parcial"
        conteo = {"completo": 0, "parcial": 0, "no_entregado": 0}
        for clase in pedidos.values():
            conteo[clase] += 1
        total_pedidos = len(pedidos)
        bloque["pedidos"] = {
            "total": total_pedidos,
            "clases": {
                c: {
                    "pedidos": n,
                    "pct": round(n / total_pedidos * 100, 2) if total_pedidos else None,
                }
                for c, n in conteo.items()
            },
        }

    return matched, bloque


def _resolve_col(df: pd.DataFrame, name: str) -> str | None:
    """Resuelve un nombre de columna tolerando los sufijos del join
    (_left/_right). Cuando ambas fuentes traen una columna homonima, el
    merge la desdobla; un breakdown que pide el nombre base debe encontrarla."""
    for cand in (name, f"{name}{_LEFT_SUFFIX}", f"{name}{_RIGHT_SUFFIX}"):
        if cand in df.columns:
            return cand
    return None


def _run_breakdown(
    spec: BreakdownSpec,
    matched: pd.DataFrame,
    solo_left: pd.DataFrame,
    right_source: pd.DataFrame,
) -> pd.DataFrame:
    if spec.universe == "matched":
        df = matched
    elif spec.universe == "left_full":
        df = pd.concat([matched, solo_left], ignore_index=True, sort=False)
    else:
        df = right_source

    # Resolver dimensiones/metricas tolerando sufijos _left/_right del join.
    # Se renombra la columna resuelta al nombre pedido para que la salida y
    # los renderers usen la etiqueta declarada en el profile.
    rename_resueltos: dict[str, str] = {}
    for base in list(spec.dimensions):
        if base not in df.columns:
            resolved = _resolve_col(df, base)
            if resolved is not None and resolved != base:
                rename_resueltos[resolved] = base
    for metric in spec.metrics:
        for attr in ("column", "numerator", "denominator"):
            base = getattr(metric, attr, None)
            if base and base not in df.columns:
                resolved = _resolve_col(df, base)
                if resolved is not None and resolved != base:
                    rename_resueltos[resolved] = base
    if rename_resueltos:
        df = df.rename(columns=rename_resueltos)

    if spec.filter_equals:
        for col, val in spec.filter_equals.items():
            if col not in df.columns:
                raise ValueError(
                    f"breakdown '{spec.id}': filtro sobre columna inexistente '{col}'"
                )
            df = df[df[col] == val]

    if spec.filter_not_equals:
        for col, val in spec.filter_not_equals.items():
            if col not in df.columns:
                raise ValueError(
                    f"breakdown '{spec.id}': filter_not_equals sobre columna "
                    f"inexistente '{col}'"
                )
            df = df[df[col] != val]

    for col in spec.require_non_null:
        if col not in df.columns:
            raise ValueError(
                f"breakdown '{spec.id}': require_non_null sobre columna "
                f"inexistente '{col}'"
            )
        serie = df[col]
        vacio = serie.isna() | (serie.astype(str).str.strip() == "")
        df = df[~vacio]

    missing = [d for d in spec.dimensions if d not in df.columns]
    if missing:
        raise ValueError(f"breakdown '{spec.id}': dimensiones inexistentes {missing}")

    if df.empty:
        cols = list(spec.dimensions) + [m.id for m in spec.metrics]
        return pd.DataFrame(columns=cols)

    grouped = df.groupby(spec.dimensions, dropna=False)
    out = pd.DataFrame(index=grouped.size().index)
    for metric in spec.metrics:
        if metric.op == "count":
            out[metric.id] = grouped.size()
        elif metric.op == "sum":
            if metric.column not in df.columns:
                raise ValueError(
                    f"breakdown '{spec.id}': metrica '{metric.id}' referencia "
                    f"columna inexistente '{metric.column}'"
                )
            out[metric.id] = grouped[metric.column].apply(
                lambda s: pd.to_numeric(s, errors="coerce").sum()
            )
        else:  # ratio_pct_of_sums
            for col in (metric.numerator, metric.denominator):
                if col not in df.columns:
                    raise ValueError(
                        f"breakdown '{spec.id}': metrica '{metric.id}' referencia "
                        f"columna inexistente '{col}'"
                    )
            num = grouped[metric.numerator].apply(
                lambda s: pd.to_numeric(s, errors="coerce").sum()
            )
            den = grouped[metric.denominator].apply(
                lambda s: pd.to_numeric(s, errors="coerce").sum()
            )
            out[metric.id] = (num / den.replace(0, np.nan) * 100).round(2)
    out = out.reset_index()

    if spec.sort_by_metric:
        if spec.sort_by_metric not in out.columns:
            raise ValueError(
                f"breakdown '{spec.id}': sort_by_metric '{spec.sort_by_metric}' no existe"
            )
        out = out.sort_values(spec.sort_by_metric, ascending=False)
    if spec.top_n:
        out = out.head(spec.top_n)
    return out.reset_index(drop=True)


def _build_no_cruzados(
    solo_left: pd.DataFrame,
    solo_right: pd.DataFrame,
    key_cols: list[str],
    profile: MatchProfile,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for _, row in solo_left.iterrows():
        rows.append(
            {
                "origen": profile.left.role,
                "key": "|".join(str(row[k]) for k in key_cols),
                "motivo": profile.unmatched_motivo_left,
            }
        )
    for _, row in solo_right.iterrows():
        rows.append(
            {
                "origen": profile.right.role,
                "key": "|".join(str(row[k]) for k in key_cols),
                "motivo": profile.unmatched_motivo_right,
            }
        )
    return pd.DataFrame(rows, columns=["origen", "key", "motivo"])


def run_profile(
    profile: MatchProfile,
    left_path: str | Path,
    right_path: str | Path,
    parameters: dict[str, Any] | None = None,
) -> GenericMatchResult:
    """Pipeline completo: load -> transforms -> join -> computed -> KPIs.

    Verifica la contabilidad cero-perdida antes de retornar.
    """
    parameters = parameters or {}
    faltantes = [
        p.name for p in profile.parameters if p.required and p.name not in parameters
    ]
    if faltantes:
        raise ValueError(f"Parametros runtime requeridos faltantes: {faltantes}")

    left_keys = [(k.left, list(k.normalizers)) for k in profile.join.keys]
    right_keys = [(k.right, list(k.normalizers)) for k in profile.join.keys]

    # LEFT: load -> pre-normalizar llaves del join -> transforms. Asi el
    # group_by del grano agrupa sobre la llave ya normalizada (grano
    # consistente con el cruce).
    left_raw, left_meta = load_source(left_path, profile.left.loader)
    left_df = _prenormalize_join_keys(left_raw, left_keys)
    for transform in profile.left.transforms:
        left_df = _apply_transform(left_df, transform, parameters)
    left_df = left_df.reset_index(drop=True)
    left_meta["num_filas_post_transform"] = len(left_df)

    # right_source (post-load, pre-transforms) se conserva ORIGINAL para
    # breakdowns con universe=right_source (ej. devoluciones por motivo). La
    # pre-normalizacion de llaves solo aplica al camino del cruce.
    right_raw, right_meta = load_source(right_path, profile.right.loader)
    right_df = _prenormalize_join_keys(right_raw, right_keys)
    for transform in profile.right.transforms:
        right_df = _apply_transform(right_df, transform, parameters)
    right_df = right_df.reset_index(drop=True)
    right_meta["num_filas_post_transform"] = len(right_df)

    merged, key_cols = _join(left_df, right_df, profile.join)

    matched = merged[merged["_merge"] == "both"].drop(columns=["_merge"]).copy()
    solo_left = merged[merged["_merge"] == "left_only"].drop(columns=["_merge"]).copy()
    solo_right = merged[merged["_merge"] == "right_only"].drop(columns=["_merge"]).copy()

    matched = _apply_computed(matched, profile.computed)
    kpis = _compute_kpis(matched, profile.kpis)
    no_cruzados = _build_no_cruzados(solo_left, solo_right, key_cols, profile)

    service_level_block: dict[str, Any] | None = None
    if profile.service_level:
        matched, service_level_block = _classify_service_level(
            matched, solo_left, solo_right, profile.service_level
        )
        kpis["service_level"] = service_level_block

    breakdowns: dict[str, pd.DataFrame] = {}
    for bd in profile.breakdowns:
        breakdowns[bd.id] = _run_breakdown(bd, matched, solo_left, right_raw)

    result = GenericMatchResult(
        profile_id=profile.profile_id,
        profile_version=profile.version,
        parameters=parameters,
        matched=matched.reset_index(drop=True),
        solo_left=solo_left.reset_index(drop=True),
        solo_right=solo_right.reset_index(drop=True),
        no_cruzados=no_cruzados,
        kpis=kpis,
        left_meta=left_meta,
        right_meta=right_meta,
        service_level=service_level_block,
        breakdowns=breakdowns,
    )
    result.verify_accounting(len(left_df), len(right_df))
    return result
