"""Infraestructura de persistencia (ADR-012).

La BD es sistema-de-registro SOLO de identidad, configuración y auditoría.
NUNCA almacena resultados de escaneo (findings/CI/quality/releases) — eso vive
en la caché en memoria (ADR-001) — ni tokens — esos viven en el entorno (ADR-002).
"""
