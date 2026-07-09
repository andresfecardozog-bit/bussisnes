"""Orquestador de la propuesta de MatchProfile.

Flujo (maquina de estados simple, sin framework de grafos):

    propose_profile:
        probe(left) + probe(right)          [deterministico]
        -> SchemaScout(left), SchemaScout(right)
        -> MappingArchitect(scouts + brief + memoria)
        -> KpiDesigner(mapping + brief + memoria)
        -> ReportDesigner(profile parcial)
        -> ensamblar MatchProfile (status=proposed)
        -> persistir preguntas nuevas (dedup contra respondidas)

    answer_question: respuesta humana -> memoria del proceso
    refine_profile:  re-corre mapping/kpis con la memoria actualizada
    approve:         rechaza si hay preguntas bloqueantes abiertas

La memoria por proceso (profile_knowledge + profile_questions) garantiza
que preguntas ya respondidas no se repitan: se inyectan al prompt como
"CONTEXTO DEL PROCESO" y ademas se deduplican al persistir.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from sqlite3 import Connection
from typing import Any

from app.agents.crew import Crew
from app.agents.file_probe import probe_file, probe_to_prompt
from app.agents.schemas import (
    KpiProposal,
    MappingProposal,
    OpenQuestion,
    ProposalStatus,
    ReportProposal,
    SchemaScoutOutput,
)
from app.platform.profile import (
    AggFn,
    Aggregation,
    GroupByAggregate,
    KpiSpec,
    MatchProfile,
    SourceSpec,
)
from app.platform.static_check import check_profile_references
from app.platform.store import init_platform_schema, save_profile


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_question(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


# ---------------------------------------------------------------------------
# Memoria del proceso
# ---------------------------------------------------------------------------

def add_knowledge(
    conn: Connection, profile_id: str, kind: str, autor: str, contenido: str
) -> int:
    init_platform_schema(conn)
    cur = conn.execute(
        """
        INSERT INTO profile_knowledge (profile_id, kind, autor, contenido)
        VALUES (?, ?, ?, ?)
        """,
        (profile_id, kind, autor, contenido),
    )
    conn.commit()
    return int(cur.lastrowid)


def get_knowledge_context(conn: Connection, profile_id: str) -> str:
    """Contexto acumulado del proceso para inyectar a los agentes."""
    init_platform_schema(conn)
    rows = conn.execute(
        """
        SELECT kind, autor, contenido FROM profile_knowledge
        WHERE profile_id = ? ORDER BY id
        """,
        (profile_id,),
    ).fetchall()
    qa_rows = conn.execute(
        """
        SELECT pregunta, respuesta FROM profile_questions
        WHERE profile_id = ? AND estado = 'respondida'
        ORDER BY id
        """,
        (profile_id,),
    ).fetchall()
    if not rows and not qa_rows:
        return "(sin contexto previo)"
    lines = ["CONTEXTO DEL PROCESO (memoria acumulada, NO repreguntar lo respondido):"]
    for r in rows:
        lines.append(f"- [{r['kind']}] ({r['autor']}) {r['contenido']}")
    for r in qa_rows:
        lines.append(f"- [pregunta respondida] P: {r['pregunta']} R: {r['respuesta']}")
    return "\n".join(lines)


def _answered_questions_normalized(conn: Connection, profile_id: str) -> set[str]:
    rows = conn.execute(
        """
        SELECT pregunta FROM profile_questions
        WHERE profile_id = ? AND estado IN ('respondida', 'asumida')
        """,
        (profile_id,),
    ).fetchall()
    return {_normalize_question(r["pregunta"]) for r in rows}


def _open_questions_normalized(conn: Connection, profile_id: str) -> set[str]:
    rows = conn.execute(
        "SELECT pregunta FROM profile_questions WHERE profile_id = ? AND estado = 'abierta'",
        (profile_id,),
    ).fetchall()
    return {_normalize_question(r["pregunta"]) for r in rows}


def persist_questions(
    conn: Connection, profile_id: str, questions: list[OpenQuestion]
) -> int:
    """Guarda preguntas nuevas; deduplica contra respondidas/asumidas Y
    contra abiertas ya registradas. Retorna cuantas se insertaron."""
    init_platform_schema(conn)
    ya = _answered_questions_normalized(conn, profile_id) | _open_questions_normalized(
        conn, profile_id
    )
    inserted = 0
    for q in questions:
        if _normalize_question(q.pregunta) in ya:
            continue
        conn.execute(
            """
            INSERT INTO profile_questions
                (profile_id, agente, sobre, pregunta, hipotesis, impacto, bloqueante)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile_id,
                q.agente,
                q.sobre,
                q.pregunta,
                q.hipotesis,
                q.impacto,
                1 if q.bloqueante else 0,
            ),
        )
        ya.add(_normalize_question(q.pregunta))
        inserted += 1
    conn.commit()
    return inserted


def list_questions(
    conn: Connection, profile_id: str, estado: str | None = None
) -> list[dict[str, Any]]:
    init_platform_schema(conn)
    if estado:
        rows = conn.execute(
            "SELECT * FROM profile_questions WHERE profile_id = ? AND estado = ? ORDER BY id",
            (profile_id, estado),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM profile_questions WHERE profile_id = ? ORDER BY id",
            (profile_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def answer_question(
    conn: Connection, question_id: int, respuesta: str, autor: str = "usuario"
) -> None:
    init_platform_schema(conn)
    row = conn.execute(
        "SELECT profile_id, pregunta FROM profile_questions WHERE id = ?",
        (question_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"Pregunta {question_id} no existe")
    conn.execute(
        """
        UPDATE profile_questions
        SET estado = 'respondida', respuesta = ?, respondido_en = ?
        WHERE id = ?
        """,
        (respuesta, _now(), question_id),
    )
    conn.commit()
    add_knowledge(
        conn,
        row["profile_id"],
        "qa",
        autor,
        f"P: {row['pregunta']} R: {respuesta}",
    )


def assume_question(conn: Connection, question_id: int) -> None:
    """Marca una pregunta NO bloqueante como asumida (se usa la hipotesis)."""
    init_platform_schema(conn)
    row = conn.execute(
        "SELECT bloqueante FROM profile_questions WHERE id = ?", (question_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"Pregunta {question_id} no existe")
    if row["bloqueante"]:
        raise ValueError("Una pregunta bloqueante no puede asumirse: requiere respuesta")
    conn.execute(
        "UPDATE profile_questions SET estado = 'asumida', respondido_en = ? WHERE id = ?",
        (_now(), question_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Propuesta
# ---------------------------------------------------------------------------

def _complete_parameters(mapping: MappingProposal) -> list:
    """Los transforms pueden referenciar "$param"; si el agente olvido
    declararlo, se auto-declara (type str) para no tumbar la propuesta.
    El humano ve la lista de parametros al aprobar."""
    from app.platform.profile import FilterEquals, FilterNotEquals, ParameterSpec

    declared = {p.name: p for p in mapping.parameters}
    for side in (mapping.left, mapping.right):
        for t in side.transforms:
            if isinstance(t, (FilterEquals, FilterNotEquals)):
                if isinstance(t.value, str) and t.value.startswith("$"):
                    name = t.value[1:]
                    if name not in declared:
                        declared[name] = ParameterSpec(
                            name=name,
                            type="str",
                            description="auto-declarado por el orquestador; revisar tipo",
                        )
    return list(declared.values())


def _sanitize_referencias(profile: MatchProfile, errors: list[str]) -> tuple[MatchProfile, list[str]]:
    """Ultimo recurso cuando el repair con el agente no converge: recorta
    las piezas con referencias rotas para que el profile guardado sea
    ejecutable. Cada recorte queda reportado para el humano."""
    recortes: list[str] = []
    err_text = "\n".join(errors)

    computed = [
        c for c in profile.computed
        if f"computed '{c.name}'" not in err_text
    ]
    if len(computed) != len(profile.computed):
        recortes.append("computed con columnas inexistentes eliminadas")

    kpis = [k for k in profile.kpis if f"kpi '{k.id}'" not in err_text]
    if not kpis:
        kpis = [KpiSpec(id="lineas_cruzadas", label="Lineas cruzadas", op="count")]
        recortes.append("todos los KPIs invalidos; se dejo el conteo basico")
    elif len(kpis) != len(profile.kpis):
        recortes.append("KPIs con columnas inexistentes eliminados")

    service_level = profile.service_level
    if service_level and "service_level:" in err_text:
        service_level = None
        recortes.append("service_level eliminado por referencias rotas")

    breakdowns = [
        b for b in profile.breakdowns if f"breakdown '{b.id}'" not in err_text
    ]
    if len(breakdowns) != len(profile.breakdowns):
        recortes.append("breakdowns con referencias rotas eliminados")

    data_model = profile.data_model
    if data_model:
        dims_ok = [
            d for d in data_model.dimensions
            if f"dimension '{d.name}'" not in err_text
        ]
        if len(dims_ok) != len(data_model.dimensions):
            data_model = data_model.model_copy(update={"dimensions": dims_ok})
            recortes.append("dimensiones del data_model con referencias rotas eliminadas")

    nuevo = profile.model_copy(
        update={
            "computed": computed,
            "kpis": kpis,
            "service_level": service_level,
            "breakdowns": breakdowns,
            "data_model": data_model,
            "report": _sanitize_report(profile.report, breakdowns),
        }
    )
    return nuevo, recortes


def _sanitize_report(report_spec, breakdowns) -> Any:
    """Los agentes trabajan por separado: el ReportDesigner puede
    referenciar breakdowns que el KpiDesigner no declaro. En vez de tumbar
    la propuesta completa, se descartan las hojas huerfanas (el humano ve
    el resto y puede pedir refine)."""
    if report_spec is None or report_spec.excel is None:
        return report_spec
    bd_ids = {b.id for b in breakdowns}
    sheets_ok = [
        s
        for s in report_spec.excel.sheets
        if s.kind != "breakdown" or s.breakdown_id in bd_ids
    ]
    if not sheets_ok:
        return report_spec.model_copy(update={"excel": None})
    if len(sheets_ok) != len(report_spec.excel.sheets):
        excel = report_spec.excel.model_copy(update={"sheets": sheets_ok})
        return report_spec.model_copy(update={"excel": excel})
    return report_spec


def _auto_fix_grano(
    profile: MatchProfile,
    left_path: str | Path,
    right_path: str | Path,
    parameters: dict[str, Any] | None = None,
) -> tuple[MatchProfile, list[str]]:
    """Corrige group_by_aggregate mal planteado (by con columnas extra que
    dejan keys de join repetidas). Patron correcto: by = solo join keys;
    descriptivas con fn=first; cantidades con fn=sum."""
    import pandas as pd

    from app.platform.engine import prepare_source

    parameters = parameters or {}
    fixes: list[str] = []

    def _fix_source(
        source: SourceSpec, path: str | Path, key_cols: list[str], lado: str
    ) -> SourceSpec:
        try:
            df, _ = prepare_source(path, source, parameters)
        except Exception:
            return source
        missing = [c for c in key_cols if c not in df.columns]
        if missing or not df.duplicated(subset=key_cols).any():
            return source

        non_group = [t for t in source.transforms if t.op != "group_by_aggregate"]
        old_groups = [
            t for t in source.transforms if t.op == "group_by_aggregate"
        ]
        pre, _ = prepare_source(
            path, source.model_copy(update={"transforms": non_group}), parameters
        )
        aggs: list[Aggregation] = []
        if old_groups:
            aggs = list(old_groups[-1].aggregations)
        else:
            for col in pre.columns:
                if col in key_cols:
                    continue
                if pd.api.types.is_numeric_dtype(pre[col]):
                    aggs.append(Aggregation(target=col, source=col, fn=AggFn.SUM))
                else:
                    aggs.append(Aggregation(target=col, source=col, fn=AggFn.FIRST))
        if not aggs:
            aggs = [Aggregation(target="_filas", source=key_cols[0], fn=AggFn.COUNT)]

        nuevo = source.model_copy(
            update={
                "transforms": non_group
                + [GroupByAggregate(by=key_cols, aggregations=aggs)]
            }
        )
        fixes.append(
            f"grano de fuente {lado}: group_by corregido a solo join keys "
            f"({', '.join(key_cols)})"
        )
        return nuevo

    left_keys = [k.left for k in profile.join.keys]
    right_keys = [k.right for k in profile.join.keys]
    new_left = _fix_source(profile.left, left_path, left_keys, "izquierda")
    new_right = _fix_source(profile.right, right_path, right_keys, "derecha")
    if new_left is profile.left and new_right is profile.right:
        return profile, fixes
    return profile.model_copy(update={"left": new_left, "right": new_right}), fixes


def _full_generate_dry_run(
    profile: MatchProfile,
    left_path: str | Path,
    right_path: str | Path,
    parameters: dict[str, Any] | None = None,
) -> str | None:
    """Ejecuta el pipeline COMPLETO de generate (cruce + render Excel +
    render PBIP) contra un directorio temporal. Retorna el error como texto
    o None si todo corre.

    Objetivo: garantizar que el flujo del usuario NUNCA vea un error en
    /generate. Todo lo que fallaria alli se detecta aqui (durante la
    propuesta/refine) y se convierte en pregunta bloqueante del Motor para
    que los agentes iteren, o se auto-corrige. Cubre no solo el motor
    (run_profile) sino tambien los renderers, que es donde aparecian los
    500 posteriores a la aprobacion."""
    import shutil
    import tempfile

    from app.platform.engine import run_profile as _run
    from app.platform.render_excel import render_excel
    from app.platform.render_pbip import render_pbip

    tmp = Path(tempfile.mkdtemp(prefix="dryrun_"))
    try:
        result = _run(
            profile, left_path=left_path, right_path=right_path,
            parameters=parameters or {},
        )
        if profile.report and profile.report.excel:
            render_excel(profile, result, tmp / "dry.xlsx")
        render_pbip(profile, result, tmp / "dry_pbip")
    except Exception as exc:  # noqa: BLE001 - se reporta como pregunta
        return f"{type(exc).__name__}: {exc}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return None


def propose_profile(
    crew: Crew,
    conn: Connection,
    profile_id: str,
    left_path: str | Path,
    right_path: str | Path,
    brief: str,
    version: int = 1,
) -> dict[str, Any]:
    """Corre el equipo completo y persiste la propuesta + preguntas.

    Retorna dict con el profile propuesto, las salidas por agente y el
    status consolidado.
    """
    init_platform_schema(conn)
    if brief.strip():
        add_knowledge(conn, profile_id, "brief", "usuario", brief.strip())
    contexto = get_knowledge_context(conn, profile_id)

    left_probe = probe_file(left_path)
    right_probe = probe_file(right_path)

    scout_left: SchemaScoutOutput = crew.run(
        crew.schema_scout,
        "SchemaScout",
        f"{contexto}\n\nBRIEF DEL USUARIO: {brief}\n\nFUENTE IZQUIERDA:\n{probe_to_prompt(left_probe)}",
        conn,
        profile_id,
    ).output
    scout_right: SchemaScoutOutput = crew.run(
        crew.schema_scout,
        "SchemaScout",
        f"{contexto}\n\nBRIEF DEL USUARIO: {brief}\n\nFUENTE DERECHA:\n{probe_to_prompt(right_probe)}",
        conn,
        profile_id,
    ).output

    mapping: MappingProposal = crew.run(
        crew.mapping_architect,
        "MappingArchitect",
        (
            f"{contexto}\n\nBRIEF DEL USUARIO: {brief}\n\n"
            f"ANALISIS FUENTE IZQUIERDA:\n{scout_left.model_dump_json(indent=2)}\n\n"
            f"SONDEO IZQUIERDA:\n{probe_to_prompt(left_probe)}\n\n"
            f"ANALISIS FUENTE DERECHA:\n{scout_right.model_dump_json(indent=2)}\n\n"
            f"SONDEO DERECHA:\n{probe_to_prompt(right_probe)}"
        ),
        conn,
        profile_id,
    ).output

    def _run_kpis(feedback: str = "") -> KpiProposal:
        return crew.run(
            crew.kpi_designer,
            "KpiDesigner",
            (
                f"{contexto}\n\nBRIEF DEL USUARIO: {brief}\n\n"
                f"MAPEO PROPUESTO:\n{mapping.model_dump_json(indent=2)}"
                + feedback
            ),
            conn,
            profile_id,
        ).output

    kpis: KpiProposal = _run_kpis()

    report: ReportProposal = crew.run(
        crew.report_designer,
        "ReportDesigner",
        (
            f"{contexto}\n\nBRIEF DEL USUARIO: {brief}\n\n"
            f"MAPEO:\n{mapping.model_dump_json(indent=2)}\n\n"
            f"KPIS:\n{kpis.model_dump_json(indent=2)}"
        ),
        conn,
        profile_id,
    ).output

    def _armar(k: KpiProposal) -> MatchProfile:
        return MatchProfile(
            profile_id=profile_id,
            version=version,
            descripcion=brief.strip()[:500],
            parameters=_complete_parameters(mapping),
            left=mapping.left,
            right=mapping.right,
            join=mapping.join,
            computed=k.computed,
            kpis=k.kpis,
            service_level=k.service_level,
            breakdowns=k.breakdowns,
            data_model=report.data_model,
            report=_sanitize_report(report.report, k.breakdowns),
        )

    profile = _armar(kpis)

    # Chequeo estatico de referencias: si el KpiDesigner invento columnas
    # que no existen tras el join, se le devuelve el error UNA vez para
    # que corrija; si persiste, se recortan las piezas rotas (el humano
    # ve el recorte en el chat).
    errores = check_profile_references(profile)
    if errores:
        kpis = _run_kpis(
            "\n\nERRORES DE REFERENCIA en tu propuesta anterior (columnas "
            "que NO existen tras el join; corrige usando SOLO columnas del "
            "mapeo):\n- " + "\n- ".join(errores)
        )
        profile = _armar(kpis)
        errores = check_profile_references(profile)
    if errores:
        profile, recortes = _sanitize_referencias(profile, errores)
        if recortes:
            add_knowledge(
                conn, profile_id, "nota", "sistema",
                "Recortes automaticos por referencias rotas: " + "; ".join(recortes),
            )

    profile, grano_fixes = _auto_fix_grano(profile, left_path, right_path)
    if grano_fixes:
        add_knowledge(
            conn, profile_id, "nota", "sistema",
            "Correccion automatica de grano: " + "; ".join(grano_fixes),
        )

    # Dry-run del generate COMPLETO (cruce + render Excel + render PBIP)
    # antes de guardar. Cualquier error que apareceria en /generate tras la
    # aprobacion se detecta aqui y se vuelve pregunta bloqueante del Motor +
    # memoria, para que los agentes iteren con 'refine'. Asi el flujo del
    # usuario nunca ve un 422/500 en generate.
    smoke_error: str | None = None
    requiere_params = any(p.required for p in profile.parameters)
    if not requiere_params:
        smoke_error = _full_generate_dry_run(profile, left_path, right_path)

    save_profile(conn, profile, status="proposed")

    if smoke_error:
        add_knowledge(
            conn, profile_id, "nota", "sistema",
            f"SMOKE RUN FALLIDO (v{version}): {smoke_error}",
        )
        persist_questions(
            conn,
            profile_id,
            [
                OpenQuestion(
                    agente="Motor",
                    sobre="ejecucion de prueba del cruce",
                    pregunta=(
                        "Ejecute el cruce de prueba con la configuracion "
                        f"propuesta y fallo con: {smoke_error[:400]}. Como "
                        "debe manejarse este caso? (al responder, pedir una "
                        "nueva propuesta con 'refine' para que los agentes "
                        "corrijan la configuracion)"
                    ),
                    hipotesis="La configuracion necesita ajuste (ej. agrupar o filtrar filas problematicas).",
                    impacto="El cruce no puede ejecutarse hasta corregirlo.",
                    bloqueante=True,
                )
            ],
        )

    todas_las_preguntas = (
        scout_left.open_questions
        + scout_right.open_questions
        + mapping.open_questions
        + kpis.open_questions
        + report.open_questions
    )
    nuevas = persist_questions(conn, profile_id, todas_las_preguntas)

    status = proposal_status(conn, profile_id, crew_confianzas=[
        scout_left.confianza, scout_right.confianza,
        mapping.confianza, kpis.confianza, report.confianza,
    ])

    return {
        "profile": profile,
        "scout_left": scout_left,
        "scout_right": scout_right,
        "mapping": mapping,
        "kpis": kpis,
        "report": report,
        "preguntas_nuevas": nuevas,
        "status": status,
    }


def proposal_status(
    conn: Connection, profile_id: str, crew_confianzas: list[float] | None = None
) -> ProposalStatus:
    init_platform_schema(conn)
    abiertas = conn.execute(
        "SELECT COUNT(*) AS n FROM profile_questions WHERE profile_id = ? AND estado = 'abierta'",
        (profile_id,),
    ).fetchone()["n"]
    bloqueantes = conn.execute(
        """
        SELECT COUNT(*) AS n FROM profile_questions
        WHERE profile_id = ? AND estado = 'abierta' AND bloqueante = 1
        """,
        (profile_id,),
    ).fetchone()["n"]
    confianza = (
        round(sum(crew_confianzas) / len(crew_confianzas), 3)
        if crew_confianzas
        else 0.0
    )
    return ProposalStatus(
        profile_id=profile_id,
        listo_para_aprobar=bloqueantes == 0,
        preguntas_abiertas=int(abiertas),
        preguntas_bloqueantes=int(bloqueantes),
        confianza_global=confianza,
    )


def approve_proposed_profile(
    conn: Connection, profile_id: str, version: int, aprobado_por: str
) -> None:
    """Aprueba el profile SOLO si no hay preguntas bloqueantes abiertas.
    Compromiso duro de rubrica.md: cero supuestos bloqueantes sin confirmar."""
    status = proposal_status(conn, profile_id)
    if status.preguntas_bloqueantes > 0:
        raise ValueError(
            f"No se puede aprobar '{profile_id}': {status.preguntas_bloqueantes} "
            "pregunta(s) bloqueante(s) sin responder. Responder primero en el chat."
        )
    from app.platform.store import approve_profile

    approve_profile(conn, profile_id, version, aprobado_por)
