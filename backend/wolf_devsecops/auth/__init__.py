"""Paquete de identidad (ADR-013).

A1 (wolf-security-crypto-agent) entrega aquí solo `security.py`: las PRIMITIVAS
criptográficas de auth (hashing de password, JWT de sesión, TOTP, cifrado del
secreto MFA, recovery codes, QR). No toca la base de datos ni implementa flujos
de login/routers — eso es A2 (`auth_service`, `routes_auth`) y A3
(`dependencies.py`, `require_role`).
"""
