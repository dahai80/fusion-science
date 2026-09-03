# Fusion-Science v1.0.8 Compliance Control Matrix

**Issue:** #25 — Formal Compliance Certification Roadmap
**Version:** v1.0.8 (post PR #26: OS sandbox, RBAC+JWT, multi-worker, audit retention+SIEM, keychain)
**Date:** 2026-09-03
**Status:** Draft for review

---

## 1. Purpose & Scope

### 1.1 Purpose

This document is the **control mapping** for fusion-science's formal compliance
certification roadmap. It maps the technical primitives already shipped in
v1.0.8 to the control objectives of three regulatory regimes — **HIPAA**
(US health data), **GDPR** (EU personal data), and **等保 2.0** (China,
等保二级 single-tenant / 等保三级 multi-tenant SaaS) — and identifies the
gaps that would block a certification audit.

This is the artifact that feeds issue #25's sub-issue breakdown: every "Gap /
Action Needed" entry in §3–5 is a candidate sub-issue, and §7 consolidates them
into a code-vs-organizational split.

### 1.2 What v1.0.8 Already Provides

v1.0.8 ships a local-first scientific AI workbench with a hardened API surface:

- **RBAC + JWT auth** — 3 roles (admin/science/viewer), HS256 JWT 1h TTL,
  no-escalation token minting, runtime key-file rotation.
- **Deny-by-default API middleware** — loopback-only when no key provisioned;
  fail-closed for non-loopback callers.
- **Tamper-evident audit trail** — SHA-256 hash chain, incremental atomic
  persist, sensitive-field redaction, age/count retention pruning, NDJSON
  export for SIEM ingest.
- **OS-level code sandbox** — `sandbox-exec` (macOS Seatbelt) / `bwrap`
  (Linux) with network deny; AST gate + rlimits as defense-in-depth.
- **Keychain secret storage** — macOS Keychain for JWT signing secret and
  API keys, with in-memory fallback.
- **Compliance checker** — data-residency / algorithm-registration /
  ethics-review / sensitive-data dimensions (Chinese research regs).
- **Local-first defaults** — loopback bind, no telemetry, local MLX inference,
  offline mode toggle.

### 1.3 Out of Scope

This matrix covers **technical controls implemented in code**. It does NOT
cover the organizational/process controls that a real certification requires:
physical security, personnel background checks, policy documents, vendor
risk management, incident response runbooks, BCP/DR drills, training records.
Those are named in §8 (Certification Path) but are not mapped to code — they
are owned by the deploying organization, not the fusion-science codebase.

---

## 2. v1.0.8 Technical Primitives Inventory

| # | Feature | File:symbol | Compliance Relevance |
|---|---------|-------------|----------------------|
| P1 | RBAC 3 roles (admin/science/viewer) | `api/auth.py:Role`, `_PERMISSIONS`, `role_allows` | HIPAA §164.312(a)(1) Access Control; 等保 安全计算环境-访问控制 |
| P2 | HS256 JWT, 1h TTL, exp verify | `api/auth.py:issue_jwt`, `decode_jwt`, `_JWT_TTL=3600` | HIPAA §164.312(d) Person/Entity Auth; GDPR Art.32(1)(b) |
| P3 | No-privilege-escalation token minting | `api/routes/auth_route.py:issue_token` (order.index check) | HIPAA §164.312(a)(1) least privilege |
| P4 | Runtime API-key rotation (file source) | `api/auth.py:load_api_keys` (`FUSION_SCIENCE_API_KEYS_FILE`) | 等保 安全管理中心-密码管理; HIPAA §164.312(a)(2)(iii) |
| P5 | Deny-by-default API middleware | `api/middleware.py:APIKeyMiddleware.dispatch` | HIPAA §164.312(a)(1); 等保 安全区域边界-访问控制 |
| P6 | Loopback-only keyless dev path | `api/middleware.py:_LOOPBACK_HOSTS`, fail-closed branch | 等保 安全通信网络-边界隔离; GDPR Art.32(1)(a) |
| P7 | Per-IP rate limiting | `api/middleware.py:RateLimitMiddleware` | 等保 安全区域边界-入侵防范; HIPAA §164.312(b) |
| P8 | SHA-256 hash-chain audit trail | `audit/tracker.py:TraceRecorder._entry_hash`, `audit_chain` | HIPAA §164.312(c)(1) Integrity; 等保 安全计算环境-安全审计 |
| P9 | Incremental atomic audit persist | `audit/tracker.py:_save_session` (temp+rename) | HIPAA §164.312(b) Audit Controls; GDPR Art.30 records |
| P10 | Sensitive-field redaction in audit | `audit/tracker.py:_sanitize_params`, `_SENSITIVE_PATTERNS` | GDPR Art.5(1)(c) data minimization; HIPAA §164.514 de-identification |
| P11 | Audit retention pruning (age+count) | `audit/tracker.py:prune` (`max_age_days=90`, `max_sessions=1000`) | GDPR Art.5(1)(e) storage limitation; 等保 安全计算环境-审计数据保留 |
| P12 | NDJSON/JSONL export for SIEM | `audit/tracker.py:export_jsonl`; `api/routes/audit_route.py:export_audit` | HIPAA §164.312(b); 等保 安全管理中心-集中审计 |
| P13 | Owner-scoped audit read (IDOR guard) | `api/routes/audit_route.py:get_audit` (`check_owner`); `tracker.get_traces` session_id filter | HIPAA §164.312(a)(1); GDPR Art.15 access |
| P14 | Integrity + provenance verification | `api/routes/audit_route.py:check_audit_integrity`, `check_provenance_integrity`; `audit/integrity.py:AuditIntegrityChecker` | HIPAA §164.312(c)(1); 等保 安全计算环境-完整性 |
| P15 | OS sandbox (macOS Seatbelt / bwrap) | `compute/isolation.py:build_isolation`, `_macos_profile` (deny network), `_bwrap_command` (unshare-net) | HIPAA §164.312(a)(2)(iii) isolation; 等保 安全计算环境-边界隔离 |
| P16 | AST gate (dangerous import/eval/subprocess) | `compute/python_executor.py:execute` → `SandboxManager.validate_code` | HIPAA §164.308(a)(5) malware; 等保 安全计算环境-恶意代码防范 |
| P17 | rlimit CPU/mem/nproc bounds | `compute/python_executor.py:_set_limits` (30s CPU, 2GB, 50 procs) | 等保 安全计算环境-资源控制; GDPR Art.32(1)(b) |
| P18 | Minimal env whitelist (no os.environ leak) | `compute/python_executor.py:execute` env dict | GDPR Art.32(1)(b); HIPAA §164.312(a)(2)(iii) |
| P19 | macOS Keychain secret storage | `utils/keychain.py:store_key/retrieve_key`, `SecureConfig` | HIPAA §164.312(a)(2)(iv) encryption; 等保 安全计算环境-密码产品 |
| P20 | JWT secret from Keychain | `api/auth.py:_jwt_secret` (Keychain path) | 等保 安全管理中心-密码管理; GDPR Art.32(1)(a) |
| P21 | Loopback bind by default | `config.py:ScienceConfig.api_host="127.0.0.1"` | 等保 安全通信网络; HIPAA §164.312(e)(1) |
| P22 | Loopback-only CORS | `config.py:ScienceConfig.api_cors_origins` default | 等保 安全区域边界; GDPR Art.32(1)(a) |
| P23 | Offline mode (no network egress) | `config.py:ScienceConfig.offline_mode` (`FUSION_OFFLINE_MODE`) | GDPR Art.5(1)(c) minimization; 等保 数据出境 |
| P24 | Compliance checker (4 dimensions) | `audit/compliance.py:ComplianceChecker` (data_residency, algorithm_registration, ethics_review, sensitive_data) | 等保 安全计算环境-数据分类分级; HIPAA sensitivity tagging |
| P25 | Local-first MLX inference (no cloud) | `core/gateway.py` → `localhost:11434`; `engine_base_url=localhost:11432` | GDPR Art.5(1)(c); 等保 数据出境; HIPAA ePHI locality |

---

## 3. HIPAA Control Mapping

Scope: **HIPAA Security Rule** (45 CFR §164.302–§164.318), applied to a
local-first scientific workbench that may process ePHI in research workflows.

| HIPAA Control | v1.0.8 Implementation | Gap / Action Needed |
|---------------|----------------------|---------------------|
| **§164.308(a)(1) Security Management Process** | `audit/tracker.py` records all db_query/code_execution/llm_call ops; `audit/integrity.py` verifies coverage. | **Gap (org):** No formal risk analysis artifact. Org must produce a documented risk assessment. |
| **§164.308(a)(3) Workforce Security** | RBAC 3 roles (`api/auth.py:Role`) gates access by route-prefix+method. | **Gap (org):** No workforce sanction policy in code scope. Org-owned. |
| **§164.308(a)(5)(ii)(B) Malware Protection** | AST gate (`SandboxManager.validate_code`) blocks dangerous imports before subprocess spawn. | OK for user-code. **Gap (code):** No antivirus scan of uploaded artifacts — candidate sub-issue §7-G3. |
| **§164.312(a)(1) Access Control** | `APIKeyMiddleware` + `role_allows` enforce least privilege; owner-scoped audit via `check_owner`. | OK. **Gap (code):** No automatic session lockout on idle — candidate §7-G4. |
| **§164.312(a)(2)(iii) Automatic Logoff** | JWT TTL 1h (`_JWT_TTL=3600`) forces re-auth. | OK. Consider shorter TTL for ePHI sessions (15min) — config knob needed (§7-G5). |
| **§164.312(a)(2)(iv) Encryption/Decryption** | Keychain stores JWT secret; OS sandbox isolates execution. | **Gap (code):** No encryption-at-rest flag for audit JSON / session DB — relies on disk encryption (§7-G1). |
| **§164.312(b) Audit Controls** | `TraceRecorder` hash-chain + NDJSON export (`export_jsonl`) for SIEM. | OK. |
| **§164.312(c)(1) Integrity** | SHA-256 chain (`audit_chain`); `AuditIntegrityChecker` detects tamper. | OK. |
| **§164.312(d) Person/Entity Authentication** | HMAC JWT verify (`decode_jwt`); API-key hmac. | OK. **Gap (code):** No MFA — single-factor only (§7-G6). |
| **§164.312(e)(1) Transmission Security** | Loopback bind default; local MLX inference over `localhost:11434`. | **Gap (code):** API is HTTP, no TLS termination (§7-G2). Requires reverse proxy for any non-loopback deploy. |
| **§164.514(b) De-identification** | `_sanitize_params` redacts patient/姓名/phone/email/身份证 patterns. | OK for audit log. **Gap (code):** Redaction pattern list is hardcoded, not extensible per data class (§7-G7). |

---

## 4. GDPR Control Mapping

Scope: **Regulation (EU) 2016/679**, applied to a local-first processor that
may handle EU personal data in research datasets. Local-first inference
strongly supports Art.25 (privacy by design) and Art.5(1)(c) (minimization).

| GDPR Article | v1.0.8 Implementation | Gap / Action Needed |
|--------------|----------------------|---------------------|
| **Art.5(1)(a) Lawfulness, fairness, transparency** | No telemetry; all processing local; audit trail visible to session owner. | **Gap (org):** No privacy notice / processing-records artifact. Org-owned. |
| **Art.5(1)(b) Purpose limitation** | `ScienceConfig` + trace `metadata` record task description per session. | OK (technical). Org must enforce purpose-binding. |
| **Art.5(1)(c) Data minimization** | `offline_mode` (no egress); local MLX inference; `_sanitize_params` redacts sensitive fields; minimal env whitelist in sandbox. | OK. |
| **Art.5(1)(d) Accuracy** | `AuditIntegrityChecker` flags missing params/duration/error details. | OK. |
| **Art.5(1)(e) Storage limitation** | `TraceRecorder.prune` (90-day age / 1000-session count cap). | OK for audit. **Gap (code):** Session DB (SQLite/Memory store) has no per-class retention policy (§7-G8). |
| **Art.5(2) Accountability** | Hash-chain audit + integrity endpoint + NDJSON export. | OK. |
| **Art.6 Lawful basis** | Out of code scope — org configures `usage_context` in `ComplianceChecker`. | **Gap (org):** Lawful-basis determination is org-owned. |
| **Art.7 Consent** | Not implemented in code. | **Gap (org):** Consent capture is upstream of the workbench. |
| **Art.15 Right of access (DSAR)** | `GET /audit` returns owner-scoped trace; `whoami` returns principal. | **Gap (code):** No DSAR endpoint aggregating all sessions for a data subject across the SQLite store (§7-G9). |
| **Art.16 Rectification** | No data-mutation endpoint beyond session delete. | **Gap (org):** Rectification handled in source systems, not workbench. |
| **Art.17 Right to erasure** | Session delete exists; `prune` drops old sessions. | **Gap (code):** No explicit "forget me" endpoint that purges all traces + artifacts for a subject (§7-G9). |
| **Art.25 Privacy by design** | Local-first defaults, loopback bind, deny-by-default auth, offline mode, sandbox network-deny. | OK — strong. |
| **Art.30 Records of processing** | `TraceRecorder` + `ComplianceChecker.check_report` produce per-session records. | OK. **Gap (org):** Org must aggregate into a ROPA register. |
| **Art.32 Security of processing** | RBAC+JWT, hash-chain audit, OS sandbox, Keychain secrets, rate limit. | OK. **Gap (code):** No encryption-at-rest (§7-G1), no TLS (§7-G2). |
| **Art.33 Breach notification (72h)** | No breach-detection automation. | **Gap (code):** Integrity endpoint detects tamper but does not alert (§7-G10). **Gap (org):** Notification workflow org-owned. |
| **Art.34 Communication to data subjects** | Out of code scope. | **Gap (org).** |
| **Art.35 DPIA** | This document + `ComplianceChecker` form the technical input. | **Gap (org):** DPIA must be finalized by DPO. |
| **Art.44 Cross-border transfers** | `offline_mode` + local inference = no transfer by default; `ComplianceChecker.check_data_residency` flags remote patterns. | OK. |

---

## 5. 等保 2.0 Control Mapping

Scope: **GB/T 22239-2019** 等级保护基本要求. Two deployment tiers:

- **等保二级** (single-tenant local deploy on one Apple Silicon workstation)
- **等保三级** (multi-tenant SaaS deploy — would require the multi-worker +
  networked bind already shipped in v1.0.8, plus additional controls)

| 等保控制项 | 级别 | v1.0.8 Implementation | Gap / Action Needed |
|-----------|------|----------------------|---------------------|
| **安全物理环境 — 物理访问控制** | 二级/三级 | N/A — local-first on user-owned hardware. | **Gap (org):** Physical security is the deploying org's datacenter/office responsibility. |
| **安全通信网络 — 网络架构** | 二级 | Loopback bind (`api_host=127.0.0.1`); local MLX at `localhost:11434`. | OK. |
| | 三级 | Multi-worker supported; but no network segmentation/VLAN in code. | **Gap (org):** 三级 requires network segmentation — deploy-side. |
| **安全通信网络 — 通信完整性/保密性** | 二级/三级 | Local HTTP only; no TLS in code. | **Gap (code):** TLS termination missing (§7-G2). 三级 mandates encrypted comms. |
| **安全区域边界 — 边界防护** | 二级 | Deny-by-default middleware; loopback-only keyless. | OK. |
| | 三级 | Rate limit present; no WAF/IPS in code. | **Gap (org):** 三级 requires boundary WAF — deploy behind reverse proxy. |
| **安全区域边界 — 访问控制** | 二级/三级 | `APIKeyMiddleware` + RBAC `role_allows` per route+method. | OK. |
| **安全区域边界 — 入侵防范** | 二级 | `RateLimitMiddleware` (per-IP fixed window). | OK for 二级. **Gap (code):** No anomaly detection for 三级 (§7-G11). |
| **安全区域边界 — 恶意代码防范** | 二级/三级 | AST gate blocks dangerous code; OS sandbox network-deny. | OK. |
| **安全区域边界 — 安全审计** | 二级/三级 | `TraceRecorder` hash chain + NDJSON export; retention 90d. | OK. 三级 retention ≥6 months — **Gap (code):** default 90d too short (§7-G12). |
| **安全计算环境 — 身份鉴别** | 二级 | API-key + JWT HS256. | OK. |
| | 三级 | Single-factor; no MFA; no password complexity (keys are opaque). | **Gap (code):** 三级 typically requires MFA (§7-G6). |
| **安全计算环境 — 访问控制** | 二级/三级 | RBAC 3 roles, no-escalation, owner-scoped audit. | OK. |
| **安全计算环境 — 安全审计** | 二级/三级 | `TraceRecorder` + `AuditIntegrityChecker` + integrity endpoints. | OK. |
| **安全计算环境 — 入侵防范** | 二级/三级 | OS sandbox + AST gate + rlimits + env whitelist. | OK. |
| **安全计算环境 — 恶意代码防范** | 二级/三级 | AST gate on user code; no AV on uploaded files. | **Gap (code):** §7-G3. |
| **安全计算环境 — 完整性** | 二级/三级 | SHA-256 hash chain; provenance integrity checker. | OK. |
| **安全计算环境 — 数据保密性** | 二级 | Keychain secrets; redaction in audit. | OK. |
| | 三级 | No encryption-at-rest for session DB / audit JSON. | **Gap (code):** §7-G1. |
| **安全计算环境 — 数据备份恢复** | 二级/三级 | Audit JSON on local disk only; no backup automation. | **Gap (org):** Backup strategy deploy-side. |
| **安全计算环境 — 剩余信息保护** | 二级/三级 | Sandbox temp files cleaned in `finally`; rlimit fallback. | OK. |
| **安全计算环境 — 个人信息保护** | 二级/三级 | `_sanitize_params` redacts PII; `ComplianceChecker.check_sensitive_data`. | OK. **Gap (code):** redaction list hardcoded (§7-G7). |
| **安全管理中心 — 系统管理** | 二级/三级 | `GET /system`, `GET /models`, `GET /metrics` endpoints (admin). | OK. |
| **安全管理中心 — 审计管理** | 二级/三级 | `/audit`, `/integrity`, `/provenance-integrity`, `/export` (NDJSON). | OK. |
| **安全管理中心 — 安全管理** | 二级/三级 | `ComplianceChecker` 4-dimension check; `describe_api_keys` for rotation audit. | OK. |
| **安全管理中心 — 集中管控** | 三级 | NDJSON export enables SIEM ingest, but no native syslog/SIEM connector. | **Gap (code):** no push-based SIEM export (§7-G13). |
| **安全管理制度 — 管理制度** | 二级/三级 | Out of code scope. | **Gap (org).** |
| **安全管理人员 — 人员管理** | 二级/三级 | Out of code scope. | **Gap (org).** |
| **安全建设管理 — 系统建设** | 二级/三级 | Out of code scope. | **Gap (org).** |
| **安全运维管理 — 运维管理** | 二级/三级 | Out of code scope. | **Gap (org).** |

---

## 6. Data-Flow Diagrams

### 6.1 HIPAA data flow (ePHI in research workflows)

```
┌─────────────────────────────────────────────────────────────────────┐
│  Apple Silicon Workstation (single-tenant, loopback)                │
│                                                                     │
│  Researcher ──HTTP(loopback:11462)──► fusion-science API            │
│                                          │  RBAC+JWT gate            │
│                                          │  (api/middleware.py)      │
│                                          ▼                          │
│                                    QueryRouterAgent                 │
│                                    │     │      │                   │
│              ┌─────────────────────┘     │      └──────────────┐    │
│              ▼                           ▼                       ▼    │
│      outbound DB connector        LLMGateway ──HTTP(11434)──► MLX   │
│      (PubMed/UniProt — ePHI       (localhost)              (local)  │
│       NOT sent outbound;                                    │        │
│       connector fetches public                               │        │
│       literature only)                                       │        │
│              │                           │                    │        │
│              ▼                           ▼                    │        │
│         audit/tracker.py ◄── every op hashed (SHA-256 chain) │        │
│              │                           │                    │        │
│              ▼                           ▼                    ▼        │
│      ~/.cache/fusion-science/traces/*.json  (local disk, 90d)         │
│              │                                                      │
│              ▼                                                      │
│      NDJSON export ──► SIEM (org-managed, optional)                 │
│                                                                     │
│  ePHI boundary: ALL processing stays on workstation. No cloud.      │
│  egress only via DB connectors (public literature) — offline_mode   │
│  blocks even that.                                                  │
└─────────────────────────────────────────────────────────────────────┘
```

### 6.2 GDPR data flow (EU personal data, local-first processor)

```
┌──────────────────────────────────────────────────────────────────────┐
│  Data Subject ──(org-owned channel)──► Controller (org)              │
│                                           │                          │
│                                           │  lawful basis captured   │
│                                           │  upstream (not in code)  │
│                                           ▼                         │
│      ┌────────────────────────────────────────────────────────┐     │
│      │  fusion-science (local-first processor)                │     │
│      │                                                        │     │
│      │  Ingress: API(loopback) ──► RBAC ──► Agent loop         │     │
│      │                                                        │     │
│      │  Personal data path:                                   │     │
│      │    input_data ──► sandbox work_dir (temp, cleaned)      │     │
│      │                  ──► MLX inference (local)              │     │
│      │                  ──► audit trail (redacted PII)         │     │
│      │                                                        │     │
│      │  Retention: audit 90d / 1000 sessions (prune)           │     │
│      │  Erasure: session DELETE + prune                       │     │
│      │                                                        │     │
│      │  Egress: NONE by default (offline_mode). DB connectors  │     │
│      │  hit public science DBs only — no personal data egress. │     │
│      └────────────────────────────────────────────────────────┘     │
│                                                                      │
│  Cross-border transfer: NONE (local-first). Art.44 satisfied.        │
│  DSAR: /audit (owner-scoped) — full DSAR endpoint is a gap (§7-G9).  │
└──────────────────────────────────────────────────────────────────────┘
```

### 6.3 等保 2.0 数据流 (China, 等保三级 multi-tenant SaaS)

```
┌──────────────────────────────────────────────────────────────────────┐
│  租户 A (Tenant A)          租户 B (Tenant B)                        │
│      │                           │                                   │
│      │ HTTPS (需反向代理+TLS)      │  ← §7-G2 gap: code is HTTP       │
│      ▼                           ▼                                   │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  fusion-science API (多worker, 0.0.0.0 bind for SaaS)        │   │
│  │  APIKeyMiddleware ──► RBAC (admin/science/viewer per tenant) │   │
│  │      │                                                       │   │
│  │      ▼                                                       │   │
│  │  SessionManager (SQLiteSessionStore — 租户隔离 by session_id) │   │
│  │      │                                                       │   │
│  │      ▼                                                       │   │
│  │  Agent loop ──► MLX (local) / DB connectors (公网科学数据库)  │   │
│  │      │                                                       │   │
│  │      ▼                                                       │   │
│  │  audit/tracker.py ──► 本地磁盘 (需加密 at rest, §7-G1)       │   │
│  │      │                                                       │   │
│  │      ▼                                                       │   │
│  │  NDJSON export ──► SIEM (集中审计, §7-G13 push export gap)   │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  数据出境: offline_mode 默认阻断; ComplianceChecker 检测远程调用.   │
│  三级额外要求: 审计保留≥6个月 (§7-G12), MFA (§7-G6), 加密at-rest.   │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 7. Identified Code Gaps

Each gap is phrased as a candidate sub-issue for issue #25. Marked **[code]**
(requires a code change in fusion-science) or **[org]** (organizational,
out of code scope — deployer/DPO/certifying body owns it).

### 7.1 Code gaps (require fusion-science changes)

- **G1 [code] encryption-at-rest flag** — ✅ **Closed in v1.0.10.**
  `audit/tracker.py` now wraps audit JSON in an AES-256-GCM envelope
  (`utils/crypto.py`, PBKDF2-HMAC-SHA256 200k-iter key from
  `FUSION_SCIENCE_ENCRYPTION_KEY` or macOS Keychain), gated by
  `FUSION_SCIENCE_ENCRYPT_AT_REST`. Plaintext stores still read back after
  the flag is toggled on (magic-prefix `FS1`). Default off.

- **G2 [code] TLS termination** — ✅ **Closed in v1.0.10.** `cli.py` serve()
  and `start.sh` pass `--ssl-certfile`/`--ssl-keyfile` to uvicorn from
  `FUSION_SCIENCE_TLS_CERTFILE`/`FUSION_SCIENCE_TLS_KEYFILE`; the startup
  health probe switches to `https://`. Default HTTP (local-first).

- **G3 [code] uploaded-artifact malware scan** — AST gate covers
  user-supplied *code*, but uploaded *files* (paper PDFs, datasets) are not
  scanned. Add a ClamAV / yara hook at the ingestion boundary.
  *File:* `literature/reader.py`, new `utils/malware_scan.py`.

- **G4 [code] idle session lockout** — JWT has 1h hard TTL but no idle
  timeout. Add an inactivity window (default 15min) that revokes the token
  server-side. *File:* `api/auth.py`, `api/middleware.py`.

- **G5 [code] per-context JWT TTL** — `_JWT_TTL=3600` is global. For ePHI
  sessions, allow a shorter TTL via config/role. *File:* `api/auth.py`.

- **G6 [code] MFA / second factor** — ✅ **Closed in v1.0.10.** New
  `utils/mfa.py` (RFC 6238 TOTP, stdlib-only, ±1 step drift, constant-time
  compare). `POST /auth/token` enforces a `totp` field when
  `FUSION_SCIENCE_MFA_REQUIRED=1`; per-subject secrets in
  `FUSION_SCIENCE_MFA_SECRETS_FILE`. Fail-closed: required but no secret
  → 401.

- **G7 [code] extensible redaction patterns** — `_SENSITIVE_PATTERNS` in
  `audit/tracker.py` is hardcoded. Load from config so deployers can add
  data-class-specific PII patterns. *File:* `audit/tracker.py`,
  `config.py:ScienceConfig`.

- **G8 [code] per-data-class retention policy** — `prune` uses a single
  `max_age_days=90`. GDPR/HIPAA require different retention per data class
  (ePHI vs literature vs audit). Add a retention map keyed by data class.
  *File:* `audit/tracker.py`, `config.py`.

- **G9 [code] DSAR / right-to-erasure endpoint** — ✅ **Closed in v1.0.10.**
  New `api/routes/privacy_route.py`: `DELETE /api/v1/data-subject/{id}`
  (GDPR Art.17 erasure, idempotent) and `GET /api/v1/data-subject/{id}/sessions`
  (Art.15 access). Admin-only via RBAC; `session/manager.py:purge_subject`
  deletes across the shared store (SQLite or Postgres HA).

- **G10 [code] breach/tamper alerting** — `audit_chain` detects tamper but
  only logs. Add a configurable alert sink (webhook/syslog) fired on
  `mismatches` non-empty. *File:* `audit/tracker.py:audit_chain`,
  `utils/events.py`.

- **G11 [code] anomaly detection for 三级** — `RateLimitMiddleware` is a
  fixed window. Add a simple anomaly detector (request-rate spike, unusual
  route sequence) for 三级入侵防范. *File:* `api/middleware.py`.

- **G12 [code] 三级 audit retention ≥180 days** — Default `max_age_days=90`
  is too short for 等保三级 (≥6 months). Raise default or add a
  等保三级 preset. *File:* `audit/tracker.py`, `config.py`.

- **G13 [code] push-based SIEM export** — `export_jsonl` is pull-only
  (GET /export). Add a background task that streams NDJSON to a configured
  SIEM endpoint (syslog/HTTP) for 三级集中管控. *File:* new
  `utils/siem_export.py`, `api/app.py` lifespan.

### 7.2 Organizational gaps (out of code scope — listed for completeness)

- **O1 [org]** Formal HIPAA risk analysis document (§164.308(a)(1)).
- **O2 [org]** GDPR DPIA finalized by DPO (Art.35).
- **O3 [org]** Data processing records / ROPA register aggregated from
  per-session audit reports (Art.30).
- **O4 [org]** Breach notification runbook — 72h GDPR / HIPAA breach
  notification workflow (Art.33, §164.404).
- **O5 [org]** Workforce security policy, sanction policy, training records.
- **O6 [org]** Physical security of the deploying workstation/datacenter.
- **O7 [org]** 等保备案 + 测评机构 engagement (see §8).
- **O8 [org]** Consent capture mechanism upstream of the workbench (Art.7).
- **O9 [org]** Backup/restore strategy for audit JSON and session DB.
- **O10 [org]** Network segmentation / WAF for 三级 SaaS deploy.

---

## 8. Certification Path per Regime

### 8.1 HIPAA (US health data)

- **Path:** No formal "HIPAA certification" exists — compliance is
  self-attested. Most organizations adopt **HITRUST CSF** as the
  certifiable framework that maps to HIPAA + NIST 800-53.
- **Scope:** fusion-science as a **Business Associate** (or sub-processor)
  to a Covered Entity; the local-first architecture means the BA operates
  on the CE's workstation or a BA-controlled on-prem host.
- **Lab:** HITRUST Authorized Assessor (e.g., Coalfire, BSI, A-LIGN).
- **Timeline:** HITRUST e1 (essentials) ~3–4 months; i1 (validatable)
  ~6–9 months; r2 (certifiable) ~9–12 months.
- **Pre-requisite (code):** close G1 (encryption-at-rest), G2 (TLS), G6
  (MFA) at minimum before assessment.

### 8.2 GDPR (EU personal data)

- **Path:** No "GDPR certification" is mandatory; voluntary certification
  schemes exist under Art.42 (e.g., EuroPriSe, ISO/IEC 27701 as a PIMS
  extension of ISO 27001).
- **Scope:** fusion-science as a **processor** (Art.28) — the deploying
  org is the controller. Local-first inference means processing happens
  on controller-owned hardware, simplifying Art.28 obligations.
- **Lab:** ISO/IEC 27701 certification via an accredited body (BSI, DNV,
  Bureau Veritas).
- **Timeline:** DPIA 2–4 weeks → ISO 27701 readiness 3–6 months →
  certification audit 2–3 months.
- **Pre-requisite (code):** close G7 (extensible redaction), G9 (DSAR
  endpoint), G1 (encryption-at-rest), G2 (TLS).

### 8.3 等保 2.0 (China)

- **二级 (single-tenant local):**
  - **Path:** 定级 → 备案 (公安网安部门) → 测评 (等级保护测评机构) →
    整改 → 复测.
  - **Lab:** 公安部认可的等级保护测评机构 (e.g., 中国信息安全测评中心,
    各省级测评站).
  - **Timeline:** 定级备案 1–2 months → 测评 1–2 months → 整改 1–3 months.
  - **Pre-requisite (code):** close G12 (audit retention) for 二级 if
    retained ≥6 months is enforced; otherwise local-first loopback deploy
    largely satisfies 二级计算环境 controls.

- **三级 (multi-tenant SaaS):**
  - **Path:** same as 二级 but 三级 requires annual 测评 + 公安部备案
    + 更严格的整改.
  - **Lab:** same 测评机构 network; 三级测评更深 (现场+渗透).
  - **Timeline:** 定级备案 2–3 months → 测评 2–3 months → 整改 3–6 months →
    年度复测.
  - **Pre-requisite (code):** close G1 (encryption-at-rest), G2 (TLS),
    G6 (MFA), G12 (180d retention), G13 (SIEM push), G11 (anomaly
    detection) before 三级测评.

---

## 9. Acceptance Criteria

This document (issue #25 control-matrix deliverable) is **complete** when:

- [x] Control matrix maps v1.0.8 technical primitives to HIPAA, GDPR, and
      等保 2.0 (§3, §4, §5) — done.
- [x] Data-flow diagrams for all three regimes (§6) — done.
- [x] v1.0.8 primitive inventory cites real file:symbol (§2) — done.
- [x] 等保二级 vs 等保三级 distinction is explicit (§5) — done.
- [x] Code gaps separated from organizational gaps (§7) — done.
- [ ] Sub-issues filed for each code gap G1–G13 (§7.1) — **pending**.
- [ ] Certification path per regime documented with lab/scope/timeline
      (§8) — done.
- [ ] This document reviewed by DPO/security lead — **pending** (org).

---

*End of compliance-matrix.md — v1.0.8, 2026-09-03.*
