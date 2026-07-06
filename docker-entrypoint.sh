#!/bin/sh
# Fase 7B - Entrypoint del container FastAPI en Railway.
#
# Objetivo: arreglar permisos del volumen persistente montado en /data.
# Railway monta el volumen como root:root en runtime, y el usuario `nutri`
# no puede escribir (bloquea init_db y uploads).
#
# Solucion: entrar como root, hacer chown de /data, y drop de privilegios
# a `nutri` para ejecutar el CMD. `runuser` (util-linux) propaga SIGTERM
# correctamente para restart limpio de Railway.

set -e

# 1. Arreglar permisos del volumen si es necesario. Es idempotente y rapido.
if [ -d "/data" ]; then
    chown -R nutri:nutri /data 2>/dev/null || true
    chmod -R u+rwX /data 2>/dev/null || true
fi

# 2. Drop de privilegios y ejecutar el CMD como `nutri`. Usamos runuser en
# vez de su porque no requiere PAM y propaga SIGTERM al proceso hijo, lo
# que permite que Railway haga restart limpio de uvicorn.
#
# `"$@"` preserva cada argumento del CMD como uno separado; usar `$*` los
# concatena y rompe el paso a `sh -c` (uvicorn recibiria solo "uvicorn"
# sin "app.api.main:app --host ... --port ...").
exec runuser -u nutri -- "$@"
