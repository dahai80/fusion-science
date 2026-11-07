---
doc_id: fusion-science/troubleshooting
product: fusion-science
class: app
entry: service
doc_type: troubleshooting
title: { zh: "故障排查", en: "Troubleshooting" }
audience: engineer
status: draft
version: 1.0.17
updated: 2026-11-07
owner: "@dahai"
source_of_truth:
  - start.sh
  - fusion_science/api
---

# Troubleshooting (故障排查)

## Service won't start (服务启动失败)

1. Read `logs/stderr.log` — `start.sh start` streams startup errors there.
2. Stale PID file: `start.sh status` says running but the port is closed →
   `rm .fusion-science.pid` and `./start.sh start`.
3. Port already bound: `lsof -i :11462` to find the holder; only one instance
   may own the port (port-registry: 11462).
4. Python env: use the shared monorepo venv —
   `cd /Users/dahai/fusion && source .venv/bin/activate` — then
   `pip install -e ".[test,api]"`. A bare-system python without fastapi fails
   at import time in `fusion_science/api/app.py`.

## Health checks disagree (健康检查不一致)

- `GET /api/v1/health` = liveness (process up). `GET /api/v1/ready` = readiness
  (session store reachable). With `FUSION_SCIENCE_SESSION_STORE=postgres`, a DSN
  failure returns 503 on ready while health stays ok — fix the DSN, not the
  process.
- TLS deploys probe `https://`; a mismatched cert makes the startup probe fail
  even though uvicorn is up. Check `FUSION_SCIENCE_TLS_CERTFILE/_KEYFILE` paths.

## LLM calls fail (模型调用失败)

1. `curl http://127.0.0.1:11434/api/v1/models` — is fusion-mlx up and a model
   loaded? Start it with `~/fusion/fusion-mlx/start.sh start`.
2. v1.0.2+ routes chat through fusion-gateway :11432 — check
   `curl http://127.0.0.1:11432/health` if :11434 answers but chat 5xx's.
3. Timeouts under load: gateway retries use exponential backoff with a
   `total_deadline` cap; a deadline error means the model is saturated, not that
   the client is misconfigured.
4. Behavior is rule-based / lower quality: no model was loaded, so literature
   reader/extractor/synthesizer fell back. Load a model in fusion-mlx.

## Auth errors (鉴权报错)

- `401` on every request: an API key env is set → deny-by-default. Send
  `X-API-Key: <key>` or mint a JWT at `POST /api/v1/auth/token`.
- `401 auto-logoff`: JWT idle timeout elapsed (`FUSION_SCIENCE_JWT_IDLE_TIMEOUT`).
  Re-authenticate; API keys do not idle out.
- Token mint rejects with MFA required but no `totp` code: provide the 6-digit
  code, or provision the subject in `FUSION_SCIENCE_MFA_SECRETS_FILE`. Missing
  provision = fail-closed rejection.
- `403` on `/api/v1/data-subject/*`: admin-only route; use an admin-role key.

## Compute endpoints fail (计算端点报错)

- Sandbox errors on Linux: bwrap missing → install it or accept rlimit fallback
  (weaker isolation, logged at startup).
- Jupyter execute fails: `[jupyter]` extra not installed
  (`pip install -e ".[all]"` covers it).
- Chart rejected: chart type not in the whitelist (injection hardening,
  v1.0.6). Use a supported type from `fusion_science/visualization/chart.py`.

## Connector / mirror failures (连接器与镜像)

- `GET /api/v1/system/mirrors/status` shows per-mirror state; run
  `POST /api/v1/system/mirrors/auto-switch` to re-probe latency (3s cap).
- A single connector failing (e.g. CNKI site change) does not fail the
  aggregator — check `/api/v1/databases/{name}/status` and pin the endpoint via
  `FUSION_SCI_CNKI_URL` (or NGDC/ScienceDB equivalents).
- Offline machine: set `FUSION_OFFLINE_MODE=1`; cached literature is served
  from SQLite.

## Audit anomalies (审计异常)

- Integrity check fails (`GET /api/v1/sessions/{id}/audit/integrity`): the hash
  chain is broken — typically manual file edits under the audit directory. Do
  not edit audit files in place; investigate the tamper alert webhook
  (`FUSION_SCIENCE_TAMPER_ALERT_URL`) logs.
- Encrypted audit unreadable after key change: the `FS1` envelope is keyed by
  PBKDF2 of `FUSION_SCIENCE_ENCRYPTION_KEY` or a Keychain entry — restore the
  original key source; old plaintext files still read normally.

## Logs (日志)

- `logs/stdout.log`, `logs/stderr.log` — service lifecycle + request errors.
- Every audit event is also queryable via the audit API; prefer it over reading
  audit files directly.
