"""Chequeo estatico de referencias del MatchProfile, sin abrir archivos.

Simula el flujo de columnas (loader -> transforms -> join -> computed) y
verifica que KPIs, service_level, breakdowns y computed referencien
columnas que realmente existiran. Atrapa "columnas fantasma" que un agente
LLM pueda inventar ANTES de guardar la propuesta, en vez de fallar en
runtime durante el generate.

Limitacion consciente: los loaders `registered` no declaran columnas, asi
que sus fuentes no se validan (retornan None = "desconocido").
"""
from __future__ import annotations

from app.platform.profile import (
    FilterEquals,
    FilterNotEquals,
    FilterRegexMatch,
    GroupByAggregate,
    MatchProfile,
    SelectRename,
    SourceSpec,
    Unpivot,
)

_LEFT_SUFFIX = "_left"
_RIGHT_SUFFIX = "_right"


def _loader_columns(source: SourceSpec) -> set[str] | None:
    loader = source.loader
    if getattr(loader, "type", "") != "tabular":
        return None
    return {c.name for c in loader.columns}


def _apply_transforms(cols: set[str], source: SourceSpec, errors: list[str], lado: str) -> set[str]:
    out = set(cols)
    for t in source.transforms:
        if isinstance(t, (FilterEquals, FilterNotEquals, FilterRegexMatch)):
            if t.column not in out:
                errors.append(
                    f"{lado}: transform {t.op} referencia columna inexistente '{t.column}'"
                )
        elif isinstance(t, GroupByAggregate):
            for b in t.by:
                if b not in out:
                    errors.append(
                        f"{lado}: group_by referencia columna inexistente '{b}'"
                    )
            for agg in t.aggregations:
                if agg.source not in out:
                    errors.append(
                        f"{lado}: agregacion '{agg.target}' referencia columna "
                        f"inexistente '{agg.source}'"
                    )
            out = set(t.by) | {a.target for a in t.aggregations}
        elif isinstance(t, SelectRename):
            for orig in t.mapping:
                if orig not in out:
                    errors.append(
                        f"{lado}: select_rename referencia columna inexistente '{orig}'"
                    )
            out = set(t.mapping.values())
        elif isinstance(t, Unpivot):
            for c in t.id_vars:
                if c not in out:
                    errors.append(
                        f"{lado}: unpivot id_var inexistente '{c}'"
                    )
            out = set(t.id_vars) | {t.var_name, t.value_name}
    return out


def _con_sufijos(left: set[str], right: set[str]) -> set[str]:
    overlap = left & right
    disponibles = (left | right) - overlap
    for c in overlap:
        disponibles.add(f"{c}{_LEFT_SUFFIX}")
        disponibles.add(f"{c}{_RIGHT_SUFFIX}")
        disponibles.add(c)  # _resolve_col del motor acepta el nombre base
    return disponibles


def check_profile_references(profile: MatchProfile) -> list[str]:
    """Retorna la lista de errores de referencia (vacia = OK)."""
    errors: list[str] = []

    left_cols = _loader_columns(profile.left)
    right_cols = _loader_columns(profile.right)
    if left_cols is not None:
        left_cols = _apply_transforms(left_cols, profile.left, errors, "fuente izquierda")
    if right_cols is not None:
        right_cols = _apply_transforms(right_cols, profile.right, errors, "fuente derecha")

    # join keys
    if left_cols is not None:
        for k in profile.join.keys:
            if k.left not in left_cols:
                errors.append(f"join: key izquierda inexistente '{k.left}'")
    if right_cols is not None:
        for k in profile.join.keys:
            if k.right not in right_cols:
                errors.append(f"join: key derecha inexistente '{k.right}'")

    if left_cols is None or right_cols is None:
        return errors  # sin columnas declaradas no se puede validar mas

    merged = _con_sufijos(left_cols, right_cols)

    disponibles = set(merged)
    for c in profile.computed:
        for ref in (c.left, c.right):
            if ref not in disponibles:
                errors.append(f"computed '{c.name}': columna inexistente '{ref}'")
        disponibles.add(c.name)

    for kpi in profile.kpis:
        for ref in (kpi.numerator, kpi.denominator):
            if ref and ref not in disponibles:
                errors.append(f"kpi '{kpi.id}': columna inexistente '{ref}'")

    if profile.service_level:
        sl = profile.service_level
        for ref in (sl.plan_column, sl.real_column, sl.pedido_key):
            if ref and ref not in disponibles:
                errors.append(f"service_level: columna inexistente '{ref}'")
        disponibles.add("nivel_servicio")

    if profile.data_model:
        for dim in profile.data_model.dimensions:
            if dim.key not in disponibles:
                errors.append(
                    f"data_model: dimension '{dim.name}' referencia columna "
                    f"inexistente '{dim.key}'"
                )
            for attr in dim.attributes:
                if attr not in disponibles:
                    errors.append(
                        f"data_model: dimension '{dim.name}' atributo "
                        f"inexistente '{attr}'"
                    )

    for bd in profile.breakdowns:
        if bd.universe == "right_source":
            base = _loader_columns(profile.right) or set()
        else:
            base = disponibles
        for d in bd.dimensions:
            if d not in base:
                errors.append(
                    f"breakdown '{bd.id}' (universe={bd.universe}): dimension "
                    f"inexistente '{d}'"
                )
        for m in bd.metrics:
            for ref in (m.column, m.numerator, m.denominator):
                if ref and ref not in base:
                    errors.append(
                        f"breakdown '{bd.id}': metrica '{m.id}' referencia "
                        f"columna inexistente '{ref}'"
                    )
        if bd.filter_equals:
            for col in bd.filter_equals:
                if col not in base:
                    errors.append(
                        f"breakdown '{bd.id}': filtro sobre columna inexistente '{col}'"
                    )

    return errors
