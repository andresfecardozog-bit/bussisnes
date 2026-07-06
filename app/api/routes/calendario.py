"""Endpoints del calendario laboral colombiano (Fase 6.6).

Frontend los usa para:
- Mostrar el mini-calendario del wizard resaltando dias no laborales.
- Explicar por que un PRE CORTE apunto a X+N en vez de X+1.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.api.schemas import CalendarioAnoResponse, DiaNoLaboral
from app.core.calendario import (
    CalendarioSinCobertura,
    COBERTURA_DESDE,
    COBERTURA_HASTA,
    festivos_del_ano,
)

router = APIRouter(prefix="/calendario", tags=["calendario"])


@router.get("/no-laborales", response_model=CalendarioAnoResponse)
def get_no_laborales(
    year: int = Query(..., description="Anio a consultar (dentro del rango cubierto)"),
) -> CalendarioAnoResponse:
    """Lista los festivos oficiales colombianos del anio (sin domingos puros).

    Excluye "Rosario de Chiquinquira" (no es festivo laboral segun Ley 51/1983).
    """
    try:
        fest = festivos_del_ano(year)
    except CalendarioSinCobertura as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return CalendarioAnoResponse(
        year=year,
        festivos=[DiaNoLaboral(**f) for f in fest],
        cobertura_desde=COBERTURA_DESDE.isoformat() if COBERTURA_DESDE else "",
        cobertura_hasta=COBERTURA_HASTA.isoformat() if COBERTURA_HASTA else "",
    )
