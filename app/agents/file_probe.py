"""Sondeo deterministico de archivos: el insumo que reciben los agentes.

Extrae metadatos + muestras de cada fuente SIN LLM. Esto es lo que viaja
a Gemini (no el archivo completo): hojas, dimensiones, headers candidatos,
tipos inferidos, estadisticas de repeticion de valores (senal de grano) y
filas de muestra.

El probe es tolerante: nunca lanza por contenido raro; reporta lo que ve.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field

from app.core.loaders import hash_file
from app.platform.loader import _open_workbook, detect_real_format

_SAMPLE_ROWS = 8
_MAX_COLS = 80
_KEY_CANDIDATE_SCAN_ROWS = 3000


class ColumnProbe(BaseModel):
    position: int = Field(description="1-based")
    header: str | None = None
    inferred_type: str = "unknown"
    non_null_pct: float = 0.0
    distinct_ratio: float | None = Field(
        default=None,
        description="valores distintos / filas no nulas; 1.0 = unico por fila",
    )
    samples: list[str] = Field(default_factory=list)


class SheetProbe(BaseModel):
    name: str
    num_rows: int
    num_cols: int
    has_header_row: bool
    header_row_index: int | None = None
    columns: list[ColumnProbe] = Field(default_factory=list)
    sample_rows: list[list[str | None]] = Field(default_factory=list)
    looks_like_pivot_or_summary: bool = False
    anomalies: list[str] = Field(
        default_factory=list,
        description=(
            "Hallazgos deterministicos que el agente debe investigar/preguntar: "
            "cruces de columnas muy vacias contra columnas de contexto, sumas "
            "sospechosas, etc."
        ),
    )


class FileProbe(BaseModel):
    filename: str
    hash_sha256: str
    declared_extension: str
    real_format: str
    sheets: list[SheetProbe] = Field(default_factory=list)
    notas: list[str] = Field(default_factory=list)


def _infer_type(values: list[Any]) -> str:
    non_null = [v for v in values if v is not None and str(v).strip() != ""]
    if not non_null:
        return "empty"
    numeric = 0
    dateish = 0
    for v in non_null:
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            numeric += 1
        elif hasattr(v, "isoformat"):
            dateish += 1
        else:
            s = str(v).strip().replace(",", "").replace(".", "", 1).lstrip("-")
            if s.isdigit():
                numeric += 1
    n = len(non_null)
    if dateish / n > 0.8:
        return "date"
    if numeric / n > 0.8:
        return "numeric"
    return "text"


def _is_blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip()) or str(v).strip() == ""


def _detect_anomalies(
    data_rows: list[tuple[Any, ...]],
    columns: list[ColumnProbe],
    num_cols: int,
) -> list[str]:
    """Investigacion deterministica de anomalias (lo que un analista haria a
    mano): cuando una columna identificadora esta parcialmente vacia, cruza
    las filas vacias contra la mejor columna categorica de contexto para
    explicar POR QUE estan vacias. El agente usa esto para preguntar con
    evidencia, sin que el LLM calcule nada.
    """
    if not data_rows:
        return []
    total = len(data_rows)

    # Columnas categoricas de contexto: texto, muy pobladas, pocas categorias.
    categoricas: list[int] = []
    for col in columns:
        if (
            col.inferred_type == "text"
            and col.non_null_pct >= 80
            and col.distinct_ratio is not None
            and col.distinct_ratio <= 0.2
        ):
            categoricas.append(col.position - 1)

    # Columnas identificadoras parcialmente vacias: candidatas a key que
    # deberian venir llenas pero no lo estan (senal de subconjunto distinto).
    sospechosas: list[ColumnProbe] = [
        col
        for col in columns
        if 3.0 <= col.non_null_pct <= 85.0
        and (col.distinct_ratio is None or col.distinct_ratio >= 0.3)
        and col.inferred_type in ("text", "numeric")
    ]
    sospechosas.sort(key=lambda c: c.non_null_pct)

    anomalies: list[str] = []
    for col in sospechosas[:2]:
        idx = col.position - 1
        blank_rows = [r for r in data_rows if idx >= len(r) or _is_blank(r[idx])]
        if not blank_rows:
            continue
        pct_blank = round(len(blank_rows) / total * 100, 1)
        etiqueta = col.header or f"col {col.position}"

        # El motor NO elige "la mejor" explicacion (eso es juicio analitico
        # del agente): entrega los cruces contra varias columnas categoricas
        # de negocio (pocas categorias: canal, tipo, segmento) para que
        # SchemaScout interprete cual explica el patron y pregunte.
        cruces: list[str] = []
        for ctx_idx in categoricas:
            if ctx_idx == idx:
                continue
            conteo: Counter = Counter()
            for r in blank_rows:
                val = r[ctx_idx] if ctx_idx < len(r) else None
                if not _is_blank(val):
                    conteo[str(val).strip()] += 1
            if not (2 <= len(conteo) <= 15):
                continue  # constante o alta cardinalidad: no es categorica util
            ctx_header = columns[ctx_idx].header or f"col {ctx_idx + 1}"
            top = conteo.most_common(3)
            detalle = ", ".join(
                f"{k} ({round(v / len(blank_rows) * 100)}%)" for k, v in top
            )
            cruces.append(f"'{ctx_header}': {detalle}")
            if len(cruces) >= 3:
                break

        if cruces:
            texto = (
                f"Columna '{etiqueta}' vacia en {pct_blank}% de las filas. "
                f"Entre las filas vacias, columnas de contexto se distribuyen "
                f"asi -> {' | '.join(cruces)}. Puede ser un subconjunto "
                f"legitimo (otro canal/origen/linea de negocio), no un error: "
                f"INTERPRETAR y PREGUNTAR que significan esas filas."
            )
        else:
            texto = (
                f"Columna '{etiqueta}' vacia en {pct_blank}% de las filas. "
                f"Investigar/preguntar por que estan vacias."
            )
        anomalies.append(texto)
    return anomalies


def _probe_sheet(name: str, rows: list[tuple[Any, ...]]) -> SheetProbe:
    if not rows:
        return SheetProbe(
            name=name, num_rows=0, num_cols=0, has_header_row=False
        )

    num_cols = min(max(len(r) for r in rows), _MAX_COLS)

    # Header: fila de mayoria texto cuyos valores NO se repiten en las
    # filas de datos de la misma columna. Una fila de datos (ej. la fila 2
    # del SAP, que va tras una fila 1 vacia) repite sus valores hacia
    # abajo (codigos de canal, nombres de regional); un header real no.
    header_idx: int | None = None
    for i, row in enumerate(rows[:5]):
        cells = [v for v in row[:num_cols] if v is not None and str(v).strip()]
        textos = [v for v in cells if isinstance(v, str)]
        if len(cells) < 2 or len(textos) / len(cells) <= 0.8 or len(rows) <= i + 1:
            continue
        vistos = 0
        repetidos = 0
        for c in range(num_cols):
            val = row[c] if c < len(row) else None
            if val is None or not str(val).strip():
                continue
            vistos += 1
            below = {
                str(r[c]).strip()
                for r in rows[i + 1 : i + 21]
                if c < len(r) and r[c] is not None
            }
            if str(val).strip() in below:
                repetidos += 1
        if vistos and repetidos / vistos > 0.3:
            continue  # sus valores reaparecen abajo: es data, no header
        header_idx = i
        break

    data_start = (header_idx + 1) if header_idx is not None else 0
    data_rows = rows[data_start : data_start + _KEY_CANDIDATE_SCAN_ROWS]

    columns: list[ColumnProbe] = []
    for c in range(num_cols):
        col_values = [r[c] if c < len(r) else None for r in data_rows]
        non_null = [v for v in col_values if v is not None and str(v).strip() != ""]
        distinct_ratio = None
        if non_null:
            distinct_ratio = round(len(set(map(str, non_null))) / len(non_null), 4)
        header_val = None
        if header_idx is not None and c < len(rows[header_idx]):
            hv = rows[header_idx][c]
            header_val = str(hv).strip() if hv is not None else None
        columns.append(
            ColumnProbe(
                position=c + 1,
                header=header_val,
                inferred_type=_infer_type(col_values[:200]),
                non_null_pct=round(len(non_null) / len(col_values) * 100, 1)
                if col_values
                else 0.0,
                distinct_ratio=distinct_ratio,
                samples=[str(v)[:40] for v in non_null[:4]],
            )
        )

    sample_rows = [
        [str(v)[:40] if v is not None else None for v in r[:num_cols]]
        for r in data_rows[:_SAMPLE_ROWS]
    ]

    # Heuristica de hoja pivote/resumen: muy pocas columnas con datos y
    # celdas tipo "(Todas)" / "Total".
    flat = [str(v).strip().lower() for r in rows[:10] for v in r if v is not None]
    pivot_markers = sum(1 for v in flat if v in ("(todas)", "(all)", "total general"))
    looks_pivot = pivot_markers > 0 or (num_cols <= 3 and len(rows) < 100)

    anomalies = _detect_anomalies(data_rows, columns, num_cols)

    return SheetProbe(
        name=name,
        num_rows=len(rows),
        num_cols=num_cols,
        has_header_row=header_idx is not None,
        header_row_index=(header_idx + 1) if header_idx is not None else None,
        columns=columns,
        sample_rows=sample_rows,
        looks_like_pivot_or_summary=looks_pivot,
        anomalies=anomalies,
    )


def probe_file(path: str | Path) -> FileProbe:
    path = Path(path)
    real_format = detect_real_format(path)
    notas: list[str] = []
    if real_format == "xlsx" and path.suffix.lower() not in (".xlsx", ".xlsm"):
        notas.append(
            f"extension mentirosa: '{path.suffix}' pero el contenido es xlsx"
        )

    sheets: list[SheetProbe] = []
    if real_format == "xlsx":
        wb = _open_workbook(path)
        try:
            for name in wb.sheetnames:
                ws = wb[name]
                rows = []
                for i, row in enumerate(ws.iter_rows(values_only=True)):
                    rows.append(row)
                    if i >= _KEY_CANDIDATE_SCAN_ROWS:
                        break
                # recorte de cola vacia (filas fantasma)
                while rows and all(
                    v is None or (isinstance(v, str) and not v.strip())
                    for v in rows[-1]
                ):
                    rows.pop()
                declared = ws.max_row
                if declared and declared > len(rows) * 10 and declared > 10000:
                    notas.append(
                        f"hoja '{name}' declara {declared} filas pero solo "
                        f"~{len(rows)} tienen datos (dimension inflada)"
                    )
                sheets.append(_probe_sheet(name, rows))
        finally:
            wb.close()
    elif real_format == "csv":
        raw = pd.read_csv(path, dtype=object, header=None, nrows=_KEY_CANDIDATE_SCAN_ROWS)
        rows = [tuple(r) for r in raw.itertuples(index=False, name=None)]
        sheets.append(_probe_sheet("csv", rows))
    else:
        notas.append("formato xls legacy (BIFF) no soportado; convertir a xlsx")

    return FileProbe(
        filename=path.name,
        hash_sha256=hash_file(path),
        declared_extension=path.suffix,
        real_format=real_format,
        sheets=sheets,
        notas=notas,
    )


def probe_to_prompt(probe: FileProbe) -> str:
    """Render compacto del probe para el prompt del agente."""
    lines = [f"ARCHIVO: {probe.filename} (formato real: {probe.real_format})"]
    for nota in probe.notas:
        lines.append(f"  NOTA: {nota}")
    for sh in probe.sheets:
        lines.append(
            f"  HOJA '{sh.name}': {sh.num_rows} filas x {sh.num_cols} cols, "
            f"header={'fila ' + str(sh.header_row_index) if sh.has_header_row else 'NO tiene'}"
            + (", posible pivote/resumen" if sh.looks_like_pivot_or_summary else "")
        )
        for col in sh.columns:
            if col.inferred_type == "empty":
                continue
            head = col.header or f"(col {col.position})"
            lines.append(
                f"    [{col.position}] {head}: {col.inferred_type}, "
                f"{col.non_null_pct}% con datos, distinct_ratio={col.distinct_ratio}, "
                f"ej: {col.samples}"
            )
        for a in sh.anomalies:
            lines.append(f"    ANOMALIA A INVESTIGAR: {a}")
        if sh.sample_rows:
            lines.append(f"    MUESTRA (primeras {len(sh.sample_rows)} filas de datos):")
            for r in sh.sample_rows[:5]:
                lines.append(f"      {r}")
    return "\n".join(lines)
