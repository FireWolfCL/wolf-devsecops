# PROGRESO — Wolf DevSecOps · Monetización (Community → Pro/Enterprise)

**Última actualización:** 2026-11-03 · **Global: 19%**

Pipeline para separar Community (gratis, source-available) de Pro/Enterprise.
Enfoque: *edition-flag primero* (WOLF_EDITION), licencia firmada WLA después.
Corte: no se gatea el valor de seguridad base; se cobra escala, automatización,
integración empresarial y soporte. Límite Community = 1 org (proyectos ilimitados).

Estados: ✅ done · 🔄 in-progress · ⬜ pending · ⛔ blocked

---

## Fase E0 — Fundación del gate (peso 2) — 100%
- ✅ Módulo `edition.py` (features + limits + `require_feature`)
- ✅ Flag `WOLF_EDITION` (settings + compose + .env.example)
- ✅ Exposición en `/api/meta` (edition/features/limits)
- ✅ Badge de edición en header (manejado por backend)
- ✅ Badge en About + login re-brandeado
- ✅ Test de `edition.py` + fixture de edición en conftest

## Fase E1 — Gates sobre features existentes (peso 3) — 33%
- ✅ AI code-fix (Feature B) → Pro
- ✅ PR-draft / branch (Feature D) → Pro
- ⬜ Notificaciones externas (Telegram/email/bot) → Pro
- ⬜ Scheduler (snapshots periódicos) → Pro
- ⬜ IA hosted (proveedores de pago) → Pro
- ⬜ Límite multi-org en Community (1 org) — enforce en create/import
- ⬜ Ventana de historial (30d Community / ilimitado Pro)

## Fase E2 — Features nuevas de Pro (peso 4) — 0%
- ⬜ SSO / LDAP / Active Directory (reciclar de otros proyectos Wolf)
- ⬜ Reportes programados (envío automático)
- ⬜ Reporte de cumplimiento (mapeo ISO/CIS, PDF)
- ⬜ Export + retención de auditoría
- ⬜ Canal de updates firmado

## Fase E3 — Enterprise (peso 2) — 0%
- ⬜ White-label / marca propia
- ⬜ Multi-tenant aislado
- ⬜ SCIM / roles custom / row-level por org

## Fase E4 — Licencia WLA (fase 2 enforcement) (peso 3) — 0%
- ⬜ Verificación de licencia firmada (ES256/JWS) al arranque
- ⬜ Expiración → degradación grácil a Community + aviso previo
- ⬜ Emisión de licencias desde la WLA (Wolf-Licenses)

## Fase E5 — Frontend upgrade UX (peso 2) — 0%
- ⬜ Badges "Pro" en features bloqueadas
- ⬜ Prompts de upgrade + manejo de 402
- ⬜ Vista comparativa de planes (pricing)
