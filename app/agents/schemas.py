"""Outputs tipados de los agentes.

Cada agente DEBE producir una instancia valida de su schema (Pydantic AI
fuerza el JSON contra el modelo). Los fragmentos del MatchProfile reusan
los schemas del contrato (app/platform/profile.py): si el agente propone
algo fuera de la whitelist, la validacion lo rechaza y Pydantic AI
re-pregunta al modelo automaticamente.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.platform.profile import (
    BreakdownSpec,
    ComputedColumn,
    DataModelSpec,
    JoinSpec,
    KpiSpec,
    ParameterSpec,
    ReportSpec,
    ServiceLevelSpec,
    SourceSpec,
)


class OpenQuestion(BaseModel):
    """Duda que el agente anota mientras analiza, como lo haria un analista
    humano. Ejemplo canonico: 'encontre numeros de orden repetidos en
    varias filas; cada fila es un producto de la orden y debo agrupar por
    numero de orden para reconstruir la orden completa?'"""

    agente: str = Field(description="Nombre del agente que pregunta")
    sobre: str = Field(
        description="Archivo/hoja/columna sobre la que trata la pregunta"
    )
    pregunta: str = Field(description="La pregunta, formulada para un humano no tecnico")
    hipotesis: str = Field(
        description="Lo que el agente asume si nadie responde (su mejor interpretacion)"
    )
    impacto: str = Field(
        description="Que se calcula mal o que riesgo hay si la hipotesis es incorrecta"
    )
    bloqueante: bool = Field(
        description="True si NO se debe ejecutar el cruce sin respuesta humana"
    )


class GranoAssessment(BaseModel):
    """Evaluacion obligatoria del grano de una fuente (que significa una fila)."""

    grano_descripcion: str = Field(
        description="Ej: 'cada fila es una linea de producto de una orden de compra'"
    )
    key_candidata: str = Field(description="Columna(s) candidatas a key de cruce")
    keys_se_repiten: bool
    requiere_agrupacion: bool = Field(
        description="True si hay que agrupar antes del join para ajustar el grano"
    )
    confianza: float = Field(ge=0.0, le=1.0)


class SchemaScoutOutput(BaseModel):
    """Ingeniero de datos: estructura y calidad de UNA fuente."""

    hoja_recomendada: str = Field(
        description="Nombre de la hoja con los datos reales (no pivotes/resumenes)"
    )
    header_row: int | None = Field(
        description="Fila 1-based del header, o null si no hay encabezados"
    )
    grano: GranoAssessment
    columnas_relevantes: list[str] = Field(
        description="Headers o posiciones (como texto) que parecen relevantes al cruce"
    )
    anomalias: list[str] = Field(
        default_factory=list,
        description="Mojibake, filas fantasma, columnas vacias, valores mezclados...",
    )
    resumen: str = Field(description="2-4 frases legibles para el humano")
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    confianza: float = Field(ge=0.0, le=1.0)


class MappingProposal(BaseModel):
    """Ingeniero ETL: fragmento de MatchProfile con sources + join."""

    left: SourceSpec
    right: SourceSpec
    join: JoinSpec
    parameters: list[ParameterSpec] = Field(
        default_factory=list,
        description="Parametros runtime referenciados como $nombre en los transforms",
    )
    justificacion: str = Field(
        description="Por que estas keys y transforms, legible para no tecnicos"
    )
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    confianza: float = Field(ge=0.0, le=1.0)


class KpiProposal(BaseModel):
    """Analista de datos: computed columns + KPIs + nivel de servicio +
    desgloses dimensionales."""

    computed: list[ComputedColumn] = Field(default_factory=list)
    kpis: list[KpiSpec] = Field(min_length=1)
    service_level: ServiceLevelSpec | None = None
    breakdowns: list[BreakdownSpec] = Field(default_factory=list)
    justificacion: str
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    confianza: float = Field(ge=0.0, le=1.0)


class ReportProposal(BaseModel):
    """Analista BI: modelo de datos exportable + spec del reporte Excel +
    Power BI (tablas, medidas, paginas y visuales con justificacion).
    Todo queda sujeto a aprobacion humana como parte del MatchProfile."""

    data_model: DataModelSpec | None = None
    report: ReportSpec
    justificacion: str
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    confianza: float = Field(ge=0.0, le=1.0)


class ProposalStatus(BaseModel):
    """Estado consolidado de una propuesta de profile."""

    profile_id: str
    listo_para_aprobar: bool
    preguntas_abiertas: int
    preguntas_bloqueantes: int
    confianza_global: float


class ChatMessage(BaseModel):
    role: Literal["usuario", "agente", "sistema"]
    agente: str | None = None
    contenido: str
    question_id: int | None = None
