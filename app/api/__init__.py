"""Capa API FastAPI: expone las funciones del core como endpoints HTTP.

Cada endpoint del pipeline es atomico e invocable individualmente por un
orquestador externo (Power Automate, n8n, o scripts) manteniendo el estado
del run en la tabla SQLite `runs`.
"""
