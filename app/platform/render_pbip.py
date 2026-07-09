"""Generador de proyecto Power BI Desktop (PBIP) de la plataforma (Fase 4).

A partir de un MatchProfile + GenericMatchResult produce una carpeta de
proyecto PBIP lista para abrir con Power BI Desktop:

    <output_dir>/
        <profile_id>.pbip              shortcut al report
        <profile_id>.Report/
            definition.pbir            referencia byPath al SemanticModel
            report.json                report PBIR-Legacy (paginas + visuales)
            StaticResources/RegisteredResources/NutriAvicolaTheme.json
        <profile_id>.SemanticModel/
            definition.pbism
            definition/                modelo en TMDL
                database.tmdl
                model.tmdl
                relationships.tmdl     fact -> dimensiones (manyToOne)
                expressions.tmdl       parametro RutaDatos (carpeta de CSVs)
                tables/<tabla>.tmdl    columnas + medidas DAX + particion
        data/
            <tabla>.csv                una por tabla del modelo
        README.md
        .gitignore

Tablas del modelo semantico:
- TODAS las tablas del data_model del profile (fact + dimensiones con ids,
  la "base de datos en Excel" del negocio) via build_data_model.
- matched / no_cruzados (particiones crudas del cruce).
- Breakdowns referenciados por visuales (`visual.table`).

Particiones: las tablas chicas (dimensiones y breakdowns < 1000 filas) van
EMBEBIDAS en el TMDL (patron "Enter Data" de Desktop: Table.FromRows sobre
JSON comprimido en base64), inmunes a rutas rotas. El fact y las
particiones crudas van por CSV via el parametro `RutaDatos`.

Semantica de medidas (correccion critica): el fact contiene TODAS las
filas del outer join (cruzado + solo_<rol_left> + solo_<rol_right>). Un
SUM ingenuo mezcla universos (ej. entregas sin pedido inflan "unidades
entregadas"). Por eso el generador deriva del contrato el lado de cada
columna (outputs del loader + transforms de cada fuente) y envuelve el
DAX en CALCULATE con filtro de estado_cruce:
- columna del lado plan (left):  IN {cruzado, solo_<rol_left>}
- columna del lado real (right): IN {cruzado}  (entregado DE LO PEDIDO)
- computed del cruce:            IN {cruzado}
- count (COUNTROWS):             sin filtro (sirve para desgloses por
  nivel_servicio donde sin_pedido es una clase legitima)
El excedente se expone aparte con la medida "Unidades entregadas sin
pedido" en vez de esconderse dentro de otra medida.

Los lineageTag se generan con uuid5 (deterministicos por profile+objeto)
para que regenerar el proyecto no produzca diffs espurios.
"""
from __future__ import annotations

import base64
import json
import re
import uuid
import zlib
from pathlib import Path
from typing import Any

import pandas as pd

from app.platform.data_model import build_data_model
from app.platform.engine import GenericMatchResult
from app.platform.profile import (
    GroupByAggregate,
    KpiSpec,
    MatchProfile,
    PowerBIMeasureSpec,
    PowerBIPageSpec,
    PowerBIReportSpec,
    PowerBIVisualSpec,
    SelectRename,
    SourceSpec,
    TabularLoaderSpec,
    Unpivot,
)

# Paleta corporativa NutriAvicola (tokens de frontend/src/styles.scss y
# app/core/excel_style.py). Cambiar aca = cambia el theme completo.
BRAND_NAVY = "#0F2E4C"
BRAND_NAVY_400 = "#2E5680"
BRAND_NAVY_300 = "#5D82A8"
BRAND_NAVY_200 = "#94AFC7"
BRAND_NAVY_50 = "#E9EFF6"
BRAND_ORANGE = "#E87722"
BRAND_ORANGE_300 = "#F5A251"
BRAND_ORANGE_600 = "#C7621B"
BRAND_ORANGE_200 = "#FBC38B"
TEXT_DARK = "#1F252E"
SEM_GOOD = "#63BE7B"
SEM_WARN = "#FFEB84"
SEM_BAD = "#F8696B"
GRAY_NEUTRAL = "#9BA3AF"

# Colores fijos por clase de nivel de servicio (semaforo corporativo).
NIVEL_SERVICIO_COLORS = {
    "completo": SEM_GOOD,
    "parcial": SEM_WARN,
    "no_entregado": SEM_BAD,
    "sin_pedido": GRAY_NEUTRAL,
}

NIVEL_SERVICIO_LABELS = {
    "completo": "Completo",
    "parcial": "Parcial",
    "no_entregado": "No entregado",
    "sin_pedido": "Sin pedido CEN",
}

THEME_RESOURCE_NAME = "NutriAvicolaTheme.json"
LOGO_RESOURCE_NAME = "NutriAvicolaLogo.jpg"
LOGO_SOURCE = Path(__file__).resolve().parents[2] / "resources" / "image_720508810_0.jpg"
MATCHED_TABLE = "matched"
NO_CRUZADOS_TABLE = "no_cruzados"

# Tablas chicas (dims/breakdowns) con menos filas que esto van embebidas.
INLINE_MAX_ROWS = 1000

_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

# Lienzo estandar 16:9 de Power BI.
PAGE_WIDTH = 1280
PAGE_HEIGHT = 720
MARGIN = 20
GAP = 16
SLICER_H = 68
CARD_H = 110
CHART_H = 250
HEADER_H = 64
LOGO_W = 180
LOGO_H = 56
BACK_BUTTON_W = 220
BACK_BUTTON_H = 30
LABEL_NO_DATO = "Sin dato"
LABEL_SIN_MOTIVO = "Sin motivo reportado"

_CHART_TYPES = {
    "tendencia": "lineChart",
    "barras_categoria": "clusteredBarChart",
    "donut": "donutChart",
    "funnel": "funnel",
    "area": "areaChart",
    "columnas_apiladas": "hundredPercentStackedColumnChart",
}

_MEASURE_FORMATS = {
    "entero": "#,##0",
    "decimal": "#,##0.00",
    # Valores 0-100 del motor: % literal escapado, sin multiplicar.
    "porcentaje": "0.00\\%",
    "moneda": '"COP" #,##0',
}

# ids reservados de las medidas de nivel de servicio autogeneradas
SL_SIN_PEDIDO_ID = "sl_unidades_sin_pedido"
SL_PEDIDOS_COMPLETOS_ID = "sl_pedidos_completos_pct"
SL_NS_UNIDADES_ID = "sl_ns_unidades_pct"
SL_NS_LINEAS_CRUZADAS_ID = "sl_ns_lineas_cruzadas_pct"
SL_LINEAS_ID = "sl_lineas_cruce"


def _tag(*parts: str) -> str:
    """lineageTag deterministico (uuid5) por objeto."""
    return str(uuid.uuid5(_NAMESPACE, "|".join(parts)))


def _snake(name: str) -> str:
    s = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return re.sub(r"__+", "_", s).strip("_")


def _pretty(col: str) -> str:
    if col in NIVEL_SERVICIO_LABELS:
        return NIVEL_SERVICIO_LABELS[col]
    return col.replace("_", " ").strip().title()


def _humanize_fact_frames(frames: dict[str, pd.DataFrame]) -> None:
    """Columna legible para categorias de nivel de servicio (sin underscores)."""
    for df in frames.values():
        if "nivel_servicio" not in df.columns:
            continue
        df["estado_entrega"] = (
            df["nivel_servicio"]
            .astype("string")
            .map(NIVEL_SERVICIO_LABELS)
            .fillna(df["nivel_servicio"].astype("string"))
        )


def _sanitize_category_labels(frames: dict[str, pd.DataFrame]) -> None:
    """Reemplaza vacios/(Blank) por etiquetas legibles en categorias."""
    label_by_column = {
        "motivo_devolucion": LABEL_SIN_MOTIVO,
        "cliente": LABEL_NO_DATO,
        "distrito": LABEL_NO_DATO,
        "descripcion_item": LABEL_NO_DATO,
        "descripcion_material": LABEL_NO_DATO,
    }
    for df in frames.values():
        for col, label in label_by_column.items():
            if col not in df.columns:
                continue
            serie = df[col].astype("string")
            clean = serie.str.strip().str.lower()
            mask = serie.isna() | clean.isin({"", "(blank)", "blank", "nan", "none", "<na>"})
            if mask.any():
                df[col] = serie.mask(mask, label)


# ---------------------------------------------------------------------------
# Datos: tablas del modelo -> frames
# ---------------------------------------------------------------------------

def _rename_key_columns(df: pd.DataFrame, profile: MatchProfile) -> pd.DataFrame:
    renames: dict[str, str] = {}
    for i, key in enumerate(profile.join.keys):
        kcol = f"_k{i}"
        if kcol not in df.columns:
            continue
        target = key.left if key.left not in df.columns else f"{key.left}_key"
        renames[kcol] = target
    df = df.rename(columns=renames)
    return df[[c for c in df.columns if not c.startswith("_")]]


def _referenced_tables(spec: PowerBIReportSpec) -> list[str]:
    out: list[str] = []
    for measure in spec.measures:
        if measure.table not in out:
            out.append(measure.table)
    for page in spec.pages:
        for visual in page.visuals:
            if visual.table and visual.table not in out:
                out.append(visual.table)
    return out


def _match_breakdown(name: str, profile: MatchProfile) -> str | None:
    """Resuelve un nombre de tabla de visual a un breakdown id: match exacto
    por id o label, luego por hoja Excel homonima, luego por prefijo o
    similitud semantica (los agentes suelen nombrar FactDevoluciones cuando
    el breakdown se llama motivos_devolucion)."""
    normalized = _snake(name)
    for bd in profile.breakdowns:
        if name == bd.id or normalized == _snake(bd.label):
            return bd.id
    if profile.report and profile.report.excel:
        for sheet in profile.report.excel.sheets:
            if sheet.kind == "breakdown" and _snake(sheet.name) == normalized:
                return sheet.breakdown_id
    for bd in profile.breakdowns:
        if bd.id.startswith(normalized) or normalized.startswith(bd.id):
            return bd.id
    name_core = normalized.removeprefix("fact_").removeprefix("dim_")
    best_score = 0
    best_id: str | None = None
    for bd in profile.breakdowns:
        for candidate in (bd.id, bd.label):
            cand = _snake(candidate).removeprefix("fact_").removeprefix("dim_")
            score = 0
            for part in name_core.split("_"):
                if not part:
                    continue
                for other in cand.split("_"):
                    if part in other or other in part:
                        score += 1
            if score > best_score:
                best_score = score
                best_id = bd.id
    if best_score > 0:
        return best_id
    return None


def _build_frames(
    profile: MatchProfile, result: GenericMatchResult
) -> tuple[dict[str, pd.DataFrame], set[str]]:
    """({nombre_tabla: DataFrame}, tablas que son breakdowns)."""
    frames: dict[str, pd.DataFrame] = dict(build_data_model(profile, result))
    _humanize_fact_frames(frames)
    frames[MATCHED_TABLE] = _rename_key_columns(result.matched.copy(), profile)
    frames[NO_CRUZADOS_TABLE] = result.no_cruzados.copy()

    breakdown_tables: set[str] = set()
    if profile.report and profile.report.powerbi:
        for name in _referenced_tables(profile.report.powerbi):
            if name in frames:
                continue
            bd_id = _match_breakdown(name, profile)
            if bd_id and bd_id in result.breakdowns:
                frames[name] = result.breakdowns[bd_id].copy()
                breakdown_tables.add(name)
    _sanitize_category_labels(frames)
    return frames, breakdown_tables


def _column_types(df: pd.DataFrame) -> dict[str, str]:
    """Nombre de columna -> tipo logico ('int64' | 'double' | 'string').

    Las fechas se exportan como texto ISO: robusto ante configuraciones
    regionales del Desktop y ordena bien de forma lexicografica.
    """
    out: dict[str, str] = {}
    for name in df.columns:
        serie = df[name]
        if pd.api.types.is_integer_dtype(serie):
            out[name] = "int64"
        elif pd.api.types.is_numeric_dtype(serie):
            out[name] = "double"
        else:
            out[name] = "string"
    return out


def _write_csvs(frames: dict[str, pd.DataFrame], data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    for name, df in frames.items():
        df.to_csv(data_dir / f"{name}.csv", index=False, encoding="utf-8")


# ---------------------------------------------------------------------------
# Contexto semantico: que lado del cruce alimenta cada columna del fact
# ---------------------------------------------------------------------------

def _source_output_columns(source: SourceSpec) -> set[str] | None:
    """Columnas finales que una fuente aporta al join, derivadas del
    contrato (loader spec + transforms). None si el loader es registrado
    (custom en codigo, outputs no declarados)."""
    loader = source.loader
    if not isinstance(loader, TabularLoaderSpec):
        return None
    cols = {c.name for c in loader.columns}
    for t in source.transforms:
        if isinstance(t, GroupByAggregate):
            cols = set(t.by) | {a.target for a in t.aggregations}
        elif isinstance(t, SelectRename):
            cols = set(t.mapping.values())
        elif isinstance(t, Unpivot):
            base = set(t.id_vars) | {t.var_name, t.value_name}
            cols = base
    return cols


class _SemanticContext:
    """Deriva, por tabla y columna, el filtro de estado_cruce que hace la
    medida coherente. Todo sale del contrato: roles left/right y outputs
    declarados de cada fuente (nada hardcodeado por caso)."""

    def __init__(self, profile: MatchProfile, frames: dict[str, pd.DataFrame]):
        self.profile = profile
        self.fact_tables = {
            name for name, df in frames.items() if "estado_cruce" in df.columns
        }
        self.left_states = ["cruzado", f"solo_{profile.left.role}"]
        self.right_only_state = f"solo_{profile.right.role}"
        self.left_cols = _source_output_columns(profile.left) or set()
        self.right_cols = _source_output_columns(profile.right) or set()
        self.key_cols = {f"key_{k.left}" for k in profile.join.keys}
        self.key_names = {k.left for k in profile.join.keys}
        self.computed = {c.name for c in profile.computed}

    def states_for(self, table: str, column: str | None) -> list[str] | None:
        """Estados de cruce que debe filtrar una medida sobre `column`.
        None = sin filtro (columna de origen desconocido o tabla sin
        estado_cruce)."""
        if table not in self.fact_tables or not column:
            return None
        base = column
        if base.endswith("_left"):
            return list(self.left_states)
        if base.endswith("_right"):
            return ["cruzado"]
        if base in self.computed:
            return ["cruzado"]
        if base in self.key_cols or base in self.key_names:
            # keys del join: el universo de negocio es lo pedido
            return list(self.left_states)
        in_left = base in self.left_cols
        in_right = base in self.right_cols
        if in_left and not in_right:
            return list(self.left_states)
        if in_right and not in_left:
            return ["cruzado"]
        if in_left and in_right:
            return list(self.left_states)
        return None


def _calculate_wrap(expr: str, table: str, states: list[str] | None) -> str:
    if not states:
        return expr
    t = _dax_table(table)
    lista = ", ".join(f'"{s}"' for s in states)
    return f"CALCULATE({expr}, {t}[estado_cruce] IN {{{lista}}})"


# ---------------------------------------------------------------------------
# Medidas DAX (declarativas, sin DAX libre)
# ---------------------------------------------------------------------------

def _dax_table(table: str) -> str:
    if table.replace("_", "").isalnum() and not table[0].isdigit():
        return table
    return f"'{table}'"


def measure_name(kpi: KpiSpec) -> str:
    """Nombre visible de la medida DAX derivada de un KPI (fallback)."""
    return (kpi.label or kpi.id).replace("'", "")


def _dax_from_kpi(kpi: KpiSpec, table: str) -> str:
    t = _dax_table(table)
    if kpi.op == "ratio_pct_of_sums":
        return (
            f"DIVIDE(SUM({t}[{kpi.numerator}]), "
            f"SUM({t}[{kpi.denominator}])) * 100"
        )
    if kpi.op == "sum":
        return f"SUM({t}[{kpi.numerator}])"
    if kpi.op == "count":
        return f"COUNTROWS({t})"
    raise ValueError(f"KPI op no soportada para DAX: {kpi.op}")


def _dax_from_measure_spec(spec: PowerBIMeasureSpec, ctx: _SemanticContext) -> str:
    t = _dax_table(spec.table)
    if spec.op == "sum":
        if not spec.column:
            raise ValueError(f"measure '{spec.id}': sum requiere column")
        return _calculate_wrap(
            f"SUM({t}[{spec.column}])", spec.table,
            ctx.states_for(spec.table, spec.column),
        )
    if spec.op == "count":
        # COUNTROWS sin filtro: cuenta todas las lineas del cruce, incluidas
        # sin_pedido (clase legitima en desgloses por nivel_servicio).
        return f"COUNTROWS({t})"
    if spec.op == "distinct_count":
        if not spec.column:
            raise ValueError(f"measure '{spec.id}': distinct_count requiere column")
        return _calculate_wrap(
            f"DISTINCTCOUNT({t}[{spec.column}])", spec.table,
            ctx.states_for(spec.table, spec.column),
        )
    if spec.op == "ratio_pct_of_sums":
        if not (spec.numerator and spec.denominator):
            raise ValueError(
                f"measure '{spec.id}': ratio_pct_of_sums requiere numerator y denominator"
            )
        num = _calculate_wrap(
            f"SUM({t}[{spec.numerator}])", spec.table,
            ctx.states_for(spec.table, spec.numerator),
        )
        den = _calculate_wrap(
            f"SUM({t}[{spec.denominator}])", spec.table,
            ctx.states_for(spec.table, spec.denominator),
        )
        return f"DIVIDE({num}, {den}) * 100"
    raise ValueError(f"measure op no soportada: {spec.op}")


class _MeasureRegistry:
    """Medidas del modelo: id -> (tabla host, nombre visible).

    `por_tabla` agrupa (nombre, dax, formato) para escribirlas en el TMDL
    de su tabla. `ensure_column_total` crea medidas implicitas para
    visuales que referencian una columna numerica de su tabla (ej.
    metricas de un breakdown) en vez de una medida declarada.
    """

    def __init__(self) -> None:
        self.by_id: dict[str, tuple[str, str]] = {}
        self.por_tabla: dict[str, list[tuple[str, str, str]]] = {}
        # Los nombres de medida son GLOBALES al modelo en Power BI: no puede
        # haber dos medidas con el mismo nombre visible, aunque esten en
        # tablas distintas. Este set garantiza unicidad.
        self.used_names: set[str] = set()

    def _unique_name(self, base: str, table: str) -> str:
        if base not in self.used_names:
            return base
        # Desambiguar por tabla (ej. varios breakdowns con columna 'lineas').
        etiqueta = table.replace("_", " ").strip()
        candidate = f"{base} ({etiqueta})"
        i = 2
        while candidate in self.used_names:
            candidate = f"{base} ({etiqueta} {i})"
            i += 1
        return candidate

    def add(self, mid: str, table: str, name: str, dax: str, fmt: str) -> None:
        name = self._unique_name(name, table)
        self.by_id[mid] = (table, name)
        self.por_tabla.setdefault(table, []).append((name, dax, fmt))
        self.used_names.add(name)

    def ensure_column_total(self, table: str, column: str) -> tuple[str, str]:
        mid = f"__total::{table}::{column}"
        if mid not in self.by_id:
            self.add(
                mid, table, f"Total {_pretty(column).lower()}",
                f"SUM({_dax_table(table)}[{column}])", "#,##0",
            )
        return self.by_id[mid]


def _resolve_fact_column(df: pd.DataFrame, name: str) -> str | None:
    for cand in (name, f"{name}_left", f"{name}_right", f"key_{name}"):
        if cand in df.columns:
            return cand
    return None


def _add_service_level_measures(
    registry: _MeasureRegistry,
    profile: MatchProfile,
    fact_name: str,
    fact: pd.DataFrame,
    ctx: _SemanticContext,
) -> None:
    """Medidas autogeneradas cuando el profile declara service_level:
    excedente sin pedido, % de pedidos completos y conteo de lineas."""
    sl = profile.service_level
    t = _dax_table(fact_name)

    real_col = _resolve_fact_column(fact, sl.real_column)
    if real_col:
        registry.add(
            SL_SIN_PEDIDO_ID, fact_name, "Unidades entregadas sin pedido",
            f'CALCULATE(SUM({t}[{real_col}]), '
            f'{t}[estado_cruce] IN {{"{ctx.right_only_state}"}})',
            "#,##0",
        )

    registry.add(
        SL_LINEAS_ID, fact_name, "Lineas del cruce",
        f"COUNTROWS({t})", "#,##0",
    )

    if sl.pedido_key and "nivel_servicio" in fact.columns:
        pedido_col = _resolve_fact_column(fact, sl.pedido_key)
        if pedido_col:
            estados = ", ".join(f'"{s}"' for s in ctx.left_states)
            dax = (
                f"VAR __pedidos = CALCULATETABLE(VALUES({t}[{pedido_col}]), "
                f"{t}[estado_cruce] IN {{{estados}}})\n"
                f"VAR __detalle = ADDCOLUMNS(\n"
                f"    __pedidos,\n"
                f'    "@incompletas", CALCULATE(\n'
                f"        COUNTROWS({t}),\n"
                f'        {t}[nivel_servicio] <> "completo",\n'
                f"        {t}[estado_cruce] IN {{{estados}}}\n"
                f"    )\n"
                f")\n"
                f"RETURN\n"
                f"    DIVIDE(\n"
                f"        COUNTROWS(FILTER(__detalle, [@incompletas] = 0)),\n"
                f"        COUNTROWS(__detalle)\n"
                f"    ) * 100"
            )
            registry.add(
                SL_PEDIDOS_COMPLETOS_ID, fact_name, "Pedidos completos (%)",
                dax, "0.00\\%",
            )

    plan_col = _resolve_fact_column(fact, sl.plan_column)
    real_col = _resolve_fact_column(fact, sl.real_column)
    if plan_col and real_col:
        estados_left = ", ".join(f'"{s}"' for s in ctx.left_states)
        num = (
            f"CALCULATE(SUM({t}[{real_col}]), {t}[estado_cruce] = \"cruzado\")"
        )
        den = (
            f"CALCULATE(SUM({t}[{plan_col}]), "
            f"{t}[estado_cruce] IN {{{estados_left}}})"
        )
        registry.add(
            SL_NS_UNIDADES_ID, fact_name, "Nivel servicio unidades (%)",
            f"DIVIDE({num}, {den}) * 100", "0.00\\%",
        )
        num_m = f"CALCULATE(SUM({t}[{real_col}]), {t}[estado_cruce] = \"cruzado\")"
        den_m = f"CALCULATE(SUM({t}[{plan_col}]), {t}[estado_cruce] = \"cruzado\")"
        registry.add(
            SL_NS_LINEAS_CRUZADAS_ID, fact_name, "Cumplimiento lineas cruzadas (%)",
            f"DIVIDE({num_m}, {den_m}) * 100", "0.00\\%",
        )


def _build_measures(
    profile: MatchProfile,
    frames: dict[str, pd.DataFrame],
    fact_name: str,
    ctx: _SemanticContext,
) -> _MeasureRegistry:
    registry = _MeasureRegistry()
    spec = profile.report.powerbi if profile.report else None

    if spec and spec.measures:
        for m in spec.measures:
            if m.table not in frames:
                continue
            # Robustez: una medida propuesta por el LLM puede estar
            # malformada (op sum sin column, columna inexistente en su
            # tabla...). Se omite esa medida sin tumbar todo el render; los
            # visuales que la referencien caen al total implicito o se saltan.
            try:
                dax = _dax_from_measure_spec(m, ctx)
            except (ValueError, KeyError):
                continue
            registry.add(
                m.id, m.table, (m.label or m.id).replace("'", ""),
                dax, _MEASURE_FORMATS[m.format],
            )
    else:
        # Fallback: medidas derivadas de los KPIs, sobre matched (que por
        # definicion solo contiene filas cruzadas: sin mezcla de universos).
        for kpi in profile.kpis:
            fmt = "0.00\\%" if kpi.op == "ratio_pct_of_sums" else "#,##0"
            registry.add(
                kpi.id, MATCHED_TABLE, measure_name(kpi),
                _dax_from_kpi(kpi, MATCHED_TABLE), fmt,
            )

    if profile.service_level and fact_name in frames:
        _add_service_level_measures(
            registry, profile, fact_name, frames[fact_name], ctx
        )
    return registry


# ---------------------------------------------------------------------------
# Semantic model (TMDL)
# ---------------------------------------------------------------------------

def _tmdl_ident(name: str) -> str:
    """Identificador TMDL: se cita con comillas simples si no es palabra simple."""
    if name.replace("_", "").isalnum() and not name[0].isdigit():
        return name
    return f"'{name}'"


def _m_csv_expression(csv_name: str, types: dict[str, str]) -> str:
    """Query M que lee el CSV desde RutaDatos y tipa columnas."""
    type_map = {"int64": "Int64.Type", "double": "type number", "string": "type text"}
    pairs = ", ".join(
        f'{{"{col}", {type_map[t]}}}' for col, t in types.items()
    )
    return (
        "let\n"
        f'\t\t\t\t    Source = Csv.Document(File.Contents(RutaDatos & "\\{csv_name}"),'
        "[Delimiter=\",\", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),\n"
        '\t\t\t\t    #"Promoted Headers" = Table.PromoteHeaders(Source, '
        "[PromoteAllScalars=true]),\n"
        '\t\t\t\t    #"Changed Type" = Table.TransformColumnTypes('
        f'#"Promoted Headers",{{{pairs}}})\n'
        "\t\t\t\tin\n"
        '\t\t\t\t    #"Changed Type"'
    )


def _json_safe(value: Any) -> Any:
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (int, float, str, bool)):
        return value
    if hasattr(value, "item"):  # numpy scalar
        return value.item()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return str(value)


def encode_inline_rows(df: pd.DataFrame) -> str:
    """Filas del DataFrame como JSON comprimido (deflate crudo) en base64,
    el mismo patron 'Enter Data' que Power BI Desktop embebe en M."""
    rows = [
        [_json_safe(v) for v in row]
        for row in df.itertuples(index=False, name=None)
    ]
    raw = json.dumps(rows, ensure_ascii=False).encode("utf-8")
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
    comprimido = compressor.compress(raw) + compressor.flush()
    return base64.b64encode(comprimido).decode("ascii")


def _m_inline_expression(df: pd.DataFrame, types: dict[str, str]) -> str:
    """Query M con los datos embebidos (patron Enter Data): el modelo no
    depende de RutaDatos para estas tablas."""
    type_map = {"int64": "Int64.Type", "double": "type number", "string": "type text"}
    b64 = encode_inline_rows(df)
    col_names = ", ".join(f'"{c}"' for c in types)
    pairs = ", ".join(f'{{"{col}", {type_map[t]}}}' for col, t in types.items())
    return (
        "let\n"
        "\t\t\t\t    Source = Table.FromRows(Json.Document(Binary.Decompress("
        f'Binary.FromText("{b64}", BinaryEncoding.Base64), Compression.Deflate)), '
        f"{{{col_names}}}),\n"
        '\t\t\t\t    #"Changed Type" = Table.TransformColumnTypes('
        f"Source,{{{pairs}}})\n"
        "\t\t\t\tin\n"
        '\t\t\t\t    #"Changed Type"'
    )


def _table_tmdl(
    table: str,
    df: pd.DataFrame,
    measures: list[tuple[str, str, str]],
    inline: bool,
) -> str:
    types = _column_types(df)
    lines: list[str] = [
        f"table {_tmdl_ident(table)}",
        f"\tlineageTag: {_tag('table', table)}",
        "",
    ]

    for name, dax, fmt in measures:
        if "\n" in dax:
            lines.append(f"\tmeasure {_tmdl_ident(name)} =")
            for dax_line in dax.split("\n"):
                lines.append(f"\t\t\t{dax_line}")
        else:
            lines.append(f"\tmeasure {_tmdl_ident(name)} = {dax}")
        lines.append(f"\t\tformatString: {fmt}")
        lines.append(f"\t\tlineageTag: {_tag('measure', table, name)}")
        lines.append("")

    fmt_map = {"int64": "0", "double": "#,##0.00"}
    for col, t in types.items():
        lines.append(f"\tcolumn {_tmdl_ident(col)}")
        lines.append(f"\t\tdataType: {t}")
        if t in fmt_map:
            lines.append(f"\t\tformatString: {fmt_map[t]}")
        lines.append(f"\t\tlineageTag: {_tag('column', table, col)}")
        lines.append("\t\tsummarizeBy: none")
        lines.append(f"\t\tsourceColumn: {col}")
        lines.append("")
        lines.append("\t\tannotation SummarizationSetBy = Automatic")
        lines.append("")

    expression = (
        _m_inline_expression(df, types)
        if inline
        else _m_csv_expression(f"{table}.csv", types)
    )
    lines.append(f"\tpartition {_tmdl_ident(table)} = m")
    lines.append("\t\tmode: import")
    lines.append("\t\tsource =")
    lines.append(f"\t\t\t\t{expression}")
    lines.append("")
    lines.append("\tannotation PBI_NavigationStepName = Navigation")
    lines.append("")
    lines.append("\tannotation PBI_ResultType = Table")
    lines.append("")
    return "\n".join(lines)


def _relationships(
    profile: MatchProfile, frames: dict[str, pd.DataFrame]
) -> list[tuple[str, str, str]]:
    """[(fact, dim, columna id)] para cada dimension del data_model."""
    out: list[tuple[str, str, str]] = []
    if profile.data_model is None:
        return out
    fact = profile.data_model.fact_name
    if fact not in frames:
        return out
    for dim in profile.data_model.dimensions:
        id_col = f"{_snake(dim.name)}_id"
        if (
            dim.name in frames
            and id_col in frames[fact].columns
            and id_col in frames[dim.name].columns
        ):
            out.append((fact, dim.name, id_col))
    return out


def _write_semantic_model(
    model_dir: Path,
    profile: MatchProfile,
    frames: dict[str, pd.DataFrame],
    registry: _MeasureRegistry,
    inline_tables: set[str],
    data_dir: Path,
) -> None:
    definition = model_dir / "definition"
    tables_dir = definition / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)

    (model_dir / "definition.pbism").write_text(
        json.dumps(
            {
                "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json",
                "version": "4.2",
                "settings": {"qnaEnabled": True},
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    (definition / "database.tmdl").write_text(
        "database\n\tcompatibilityLevel: 1550\n", encoding="utf-8"
    )

    query_order = json.dumps(["RutaDatos", *frames.keys()])
    model_lines = [
        "model Model",
        "\tculture: es-CO",
        "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
        "\tsourceQueryCulture: es-CO",
        "\tdataAccessOptions",
        "\t\tlegacyRedirects",
        "\t\treturnErrorValuesAsNull",
        "",
        f"annotation PBI_QueryOrder = {query_order}",
        "",
        "annotation __PBI_TimeIntelligenceEnabled = 0",
        "",
    ]
    for name in frames:
        model_lines.append(f"ref table {_tmdl_ident(name)}")
    model_lines.append("")
    (definition / "model.tmdl").write_text("\n".join(model_lines), encoding="utf-8")

    rels = _relationships(profile, frames)
    if rels:
        rel_lines: list[str] = []
        for fact, dim, id_col in rels:
            rel_lines.append(f"relationship {_tag('relationship', fact, dim)}")
            rel_lines.append("\tcrossFilteringBehavior: oneDirection")
            rel_lines.append(f"\tfromColumn: {_tmdl_ident(fact)}.{_tmdl_ident(id_col)}")
            rel_lines.append(f"\ttoColumn: {_tmdl_ident(dim)}.{_tmdl_ident(id_col)}")
            rel_lines.append("")
        (definition / "relationships.tmdl").write_text(
            "\n".join(rel_lines), encoding="utf-8"
        )

    # Parametro de ruta: apunta por defecto a la carpeta data/ del proyecto.
    ruta_default = str(data_dir.resolve()).replace("\\", "\\\\")
    expressions = (
        f'expression RutaDatos = "{ruta_default}" '
        'meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]\n'
        f"\tlineageTag: {_tag('expression', 'RutaDatos')}\n"
        "\n"
        "\tannotation PBI_ResultType = Text\n"
    )
    (definition / "expressions.tmdl").write_text(expressions, encoding="utf-8")

    for name, df in frames.items():
        (tables_dir / f"{name}.tmdl").write_text(
            _table_tmdl(
                name, df, registry.por_tabla.get(name, []),
                inline=name in inline_tables,
            ),
            encoding="utf-8",
        )


# ---------------------------------------------------------------------------
# Theme corporativo
# ---------------------------------------------------------------------------

# Variantes de theme elegibles por el usuario (design.theme). Cada variante
# define fondo de lienzo, fondo de visual y color de texto base; la paleta
# de marca (navy + naranja) se mantiene en todas.
_THEME_VARIANTS: dict[str, dict[str, str]] = {
    "nutriavicola": {
        "canvas": BRAND_NAVY_50,
        "visual_bg": "#FFFFFF",
        "text": TEXT_DARK,
        "title": BRAND_NAVY,
        "border": "#E4E7EB",
    },
    "nutriavicola_claro": {
        "canvas": "#FFFFFF",
        "visual_bg": "#FFFFFF",
        "text": TEXT_DARK,
        "title": BRAND_NAVY,
        "border": "#E4E7EB",
    },
    "nutriavicola_oscuro": {
        "canvas": BRAND_NAVY,
        "visual_bg": "#12395E",
        "text": "#F2F5F9",
        "title": "#FFFFFF",
        "border": "#2E5680",
    },
}


def build_theme(variant: str = "nutriavicola") -> dict:
    """Theme JSON Power BI con la paleta NutriAvicola (navy dominante,
    naranja solo acento, semaforo reservado a estados).

    Diseno alineado al PBIX corporativo de referencia: visuales tipo
    tarjeta con sombra suave, esquinas redondeadas y titulos en banda.
    """
    v = _THEME_VARIANTS.get(variant, _THEME_VARIANTS["nutriavicola"])
    drop_shadow = [{
        "show": True,
        "color": {"solid": {"color": "#1F252E"}},
        "position": "Outer",
        "preset": "BottomRight",
        "transparency": 85,
    }]
    return {
        "name": "NutriAvicola",
        "dataColors": [
            BRAND_NAVY,
            BRAND_ORANGE,
            BRAND_NAVY_400,
            BRAND_ORANGE_300,
            BRAND_NAVY_300,
            BRAND_ORANGE_600,
            BRAND_NAVY_200,
            BRAND_ORANGE_200,
        ],
        "background": v["canvas"],
        "foreground": v["text"],
        "tableAccent": BRAND_NAVY,
        "good": SEM_GOOD,
        "neutral": SEM_WARN,
        "bad": SEM_BAD,
        "maximum": BRAND_NAVY,
        "center": BRAND_NAVY_300,
        "minimum": BRAND_NAVY_50,
        "textClasses": {
            "title": {"fontFace": "Segoe UI Semibold", "fontSize": 14, "color": v["title"]},
            "header": {"fontFace": "Segoe UI Semibold", "fontSize": 13, "color": v["title"]},
            "label": {"fontFace": "Segoe UI", "fontSize": 12, "color": v["text"]},
            "callout": {"fontFace": "Segoe UI Semibold", "fontSize": 32, "color": v["title"]},
        },
        "visualStyles": {
            "*": {
                "*": {
                    "background": [{"show": True, "color": {"solid": {"color": v["visual_bg"]}}, "transparency": 0}],
                    "border": [{"show": True, "color": {"solid": {"color": v["border"]}}, "radius": 8}],
                    "dropShadow": drop_shadow,
                    "title": [{
                        "show": True,
                        "fontColor": {"solid": {"color": v["title"]}},
                        "background": {"solid": {"color": v["visual_bg"]}},
                        "fontSize": 12,
                        "fontFamily": "Segoe UI Semibold",
                    }],
                    "visualHeader": [{"show": False}],
                }
            },
            "card": {
                "*": {
                    "labels": [{"fontSize": 30, "color": {"solid": {"color": v["title"]}}}],
                    "categoryLabels": [{"show": True, "fontSize": 11, "color": {"solid": {"color": v["text"]}}}],
                }
            },
            "slicer": {
                "*": {
                    "header": [{"show": True, "fontColor": {"solid": {"color": v["title"]}}, "fontSize": 11}],
                    "items": [{"fontColor": {"solid": {"color": v["text"]}}, "fontSize": 10}],
                }
            },
        },
    }


# ---------------------------------------------------------------------------
# Report (PBIR-Legacy: report.json)
# ---------------------------------------------------------------------------

def _lit(value: str) -> dict:
    return {"expr": {"Literal": {"Value": value}}}


def _nivel_servicio_data_points(entity: str, column: str) -> list[dict]:
    """Colores fijos del semaforo por clase de nivel de servicio."""
    points = []
    for clase, color in NIVEL_SERVICIO_COLORS.items():
        points.append({
            "properties": {"fill": {"solid": {"color": _lit(f"'{color}'")}}},
            "selector": {
                "data": [{
                    "scopeId": {
                        "Comparison": {
                            "ComparisonKind": 0,
                            "Left": {
                                "Column": {
                                    "Expression": {"SourceRef": {"Entity": entity}},
                                    "Property": column,
                                }
                            },
                            "Right": {"Literal": {"Value": f"'{clase}'"}},
                        }
                    }
                }]
            },
        })
    return points


def _visual_container(
    name: str,
    visual_type: str,
    entity: str,
    x: int,
    y: int,
    width: int,
    height: int,
    z: int,
    projections: dict,
    select: list[dict],
    title: str | None,
    objects: dict | None = None,
    column_properties: dict | None = None,
) -> dict:
    position = {"x": x, "y": y, "z": z, "width": width, "height": height}
    single_visual: dict = {
        "visualType": visual_type,
        "projections": projections,
        "prototypeQuery": {
            "Version": 2,
            "From": [{"Name": "t", "Entity": entity, "Type": 0}],
            "Select": select,
        },
        "drillFilterOtherVisuals": True,
    }
    if column_properties:
        single_visual["columnProperties"] = column_properties
    if objects:
        single_visual["objects"] = objects
    if title:
        single_visual["vcObjects"] = {
            "title": [
                {
                    "properties": {
                        "show": _lit("true"),
                        "text": _lit(f"'{title}'"),
                        "fontSize": _lit("13D"),
                        "bold": _lit("true"),
                    }
                }
            ]
        }
    config = {
        "name": name,
        "layouts": [{"id": 0, "position": position}],
        "singleVisual": single_visual,
    }
    return {
        "x": x,
        "y": y,
        "z": z,
        "width": width,
        "height": height,
        "config": json.dumps(config, ensure_ascii=False),
        "filters": "[]",
    }


def _image_container(
    name: str,
    x: int,
    y: int,
    width: int,
    height: int,
    z: int,
    resource_name: str,
) -> dict:
    config = {
        "name": name,
        "layouts": [{"id": 0, "position": {"x": x, "y": y, "z": z, "width": width, "height": height}}],
        "singleVisual": {
            "visualType": "image",
            "drillFilterOtherVisuals": True,
            "objects": {
                "general": [{
                    "properties": {
                        "imageUrl": {
                            "expr": {
                                "ResourcePackageItem": {
                                    "PackageName": "RegisteredResources",
                                    "PackageType": 1,
                                    "ItemName": resource_name,
                                }
                            }
                        }
                    }
                }]
            },
            "vcObjects": {
                "background": [{"properties": {"show": _lit("false")}}],
            },
        },
    }
    return {
        "x": x, "y": y, "z": z, "width": width, "height": height,
        "config": json.dumps(config, ensure_ascii=False),
        "filters": "[]",
    }


def _textbox_container(
    name: str,
    x: int,
    y: int,
    width: int,
    height: int,
    z: int,
    text: str,
    font_size: str = "24pt",
) -> dict:
    config = {
        "name": name,
        "layouts": [{"id": 0, "position": {"x": x, "y": y, "z": z, "width": width, "height": height}}],
        "singleVisual": {
            "visualType": "textbox",
            "drillFilterOtherVisuals": True,
            "objects": {
                "general": [{
                    "properties": {
                        "paragraphs": [{
                            "textRuns": [{
                                "value": text,
                                "textStyle": {
                                    "fontWeight": "bold",
                                    "fontSize": font_size,
                                    "color": BRAND_NAVY,
                                },
                            }],
                        }],
                    }
                }]
            },
            "vcObjects": {
                "background": [{"properties": {"show": _lit("false")}}],
            },
        },
    }
    return {
        "x": x, "y": y, "z": z, "width": width, "height": height,
        "config": json.dumps(config, ensure_ascii=False),
        "filters": "[]",
    }


def _back_button_container(
    name: str,
    x: int,
    y: int,
    width: int,
    height: int,
    z: int,
    text: str,
) -> dict:
    """Boton visual de navegacion para paginas drill."""
    config = {
        "name": name,
        "layouts": [{"id": 0, "position": {"x": x, "y": y, "z": z, "width": width, "height": height}}],
        "singleVisual": {
            "visualType": "textbox",
            "drillFilterOtherVisuals": True,
            "objects": {
                "general": [{
                    "properties": {
                        "paragraphs": [{
                            "textRuns": [{
                                "value": text,
                                "textStyle": {
                                    "fontWeight": "normal",
                                    "fontSize": "10pt",
                                    "color": BRAND_NAVY,
                                },
                            }],
                        }],
                    }
                }]
            },
            "vcObjects": {
                "background": [{
                    "properties": {
                        "show": _lit("true"),
                        "color": _lit(f"'{BRAND_NAVY_50}'"),
                        "transparency": _lit("0D"),
                    }
                }],
                "border": [{
                    "properties": {
                        "show": _lit("true"),
                        "color": _lit(f"'{BRAND_NAVY_200}'"),
                    }
                }],
            },
        },
    }
    return {
        "x": x, "y": y, "z": z, "width": width, "height": height,
        "config": json.dumps(config, ensure_ascii=False),
        "filters": "[]",
    }


def _append_page_header(
    containers: list[dict],
    page: PowerBIPageSpec,
    include_logo: bool,
    show_back_button: bool = False,
    back_target: str = "Nivel de servicio",
) -> None:
    """Banda superior: logo NutriAvicola + titulo de pagina (referencia PBIX)."""
    if include_logo:
        containers.append(_image_container(
            name=_tag("visual", f"{page.name}|logo")[:20],
            x=MARGIN, y=4, width=LOGO_W, height=LOGO_H, z=10000,
            resource_name=LOGO_RESOURCE_NAME,
        ))
    title_x = MARGIN + (LOGO_W + GAP if include_logo else 0)
    title_w = PAGE_WIDTH - title_x - MARGIN
    if show_back_button:
        title_w -= BACK_BUTTON_W + GAP
    containers.append(_textbox_container(
        name=_tag("visual", f"{page.name}|title")[:20],
        x=title_x, y=10, width=title_w, height=HEADER_H - 12, z=9999,
        text=page.name,
    ))
    if show_back_button:
        containers.append(_back_button_container(
            name=_tag("visual", f"{page.name}|back")[:20],
            x=PAGE_WIDTH - MARGIN - BACK_BUTTON_W,
            y=16,
            width=BACK_BUTTON_W,
            height=BACK_BUTTON_H,
            z=10001,
            text=f"<- Volver a {back_target}",
        ))


def _measure_select(entity: str, name: str) -> tuple[dict, str]:
    query_ref = f"{entity}.{name}"
    return (
        {
            "Measure": {
                "Expression": {"SourceRef": {"Source": "t"}},
                "Property": name,
            },
            "Name": query_ref,
        },
        query_ref,
    )


def _column_select(entity: str, name: str) -> tuple[dict, str]:
    query_ref = f"{entity}.{name}"
    return (
        {
            "Column": {
                "Expression": {"SourceRef": {"Source": "t"}},
                "Property": name,
            },
            "Name": query_ref,
        },
        query_ref,
    )


def _chart_category_col(
    entity: str, column: str, frames: dict[str, pd.DataFrame]
) -> str:
    """Usa etiquetas humanas cuando existen (estado_entrega)."""
    if column == "nivel_servicio" and entity in frames:
        if "estado_entrega" in frames[entity].columns:
            return "estado_entrega"
    return column


def _slicer_candidates(
    profile: MatchProfile,
    plan: dict[str, list[dict]],
    fact_name: str,
    frames: dict[str, pd.DataFrame],
) -> list[tuple[str, str]]:
    """Slicers via dimensiones (nombres legibles), no IDs crudos del fact."""
    out: list[tuple[str, str]] = []
    vistos: set[str] = set()

    if profile.data_model:
        dim_by_key = {d.key: d for d in profile.data_model.dimensions}
        for item in plan["charts"] + [
            b for b in plan["bottom"] if b.get("kind") == "matrix"
        ]:
            cat = item.get("category")
            if not cat or cat in vistos:
                continue
            dim = dim_by_key.get(cat)
            if dim and dim.name in frames:
                attr = dim.attributes[0] if dim.attributes else dim.key
                if attr in frames[dim.name].columns:
                    out.append((dim.name, attr))
                    vistos.add(cat)
                    if len(out) == 2:
                        break

    if (
        profile.service_level
        and fact_name in frames
        and "estado_entrega" in frames[fact_name].columns
        and "estado_entrega" not in vistos
    ):
        out.append((fact_name, "estado_entrega"))
    return out[:3]


def _default_powerbi_spec(profile: MatchProfile) -> PowerBIReportSpec:
    """Sin spec del ReportDesigner: 1 card por KPI + tabla detalle."""
    visuals: list[PowerBIVisualSpec] = [
        PowerBIVisualSpec(kind="card_kpi", title=kpi.label or kpi.id, measure=kpi.id)
        for kpi in profile.kpis
    ]
    visuals.append(PowerBIVisualSpec(kind="tabla_detalle", title="Detalle del cruce"))
    return PowerBIReportSpec(
        pages=[PowerBIPageSpec(name="Resumen", visuals=visuals)]
    )


def _with_drill_pages(
    spec: PowerBIReportSpec,
    profile: MatchProfile,
    frames: dict[str, pd.DataFrame],
    fact_name: str,
) -> PowerBIReportSpec:
    """Agrega paginas drill estandar para CEN/SAP cuando aplica.

    Se activa solo en perfiles con service_level + data_model y evita
    duplicados si el ReportDesigner ya definio paginas con esos nombres.
    """
    if profile.service_level is None or profile.data_model is None:
        return spec
    page_names = {p.name.lower().strip() for p in spec.pages}
    drill_defs = [
        {
            "name": "Logistica",
            "proposito": "Drill operativo por distrito para gestion logistica.",
            "visuals": [
                {"kind": "card_kpi", "title": "Nivel de servicio (%)",
                 "measure": "nivel_servicio_pct", "table": fact_name},
                {"kind": "barras_categoria", "title": "Unidades entregadas por distrito",
                 "measure": "unidades_entregadas", "category": "distrito", "table": fact_name},
                {"kind": "tendencia", "title": "Nivel de servicio por distrito",
                 "measure": "nivel_servicio_pct", "category": "distrito", "table": fact_name},
                {"kind": "donut", "title": "Lineas por estado de entrega",
                 "measure": "lineas_totales", "category": "nivel_servicio", "table": fact_name},
                {"kind": "matriz", "title": "Unidades pedidas por distrito",
                 "measure": "unidades_pedidas", "category": "distrito", "table": fact_name},
                {"kind": "tabla_detalle", "title": "Detalle logistica", "table": fact_name},
            ],
        },
        {
            "name": "Venta",
            "proposito": "Drill comercial por cliente y material.",
            "visuals": [
                {"kind": "card_kpi", "title": "Nivel de servicio (%)",
                 "measure": "nivel_servicio_pct", "table": fact_name},
                {"kind": "barras_categoria", "title": "Unidades pedidas por cliente",
                 "measure": "unidades_pedidas", "category": "cliente", "table": fact_name},
                {"kind": "funnel", "title": "Unidades pedidas por material",
                 "measure": "unidades_pedidas", "category": "descripcion_item", "table": fact_name},
                {"kind": "tendencia", "title": "Nivel de servicio por cliente",
                 "measure": "nivel_servicio_pct", "category": "cliente", "table": fact_name},
                {"kind": "tabla_detalle", "title": "Detalle comercial", "table": fact_name},
            ],
        },
        {
            "name": "Destinatario x causal",
            "proposito": "Drill de devoluciones para analizar causal dominante.",
            "visuals": [
                {"kind": "card_kpi", "title": "Total unidades devueltas",
                 "measure": "unidades_devueltas", "table": "Devoluciones"},
                {"kind": "barras_categoria", "title": "Unidades devueltas por motivo",
                 "measure": "unidades_devueltas", "category": "motivo_devolucion", "table": "Devoluciones"},
                {"kind": "donut", "title": "Participacion de lineas por motivo",
                 "measure": "lineas", "category": "motivo_devolucion", "table": "Devoluciones"},
                {"kind": "matriz", "title": "Lineas devueltas por motivo",
                 "measure": "lineas", "category": "motivo_devolucion", "table": "Devoluciones"},
                {"kind": "tabla_detalle", "title": "Detalle destinatario x causal", "table": "Devoluciones"},
            ],
        },
    ]

    pages = [p.model_copy(deep=True) for p in spec.pages]
    for d in drill_defs:
        if d["name"].lower().strip() in page_names:
            continue
        valid_visuals: list[PowerBIVisualSpec] = []
        for v in d["visuals"]:
            table = v.get("table")
            if table and table not in frames:
                continue
            valid_visuals.append(PowerBIVisualSpec.model_validate(v))
        if not valid_visuals:
            continue
        pages.append(
            PowerBIPageSpec(
                name=d["name"],
                proposito=d["proposito"],
                visuals=valid_visuals,
            )
        )

    return PowerBIReportSpec(
        theme=spec.theme, design=spec.design, measures=spec.measures, pages=pages
    )


_CHART_KINDS = {
    "tendencia", "barras_categoria", "donut", "funnel", "area",
    "columnas_apiladas",
}


def _apply_design_prefs(spec: PowerBIReportSpec) -> PowerBIReportSpec:
    """Aplica de forma deterministica las preferencias de diseno elegidas
    por el usuario: numero de paginas, charts por pagina y tipos preferidos.
    Cards y tablas se conservan siempre (contexto numerico + auditoria)."""
    design = spec.design
    if design is None:
        return spec

    pages: list[PowerBIPageSpec] = []
    preferidos = set(design.tipos_preferidos)
    for page in spec.pages[: design.max_paginas]:
        charts = [v for v in page.visuals if v.kind in _CHART_KINDS]
        otros = [v for v in page.visuals if v.kind not in _CHART_KINDS]
        if preferidos:
            elegidos = [v for v in charts if v.kind in preferidos]
            # nunca dejar la pagina sin graficos por una preferencia estricta
            if not elegidos and charts:
                elegidos = charts
            charts = elegidos
        charts = charts[: design.max_charts_por_pagina]
        visuals = [v for v in page.visuals if v in otros or v in charts]
        if not visuals:
            continue
        pages.append(
            PowerBIPageSpec(
                name=page.name, proposito=page.proposito, visuals=visuals
            )
        )
    if not pages:
        return spec
    return PowerBIReportSpec(
        theme=spec.theme, design=design, measures=spec.measures, pages=pages
    )


def _resolve_visual_measure(
    visual_measure: str | None,
    visual_table: str,
    registry: _MeasureRegistry,
    frames: dict[str, pd.DataFrame],
) -> tuple[str, str] | None:
    """(tabla host, nombre de medida) para el visual, o None si no resuelve.

    Orden: medida declarada (measures spec o fallback KPI); si no, columna
    numerica homonima en la tabla del visual (total implicito, caso
    metricas de breakdown como 'unidades_devueltas')."""
    if visual_measure and visual_measure in registry.by_id:
        return registry.by_id[visual_measure]
    if (
        visual_measure
        and visual_table in frames
        and visual_measure in frames[visual_table].columns
    ):
        return registry.ensure_column_total(visual_table, visual_measure)
    return None


# --------------------------- plan de pagina --------------------------------

def _plan_page(
    page: PowerBIPageSpec,
    profile: MatchProfile,
    registry: _MeasureRegistry,
    frames: dict[str, pd.DataFrame],
    fact_name: str,
    enrich: bool,
) -> dict[str, list[dict]]:
    """Convierte los visuales del spec en un plan por bandas (slicers /
    cards / charts / bottom), resolviendo bindings y aplicando el
    enriquecimiento de la pagina ejecutiva."""
    plan: dict[str, list[dict]] = {
        "slicers": [], "cards": [], "charts": [], "bottom": [],
    }

    def entity_for(v: PowerBIVisualSpec) -> str:
        return v.table if v.table in frames else MATCHED_TABLE

    uses_fact = False
    for v in page.visuals:
        entity = entity_for(v)
        if entity == fact_name:
            uses_fact = True
        if v.kind == "card_kpi":
            resolved = _resolve_visual_measure(v.measure, entity, registry, frames)
            if resolved:
                plan["cards"].append(
                    {"title": v.title, "entity": resolved[0], "measure": resolved[1]}
                )
        elif v.kind in _CHART_TYPES:
            resolved = _resolve_visual_measure(v.measure, entity, registry, frames)
            if resolved is None:
                continue
            host, nombre = resolved
            cat = _chart_category_col(host, v.category, frames)
            if not v.category or cat not in frames.get(host, pd.DataFrame()).columns:
                continue
            # El donut usa la categoria como leyenda/slices; NO se le pone
            # tambien como 'series' (romperia el binding y saldria vacio).
            # 'series' se reserva para columnas apiladas.
            series = None
            plan["charts"].append({
                "title": v.title, "visual_type": _CHART_TYPES[v.kind],
                "entity": host, "category": cat, "series": series,
                "measure": nombre,
            })
        elif v.kind == "matriz":
            resolved = _resolve_visual_measure(v.measure, entity, registry, frames)
            if resolved is None:
                continue
            host, nombre = resolved
            if not v.category or v.category not in frames.get(host, pd.DataFrame()).columns:
                continue
            plan["bottom"].append({
                "kind": "matrix", "title": v.title, "entity": host,
                "category": v.category, "measure": nombre,
            })
        elif v.kind == "tabla_detalle":
            plan["bottom"].append({
                "kind": "table", "title": v.title, "entity": entity,
                "columns": list(frames[entity].columns),
            })

    # Enriquecimiento de la pagina ejecutiva (service_level presente)
    if enrich and fact_name in frames:
        fact_cols = frames[fact_name].columns
        # Solo agregar la card de nivel de servicio (%) si la pagina NO trae
        # ya una card equivalente: no repetir el mismo KPI (cada espacio debe
        # aportar un insight distinto).
        ya_hay_ns = any(
            "nivel" in c["measure"].lower() and "servicio" in c["measure"].lower()
            for c in plan["cards"]
        )
        if SL_NS_UNIDADES_ID in registry.by_id and not ya_hay_ns:
            host, nombre = registry.by_id[SL_NS_UNIDADES_ID]
            plan["cards"].insert(0, {
                "title": "Nivel servicio unidades (%)",
                "entity": host, "measure": nombre,
            })
        if SL_PEDIDOS_COMPLETOS_ID in registry.by_id:
            host, nombre = registry.by_id[SL_PEDIDOS_COMPLETOS_ID]
            plan["cards"].append(
                {"title": "Pedidos completos (%)", "entity": host, "measure": nombre}
            )
        if SL_SIN_PEDIDO_ID in registry.by_id:
            host, nombre = registry.by_id[SL_SIN_PEDIDO_ID]
            plan["cards"].append(
                {"title": "Unidades entregadas sin pedido",
                 "entity": host, "measure": nombre}
            )
        if SL_LINEAS_ID in registry.by_id and "estado_entrega" in fact_cols:
            stacked_cat = next(
                (c["category"] for c in plan["charts"]
                 if c["entity"] == fact_name and c["category"] != "estado_entrega"),
                None,
            )
            if stacked_cat is None and profile.data_model:
                for dim in profile.data_model.dimensions:
                    resolved = _chart_category_col(fact_name, dim.key, frames)
                    if resolved in fact_cols:
                        stacked_cat = resolved
                        break
            if stacked_cat:
                host, nombre = registry.by_id[SL_LINEAS_ID]
                plan["charts"].append({
                    "title": f"Nivel de servicio por {_pretty(stacked_cat).lower()}",
                    "visual_type": "hundredPercentStackedColumnChart",
                    "entity": host, "category": stacked_cat,
                    "series": "estado_entrega", "measure": nombre,
                })

    for entity, col in _slicer_candidates(profile, plan, fact_name, frames):
        plan["slicers"].append({"entity": entity, "column": col})

    return plan


def _estado_entrega_data_points(entity: str, column: str) -> list[dict]:
    points = []
    for key, label in NIVEL_SERVICIO_LABELS.items():
        color = NIVEL_SERVICIO_COLORS[key]
        points.append({
            "properties": {"fill": {"solid": {"color": _lit(f"'{color}'")}}},
            "selector": {
                "data": [{
                    "scopeId": {
                        "Comparison": {
                            "ComparisonKind": 0,
                            "Left": {
                                "Column": {
                                    "Expression": {"SourceRef": {"Entity": entity}},
                                    "Property": column,
                                }
                            },
                            "Right": {"Literal": {"Value": f"'{label}'"}},
                        }
                    }
                }]
            },
        })
    return points


def _chart_objects(entity: str, category: str, series: str | None) -> dict:
    """Formato del chart: data labels visibles + semaforo si el eje de
    color es nivel de servicio."""
    objects: dict = {
        "labels": [{
            "properties": {
                "show": _lit("true"),
                # Sin forzar K/M: los valores pequenos se veian como '0K'.
                "labelDisplayUnits": _lit("0D"),
                "fontSize": _lit("12D"),
            }
        }]
    }
    color_col = series or category
    if color_col in ("nivel_servicio", "estado_entrega"):
        col = "estado_entrega" if color_col == "estado_entrega" else color_col
        if col == "nivel_servicio":
            objects["dataPoint"] = _nivel_servicio_data_points(entity, col)
        else:
            objects["dataPoint"] = _estado_entrega_data_points(entity, col)
    return objects


def _layout_page(
    page: PowerBIPageSpec, ordinal: int, plan: dict[str, list[dict]],
    include_logo: bool = False,
    show_back_button: bool = False,
    back_target: str = "Nivel de servicio",
) -> dict:
    """Renderiza el plan en un grid por bandas sin superposiciones:
    header / slicers / cards / charts / bottom (matriz + tabla)."""
    containers: list[dict] = []
    _append_page_header(
        containers,
        page,
        include_logo,
        show_back_button=show_back_button,
        back_target=back_target,
    )
    z = 0
    y = HEADER_H + MARGIN

    def row_width(n: int) -> int:
        return (PAGE_WIDTH - 2 * MARGIN - GAP * (n - 1)) // n

    slicers = plan["slicers"]
    if slicers:
        if len(slicers) <= 3:
            w = row_width(len(slicers))
        else:
            w = min(320, row_width(len(slicers)))
        for i, s in enumerate(slicers):
            select, ref = _column_select(s["entity"], s["column"])
            containers.append(_visual_container(
                name=_tag("visual", f"{page.name}|slicer|{i}")[:20],
                visual_type="slicer",
                entity=s["entity"],
                x=MARGIN + i * (w + GAP), y=y, width=w, height=SLICER_H, z=z,
                projections={"Values": [{"queryRef": ref}]},
                select=[select],
                title=None,
                objects={
                    "data": [{"properties": {"mode": _lit("'Basic'")}}],
                    "general": [{"properties": {"orientation": _lit("1D")}}],
                },
                column_properties={ref: {"displayName": _pretty(s["column"])}},
            ))
            z += 1
        y += SLICER_H + GAP

    cards = plan["cards"]
    if cards:
        w = row_width(len(cards))
        for i, c in enumerate(cards):
            select, ref = _measure_select(c["entity"], c["measure"])
            containers.append(_visual_container(
                name=_tag("visual", f"{page.name}|card|{i}")[:20],
                visual_type="card",
                entity=c["entity"],
                x=MARGIN + i * (w + GAP), y=y, width=w, height=CARD_H, z=z,
                projections={"Values": [{"queryRef": ref}]},
                select=[select],
                title=c["title"],
            ))
            z += 1
        y += CARD_H + GAP

    charts = plan["charts"]
    if charts:
        h = CHART_H if plan["bottom"] else max(CHART_H, PAGE_HEIGHT - y - MARGIN)
        w = row_width(len(charts))
        for i, c in enumerate(charts):
            cat_select, cat_ref = _column_select(c["entity"], c["category"])
            mea_select, mea_ref = _measure_select(c["entity"], c["measure"])
            selects = [cat_select, mea_select]
            projections: dict = {
                "Category": [{"queryRef": cat_ref}],
                "Y": [{"queryRef": mea_ref}],
            }
            column_properties = {cat_ref: {"displayName": _pretty(c["category"])}}
            if c["series"]:
                ser_select, ser_ref = _column_select(c["entity"], c["series"])
                selects.append(ser_select)
                projections["Series"] = [{"queryRef": ser_ref}]
                column_properties[ser_ref] = {"displayName": _pretty(c["series"])}
            containers.append(_visual_container(
                name=_tag("visual", f"{page.name}|chart|{i}")[:20],
                visual_type=c["visual_type"],
                entity=c["entity"],
                x=MARGIN + i * (w + GAP), y=y, width=w, height=h, z=z,
                projections=projections,
                select=selects,
                title=c["title"],
                objects=_chart_objects(c["entity"], c["category"], c["series"]),
                column_properties=column_properties,
            ))
            z += 1
        y += h + GAP

    bottom = plan["bottom"]
    if bottom:
        h = max(PAGE_HEIGHT - y - MARGIN, 180)
        w = row_width(len(bottom))
        for i, b in enumerate(bottom):
            x = MARGIN + i * (w + GAP)
            if b["kind"] == "matrix":
                cat_select, cat_ref = _column_select(b["entity"], b["category"])
                mea_select, mea_ref = _measure_select(b["entity"], b["measure"])
                containers.append(_visual_container(
                    name=_tag("visual", f"{page.name}|bottom|{i}")[:20],
                    visual_type="pivotTable",
                    entity=b["entity"],
                    x=x, y=y, width=w, height=h, z=z,
                    projections={
                        "Rows": [{"queryRef": cat_ref}],
                        "Values": [{"queryRef": mea_ref}],
                    },
                    select=[cat_select, mea_select],
                    title=b["title"],
                    column_properties={cat_ref: {"displayName": _pretty(b["category"])}},
                ))
            else:
                selects: list[dict] = []
                refs: list[dict] = []
                column_properties: dict = {}
                for col in b["columns"]:
                    select, ref = _column_select(b["entity"], col)
                    selects.append(select)
                    refs.append({"queryRef": ref})
                    column_properties[ref] = {"displayName": _pretty(col)}
                containers.append(_visual_container(
                    name=_tag("visual", f"{page.name}|bottom|{i}")[:20],
                    visual_type="tableEx",
                    entity=b["entity"],
                    x=x, y=y, width=w, height=h, z=z,
                    projections={"Values": refs},
                    select=selects,
                    title=b["title"],
                    column_properties=column_properties,
                ))
            z += 1

    return {
        "name": f"ReportSection{ordinal + 1}",
        "displayName": page.name,
        "filters": "[]",
        "ordinal": ordinal,
        "visualContainers": containers,
        "config": "{}",
        "displayOption": 1,
        "width": PAGE_WIDTH,
        "height": PAGE_HEIGHT,
    }


def _build_sections(
    spec: PowerBIReportSpec,
    profile: MatchProfile,
    registry: _MeasureRegistry,
    frames: dict[str, pd.DataFrame],
    fact_name: str,
) -> list[dict]:
    exec_idx = next(
        (i for i, p in enumerate(spec.pages)
         if any(v.kind == "card_kpi" for v in p.visuals)),
        0,
    )
    sections = []
    include_logo = LOGO_SOURCE.is_file()
    home_name = spec.pages[0].name if spec.pages else "Resumen"
    for i, page in enumerate(spec.pages):
        enrich = i == exec_idx and profile.service_level is not None
        plan = _plan_page(page, profile, registry, frames, fact_name, enrich)
        sections.append(
            _layout_page(
                page,
                i,
                plan,
                include_logo=include_logo,
                show_back_button=i > 0,
                back_target=home_name,
            )
        )
    return sections


def _write_report(
    report_dir: Path,
    sections: list[dict],
    model_dir_name: str,
    theme_variant: str = "nutriavicola",
) -> None:
    import shutil

    resources_dir = report_dir / "StaticResources" / "RegisteredResources"
    resources_dir.mkdir(parents=True, exist_ok=True)

    (resources_dir / THEME_RESOURCE_NAME).write_text(
        json.dumps(build_theme(theme_variant), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    resource_items: list[dict] = [
        {"name": THEME_RESOURCE_NAME, "path": THEME_RESOURCE_NAME, "type": 202},
    ]
    if LOGO_SOURCE.is_file():
        shutil.copy2(LOGO_SOURCE, resources_dir / LOGO_RESOURCE_NAME)
        resource_items.append(
            {"name": LOGO_RESOURCE_NAME, "path": LOGO_RESOURCE_NAME, "type": 100}
        )

    (report_dir / "definition.pbir").write_text(
        json.dumps(
            {
                "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/1.0.0/schema.json",
                "version": "1.0",
                "datasetReference": {"byPath": {"path": f"../{model_dir_name}"}},
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    report_config = {
        "version": "5.43",
        "themeCollection": {
            "customTheme": {"name": THEME_RESOURCE_NAME, "type": 1}
        },
        "activeSectionIndex": 0,
        "defaultDrillFilterOtherVisuals": True,
    }
    report_json = {
        "config": json.dumps(report_config, ensure_ascii=False),
        "layoutOptimization": 0,
        "resourcePackages": [
            {
                "resourcePackage": {
                    "disabled": False,
                    "items": resource_items,
                    "name": "RegisteredResources",
                    "type": 1,
                }
            }
        ],
        "sections": sections,
    }
    (report_dir / "report.json").write_text(
        json.dumps(report_json, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Entrada publica
# ---------------------------------------------------------------------------

def render_pbip(
    profile: MatchProfile, result: GenericMatchResult, output_dir: Path
) -> Path:
    """Genera el proyecto PBIP completo en `output_dir`.

    Retorna la ruta del archivo .pbip (el shortcut que abre el proyecto en
    Power BI Desktop).
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    name = profile.profile_id
    report_dir_name = f"{name}.Report"
    model_dir_name = f"{name}.SemanticModel"

    frames, breakdown_tables = _build_frames(profile, result)
    fact_name = profile.data_model.fact_name if profile.data_model else "FactCruce"
    ctx = _SemanticContext(profile, frames)
    registry = _build_measures(profile, frames, fact_name, ctx)

    spec = (
        profile.report.powerbi
        if profile.report is not None and profile.report.powerbi is not None
        else _default_powerbi_spec(profile)
    )
    design = spec.design
    if design is None or design.incluir_paginas_drill:
        spec = _with_drill_pages(spec, profile, frames, fact_name)
    spec = _apply_design_prefs(spec)
    # Las secciones pueden crear medidas implicitas (totales de columnas de
    # breakdowns): construirlas ANTES de escribir el semantic model.
    sections = _build_sections(spec, profile, registry, frames, fact_name)

    dim_tables = (
        {d.name for d in profile.data_model.dimensions} if profile.data_model else set()
    )
    inline_tables = {
        t for t in (dim_tables | breakdown_tables)
        if t in frames and len(frames[t]) < INLINE_MAX_ROWS
    }

    data_dir = output_dir / "data"
    _write_csvs(frames, data_dir)
    _write_semantic_model(
        output_dir / model_dir_name, profile, frames, registry,
        inline_tables, data_dir,
    )
    theme_variant = design.theme if design else "nutriavicola"
    _write_report(
        output_dir / report_dir_name, sections, model_dir_name,
        theme_variant=theme_variant,
    )

    pbip_path = output_dir / f"{name}.pbip"
    pbip_path.write_text(
        json.dumps(
            {
                "$schema": "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json",
                "version": "1.0",
                "artifacts": [{"report": {"path": report_dir_name}}],
                "settings": {"enableAutoRecovery": True},
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    (output_dir / ".gitignore").write_text(
        "**/.pbi/localSettings.json\n**/.pbi/cache.abf\n", encoding="utf-8"
    )

    (output_dir / "README.md").write_text(
        _readme_text(name, data_dir, frames, inline_tables, spec),
        encoding="utf-8",
    )
    return pbip_path


def _design_section_text(spec: PowerBIReportSpec) -> str:
    lines: list[str] = ["## Diseno propuesto por ReportDesigner", ""]
    for page in spec.pages:
        lines.append(f"### Pagina: {page.name}")
        if page.proposito:
            lines.append(f"Proposito: {page.proposito}")
        lines.append("")
        for visual in page.visuals:
            detalle = f"- [{visual.kind}] {visual.title}"
            if visual.table:
                detalle += f" (tabla: {visual.table})"
            lines.append(detalle)
            if visual.justificacion:
                lines.append(f"  Justificacion: {visual.justificacion}")
        lines.append("")
    return "\n".join(lines)


def _readme_text(
    name: str,
    data_dir: Path,
    frames: dict[str, pd.DataFrame],
    inline_tables: set[str],
    spec: PowerBIReportSpec,
) -> str:
    filas_tabla = []
    for t, df in frames.items():
        origen = "embebida en el modelo" if t in inline_tables else "CSV en data/"
        filas_tabla.append(f"- `{t}` ({len(df)} filas, {origen})")
    tablas = "\n".join(filas_tabla)
    return (
        f"# Reporte Power BI: {name}\n"
        "\n"
        "## Como abrir (2 pasos)\n"
        "\n"
        f"1. Doble clic al archivo `{name}.pbip` (se abre Power BI Desktop).\n"
        "2. Clic en el boton **Actualizar** (Refresh) de la cinta **Inicio**.\n"
        "\n"
        "Listo. Los visuales se llenan solos con los datos del proyecto.\n"
        "No hay que configurar nada mas.\n"
        "\n"
        "### Si los datos no cargan (error de ruta)\n"
        "\n"
        "Solo puede pasar si movio o renombro esta carpeta despues de\n"
        "generarla. Se corrige en 3 clics:\n"
        "\n"
        "1. Menu **Inicio > Transformar datos > Editar parametros**\n"
        "   (Home > Transform data > Edit parameters).\n"
        "2. En el campo **RutaDatos** pegue la ruta completa de la carpeta\n"
        f"   `data/` de este proyecto. Ahora mismo es:\n\n       {data_dir.resolve()}\n"
        "3. **Aceptar** y luego **Actualizar**.\n"
        "\n"
        "Las tablas de dimensiones y desgloses van embebidas dentro del\n"
        "modelo (no dependen de la ruta); solo las tablas grandes se leen\n"
        "de los CSV de `data/`.\n"
        "\n"
        "## Datos del modelo\n"
        "\n"
        f"{tablas}\n"
        "\n"
        "## Marca\n"
        "\n"
        "El reporte usa el theme corporativo NutriAvicola (navy #0F2E4C\n"
        "dominante, naranja #E87722 como acento, semaforo verde/ambar/rojo\n"
        "reservado al nivel de servicio) registrado en\n"
        f"`{name}.Report/StaticResources/RegisteredResources/{THEME_RESOURCE_NAME}`.\n"
        "\n"
        f"{_design_section_text(spec)}"
    )
