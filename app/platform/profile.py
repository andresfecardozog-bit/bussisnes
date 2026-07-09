"""MatchProfile: contrato declarativo de un proceso de cruce.

Este es el corazon de la plataforma. Un MatchProfile describe TODO lo que
se necesita para ejecutar un cruce sin escribir codigo:

- de donde vienen los datos (sources con su loader spec),
- que transformaciones previas aplicar (filtros, group-by para ajustar
  el grano, renombres),
- como cruzar (keys izquierda/derecha con normalizadores),
- que calcular (columnas computadas y KPIs declarativos, sin eval libre),
- que reportar (spec de Excel y Power BI, consumido en Fase 4).

Los agentes LLM (Fase 2) PROPONEN instancias de este contrato; un humano
las aprueba; el motor deterministico (engine.py) las ejecuta. El LLM nunca
ejecuta nada: si una operacion no existe en estos schemas, no se puede
expresar y por tanto no se puede correr.

Los valores de parametros runtime (ej. fecha de produccion) se referencian
con la sintaxis "$nombre_parametro" y se resuelven al ejecutar.
"""
from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, field_validator, model_validator

SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Normalizadores de keys (whitelist cerrada; el LLM solo puede elegir de aqui)
# ---------------------------------------------------------------------------

class KeyNormalizer(str, Enum):
    STRIP = "strip"
    UPPER = "upper"
    LSTRIP_ZEROS = "lstrip_zeros"
    DIGITS_ONLY = "digits_only"
    TO_INT = "to_int"
    TO_STR = "to_str"


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

class ColumnDtype(str, Enum):
    STR = "str"
    INT = "int"
    FLOAT = "float"
    DATE = "date"
    # Limpieza numerica robusta (comas de miles, moneda, errores Excel).
    FLOAT_CLEAN = "float_clean"


class ColumnSpec(BaseModel):
    """Una columna a extraer de la fuente.

    `source` es el nombre del header (si la hoja tiene headers) o la
    posicion 1-based de la columna (si `header_row` es None, caso SAP sin
    encabezados).
    """

    name: str = Field(min_length=1, description="Nombre destino (snake_case)")
    source: str | int
    dtype: ColumnDtype = ColumnDtype.STR

    @field_validator("source")
    @classmethod
    def _source_no_vacio(cls, v: str | int) -> str | int:
        if isinstance(v, str) and not v.strip():
            raise ValueError("source no puede ser cadena vacia")
        if isinstance(v, int) and v < 1:
            raise ValueError("source posicional es 1-based (>= 1)")
        return v
    date_format: str | None = Field(
        default=None, description="strptime format si dtype=date y viene como texto"
    )
    required: bool = True


class TabularLoaderSpec(BaseModel):
    """Loader generico para fuentes tabulares (xlsx/xls/csv).

    Maneja las trampas documentadas en AGENTS.md:
    - `sheet=None` -> auto-deteccion: elige la hoja cuyo header row contiene
      todas las columnas requeridas (caso CEN con Hoja1/Hoja2/Hoja3 pivote).
    - `header_row=None` -> sin encabezados, columnas posicionales (caso SAP).
    - Extension mentirosa: el loader detecta el formato real por firma
      binaria (PK zip = xlsx), no por extension.
    - Filas fantasma: se recortan las filas finales completamente vacias.
    """

    type: Literal["tabular"] = "tabular"
    sheet: str | int | None = Field(
        default=None,
        description="Nombre de hoja, indice 0-based, o None para auto-detectar",
    )
    header_row: int | None = Field(
        default=1,
        ge=1,
        description="Fila 1-based del header; None = sin header (posicional)",
    )
    columns: list[ColumnSpec] = Field(min_length=1)
    drop_rows_where_null: list[str] = Field(
        default_factory=list,
        description="Filas con null en estas columnas destino se descartan y se contabilizan",
    )

    @model_validator(mode="after")
    def _source_type_coherente_con_header(self) -> "TabularLoaderSpec":
        if self.header_row is None:
            bad = [c.name for c in self.columns if not isinstance(c.source, int)]
            if bad:
                raise ValueError(
                    f"Sin header_row las columnas deben ser posicionales (int): {bad}"
                )
        else:
            bad = [c.name for c in self.columns if isinstance(c.source, int)]
            if bad:
                raise ValueError(
                    f"Con header_row las columnas deben referenciarse por el "
                    f"nombre exacto del header (str), no por posicion: {bad}"
                )
        return self

    @model_validator(mode="after")
    def _unique_target_names(self) -> "TabularLoaderSpec":
        names = [c.name for c in self.columns]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f"Nombres destino duplicados en columns: {sorted(dupes)}")
        return self


class RegisteredLoaderSpec(BaseModel):
    """Loader custom registrado en codigo (registry en loader.py).

    Para fuentes que un spec tabular no puede expresar (ej. la hoja RESUMEN
    del PRE CORTE con merged cells + catalogo SKU). El caso PRE CORTE usa
    esto para reusar el parser legado ya validado.
    """

    type: Literal["registered"] = "registered"
    name: str = Field(min_length=1)


LoaderSpec = Annotated[
    Union[TabularLoaderSpec, RegisteredLoaderSpec],
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Transformaciones previas al cruce (whitelist cerrada)
# ---------------------------------------------------------------------------

class FilterEquals(BaseModel):
    """Mantiene solo las filas donde `column == value`.

    `value` puede ser un literal o una referencia "$param" a un parametro
    runtime del profile.
    """

    op: Literal["filter_equals"] = "filter_equals"
    column: str
    value: str | int | float


class FilterNotEquals(BaseModel):
    """Descarta las filas donde `column == value` (contabilizadas como
    descarte). Caso de uso: excluir DEVOLUCIONES de las entregas."""

    op: Literal["filter_not_equals"] = "filter_not_equals"
    column: str
    value: str | int | float


class FilterRegexMatch(BaseModel):
    """Mantiene (keep='matched') o descarta (keep='not_matched') las filas
    cuya `column` matchea el `pattern` (regex ancladas al inicio).

    Caso de uso real: la col de pedido del SAP mezcla ordenes CEN
    ('004-0018849') con placeholders escritos a mano ('*', 'SIN DC') y
    pedidos de otros origenes; keep='matched' con '^\\d{3}-\\d+' deja solo
    las ordenes CEN."""

    op: Literal["filter_regex_match"] = "filter_regex_match"
    column: str
    pattern: str
    keep: Literal["matched", "not_matched"] = "matched"

    @field_validator("pattern")
    @classmethod
    def _pattern_compila(cls, v: str) -> str:
        import re as _re

        try:
            _re.compile(v)
        except _re.error as exc:
            raise ValueError(f"regex invalida: {exc}") from exc
        return v


class AggFn(str, Enum):
    SUM = "sum"
    COUNT = "count"
    FIRST = "first"
    MEAN = "mean"
    MAX = "max"
    MIN = "min"


class Aggregation(BaseModel):
    target: str
    source: str
    fn: AggFn


class GroupByAggregate(BaseModel):
    """Agrupa por `by` y agrega. ESTA es la transformacion que ajusta el
    grano de una tabla (ej. lineas de producto -> orden completa). Los
    agentes la proponen despues de confirmar el grano con el usuario."""

    op: Literal["group_by_aggregate"] = "group_by_aggregate"
    by: list[str] = Field(min_length=1)
    aggregations: list[Aggregation] = Field(min_length=1)


class SelectRename(BaseModel):
    """Selecciona y renombra columnas: {origen: destino}."""

    op: Literal["select_rename"] = "select_rename"
    mapping: dict[str, str] = Field(min_length=1)


class Unpivot(BaseModel):
    """Convierte un layout matriz (una columna por categoria) a formato
    largo, tipo pd.melt. Caso de uso: hojas donde cada columna es un
    formato/presentacion y las filas son referencias (estilo RESUMEN)."""

    op: Literal["unpivot"] = "unpivot"
    id_vars: list[str] = Field(min_length=1)
    value_vars: list[str] | None = Field(
        default=None, description="None = todas las columnas fuera de id_vars"
    )
    var_name: str = "categoria"
    value_name: str = "valor"
    drop_null_values: bool = True


Transform = Annotated[
    Union[
        FilterEquals,
        FilterNotEquals,
        FilterRegexMatch,
        GroupByAggregate,
        SelectRename,
        Unpivot,
    ],
    Field(discriminator="op"),
]


# ---------------------------------------------------------------------------
# Fuentes
# ---------------------------------------------------------------------------

class SourceSpec(BaseModel):
    role: str = Field(
        min_length=1,
        description="Rol de negocio legible: 'plan', 'real', 'ordenes_cen', ...",
    )
    label: str = Field(min_length=1, description="Nombre legible para reportes")
    loader: LoaderSpec
    transforms: list[Transform] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Join
# ---------------------------------------------------------------------------

class JoinKey(BaseModel):
    left: str
    right: str
    normalizers: list[KeyNormalizer] = Field(default_factory=list)


class JoinSpec(BaseModel):
    keys: list[JoinKey] = Field(min_length=1)
    # Solo outer: garantiza que ninguna fila se pierde (compromiso duro).
    type: Literal["outer"] = "outer"


# ---------------------------------------------------------------------------
# Columnas computadas y KPIs (operaciones declarativas, sin eval)
# ---------------------------------------------------------------------------

class ComputedColumn(BaseModel):
    """Columna derivada sobre las filas matched.

    Operaciones soportadas:
    - subtract: left - right
    - ratio_pct: left / right * 100 (NaN si right <= 0)
    """

    name: str
    op: Literal["subtract", "ratio_pct"]
    left: str
    right: str
    round: int | None = None


class SemaforoSpec(BaseModel):
    """Rangos discretos verde/amarillo/rojo para un KPI porcentual."""

    verde_min: float
    verde_max: float | None = None
    amarillo_min: float
    amarillo_max: float | None = None

    @model_validator(mode="after")
    def _rangos_coherentes(self) -> "SemaforoSpec":
        if self.amarillo_min > self.verde_min:
            raise ValueError("amarillo_min debe ser <= verde_min")
        return self


class KpiSpec(BaseModel):
    """KPI agregado del cruce.

    Operaciones soportadas:
    - ratio_pct_of_sums: sum(numerator) / sum(denominator) * 100
    - sum: sum(numerator)
    - count: numero de filas matched
    """

    id: str = Field(min_length=1)
    label: str
    op: Literal["ratio_pct_of_sums", "sum", "count"]
    numerator: str | None = None
    denominator: str | None = None
    semaforo: SemaforoSpec | None = None

    @model_validator(mode="after")
    def _campos_por_op(self) -> "KpiSpec":
        if self.op == "ratio_pct_of_sums" and not (self.numerator and self.denominator):
            raise ValueError("ratio_pct_of_sums requiere numerator y denominator")
        if self.op == "sum" and not self.numerator:
            raise ValueError("sum requiere numerator")
        return self


# ---------------------------------------------------------------------------
# Nivel de servicio: clasificacion declarativa de cada linea del cruce
# ---------------------------------------------------------------------------

class ServiceLevelSpec(BaseModel):
    """Clasifica cada linea (y por agregacion cada pedido) del outer join:

    - completo:      real >= plan (con tolerancia_pct)
    - parcial:       0 < real < plan
    - no_entregado:  linea solo_left (pedido sin contraparte) o real <= 0
    - sin_pedido:    linea solo_right (entrega sin pedido)

    El motor produce la columna `nivel_servicio` en matched y el bloque
    `service_level` en los KPIs con conteos, unidades y porcentajes de
    cada clase, tanto a nivel linea como a nivel pedido si `pedido_key`
    esta definido.
    """

    plan_column: str
    real_column: str
    pedido_key: str | None = Field(
        default=None,
        description="Columna que identifica el pedido para agregar el nivel de servicio a nivel orden",
    )
    tolerancia_pct: float = Field(
        default=0.0, ge=0.0,
        description="Margen: real >= plan * (1 - tolerancia/100) cuenta como completo",
    )


# ---------------------------------------------------------------------------
# Breakdowns: desgloses dimensionales declarativos
# ---------------------------------------------------------------------------

class BreakdownMetric(BaseModel):
    """Metrica agregada dentro de un breakdown."""

    id: str = Field(min_length=1)
    op: Literal["sum", "count", "ratio_pct_of_sums"]
    column: str | None = None
    numerator: str | None = None
    denominator: str | None = None

    @model_validator(mode="after")
    def _campos_por_op(self) -> "BreakdownMetric":
        if self.op == "sum" and not self.column:
            raise ValueError("sum requiere column")
        if self.op == "ratio_pct_of_sums" and not (self.numerator and self.denominator):
            raise ValueError("ratio_pct_of_sums requiere numerator y denominator")
        return self


class BreakdownSpec(BaseModel):
    """Desglose por una o mas dimensiones (material, distrito, cliente,
    cliente+material...). El motor produce una tabla por breakdown que los
    renderers convierten en hoja Excel / visual Power BI.

    `universe` define sobre que particion se agrupa:
    - matched:      solo lineas cruzadas
    - left_full:    matched + solo_left (todo lo pedido)
    - right_source: el DataFrame de la fuente derecha POST-load pero
      PRE-transforms de agregacion (para desglosar p.ej. devoluciones por
      motivo, que desaparecerian tras el group_by del join)
    """

    id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    label: str
    dimensions: list[str] = Field(min_length=1)
    metrics: list[BreakdownMetric] = Field(min_length=1)
    universe: Literal["matched", "left_full", "right_source"] = "matched"
    filter_equals: dict[str, str | int | float] | None = Field(
        default=None,
        description="Filtro previo opcional, ej {'tipo_operacion': 'DEVOLUCIONES'}",
    )
    filter_not_equals: dict[str, str | int | float] | None = Field(
        default=None,
        description=(
            "Descarta filas donde columna == valor, ej "
            "{'tipo_operacion': 'DEVOLUCIONES'} para rechazos en ventas "
            "que no son devoluciones."
        ),
    )
    require_non_null: list[str] = Field(
        default_factory=list,
        description=(
            "Mantiene solo filas con valor no nulo/no vacio en estas columnas "
            "(ej. ['motivo_devolucion'] para quedarse solo con lineas que "
            "tienen un motivo de rechazo reportado)."
        ),
    )
    top_n: int | None = Field(default=None, ge=1)
    sort_by_metric: str | None = None


# ---------------------------------------------------------------------------
# Parametros runtime
# ---------------------------------------------------------------------------

class ParameterSpec(BaseModel):
    name: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    type: Literal["date", "str", "int", "float"]
    description: str = ""
    required: bool = True


# ---------------------------------------------------------------------------
# Modelo de datos exportable (la "base de datos en Excel")
# ---------------------------------------------------------------------------

class DimensionSpec(BaseModel):
    """Una tabla de dimension del modelo estrella ligero.

    El motor extrae los valores distintos de `key` (+ `attributes`) del
    fact, les asigna un id entero estable (orden alfabetico de la key) y
    deja en el fact la columna `<name_snake>_id` como foreign key.
    """

    name: str = Field(min_length=1, description="Ej: DimCliente, DimMaterial")
    key: str = Field(min_length=1, description="Columna del fact que identifica la dimension")
    attributes: list[str] = Field(
        default_factory=list,
        description="Columnas descriptivas adicionales (ej. descripcion del material)",
    )


class DataModelSpec(BaseModel):
    """La 'base de datos en Excel': tablas funcionales listas para que el
    equipo de BI las consuma via Power Query / import en Power BI.

    El motor produce:
    - Tabla de hechos (`fact_name`): una fila por linea del outer join
      completo (matched + solo_left + solo_right), con `fact_id`, columna
      `estado_cruce` (cruzado | solo_<rol_left> | solo_<rol_right>),
      `nivel_servicio` si aplica, computed columns, y FKs a dimensiones.
    - Una tabla por dimension con `<dim>_id` + key + atributos.

    Los datos van completos y planos (no cross-tab): cada tabla se escribe
    como Excel Table nativa con nombre estable para que Power BI la importe
    por nombre.
    """

    fact_name: str = Field(default="FactCruce", min_length=1)
    dimensions: list[DimensionSpec] = Field(default_factory=list)
    include_unmatched: bool = Field(
        default=True,
        description="Si False el fact solo lleva filas cruzadas (no recomendado)",
    )

    @model_validator(mode="after")
    def _nombres_unicos(self) -> "DataModelSpec":
        nombres = [self.fact_name] + [d.name for d in self.dimensions]
        dupes = {n for n in nombres if nombres.count(n) > 1}
        if dupes:
            raise ValueError(f"Nombres de tabla duplicados en data_model: {sorted(dupes)}")
        return self


# ---------------------------------------------------------------------------
# Reporte (spec minimo; Fase 4 lo consume y extiende)
# ---------------------------------------------------------------------------

class ExcelSheetSpec(BaseModel):
    name: str
    kind: Literal["portada", "kpi_resumen", "tabla", "breakdown"]
    source: Literal["matched", "no_cruzados", "solo_left", "solo_right", "kpis"] | None = None
    breakdown_id: str | None = Field(
        default=None, description="Requerido si kind=breakdown: id del BreakdownSpec"
    )
    group_by: str | None = None
    columns: list[str] | None = None

    @model_validator(mode="after")
    def _breakdown_requiere_id(self) -> "ExcelSheetSpec":
        if self.kind == "breakdown" and not self.breakdown_id:
            raise ValueError("kind=breakdown requiere breakdown_id")
        return self


class ExcelReportSpec(BaseModel):
    filename_prefix: str
    sheets: list[ExcelSheetSpec] = Field(min_length=1)


class PowerBIVisualSpec(BaseModel):
    kind: Literal[
        "card_kpi",
        "tendencia",
        "barras_categoria",
        "tabla_detalle",
        "donut",
        "matriz",
        "funnel",
        "area",
        "columnas_apiladas",
    ]
    title: str
    measure: str | None = None
    category: str | None = None
    table: str | None = Field(
        default=None,
        description="Tabla del data_model sobre la que se construye el visual",
    )
    justificacion: str = Field(
        default="",
        description="Por que este visual aporta (lo escribe el ReportDesigner para el humano)",
    )


class PowerBIMeasureSpec(BaseModel):
    """Medida DAX declarativa del modelo Power BI (whitelist, sin DAX libre)."""

    id: str = Field(min_length=1)
    label: str
    op: Literal["sum", "count", "ratio_pct_of_sums", "distinct_count"]
    table: str = "FactCruce"
    column: str | None = None
    numerator: str | None = None
    denominator: str | None = None
    format: Literal["entero", "decimal", "porcentaje", "moneda"] = "entero"


class PowerBIPageSpec(BaseModel):
    name: str
    proposito: str = Field(
        default="", description="Que pregunta de negocio responde esta pagina"
    )
    visuals: list[PowerBIVisualSpec] = Field(min_length=1)


class PowerBIDesignPrefs(BaseModel):
    """Preferencias de diseno del tablero elegidas/confirmadas por el usuario.

    El ReportDesigner las propone, el humano las confirma o edita, y el
    renderer las respeta de forma deterministica.
    """

    theme: Literal["nutriavicola", "nutriavicola_claro", "nutriavicola_oscuro"] = (
        "nutriavicola"
    )
    max_paginas: int = Field(default=6, ge=1, le=10)
    max_charts_por_pagina: int = Field(default=4, ge=1, le=8)
    tipos_preferidos: list[
        Literal[
            "card_kpi", "tendencia", "barras_categoria", "donut",
            "matriz", "tabla_detalle", "funnel", "area", "columnas_apiladas",
        ]
    ] = Field(default_factory=list)
    incluir_paginas_drill: bool = True
    notas_usuario: str = Field(
        default="",
        description="Respuesta libre del usuario sobre el diseno (se respeta al re-proponer)",
    )


class PowerBIReportSpec(BaseModel):
    theme: str = "nutriavicola"
    design: PowerBIDesignPrefs | None = None
    measures: list[PowerBIMeasureSpec] = Field(default_factory=list)
    pages: list[PowerBIPageSpec] = Field(min_length=1)


class ReportSpec(BaseModel):
    excel: ExcelReportSpec | None = None
    powerbi: PowerBIReportSpec | None = None


# ---------------------------------------------------------------------------
# El contrato completo
# ---------------------------------------------------------------------------

class MatchProfile(BaseModel):
    profile_id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    version: int = Field(ge=1)
    schema_version: int = SCHEMA_VERSION
    descripcion: str = ""
    parameters: list[ParameterSpec] = Field(default_factory=list)
    left: SourceSpec
    right: SourceSpec
    join: JoinSpec
    computed: list[ComputedColumn] = Field(default_factory=list)
    kpis: list[KpiSpec] = Field(min_length=1)
    service_level: ServiceLevelSpec | None = None
    breakdowns: list[BreakdownSpec] = Field(default_factory=list)
    data_model: DataModelSpec | None = None
    unmatched_motivo_left: str = "sin contraparte en la fuente derecha"
    unmatched_motivo_right: str = "sin contraparte en la fuente izquierda"
    report: ReportSpec | None = None

    @field_validator("kpis")
    @classmethod
    def _kpi_ids_unicos(cls, v: list[KpiSpec]) -> list[KpiSpec]:
        ids = [k.id for k in v]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"KPI ids duplicados: {sorted(dupes)}")
        return v

    @model_validator(mode="after")
    def _breakdown_refs_validas(self) -> "MatchProfile":
        bd_ids = {b.id for b in self.breakdowns}
        dupes = [b.id for b in self.breakdowns if [x.id for x in self.breakdowns].count(b.id) > 1]
        if dupes:
            raise ValueError(f"Breakdown ids duplicados: {sorted(set(dupes))}")
        if self.report and self.report.excel:
            for sheet in self.report.excel.sheets:
                if sheet.kind == "breakdown" and sheet.breakdown_id not in bd_ids:
                    raise ValueError(
                        f"Hoja '{sheet.name}' referencia breakdown inexistente: "
                        f"'{sheet.breakdown_id}'"
                    )
        return self

    @model_validator(mode="after")
    def _param_refs_declarados(self) -> "MatchProfile":
        declared = {p.name for p in self.parameters}
        for side in (self.left, self.right):
            for t in side.transforms:
                if isinstance(t, (FilterEquals, FilterNotEquals)) and isinstance(t.value, str):
                    if t.value.startswith("$"):
                        ref = t.value[1:]
                        if ref not in declared:
                            raise ValueError(
                                f"Transform referencia parametro no declarado: ${ref}"
                            )
        return self

    def to_json(self, indent: int = 2) -> str:
        # Sin exclude_none: header_row=None (loader posicional) es
        # semanticamente distinto de omitirlo (default 1). Excluir los
        # None corrompia el profile en el round-trip de persistencia.
        return self.model_dump_json(indent=indent)

    @classmethod
    def from_json(cls, raw: str) -> "MatchProfile":
        return cls.model_validate_json(raw)
