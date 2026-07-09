"""Constructor del modelo de datos exportable (la "base de datos en Excel").

A partir del resultado del cruce produce tablas funcionales estilo estrella
ligero, listas para Power Query / import en Power BI:

- Fact (una fila por linea del outer join completo): fact_id, estado_cruce
  (cruzado | solo_<rol>), keys del join con nombre legible, columnas de
  ambas fuentes, computed columns, nivel_servicio si aplica, y FKs
  <dim>_id hacia cada dimension declarada.
- Una tabla por DimensionSpec: <dim>_id + key + atributos descriptivos.

Todo plano, sin cross-tab, sin merges: el equipo de BI la consume tal cual.
"""
from __future__ import annotations

import re

import pandas as pd

from app.platform.engine import GenericMatchResult, _LEFT_SUFFIX, _RIGHT_SUFFIX
from app.platform.profile import DataModelSpec, DimensionSpec, MatchProfile


def _snake(name: str) -> str:
    s = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    return re.sub(r"__+", "_", s)


def _resolve(df: pd.DataFrame, name: str) -> str | None:
    for cand in (name, f"{name}{_LEFT_SUFFIX}", f"{name}{_RIGHT_SUFFIX}"):
        if cand in df.columns:
            return cand
    return None


def _build_fact(profile: MatchProfile, result: GenericMatchResult) -> pd.DataFrame:
    partes = [result.matched.assign(estado_cruce="cruzado")]
    if profile.data_model is None or profile.data_model.include_unmatched:
        partes.append(
            result.solo_left.assign(estado_cruce=f"solo_{profile.left.role}")
        )
        partes.append(
            result.solo_right.assign(estado_cruce=f"solo_{profile.right.role}")
        )
    fact = pd.concat(partes, ignore_index=True, sort=False)

    # keys internas _k{i} -> nombre legible de la key izquierda del join
    renames = {}
    for i, key in enumerate(profile.join.keys):
        kcol = f"_k{i}"
        if kcol in fact.columns:
            renames[kcol] = f"key_{key.left}"
    fact = fact.rename(columns=renames)

    # El motor solo clasifica nivel_servicio en matched: completar las filas
    # no cruzadas con su clase real (pedido sin entrega / entrega sin pedido)
    # para que los desgloses por nivel_servicio cuadren con el bloque
    # service_level de los KPIs.
    if "nivel_servicio" in fact.columns:
        sin_clase = fact["nivel_servicio"].isna()
        estado = fact["estado_cruce"]
        fact.loc[sin_clase & (estado == f"solo_{profile.left.role}"),
                 "nivel_servicio"] = "no_entregado"
        fact.loc[sin_clase & (estado == f"solo_{profile.right.role}"),
                 "nivel_servicio"] = "sin_pedido"

    fact.insert(0, "fact_id", range(1, len(fact) + 1))
    return fact


def _build_dimension(
    fact: pd.DataFrame, dim: DimensionSpec
) -> tuple[pd.DataFrame, pd.Series | None]:
    """Retorna (tabla dimension, serie de FKs alineada al fact)."""
    key_col = _resolve(fact, dim.key)
    if key_col is None:
        raise ValueError(
            f"data_model: la dimension '{dim.name}' referencia columna "
            f"inexistente '{dim.key}' en el fact"
        )
    attr_cols = []
    attrs_usados = []
    for attr in dim.attributes:
        resolved = _resolve(fact, attr)
        # Evitar seleccionar dos veces la misma columna (key == atributo, o
        # dos atributos que resuelven a la misma columna): un subset con
        # labels duplicados rompe sort_values.
        if resolved and resolved != key_col and resolved not in attr_cols:
            attr_cols.append(resolved)
            attrs_usados.append(attr)

    subset = fact[[key_col, *attr_cols]].copy()
    subset[key_col] = subset[key_col].astype("string")
    dim_table = (
        subset.dropna(subset=[key_col])
        .drop_duplicates(subset=[key_col])
        .sort_values(key_col)
        .reset_index(drop=True)
    )
    id_col = f"{_snake(dim.name)}_id"
    dim_table.insert(0, id_col, range(1, len(dim_table) + 1))
    # nombres limpios en la dimension (sin sufijos _left/_right)
    limpio = {key_col: dim.key}
    for orig, attr in zip(attr_cols, attrs_usados):
        limpio[orig] = attr
    dim_table = dim_table.rename(columns=limpio)

    mapping = dict(zip(dim_table[dim.key], dim_table[id_col]))
    fks = fact[key_col].astype("string").map(mapping)
    return dim_table, fks


def build_data_model(
    profile: MatchProfile, result: GenericMatchResult
) -> dict[str, pd.DataFrame]:
    """Produce {nombre_tabla: DataFrame} segun profile.data_model.

    Si el profile no declara data_model, retorna un fact minimo sin
    dimensiones (siempre hay una tabla usable).
    """
    spec = profile.data_model or DataModelSpec()
    fact = _build_fact(profile, result)

    # Robustez: un profile (sobre todo los propuestos por el LLM) puede
    # producir un fact con columnas de nombre DUPLICADO (ej. una key del join
    # que coincide con el nombre de una columna de atributo, o dos fuentes
    # que aportan la misma etiqueta). Un DataFrame con labels duplicados
    # rompe sort_values y no es importable a Power BI/Excel. Se conserva la
    # primera aparicion de cada columna.
    if fact.columns.duplicated().any():
        fact = fact.loc[:, ~fact.columns.duplicated()].copy()

    tablas: dict[str, pd.DataFrame] = {}
    for dim in spec.dimensions:
        dim_table, fks = _build_dimension(fact, dim)
        id_col = f"{_snake(dim.name)}_id"
        # Int64 nullable: mismo dtype que el id de la dimension, para que
        # las relaciones fact -> dim casen tipos en el modelo Power BI.
        fact[id_col] = pd.to_numeric(fks, errors="coerce").astype("Int64")
        tablas[dim.name] = dim_table

    tablas[spec.fact_name] = fact
    return tablas
