@echo off
REM Levanta el backend FastAPI en http://127.0.0.1:8000
REM Swagger UI: http://127.0.0.1:8000/docs
REM
REM Requiere que el venv este creado y las dependencias instaladas:
REM   python -m venv venv
REM   venv\Scripts\pip install -r requirements.txt

setlocal
cd /d "%~dp0"

if not exist venv\Scripts\python.exe (
    echo [ERROR] No existe venv. Ejecuta primero:
    echo   python -m venv venv
    echo   venv\Scripts\pip install -r requirements.txt
    exit /b 1
)

echo Levantando FastAPI en http://127.0.0.1:8000 (Ctrl+C para detener)
venv\Scripts\python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000 --reload
