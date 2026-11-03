# PROGRESO — Wolf DevSecOps · Monetización (Community → Pro/Enterprise)

**Última actualización:** 2026-11-03 · **Global: 68%**

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

## Fase E1 — Gates sobre features existentes (peso 3) — 100%
- ✅ AI code-fix (Feature B) → Pro (402 + reviewer local teaser Community)
- ✅ PR-draft / branch (Feature D) → Pro (botón oculto en Community)
- ✅ Notificaciones externas (Telegram/email/bot) → Pro (gate en _has_channel)
- ✅ Scheduler → snapshots en Community (alimentan campana/historial, base);
     el envío externo es lo Pro (cubierto por el gate de notificaciones)
- ✅ IA hosted → cubierto: code-fix es Pro; el reviewer Community es local
- ✅ Límite multi-org en Community (1 org) — enforce en import-org **y en el alta web**
     (campo Organización en el formulario de repos + gate 402 al introducir org nueva)
- ✅ Ventana de historial (30d Community / ilimitado Pro)

## Fase E2 — Features nuevas de Pro (peso 4) — 20%
- ⬜ SSO / LDAP / Active Directory (reciclar de otros proyectos Wolf)
- ⬜ Reportes programados (envío automático)
- ⬜ Reporte de cumplimiento (mapeo ISO/CIS, PDF)
- ✅ Export + retención de auditoría (endpoint gated + tab Admin + purga en scheduler)
- ⬜ Canal de updates firmado

## Fase E3 — Enterprise (peso 2) — 0%
- ⬜ White-label / marca propia
- ⬜ Multi-tenant aislado
- ⬜ SCIM / roles custom / row-level por org

## Fase E4 — Licencia WLA (fase 2 enforcement) (peso 3) — 100%
- ✅ Verificación de licencia firmada (ES256/JWS) con llave pública embebida
     (`licensing/`); la licencia válida MANDA sobre el flag (precedencia).
- ✅ Expiración → degradación grácil (licencia vencida/inválida → flag/community)
     + aviso en About (vence en N días / vencida). `license_required` para prod.
- ✅ Emisión desde la WLA: CLI `issue-license` (firma con la llave privada que
     vive en tu WLA; la PÚBLICA va al repo, la PRIVADA nunca).

## Fase E5 — Frontend upgrade UX (peso 2) — 100%
- ✅ Hook `useFeature` + componentes `ProBadge`/`ProNotice` (reutilizables)
- ✅ Vista comparativa de planes `/plans` (resalta el plan actual), enlazada
     desde About y el login
- ✅ Manejo de 402 (AI code-fix muestra aviso Pro + "Ver planes"; botón PR
     oculto en Community)
- ✅ Badges "Pro" en las features gated principales (code-fix, PR-draft)
