# Manual de uso para analistas

Guia operativa de la plataforma de cruces (flujo generico por perfiles).

Este manual se alinea con los compromisos de `AGENTS.md` y con los gates de
`rubrica.md` (Fase 5 y Fase 6).

---

## 1) Requisitos previos

- Backend FastAPI activo.
- Frontend Angular activo.
- Archivos fuente listos (izquierda y derecha).
- Si aplica, archivo de homologacion (material/cliente/distrito).

---

## 2) Flujo recomendado (sin tocar codigo)

1. Ir a `Procesos > Nuevo proceso`.
2. Definir `profile_id` en snake_case (ejemplo: `cen_vs_sap_junio`).
3. Escribir brief en lenguaje natural (que quieres medir, como interpretar).
4. Adjuntar:
   - fuente izquierda (plan/pedido),
   - fuente derecha (real/entregado),
   - homologacion opcional.
5. Enviar a agentes.
6. Responder las preguntas del chat:
   - primero las bloqueantes,
   - luego las no bloqueantes (o usar "asumir hipotesis" cuando aplique).
7. Re-proponer si agregaste contexto.
8. Aprobar el profile.
9. Ejecutar cruce.
10. Generar entregables.
11. Descargar:
    - Excel corporativo,
    - PBIP zip.

---

## 3) Como interpretar el resultado rapido

- `Cruzadas`: filas con match entre ambas fuentes.
- `Solo izquierda`: pedidos sin entrega.
- `Solo derecha`: entregas sin pedido.
- `No cruzados`: detalle de faltantes/excedentes por origen y motivo.

Para CEN/SAP:

- `Nivel servicio unidades (%)` = entregadas (solo cruzado) / pedidas
  (cruzado + solo_cen) * 100.
- `Pedidos completos (%)` evalua completitud por pedido (no por linea).

---

## 4) Homologacion (cuando usarla)

Adjunta homologacion cuando existan codigos sin significado claro
(material/cliente/distrito). Se puede adjuntar:

- al crear el draft, o
- durante la entrevista en el detalle del proceso.

El pipeline la importa y en las salidas agrega columnas `*_homologado`.

---

## 5) Errores comunes y accion recomendada

- Error de conexion `status 0`:
  - verificar backend activo,
  - recargar y reintentar.
- No se puede aprobar:
  - hay preguntas bloqueantes abiertas.
- `GranoNoResueltoError`:
  - responder pregunta de grano o ajustar profile.
- PBIP abre pero no refresca:
  - revisar parametro `RutaDatos` segun README del PBIP.

---

## 6) Checklist de cierre por proceso

- Preguntas bloqueantes = 0.
- Profile aprobado.
- Run ejecutado.
- Entregables descargables visibles.
- Excel abre sin reparacion.
- PBIP abre y refresca.
- Numeros clave consistentes (script de verificacion PBIP).

