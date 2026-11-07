---
doc_id: fusion-science/config-reference
product: fusion-science
class: app
entry: service
doc_type: config-reference
title: { zh: "配置参考", en: "Configuration Reference" }
audience: engineer
status: draft
version: 1.0.17
updated: 2026-11-07
owner: "@dahai"
source_of_truth:
  - fusion_science/config.py
  - start.sh
  - README.md
---

# Configuration Reference (配置参考)

All configuration is environment-driven (`FUSION_SCIENCE_*`), read by
`fusion_science/config.py`. Defaults below are from `start.sh` and `config.py`.

## Core service (核心服务)

| Variable | Default | Purpose |
|---|---|---|
| `FUSION_SCIENCE_PORT` / `FUSION_SCIENCE_API_PORT` | `11462` | HTTP port (port-registry: 11462) |
| `FUSION_SCIENCE_HOST` / `FUSION_SCIENCE_API_HOST` | `127.0.0.1` | Bind address. LAN exposure requires `0.0.0.0` AND an API key |
| `FUSION_SCIENCE_WORKERS` | `1` | uvicorn workers; >1 requires the SQLite or Postgres shared store |
| `FUSION_SCIENCE_API_CORS_ORIGINS` | (see config.py) | CORS allowlist |

## Auth and sessions (鉴权与会话)

| Variable | Default | Purpose |
|---|---|---|
| `FUSION_SCIENCE_API_KEY` | unset | Single admin key (legacy form) |
| `FUSION_SCIENCE_API_KEYS` | unset | `role:key` pairs, comma/newline separated |
| `FUSION_SCIENCE_API_KEYS_FILE` | unset | Key file rewritten at runtime without restart |
| `FUSION_SCIENCE_JWT_TTL` | `3600` | JWT lifetime seconds; `0` keeps default |
| `FUSION_SCIENCE_JWT_IDLE_TIMEOUT` | `0` (off) | Idle auto-logoff seconds (HIPAA §164.312(a)(2)(iii)) |
| `FUSION_SCIENCE_MFA_REQUIRED` | `0` | Require TOTP at token mint |
| `FUSION_SCIENCE_MFA_SECRETS_FILE` | unset | `subject:base32secret` per line |
| `FUSION_SCIENCE_OIDC_ISSUER` / `_JWKS_URL` / `_AUDIENCE` / `_ROLE_MAP` | unset | External OIDC (RS256 via JWKS); `[oidc]` extra |

## Session store / HA (会话存储与高可用)

| Variable | Default | Purpose |
|---|---|---|
| `FUSION_SCIENCE_SESSION_STORE` | `memory`/`sqlite` (see config.py) | `postgres` enables the HA store (`[ha]` extra) |
| `FUSION_SCIENCE_SESSION_DSN` | unset | Postgres DSN, e.g. `postgresql://user:pass@host:5432/fusion_science` |

## Audit and compliance (审计与合规)

| Variable | Default | Purpose |
|---|---|---|
| `FUSION_SCIENCE_AUDIT_MAX_AGE_DAYS` | `90` | Retention prune age |
| `FUSION_SCIENCE_AUDIT_MAX_SESSIONS` | `1000` | Retention prune count |
| `FUSION_SCIENCE_AUDIT_SINK_URL` | unset | SIEM NDJSON fan-out (fire-and-forget) |
| `FUSION_SCIENCE_COMPLIANCE_LEVEL` | unset | `3` raises retention to 180d |
| `FUSION_SCIENCE_RETENTION_MAP` | unset | Per-class overrides, e.g. `ephi:2555,literature:365,audit:0` |
| `FUSION_SCIENCE_ENCRYPT_AT_REST` | `0` | AES-256-GCM envelope for audit JSON (`[security]` extra) |
| `FUSION_SCIENCE_ENCRYPTION_KEY` | unset | PBKDF2 passphrase; omit → macOS Keychain auto-generate |
| `FUSION_SCIENCE_REDACT_PATTERNS` | built-ins | Extra redaction terms, comma separated |
| `FUSION_SCIENCE_TAMPER_ALERT_URL` | unset | Audit-chain tamper alert webhook |
| `FUSION_SCIENCE_ANOMALY_DETECT` | `0` | Audit anomaly detection |

## TLS (传输加密)

| Variable | Default | Purpose |
|---|---|---|
| `FUSION_SCIENCE_TLS_CERTFILE` / `FUSION_SCIENCE_TLS_KEYFILE` | unset | Both set → uvicorn `--ssl-*` and https health probe |

## Engine and connectors (引擎与连接器)

| Variable | Default | Purpose |
|---|---|---|
| `FUSION_SCIENCE_ENGINE_API_KEY` | (set by start.sh) | Key forwarded to fusion-mlx/fusion-gateway |
| `FUSION_SCIENCE_MODEL_NAME` | (set by start.sh) | Model discovered from the loaded-model status |
| `FUSION_SCIENCE_MODEL_REASONING` / `_SUMMARIZATION` / `_CODE` | unset | Per-role model override |
| `FUSION_OFFLINE_MODE` | `0` | Force offline behavior |
| `FUSION_SCI_NGDC_URL` / `FUSION_SCI_CNKI_URL` / `FUSION_SCI_SCIENCEDB_URL` | unset | Domestic connector endpoint overrides |

Notes (说明):

- Per-role model fields require fusion-mlx to have the model available; the
  gateway does not pull models on demand.
- Unknown values (e.g. exact CORS default) stay in `fusion_science/config.py` —
  read that file; this doc does not duplicate every default.

## Configuration precedence (优先级)

Explicit env var > `FUSION_SCIENCE_COMPLIANCE_LEVEL` profile > built-in default.
Example: with `COMPLIANCE_LEVEL=3`, `AUDIT_MAX_AGE_DAYS` defaults to 180 unless
explicitly set.
