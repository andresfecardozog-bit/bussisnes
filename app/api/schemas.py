"""Modelos Pydantic para requests y responses de la API.

Convencion: `*Response` son datos que la API envia; `*Request` son datos
que el cliente envia (excluyendo los uploads multipart que se manejan con
UploadFile directamente).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------- File uploads ----------

class FileUploadResponse(BaseModel):
    carga_id: int
    tipo: Literal["pre_corte", "flash"]
    filename: str
    hash_sha256: str
    num_filas_original: int
    num_filas_procesadas: int
    fecha_archivo: date | None = None
    fecha_produccion: date | None = None
    ya_existia: bool = Field(
        description="True si el hash ya estaba cargado (idempotencia)"
    )
    notificacion_presente: bool | None = Field(
        default=None,
        description="Solo aplica a pre_corte: True si el .xlsx incluia la hoja NOTIFICACION",
    )
    num_filas_sin_sap: int | None = Field(
        default=None,
        description="Filas del RESUMEN que no resolvieron a SAP (pendientes de mapeo manual)",
    )
    catalog_coverage_pct: float | None = Field(
        default=None,
        description="Porcentaje de filas del RESUMEN resueltas a SAP",
    )
    pair_learn_stats: dict[str, int] | None = Field(
        default=None,
        description="Estadisticas de aprendizaje RESUMEN<->NOTIFICACION al cargar",
    )
    dias_saltados: int | None = Field(
        default=None,
        description=(
            "Solo aplica a pre_corte: cantidad de dias que se saltaron desde "
            "fecha_archivo + 1 hasta encontrar dia habil (domingo/festivo)."
        ),
    )
    motivos_saltados: list[str] | None = Field(
        default=None,
        description=(
            "Detalle de cada dia saltado, formato 'YYYY-MM-DD: motivo'. "
            "Se muestra en el preview para dar transparencia al usuario."
        ),
    )


# ---------- Catalogo SKU ----------

class CatalogEntry(BaseModel):
    id: int
    referencia: str
    tipo: str
    formato: str
    unidades_por_empaque: int
    material_sap: int
    nombre_notificacion: str | None = None
    fuente: str
    primera_vez_visto: str | None = None
    ultima_vez_visto: str | None = None
    veces_visto: int = 1


class CatalogListResponse(BaseModel):
    total: int
    por_fuente: dict[str, int]
    entradas: list[CatalogEntry]


class ManualMappingRequest(BaseModel):
    referencia: str
    tipo: str
    formato: str
    unidades_por_empaque: int
    material_sap: int
    nombre_notificacion: str | None = None


class ManualMappingResponse(BaseModel):
    id: int
    es_nueva: bool
    material_sap: int


class BatchUploadResponse(BaseModel):
    pre_cortes: list[FileUploadResponse]
    errores: list[dict[str, str]] = Field(default_factory=list)


# ---------- Runs ----------

class StartBatchRequest(BaseModel):
    pre_corte_carga_ids: list[int] = Field(..., min_length=1)
    flash_carga_id: int


class RunSummary(BaseModel):
    id: str
    parent_run_id: str | None
    status: str
    current_step: str | None
    fecha_produccion: date | None
    pre_corte_carga_id: int | None
    flash_carga_id: int | None
    started_at: str | None
    ended_at: str | None
    summary: dict[str, Any] | None = None
    notes: str | None = None


class StartBatchResponse(BaseModel):
    master_run_id: str
    sub_run_ids: list[str]
    total_sub_runs: int


class ApproveRequest(BaseModel):
    aprobado_por: str | None = None
    comentarios: str | None = None


class RejectRequest(BaseModel):
    motivo: str
    rechazado_por: str | None = None


# ---------- Pipeline steps ----------

class ExtractDateResponse(BaseModel):
    fecha_archivo: date
    fecha_produccion: date
    filename: str


class LoadStepResponse(BaseModel):
    tipo: Literal["pre_corte", "flash"]
    filename: str
    hash_sha256: str
    num_filas_original: int
    num_filas_procesadas: int
    columnas: list[str]


class ValidationItem(BaseModel):
    nombre: str
    ok: bool
    detalle: dict[str, Any]


class ValidateResponse(BaseModel):
    validaciones: list[ValidationItem]
    all_ok: bool


class AggregateResponse(BaseModel):
    fecha_produccion: date
    materiales_flash: int
    cantidad_total: float
    facturado_total: float
    num_facturas_total: int


class MatchStepResponse(BaseModel):
    fecha_produccion: date
    matched: int
    solo_pre_corte: int
    solo_flash: int
    no_cruzados: int
    top_5_desviaciones: list[dict[str, Any]]


class KpiCard(BaseModel):
    nombre: str
    valor: float | int | str
    formato: str
    descripcion: str


class KpisPreviewResponse(BaseModel):
    fecha_produccion: date
    cards: list[KpiCard]
    tabla_ejecutiva: list[dict[str, Any]]


class PersistResponse(BaseModel):
    pre_carga_id: int
    flash_carga_id: int
    cruce_filas_insertadas: int
    no_cruzados_filas_insertadas: int
    ya_existia: bool


# ---------- Health ----------

class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str
    now: datetime


# ---------- Calendario laboral (Fase 6.6) ----------

class DiaNoLaboral(BaseModel):
    fecha: str
    motivo: str
    es_domingo: bool


class CalendarioAnoResponse(BaseModel):
    year: int
    festivos: list[DiaNoLaboral]
    cobertura_desde: str
    cobertura_hasta: str


# ---------- Batches (Fase 7A) ----------

class CreateBatchRequest(BaseModel):
    nombre: str | None = None
    notas: str | None = None


class PatchBatchRequest(BaseModel):
    nombre: str | None = None
    notas: str | None = None


class FlashInfo(BaseModel):
    flash_carga_id: int
    filename: str
    periodo_year: int | None = None
    periodo_month: int | None = None
    num_filas_original: int | None = None


class BatchSummary(BaseModel):
    id: str
    status: str
    nombre: str | None = None
    notas: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    num_pre_cortes: int
    flash: FlashInfo | None = None
    output_dir: str | None = None


class BatchPreCorteItem(BaseModel):
    carga_id: int
    filename: str
    fecha_archivo: date | None = None
    fecha_produccion: date | None = None
    hash_sha256: str
    num_filas_original: int
    num_filas_procesadas: int
    cargado_en: str | None = None
    agregado_en: str | None = None


class BatchDetailResponse(BatchSummary):
    pre_cortes: list[BatchPreCorteItem] = Field(default_factory=list)


class AttachFlashRequest(BaseModel):
    flash_carga_id: int
    year: int = Field(..., ge=2000, le=2100)
    month: int = Field(..., ge=1, le=12)


class ZipUploadIgnorado(BaseModel):
    filename: str
    motivo: str


class ZipUploadResponse(BaseModel):
    procesados: list[FileUploadResponse]
    ignorados: list[ZipUploadIgnorado]


class DiaColision(BaseModel):
    fecha: date
    pre_corte_carga_ids: list[int]


class PreviewDia(BaseModel):
    fecha_produccion: date
    materiales_matched: int
    materiales_solo_pre: int
    materiales_solo_flash: int
    plan_total: int
    real_total: int
    delta_total: int
    cumplimiento_pct: float


class PreviewResponse(BaseModel):
    batch_id: str
    dias: list[PreviewDia]
    colisiones: list[DiaColision]
    fechas_no_laborales_saltadas: list[dict[str, Any]] = Field(default_factory=list)
    flash_periodo_ok: bool
    flash_periodo_mensajes: list[str] = Field(default_factory=list)
    listo_para_confirmar: bool


class GenerateResponse(BaseModel):
    batch_id: str
    consolidado_filename: str
    dailies_filenames: list[str]
    zip_filename: str
    fechas_procesadas: list[date]
    fechas_sin_datos_en_rango: list[date]


class DownloadItem(BaseModel):
    filename: str
    size_bytes: int
    kind: Literal["consolidado", "daily", "zip"]


class DownloadsResponse(BaseModel):
    batch_id: str
    output_dir: str
    items: list[DownloadItem]
