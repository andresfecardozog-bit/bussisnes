"""Tests del generador PBIP (Fase 4, app/platform/render_pbip.py).

Cubre:
- Estructura completa del proyecto: .pbip + Report/ + SemanticModel/ +
  data/ + README + .gitignore.
- Todos los JSON parsean (incluidos los config embebidos de visuales).
- El theme registrado lleva los colores de marca navy/naranja.
- Una medida DAX por KPI con la formula correspondiente a su op (fallback).
- Medidas declarativas de report.powerbi.measures (DISTINCTCOUNT, formatos).
- La particion TMDL importa los CSV via el parametro RutaDatos.
- Pagina default (sin report.powerbi): 1 card por KPI + tabla detalle.
- Contrato extendido: CSVs/tablas del data_model, breakdowns referenciados
  por visuales, visuales donut/matriz, README con proposito/justificacion,
  y end-to-end con el borrador CEN + fixtures reales.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from app.platform.engine import GenericMatchResult, run_profile
from app.platform.profile import MatchProfile
from app.platform.render_pbip import _match_breakdown, build_theme, measure_name, render_pbip

PROFILES_DIR = Path(__file__).resolve().parents[1] / "profiles"
FIXTURES = Path(__file__).parent / "fixtures"
CEN_JUNIO = FIXTURES / "cen" / "cen_junio_muestra.xlsx"
SAP_MUESTRA = FIXTURES / "cen" / "sap_junio_muestra.xlsx"


def _mini_profile(powerbi: dict | None = None) -> MatchProfile:
    report: dict = {
        "excel": {
            "filename_prefix": "mini",
            "sheets": [{"name": "Portada", "kind": "portada"}],
        }
    }
    if powerbi is not None:
        report["powerbi"] = powerbi
    return MatchProfile.model_validate({
        "profile_id": "mini_pbip",
        "version": 1,
        "descripcion": "Cruce sintetico para PBIP",
        "left": {
            "role": "plan",
            "label": "Plan",
            "loader": {
                "type": "tabular",
                "columns": [{"name": "material", "source": "MATERIAL"}],
            },
        },
        "right": {
            "role": "real",
            "label": "Real",
            "loader": {
                "type": "tabular",
                "columns": [{"name": "material", "source": "MATERIAL"}],
            },
        },
        "join": {"keys": [{"left": "material", "right": "material"}]},
        "kpis": [
            {
                "id": "cumplimiento_global_pct",
                "label": "Cumplimiento global (%)",
                "op": "ratio_pct_of_sums",
                "numerator": "real",
                "denominator": "plan",
            },
            {"id": "plan_total", "label": "Plan total", "op": "sum", "numerator": "plan"},
            {"id": "filas", "label": "Filas cruzadas", "op": "count"},
        ],
        "report": report,
    })


def _mini_result() -> GenericMatchResult:
    matched = pd.DataFrame({
        "_k0": ["30018", "30049"],
        "categoria": ["A", "B"],
        "fecha": ["2026-02-14", "2026-02-15"],
        "plan": [100.0, 200.0],
        "real": [95.0, 210.0],
        "cumplimiento_pct": [95.0, 105.0],
    })
    no_cruzados = pd.DataFrame({
        "origen": ["plan"],
        "key": ["30099"],
        "motivo": ["sin contraparte en la fuente derecha"],
    })
    return GenericMatchResult(
        profile_id="mini_pbip",
        profile_version=1,
        parameters={},
        matched=matched,
        solo_left=pd.DataFrame({"_k0": ["30099"], "plan": [50.0]}),
        solo_right=pd.DataFrame(columns=["_k0", "real"]),
        no_cruzados=no_cruzados,
        kpis={"cumplimiento_global_pct": 101.67, "plan_total": 300.0, "filas": 2},
    )


@pytest.fixture
def proyecto(tmp_path) -> Path:
    """Proyecto PBIP generado con el mini-profile (pagina default)."""
    out = tmp_path / "pbip_default"
    render_pbip(_mini_profile(), _mini_result(), out)
    return out


# ---------------------------------------------------------------------------
# Estructura de archivos
# ---------------------------------------------------------------------------

def test_estructura_completa_del_proyecto(proyecto):
    esperados = [
        "mini_pbip.pbip",
        "mini_pbip.Report/definition.pbir",
        "mini_pbip.Report/report.json",
        "mini_pbip.Report/StaticResources/RegisteredResources/NutriAvicolaTheme.json",
        "mini_pbip.SemanticModel/definition.pbism",
        "mini_pbip.SemanticModel/definition/database.tmdl",
        "mini_pbip.SemanticModel/definition/model.tmdl",
        "mini_pbip.SemanticModel/definition/tables/matched.tmdl",
        "mini_pbip.SemanticModel/definition/tables/no_cruzados.tmdl",
        "data/matched.csv",
        "data/no_cruzados.csv",
        "README.md",
        ".gitignore",
    ]
    for rel in esperados:
        assert (proyecto / rel).exists(), f"falta {rel}"


def test_render_pbip_retorna_ruta_del_pbip(tmp_path):
    out = tmp_path / "p"
    pbip = render_pbip(_mini_profile(), _mini_result(), out)
    assert pbip == out / "mini_pbip.pbip"
    assert pbip.exists()


def test_jsons_del_proyecto_parsean(proyecto):
    for rel in (
        "mini_pbip.pbip",
        "mini_pbip.Report/definition.pbir",
        "mini_pbip.Report/report.json",
        "mini_pbip.Report/StaticResources/RegisteredResources/NutriAvicolaTheme.json",
        "mini_pbip.SemanticModel/definition.pbism",
    ):
        raw = (proyecto / rel).read_text(encoding="utf-8")
        json.loads(raw)


def test_pbip_apunta_al_report_y_pbir_al_semantic_model(proyecto):
    pbip = json.loads((proyecto / "mini_pbip.pbip").read_text(encoding="utf-8"))
    assert pbip["artifacts"][0]["report"]["path"] == "mini_pbip.Report"

    pbir = json.loads(
        (proyecto / "mini_pbip.Report/definition.pbir").read_text(encoding="utf-8")
    )
    assert pbir["datasetReference"]["byPath"]["path"] == "../mini_pbip.SemanticModel"


def test_csvs_contienen_los_datos_con_key_renombrada(proyecto):
    matched = pd.read_csv(proyecto / "data" / "matched.csv")
    assert len(matched) == 2
    assert "material" in matched.columns, "_k0 debe salir como 'material'"
    assert not any(c.startswith("_") for c in matched.columns)

    no_cruz = pd.read_csv(proyecto / "data" / "no_cruzados.csv")
    assert len(no_cruz) == 1
    assert list(no_cruz.columns) == ["origen", "key", "motivo"]


def test_readme_documenta_apertura_portable(proyecto):
    texto = (proyecto / "README.md").read_text(encoding="utf-8")
    # Con todo embebido el PBIP es portable: el README no depende de RutaDatos.
    assert "Power BI Desktop" in texto
    assert "embebidos dentro del modelo" in texto
    assert "sin configurar rutas" in texto


# ---------------------------------------------------------------------------
# Theme de marca
# ---------------------------------------------------------------------------

def test_theme_tiene_colores_de_marca(proyecto):
    theme = json.loads(
        (proyecto / "mini_pbip.Report/StaticResources/RegisteredResources/"
         "NutriAvicolaTheme.json").read_text(encoding="utf-8")
    )
    assert theme["name"] == "NutriAvicola"
    assert theme["dataColors"][0] == "#0F2E4C", "navy primario"
    assert theme["dataColors"][1] == "#E87722", "naranja acento"
    assert theme["tableAccent"] == "#0F2E4C"
    assert theme["background"] == "#E9EFF6", "fondo corporativo no blanco"
    # Semaforo corporativo para estados
    assert theme["good"] == "#63BE7B"
    assert theme["bad"] == "#F8696B"


def test_theme_tiene_sombra_y_estilo_card():
    theme = build_theme()
    estilo = theme["visualStyles"]["*"]["*"]
    assert estilo["dropShadow"][0]["show"] is True
    assert estilo["border"][0]["radius"] == 8
    assert theme["visualStyles"]["card"]["*"]["labels"][0]["fontSize"] == 30


def test_theme_variantes_claro_y_oscuro():
    claro = build_theme("nutriavicola_claro")
    assert claro["background"] == "#FFFFFF"
    oscuro = build_theme("nutriavicola_oscuro")
    assert oscuro["background"] == "#0F2E4C"
    assert oscuro["textClasses"]["title"]["color"] == "#FFFFFF"
    # variante desconocida cae al corporativo por defecto
    assert build_theme("no_existe")["background"] == "#E9EFF6"


def test_theme_registrado_en_report_json(proyecto):
    report = json.loads(
        (proyecto / "mini_pbip.Report/report.json").read_text(encoding="utf-8")
    )
    items = report["resourcePackages"][0]["resourcePackage"]["items"]
    assert any(i["name"] == "NutriAvicolaTheme.json" for i in items)
    config = json.loads(report["config"])
    assert config["themeCollection"]["customTheme"]["name"] == "NutriAvicolaTheme.json"


def test_logo_registrado_en_report(proyecto):
    from app.platform.render_pbip import LOGO_RESOURCE_NAME, LOGO_SOURCE

    if not LOGO_SOURCE.is_file():
        pytest.skip("logo corporativo no presente en resources/")
    logo = proyecto / (
        "mini_pbip.Report/StaticResources/RegisteredResources/" + LOGO_RESOURCE_NAME
    )
    assert logo.exists()
    report = json.loads(
        (proyecto / "mini_pbip.Report/report.json").read_text(encoding="utf-8")
    )
    items = report["resourcePackages"][0]["resourcePackage"]["items"]
    assert any(i["name"] == LOGO_RESOURCE_NAME and i["type"] == 100 for i in items)


def test_pagina_lleva_header_logo_y_titulo(proyecto_cen):
    from app.platform.render_pbip import LOGO_RESOURCE_NAME, LOGO_SOURCE

    out, profile, _ = proyecto_cen
    report = json.loads(
        (out / "cen_vs_sap_v1_borrador.Report/report.json").read_text(encoding="utf-8")
    )
    page = report["sections"][0]
    tipos = [
        json.loads(vc["config"])["singleVisual"]["visualType"]
        for vc in page["visualContainers"]
    ]
    assert "textbox" in tipos
    if LOGO_SOURCE.is_file():
        assert "image" in tipos
        assert (out / "cen_vs_sap_v1_borrador.Report/StaticResources"
                / "RegisteredResources" / LOGO_RESOURCE_NAME).exists()
    titulo = next(
        json.loads(vc["config"])
        for vc in page["visualContainers"]
        if json.loads(vc["config"])["singleVisual"]["visualType"] == "textbox"
    )
    texto = titulo["singleVisual"]["objects"]["general"][0]["properties"]["paragraphs"][0]["textRuns"][0]["value"]
    assert texto == profile.report.powerbi.pages[0].name


def test_build_theme_es_json_serializable():
    json.dumps(build_theme())


# ---------------------------------------------------------------------------
# Semantic model: medidas DAX por KPI + particion CSV
# ---------------------------------------------------------------------------

def test_una_medida_dax_por_kpi(proyecto):
    tmdl = (
        proyecto / "mini_pbip.SemanticModel/definition/tables/matched.tmdl"
    ).read_text(encoding="utf-8")
    profile = _mini_profile()
    for kpi in profile.kpis:
        assert f"measure '{measure_name(kpi)}'" in tmdl or (
            f"measure {measure_name(kpi)}" in tmdl
        ), f"falta la medida del KPI {kpi.id}"
    assert "DIVIDE(SUM(matched[real]), SUM(matched[plan])) * 100" in tmdl
    assert "SUM(matched[plan])" in tmdl
    assert "COUNTROWS(matched)" in tmdl


def test_particion_inline_por_defecto_sin_rutadatos(proyecto):
    """Por defecto (tablas bajo el umbral) todo va embebido: la particion no
    usa CSV ni RutaDatos, y no se emite el archivo de parametros."""
    tmdl = (
        proyecto / "mini_pbip.SemanticModel/definition/tables/matched.tmdl"
    ).read_text(encoding="utf-8")
    assert "partition matched = m" in tmdl
    assert "mode: import" in tmdl
    assert "Table.FromRows(Json.Document(Binary.Decompress(" in tmdl
    assert "RutaDatos" not in tmdl
    assert not (
        proyecto / "mini_pbip.SemanticModel/definition/expressions.tmdl"
    ).exists()


def test_fallback_csv_para_tablas_enormes(tmp_path, monkeypatch):
    """Si una tabla supera el umbral de inline, cae al fallback CSV + el
    parametro RutaDatos (ruta que el usuario ajusta si mueve el proyecto)."""
    import app.platform.render_pbip as rp

    monkeypatch.setattr(rp, "INLINE_MAX_ROWS", 1)
    out = tmp_path / "csv_fallback"
    rp.render_pbip(_mini_profile(), _mini_result(), out)
    tmdl = (
        out / "mini_pbip.SemanticModel/definition/tables/matched.tmdl"
    ).read_text(encoding="utf-8")
    assert 'RutaDatos & "\\matched.csv"' in tmdl
    assert "Csv.Document" in tmdl
    expressions = (
        out / "mini_pbip.SemanticModel/definition/expressions.tmdl"
    ).read_text(encoding="utf-8")
    assert "expression RutaDatos" in expressions
    assert "IsParameterQuery=true" in expressions


def test_model_tmdl_referencia_las_dos_tablas(proyecto):
    tmdl = (
        proyecto / "mini_pbip.SemanticModel/definition/model.tmdl"
    ).read_text(encoding="utf-8")
    assert "ref table matched" in tmdl
    assert "ref table no_cruzados" in tmdl


def test_columnas_tmdl_coinciden_con_el_csv(proyecto):
    tmdl = (
        proyecto / "mini_pbip.SemanticModel/definition/tables/matched.tmdl"
    ).read_text(encoding="utf-8")
    matched = pd.read_csv(proyecto / "data" / "matched.csv")
    for col in matched.columns:
        assert f"sourceColumn: {col}" in tmdl, f"columna {col} sin definir en TMDL"


# ---------------------------------------------------------------------------
# Report: visuales
# ---------------------------------------------------------------------------

def _visual_configs(proyecto: Path, name: str = "mini_pbip") -> list[dict]:
    report = json.loads(
        (proyecto / f"{name}.Report/report.json").read_text(encoding="utf-8")
    )
    out = []
    for section in report["sections"]:
        for vc in section["visualContainers"]:
            out.append(json.loads(vc["config"]))
    return out


def test_pagina_default_un_card_por_kpi_mas_tabla(proyecto):
    configs = _visual_configs(proyecto)
    cards = [c for c in configs if c["singleVisual"]["visualType"] == "card"]
    tablas = [c for c in configs if c["singleVisual"]["visualType"] == "tableEx"]
    assert len(cards) == 3, "1 card por cada KPI del profile"
    assert len(tablas) == 1


def test_visuales_sin_superposicion_cards_arriba_tabla_abajo(proyecto):
    configs = _visual_configs(proyecto)
    cards = [c for c in configs if c["singleVisual"]["visualType"] == "card"]
    tabla = next(c for c in configs if c["singleVisual"]["visualType"] == "tableEx")
    card_bottom = max(
        c["layouts"][0]["position"]["y"] + c["layouts"][0]["position"]["height"]
        for c in cards
    )
    tabla_top = tabla["layouts"][0]["position"]["y"]
    assert tabla_top >= card_bottom, "la tabla debe ir debajo de los cards"
    # Cards no se pisan entre si (rangos x disjuntos).
    xs = sorted(
        (c["layouts"][0]["position"]["x"], c["layouts"][0]["position"]["width"])
        for c in cards
    )
    for (x1, w1), (x2, _) in zip(xs, xs[1:]):
        assert x1 + w1 <= x2, "cards superpuestos en x"


def test_cards_referencian_medidas_del_modelo(proyecto):
    configs = _visual_configs(proyecto)
    cards = [c for c in configs if c["singleVisual"]["visualType"] == "card"]
    refs = {
        c["singleVisual"]["projections"]["Values"][0]["queryRef"] for c in cards
    }
    assert "matched.Cumplimiento global (%)" in refs
    assert "matched.Plan total" in refs


def test_report_powerbi_spec_declarado_genera_paginas_y_graficos(tmp_path):
    powerbi = {
        "pages": [
            {
                "name": "Vision general",
                "visuals": [
                    {"kind": "card_kpi", "title": "Cumplimiento",
                     "measure": "cumplimiento_global_pct"},
                    {"kind": "tendencia", "title": "Tendencia",
                     "measure": "plan_total", "category": "fecha"},
                    {"kind": "barras_categoria", "title": "Por categoria",
                     "measure": "plan_total", "category": "categoria"},
                    {"kind": "tabla_detalle", "title": "Detalle"},
                ],
            }
        ]
    }
    out = tmp_path / "con_spec"
    render_pbip(_mini_profile(powerbi), _mini_result(), out)

    report = json.loads(
        (out / "mini_pbip.Report/report.json").read_text(encoding="utf-8")
    )
    assert len(report["sections"]) == 1
    assert report["sections"][0]["displayName"] == "Vision general"

    configs = _visual_configs(out)
    tipos = sorted(c["singleVisual"]["visualType"] for c in configs)
    # sin data_model no hay dimensiones: no se generan slicers (4B-B)
    assert tipos == [
        "card", "clusteredBarChart", "image", "lineChart", "tableEx", "textbox",
    ]


def test_visual_con_categoria_inexistente_se_omite(tmp_path):
    powerbi = {
        "pages": [
            {
                "name": "P1",
                "visuals": [
                    {"kind": "tendencia", "title": "Rota",
                     "measure": "plan_total", "category": "no_existe"},
                    {"kind": "tabla_detalle", "title": "Detalle"},
                ],
            }
        ]
    }
    out = tmp_path / "invalida"
    render_pbip(_mini_profile(powerbi), _mini_result(), out)
    configs = _visual_configs(out)
    tipos = [c["singleVisual"]["visualType"] for c in configs]
    assert "lineChart" not in tipos
    assert "tableEx" in tipos


# ---------------------------------------------------------------------------
# Contrato extendido: data_model + measures + donut/matriz + README
# ---------------------------------------------------------------------------

def _profile_extendido() -> MatchProfile:
    """Mini-profile con data_model, breakdown y powerbi spec completo."""
    base = _mini_profile()
    raw = base.model_dump()
    raw["breakdowns"] = [
        {
            "id": "por_grupo",
            "label": "Cumplimiento por grupo",
            "dimensions": ["categoria"],
            "metrics": [
                {"id": "plan_total", "op": "sum", "column": "plan"},
                {
                    "id": "cumplimiento_pct",
                    "op": "ratio_pct_of_sums",
                    "numerator": "real",
                    "denominator": "plan",
                },
            ],
        }
    ]
    raw["data_model"] = {
        "fact_name": "FactMini",
        "dimensions": [{"name": "DimCategoria", "key": "categoria"}],
    }
    raw["report"]["powerbi"] = {
        "measures": [
            {"id": "plan_total_m", "label": "Plan total", "op": "sum",
             "table": "FactMini", "column": "plan", "format": "entero"},
            {"id": "nivel_pct", "label": "Nivel de servicio (%)",
             "op": "ratio_pct_of_sums", "table": "FactMini",
             "numerator": "real", "denominator": "plan", "format": "porcentaje"},
            {"id": "materiales", "label": "Materiales distintos",
             "op": "distinct_count", "table": "FactMini",
             "column": "key_material", "format": "entero"},
            {"id": "lineas_m", "label": "Lineas", "op": "count",
             "table": "FactMini", "format": "entero"},
        ],
        "pages": [
            {
                "name": "Vision general",
                "proposito": "Vision ejecutiva del cruce plan vs real",
                "visuals": [
                    {"kind": "card_kpi", "title": "Nivel de servicio",
                     "measure": "nivel_pct", "table": "FactMini",
                     "justificacion": "KPI principal pedido por negocio"},
                    {"kind": "donut", "title": "Lineas por estado",
                     "measure": "lineas_m", "category": "estado_cruce",
                     "table": "FactMini",
                     "justificacion": "Distribucion de estados de un vistazo"},
                    {"kind": "barras_categoria", "title": "Plan por grupo",
                     "measure": "plan_total", "category": "categoria",
                     "table": "por_grupo",
                     "justificacion": "Volumen por grupo desde el breakdown"},
                    {"kind": "matriz", "title": "Nivel por categoria",
                     "measure": "nivel_pct", "category": "categoria",
                     "table": "FactMini",
                     "justificacion": "Cruce dimensional para detalle"},
                    {"kind": "tabla_detalle", "title": "Detalle",
                     "table": "FactMini",
                     "justificacion": "Drill-down completo para auditoria"},
                ],
            }
        ],
    }
    return MatchProfile.model_validate(raw)


def _result_extendido() -> GenericMatchResult:
    result = _mini_result()
    result.breakdowns = {
        "por_grupo": pd.DataFrame({
            "categoria": ["A", "B"],
            "plan_total": [100.0, 200.0],
            "cumplimiento_pct": [95.0, 105.0],
        })
    }
    return result


@pytest.fixture
def proyecto_extendido(tmp_path) -> Path:
    out = tmp_path / "pbip_ext"
    render_pbip(_profile_extendido(), _result_extendido(), out)
    return out


def test_csvs_y_tablas_tmdl_del_data_model(proyecto_extendido):
    # CSVs del data_model (fact + dims) ademas de matched/no_cruzados
    for csv in ("FactMini.csv", "DimCategoria.csv", "matched.csv", "no_cruzados.csv"):
        assert (proyecto_extendido / "data" / csv).exists(), f"falta data/{csv}"

    fact = pd.read_csv(proyecto_extendido / "data" / "FactMini.csv")
    assert len(fact) == 3, "matched(2) + solo_left(1) + solo_right(0)"
    assert "fact_id" in fact.columns
    assert "estado_cruce" in fact.columns
    assert "dim_categoria_id" in fact.columns

    tables_dir = proyecto_extendido / "mini_pbip.SemanticModel/definition/tables"
    for tabla in ("FactMini", "DimCategoria", "matched", "no_cruzados"):
        assert (tables_dir / f"{tabla}.tmdl").exists(), f"falta tabla TMDL {tabla}"

    model = (
        proyecto_extendido / "mini_pbip.SemanticModel/definition/model.tmdl"
    ).read_text(encoding="utf-8")
    assert "ref table FactMini" in model
    assert "ref table DimCategoria" in model


def test_breakdown_referenciado_por_visual_se_exporta(proyecto_extendido):
    assert (proyecto_extendido / "data" / "por_grupo.csv").exists()
    tmdl = (
        proyecto_extendido
        / "mini_pbip.SemanticModel/definition/tables/por_grupo.tmdl"
    ).read_text(encoding="utf-8")
    assert "sourceColumn: cumplimiento_pct" in tmdl
    # medida implicita del visual de barras sobre la columna del breakdown
    # (nombre humanizado, sin underscore, para que sea legible en el visual)
    assert "measure 'Total plan total'" in tmdl


def test_medidas_desde_measures_spec(proyecto_extendido):
    tmdl = (
        proyecto_extendido
        / "mini_pbip.SemanticModel/definition/tables/FactMini.tmdl"
    ).read_text(encoding="utf-8")
    assert "DISTINCTCOUNT(FactMini[key_material])" in tmdl
    assert "DIVIDE(SUM(FactMini[real]), SUM(FactMini[plan])) * 100" in tmdl
    assert "COUNTROWS(FactMini)" in tmdl
    assert "measure 'Nivel de servicio (%)'" in tmdl
    # formato porcentaje: % literal escapado sobre el valor 0-100
    assert "formatString: 0.00\\%" in tmdl

    # con measures declaradas NO se generan las medidas fallback de KPIs
    matched_tmdl = (
        proyecto_extendido
        / "mini_pbip.SemanticModel/definition/tables/matched.tmdl"
    ).read_text(encoding="utf-8")
    assert "measure" not in matched_tmdl


def test_visual_donut_y_matriz_en_report(proyecto_extendido):
    configs = _visual_configs(proyecto_extendido)
    tipos = sorted(c["singleVisual"]["visualType"] for c in configs)
    # slicer solo via dimension declarada (estado_cruce queda en el donut, no en slicer)
    assert tipos == [
        "card", "clusteredBarChart", "donutChart", "image", "pivotTable",
        "slicer", "tableEx", "textbox",
    ]
    donut = next(c for c in configs if c["singleVisual"]["visualType"] == "donutChart")
    assert donut["singleVisual"]["projections"]["Category"][0]["queryRef"] == (
        "FactMini.estado_cruce"
    )
    matriz = next(c for c in configs if c["singleVisual"]["visualType"] == "pivotTable")
    assert "Rows" in matriz["singleVisual"]["projections"]
    assert "Values" in matriz["singleVisual"]["projections"]


def test_visual_de_breakdown_se_bindea_a_su_tabla(proyecto_extendido):
    configs = _visual_configs(proyecto_extendido)
    barras = next(
        c for c in configs if c["singleVisual"]["visualType"] == "clusteredBarChart"
    )
    entity = barras["singleVisual"]["prototypeQuery"]["From"][0]["Entity"]
    assert entity == "por_grupo"


def test_readme_con_proposito_y_justificaciones(proyecto_extendido):
    texto = (proyecto_extendido / "README.md").read_text(encoding="utf-8")
    assert "Diseno propuesto por ReportDesigner" in texto
    assert "Vision general" in texto
    assert "Proposito: Vision ejecutiva del cruce plan vs real" in texto
    assert "Justificacion: KPI principal pedido por negocio" in texto
    assert "(tabla: por_grupo)" in texto


def test_design_prefs_limita_paginas_y_aplica_theme(tmp_path):
    profile = _profile_extendido()
    raw = profile.model_dump()
    raw["report"]["powerbi"]["design"] = {
        "theme": "nutriavicola_oscuro",
        "max_paginas": 1,
        "max_charts_por_pagina": 1,
        "tipos_preferidos": ["donut"],
        "incluir_paginas_drill": False,
    }
    profile = MatchProfile.model_validate(raw)
    out = tmp_path / "con_design"
    render_pbip(profile, _result_extendido(), out)

    report = json.loads(
        (out / "mini_pbip.Report/report.json").read_text(encoding="utf-8")
    )
    assert len(report["sections"]) == 1, "max_paginas=1 y sin drill pages"

    configs = _visual_configs(out)
    charts = [
        c for c in configs
        if c["singleVisual"]["visualType"] in
        ("donutChart", "clusteredBarChart", "lineChart", "funnel", "areaChart")
    ]
    assert len(charts) == 1
    assert charts[0]["singleVisual"]["visualType"] == "donutChart", (
        "tipos_preferidos=[donut] debe dejar solo el donut"
    )

    theme = json.loads(
        (out / "mini_pbip.Report/StaticResources/RegisteredResources/"
         "NutriAvicolaTheme.json").read_text(encoding="utf-8")
    )
    assert theme["background"] == "#0F2E4C", "theme oscuro elegido por el usuario"


def test_visual_funnel_y_area_soportados(tmp_path):
    powerbi = {
        "pages": [
            {
                "name": "P1",
                "visuals": [
                    {"kind": "funnel", "title": "Funnel por categoria",
                     "measure": "plan_total", "category": "categoria"},
                    {"kind": "area", "title": "Area por fecha",
                     "measure": "plan_total", "category": "fecha"},
                    {"kind": "tabla_detalle", "title": "Detalle"},
                ],
            }
        ]
    }
    out = tmp_path / "funnel_area"
    render_pbip(_mini_profile(powerbi), _mini_result(), out)
    configs = _visual_configs(out)
    tipos = {c["singleVisual"]["visualType"] for c in configs}
    assert "funnel" in tipos
    assert "areaChart" in tipos


def test_match_breakdown_resuelve_nombres_semanticos_de_agentes():
    raw = (PROFILES_DIR / "cen_vs_sap_v1_borrador.json").read_text(encoding="utf-8")
    profile = MatchProfile.from_json(raw)
    assert _match_breakdown("FactDevoluciones", profile) == "devoluciones_por_motivo"


def test_measure_sobre_tabla_inexistente_se_omite_sin_tumbar(tmp_path):
    profile = _profile_extendido()
    raw = profile.model_dump()
    raw["report"]["powerbi"]["measures"][0]["table"] = "NoExiste"
    profile = MatchProfile.model_validate(raw)
    pbip = render_pbip(profile, _mini_result(), tmp_path / "x")
    assert pbip.exists()
    tmdl = (tmp_path / "x" / f"{profile.profile_id}.SemanticModel" / "definition" / "tables").glob("*.tmdl")
    assert not any("NoExiste" in p.name for p in tmdl)


# ---------------------------------------------------------------------------
# End-to-end: borrador CEN vs SAP con fixtures reales de junio
# ---------------------------------------------------------------------------

def test_cen_vs_sap_end_to_end_pbip(tmp_path):
    raw = (PROFILES_DIR / "cen_vs_sap_v1_borrador.json").read_text(encoding="utf-8")
    profile = MatchProfile.from_json(raw)
    result = run_profile(profile, left_path=CEN_JUNIO, right_path=SAP_MUESTRA)

    out = tmp_path / "cen_pbip"
    pbip = render_pbip(profile, result, out)
    assert pbip.exists()
    name = profile.profile_id

    # CSVs: data_model completo + particiones + breakdown Devoluciones
    data = out / "data"
    for csv in (
        "FactNivelServicio.csv", "DimCliente.csv", "DimMaterial.csv",
        "DimDistrito.csv", "matched.csv", "no_cruzados.csv", "Devoluciones.csv",
    ):
        assert (data / csv).exists(), f"falta data/{csv}"

    fact = pd.read_csv(data / "FactNivelServicio.csv")
    total = sum(result.summary()[k] for k in ("matched", "solo_left", "solo_right"))
    assert len(fact) == total

    # medidas del spec en el TMDL del fact
    tmdl = (
        out / f"{name}.SemanticModel/definition/tables/FactNivelServicio.tmdl"
    ).read_text(encoding="utf-8")
    assert "DISTINCTCOUNT(FactNivelServicio[key_numero_orden])" in tmdl
    # ratio con semantica corregida: entregado DE LO PEDIDO / total pedido
    assert (
        'DIVIDE(CALCULATE(SUM(FactNivelServicio[cantidad_entregada]), '
        'FactNivelServicio[estado_cruce] IN {"cruzado"}), '
        'CALCULATE(SUM(FactNivelServicio[cantidad_pedida]), '
        'FactNivelServicio[estado_cruce] IN {"cruzado", "solo_cen"})) * 100'
    ) in tmdl

    # report: paginas base + drill pages, con donut y matriz
    report = json.loads(
        (out / f"{name}.Report/report.json").read_text(encoding="utf-8")
    )
    nombres = [s["displayName"] for s in report["sections"]]
    assert nombres[:3] == ["Nivel de servicio", "Clientes y materiales", "Devoluciones"]
    assert {"Logistica", "Venta", "Destinatario x causal"}.issubset(set(nombres))
    configs = _visual_configs(out, name=name)
    tipos = {c["singleVisual"]["visualType"] for c in configs}
    assert "donutChart" in tipos
    assert "pivotTable" in tipos

    # README con el racional del ReportDesigner
    readme = (out / "README.md").read_text(encoding="utf-8")
    assert "Diseno propuesto por ReportDesigner" in readme
    assert "Ranking de causas para atacar la raiz" in readme


# ---------------------------------------------------------------------------
# Correccion de semantica + enriquecimiento (feedback usuario 2026-07-08)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def proyecto_cen(tmp_path_factory):
    """Proyecto CEN completo generado una sola vez para los tests de
    semantica/enriquecimiento."""
    raw = (PROFILES_DIR / "cen_vs_sap_v1_borrador.json").read_text(encoding="utf-8")
    profile = MatchProfile.from_json(raw)
    result = run_profile(profile, left_path=CEN_JUNIO, right_path=SAP_MUESTRA)
    out = tmp_path_factory.mktemp("cen_pbip")
    render_pbip(profile, result, out)
    return out, profile, result


def test_nombres_de_medida_unicos_en_todo_el_modelo(proyecto_cen):
    """Power BI exige nombres de medida unicos en TODO el modelo (no por
    tabla). Varios breakdowns comparten nombres de columna (unidades_devueltas,
    lineas...) y el total implicito podria colisionar: el registry debe
    desambiguar. Regresion del error 'Could not add Measure ... already
    exists in the Model'."""
    import re as _re

    out, profile, _ = proyecto_cen
    tables_dir = (
        out / f"{profile.profile_id}.SemanticModel" / "definition" / "tables"
    )
    nombres: list[str] = []
    for tmdl in tables_dir.glob("*.tmdl"):
        for m in _re.finditer(
            r"^\s*measure\s+(?:'([^']+)'|([^\s=]+))",
            tmdl.read_text(encoding="utf-8"),
            _re.M,
        ):
            nombres.append(m.group(1) or m.group(2))
    duplicados = {n for n in nombres if nombres.count(n) > 1}
    assert not duplicados, f"nombres de medida duplicados en el modelo: {duplicados}"


def test_numeros_coherentes_medidas_vs_result(proyecto_cen):
    """La causa del descuadre reportado: SUM sobre todo el fact mezcla
    universos (entregas sin pedido inflan 'unidades entregadas'). Las
    medidas generadas deben reproducir los KPIs del motor al emular sus
    filtros de estado_cruce sobre el fact exportado."""
    out, profile, result = proyecto_cen
    fact = pd.read_csv(out / "data" / "FactNivelServicio.csv")

    left_states = {"cruzado", f"solo_{profile.left.role}"}

    # Unidades pedidas (filtro left_states) == total plan del service_level
    pedidas = fact[fact["estado_cruce"].isin(left_states)]["cantidad_pedida"].sum()
    assert pedidas == pytest.approx(result.service_level["total_unidades_plan"])

    # Unidades entregadas (solo cruzado) == KPI del motor (matched)
    entregadas = fact[fact["estado_cruce"] == "cruzado"]["cantidad_entregada"].sum()
    assert entregadas == pytest.approx(result.kpis["unidades_entregadas"])

    # El excedente queda expuesto en su propia medida, no mezclado
    sin_pedido = fact[
        fact["estado_cruce"] == f"solo_{profile.right.role}"
    ]["cantidad_entregada"].sum()
    assert sin_pedido == pytest.approx(
        result.service_level["clases"]["sin_pedido"]["unidades_real"]
    )
    assert sin_pedido > 0, "el fixture trae entregas sin pedido"

    # Y el TMDL contiene exactamente esos filtros CALCULATE
    tmdl = (
        out / "cen_vs_sap_v1_borrador.SemanticModel/definition/tables/"
        "FactNivelServicio.tmdl"
    ).read_text(encoding="utf-8")
    assert (
        'CALCULATE(SUM(FactNivelServicio[cantidad_entregada]), '
        'FactNivelServicio[estado_cruce] IN {"cruzado"})'
    ) in tmdl
    assert (
        'CALCULATE(SUM(FactNivelServicio[cantidad_pedida]), '
        'FactNivelServicio[estado_cruce] IN {"cruzado", "solo_cen"})'
    ) in tmdl
    assert (
        'CALCULATE(SUM(FactNivelServicio[cantidad_entregada]), '
        'FactNivelServicio[estado_cruce] IN {"solo_sap"})'
    ) in tmdl, "medida 'Unidades entregadas sin pedido'"


def test_fact_clasifica_nivel_servicio_en_no_cruzados(proyecto_cen):
    """El donut mostraba casi todo 'completo' porque las filas no cruzadas
    quedaban con nivel_servicio nulo. Ahora se clasifican."""
    out, profile, result = proyecto_cen
    fact = pd.read_csv(out / "data" / "FactNivelServicio.csv")
    assert fact["nivel_servicio"].notna().all()
    solo_left = fact[fact["estado_cruce"] == f"solo_{profile.left.role}"]
    assert (solo_left["nivel_servicio"] == "no_entregado").all()
    solo_right = fact[fact["estado_cruce"] == f"solo_{profile.right.role}"]
    assert (solo_right["nivel_servicio"] == "sin_pedido").all()


def test_medidas_de_service_level_autogeneradas(proyecto_cen):
    out, _, _ = proyecto_cen
    tmdl = (
        out / "cen_vs_sap_v1_borrador.SemanticModel/definition/tables/"
        "FactNivelServicio.tmdl"
    ).read_text(encoding="utf-8")
    assert "measure 'Unidades entregadas sin pedido'" in tmdl
    assert "measure 'Pedidos completos (%)'" in tmdl
    assert "VAR __pedidos" in tmdl
    assert "measure 'Lineas del cruce'" in tmdl


def test_relaciones_tmdl_fact_a_dimensiones(proyecto_cen):
    out, _, _ = proyecto_cen
    rels_path = (
        out / "cen_vs_sap_v1_borrador.SemanticModel/definition/relationships.tmdl"
    )
    assert rels_path.exists()
    rels = rels_path.read_text(encoding="utf-8")
    for dim, col in (
        ("DimCliente", "dim_cliente_id"),
        ("DimMaterial", "dim_material_id"),
        ("DimDistrito", "dim_distrito_id"),
    ):
        assert f"fromColumn: FactNivelServicio.{col}" in rels
        assert f"toColumn: {dim}.{col}" in rels
    assert "crossFilteringBehavior: oneDirection" in rels
    # los tipos de las FKs deben casar con los ids de las dimensiones
    fact = pd.read_csv(out / "data" / "FactNivelServicio.csv")
    assert pd.api.types.is_numeric_dtype(fact["dim_cliente_id"])


def test_todas_las_tablas_embebidas_portable(proyecto_cen):
    """Portabilidad: TODAS las tablas (dims, breakdowns, fact y crudas) van
    embebidas (patron Enter Data) y ninguna depende de RutaDatos, para que el
    zip abra en cualquier maquina sin configurar rutas."""
    out, _, _ = proyecto_cen
    tables_dir = out / "cen_vs_sap_v1_borrador.SemanticModel/definition/tables"
    for tabla in (
        "DimCliente", "DimMaterial", "DimDistrito", "Devoluciones",
        "FactNivelServicio", "matched", "no_cruzados",
    ):
        tmdl = (tables_dir / f"{tabla}.tmdl").read_text(encoding="utf-8")
        assert "Table.FromRows(Json.Document(Binary.Decompress(" in tmdl
        assert "RutaDatos" not in tmdl
    # sin tablas CSV no se emite el archivo de parametros
    assert not (
        out / "cen_vs_sap_v1_borrador.SemanticModel/definition/expressions.tmdl"
    ).exists()


def test_particion_inline_decodifica_los_datos_exactos(proyecto_extendido):
    """El base64 embebido debe decodificar EXACTAMENTE a las filas del
    breakdown (mismo patron Enter Data que usa Desktop)."""
    import base64
    import re
    import zlib

    tmdl = (
        proyecto_extendido
        / "mini_pbip.SemanticModel/definition/tables/por_grupo.tmdl"
    ).read_text(encoding="utf-8")
    m = re.search(r'Binary\.FromText\("([A-Za-z0-9+/=]+)"', tmdl)
    assert m, "la particion de por_grupo debe ir embebida"
    rows = json.loads(zlib.decompress(base64.b64decode(m.group(1)), -15))
    assert rows == [["A", 100.0, 95.0], ["B", 200.0, 105.0]]


def test_pagina_ejecutiva_enriquecida(proyecto_cen):
    """La pagina con cards KPI gana: card 'Pedidos completos (%)', card del
    excedente sin pedido y barras 100% apiladas por nivel de servicio."""
    out, _, _ = proyecto_cen
    report = json.loads(
        (out / "cen_vs_sap_v1_borrador.Report/report.json").read_text(encoding="utf-8")
    )
    exec_page = report["sections"][0]
    configs = [json.loads(vc["config"]) for vc in exec_page["visualContainers"]]

    tipos = [c["singleVisual"]["visualType"] for c in configs]
    assert "hundredPercentStackedColumnChart" in tipos

    cards = [c for c in configs if c["singleVisual"]["visualType"] == "card"]
    refs = {
        c["singleVisual"]["projections"]["Values"][0]["queryRef"] for c in cards
    }
    assert "FactNivelServicio.Pedidos completos (%)" in refs
    assert "FactNivelServicio.Unidades entregadas sin pedido" in refs

    stacked = next(
        c for c in configs
        if c["singleVisual"]["visualType"] == "hundredPercentStackedColumnChart"
    )
    proj = stacked["singleVisual"]["projections"]
    assert proj["Series"][0]["queryRef"] == "FactNivelServicio.estado_entrega"


def test_donut_y_stacked_con_colores_semaforo(proyecto_cen):
    out, _, _ = proyecto_cen
    report = json.loads(
        (out / "cen_vs_sap_v1_borrador.Report/report.json").read_text(encoding="utf-8")
    )
    configs = [
        json.loads(vc["config"])
        for s in report["sections"]
        for vc in s["visualContainers"]
    ]
    donut = next(
        c for c in configs if c["singleVisual"]["visualType"] == "donutChart"
    )
    points = donut["singleVisual"]["objects"]["dataPoint"]
    colores = {}
    for p in points:
        clase = p["selector"]["data"][0]["scopeId"]["Comparison"]["Right"]["Literal"]["Value"]
        color = p["properties"]["fill"]["solid"]["color"]["expr"]["Literal"]["Value"]
        colores[clase.strip("'")] = color.strip("'")
    assert colores["Completo"] == "#63BE7B"
    assert colores["Parcial"] == "#FFEB84"
    assert colores["No entregado"] == "#F8696B"
    assert colores["Sin pedido CEN"] == "#9BA3AF"
    # data labels visibles
    assert donut["singleVisual"]["objects"]["labels"][0]["properties"]["show"]


def test_slicers_por_pagina_cen(proyecto_cen):
    out, _, _ = proyecto_cen
    report = json.loads(
        (out / "cen_vs_sap_v1_borrador.Report/report.json").read_text(encoding="utf-8")
    )
    exec_page = report["sections"][0]
    configs = [json.loads(vc["config"]) for vc in exec_page["visualContainers"]]
    slicers = [c for c in configs if c["singleVisual"]["visualType"] == "slicer"]
    assert slicers, "la pagina ejecutiva debe tener slicers"
    columnas = {
        s["singleVisual"]["projections"]["Values"][0]["queryRef"].split(".")[-1]
        for s in slicers
    }
    assert columnas <= {"estado_entrega", "distrito", "cliente"}
    assert "distrito" in columnas


def test_slicers_no_quedan_pequenos_en_paginas_drill(proyecto_cen):
    out, _, _ = proyecto_cen
    report = json.loads(
        (out / "cen_vs_sap_v1_borrador.Report/report.json").read_text(encoding="utf-8")
    )
    logistica = next(s for s in report["sections"] if s["displayName"] == "Logistica")
    slicers = [
        vc for vc in logistica["visualContainers"]
        if json.loads(vc["config"])["singleVisual"]["visualType"] == "slicer"
    ]
    assert len(slicers) >= 2
    for s in slicers:
        pos = json.loads(s["config"])["layouts"][0]["position"]
        assert pos["width"] >= 350
        assert pos["height"] >= 68


def test_motivo_devolucion_sin_blank_en_salidas(proyecto_cen):
    out, _, _ = proyecto_cen
    devol = pd.read_csv(out / "data" / "Devoluciones.csv")
    vals = set(devol["motivo_devolucion"].astype("string").str.strip())
    assert "(Blank)" not in vals
    assert "blank" not in {v.lower() for v in vals}


def test_campos_con_display_name_legible(proyecto_cen):
    out, _, _ = proyecto_cen
    report = json.loads(
        (out / "cen_vs_sap_v1_borrador.Report/report.json").read_text(encoding="utf-8")
    )
    configs = [
        json.loads(vc["config"])
        for s in report["sections"]
        for vc in s["visualContainers"]
    ]
    barras = next(
        c for c in configs if c["singleVisual"]["visualType"] == "clusteredBarChart"
    )
    props = barras["singleVisual"]["columnProperties"]
    ref, meta = next(iter(props.items()))
    assert "displayName" in meta
    assert "_" not in meta["displayName"], "los ejes llevan nombre legible"


def test_paginas_drill_tienen_boton_volver(proyecto_cen):
    out, _, _ = proyecto_cen
    report = json.loads(
        (out / "cen_vs_sap_v1_borrador.Report/report.json").read_text(encoding="utf-8")
    )
    for section in report["sections"][1:]:
        configs = [json.loads(vc["config"]) for vc in section["visualContainers"]]
        textos = []
        for c in configs:
            if c["singleVisual"]["visualType"] != "textbox":
                continue
            props = c["singleVisual"].get("objects", {}).get("general", [])
            if not props:
                continue
            paragraphs = props[0]["properties"].get("paragraphs", [])
            if not paragraphs:
                continue
            textos.append(paragraphs[0]["textRuns"][0]["value"])
        assert any("Volver a Nivel de servicio" in t for t in textos), (
            f"pagina '{section['displayName']}' sin boton volver"
        )


def test_readme_empieza_con_como_abrir(proyecto_cen):
    out, _, _ = proyecto_cen
    texto = (out / "README.md").read_text(encoding="utf-8")
    assert "## Como abrir (2 pasos)" in texto
    assert "Doble clic" in texto
    assert "Actualizar" in texto
    # Portable: datos embebidos, sin paso de configuracion de ruta.
    assert "embebida en el modelo" in texto
    assert "sin configurar rutas" in texto


def test_pre_corte_v1_genera_proyecto_valido(tmp_path):
    raw = (PROFILES_DIR / "pre_corte_v1.json").read_text(encoding="utf-8")
    profile = MatchProfile.from_json(raw)
    matched = pd.DataFrame({
        "_k0": [30018, 30049],
        "referencia": ["HUEVO AA X30", "HUEVO AAA X30"],
        "notificado_unidades": [1000.0, 2000.0],
        "real_unidades_flash": [980.0, 2050.0],
        "delta_unidades": [-20.0, 50.0],
        "cumplimiento_pct": [98.0, 102.5],
    })
    result = GenericMatchResult(
        profile_id="pre_corte_v1",
        profile_version=1,
        parameters={"fecha_produccion": "2026-02-14"},
        matched=matched,
        solo_left=pd.DataFrame(columns=["_k0"]),
        solo_right=pd.DataFrame(columns=["_k0"]),
        no_cruzados=pd.DataFrame(columns=["origen", "key", "motivo"]),
        kpis={
            "cumplimiento_global_pct": 101.0,
            "plan_total_unidades": 3000.0,
            "real_total_unidades": 3030.0,
            "materiales_cruzados": 2,
        },
    )
    out = tmp_path / "pre_corte"
    pbip = render_pbip(profile, result, out)
    assert pbip.name == "pre_corte_v1.pbip"

    tmdl = (
        out / "pre_corte_v1.SemanticModel/definition/tables/matched.tmdl"
    ).read_text(encoding="utf-8")
    # profile.report.powerbi es None: pagina default con 4 cards + tabla.
    configs = _visual_configs(out, name="pre_corte_v1")
    cards = [c for c in configs if c["singleVisual"]["visualType"] == "card"]
    assert len(cards) == 4
    assert "DIVIDE(SUM(matched[real_unidades_flash])" in tmdl
