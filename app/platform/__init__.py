"""Plataforma generica de cruces de datos.

Capa nueva (proyecto multiagente) que generaliza el pipeline PRE CORTE vs
FLASH a cualquier proceso de cruce descrito por un MatchProfile:

- profile.py: contrato Pydantic del MatchProfile (lo que los agentes LLM
  proponen y el humano aprueba).
- loader.py: ConfigurableLoader que interpreta el loader spec de cada
  fuente (deterministico, sin LLM).
- engine.py: motor de cruce generico (outer join + particiones + KPIs
  declarativos + contabilidad cero-perdida).
- store.py: persistencia SQLite de profiles y corridas genericas.
"""
