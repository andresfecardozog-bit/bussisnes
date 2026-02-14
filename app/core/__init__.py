"""Nucleo de procesamiento: funciones puras invocables por FastAPI, tests y CLI."""

from app.core.date_extractor import (
    extract_file_date,
    extract_production_date,
)
from app.core.loaders import (
    hash_file,
    load_flash,
    load_notificacion,
    load_pre_corte,
)
from app.core.resumen_parser import load_resumen
from app.core.sku_catalog import (
    attach_sap_to_resumen,
    catalog_stats,
    import_from_homologacion,
    list_catalog,
    resolve_sap,
    update_catalog_from_pair,
)
from app.core.aggregator import aggregate_flash
from app.core.matcher import (
    MatchResult,
    match_by_material,
    run_batch_pipeline,
    run_full_pipeline,
)
from app.core.validators import (
    Validation,
    all_ok,
    as_dict_list,
    run_all_validations,
    validate_all_materials_accounted,
    validate_catalog_coverage,
    validate_no_duplicates,
    validate_resumen_total_preserved,
    validate_resumen_vs_notificacion,
    validate_row_count,
    validate_sum_preserved,
)
from app.core.logging_setup import log_validaciones, setup_logging
from app.core.db import (
    already_loaded,
    create_run,
    get_conn,
    get_or_insert_carga,
    get_run,
    init_db,
    list_cargas,
    list_recent_runs,
    persist_cruce,
    persist_flash_agregado,
    persist_no_cruzados,
    persist_pre_corte,
    persist_run,
    update_run,
)
from app.core.exporters import export_monthly_view, export_run_summary

__all__ = [
    "extract_file_date",
    "extract_production_date",
    "hash_file",
    "load_flash",
    "load_notificacion",
    "load_pre_corte",
    "load_resumen",
    "attach_sap_to_resumen",
    "catalog_stats",
    "import_from_homologacion",
    "list_catalog",
    "resolve_sap",
    "update_catalog_from_pair",
    "aggregate_flash",
    "match_by_material",
    "run_batch_pipeline",
    "run_full_pipeline",
    "MatchResult",
    "Validation",
    "all_ok",
    "as_dict_list",
    "run_all_validations",
    "validate_all_materials_accounted",
    "validate_catalog_coverage",
    "validate_no_duplicates",
    "validate_resumen_total_preserved",
    "validate_resumen_vs_notificacion",
    "validate_row_count",
    "validate_sum_preserved",
    "log_validaciones",
    "setup_logging",
    "already_loaded",
    "create_run",
    "get_conn",
    "get_or_insert_carga",
    "get_run",
    "init_db",
    "list_cargas",
    "list_recent_runs",
    "persist_cruce",
    "persist_flash_agregado",
    "persist_no_cruzados",
    "persist_pre_corte",
    "persist_run",
    "update_run",
    "export_monthly_view",
    "export_run_summary",
]
