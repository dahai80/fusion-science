# Multi-Node HA Deployment Topology (Issue #24)

> Status: **implemented** (v1.0.9) — shared session store, readiness endpoint,
> central audit sink. TLS termination + external LB remain operator-side.

## 1. Goal

Fusion-Science runs as a stateless API tier in front of stateful dependencies.
This document defines the supported topology for multi-node high availability:
N identical `fusion-science serve` workers behind a load balancer, all reading
and writing one shared Postgres session store, all streaming audit events to one
central collector. No node holds session state in memory; any node can serve any
request; a failed node is pulled from rotation without session loss.

## 2. Topology

```
                     ┌──────────────────────────┐
                     │   External LB / Ingress   │
                     │  (TLS, health-checked)    │
                     └───────────┬──────────────┘
            ┌────────────────────┼────────────────────┐
            ▼                    ▼                    ▼
   ┌────────────────┐  ┌────────────────┐  ┌────────────────┐
   │ fusion-science  │  │ fusion-science  │  │ fusion-science  │
   │   worker #1     │  │   worker #2     │  │   worker #N     │
   │  :11462         │  │  :11462         │  │  :11462         │
   │ (no session     │  │ (no session     │  │ (no session     │
   │  state in mem)  │  │  state in mem)  │  │  state in mem)  │
   └────┬───────┬────┘  └────┬───────┬────┘  └────┬───────┬────┘
        │       │            │       │            │       │
        │       └────────────┼───────┼────────────┘       │
        │                    │       │                    │
        ▼                    ▼       ▼                    ▼
   ┌─────────────┐    ┌────────────────────┐      ┌─────────────────┐
   │ fusion-mlx  │    │  Postgres (shared  │      │  SIEM / audit   │
   │ (inference, │    │   session store)   │      │  collector      │
   │  shared)    │    │  sessions table    │      │  (NDJSON sink)  │
   └─────────────┘    └────────────────────┘      └─────────────────┘
```

- **LB** — terminates TLS, runs a readiness probe against each worker's
  `/api/v1/ready`, routes only to ready nodes. Sticky sessions are NOT required:
  every session lives in Postgres.
- **Workers** — N replicas of `fusion-science serve`. Each is stateless except
  for its LLM gateway connection (to the shared fusion-mlx) and its Postgres
  pool. Multiple uvicorn workers per pod are supported (PR #23 multi-worker fix).
- **Postgres** — the single source of truth for sessions. `PostgresSessionStore`
  uses JSONB columns + optimistic locking (`version`), reusing the same
  `ResearchSession.to_dict`/`from_dict` contract as SQLite, so a deployment can
  migrate `sqlite → postgres` with no session-shape change.
- **SIEM collector** — receives audit NDJSON from every node via
  `FUSION_SCIENCE_AUDIT_SINK_URL` so the full tamper-evident trail aggregates in
  one place regardless of which node served the request.
- **fusion-mlx** — shared inference engine. All workers point at the same
  `FUSION_SCIENCE_ENGINE_BASE_URL`.

## 3. Configuration

Per worker (identical across the fleet):

| Variable | Value | Notes |
|---|---|---|
| `FUSION_SCIENCE_API_HOST` | `0.0.0.0` | listen on all interfaces for the LB |
| `FUSION_SCIENCE_API_PORT` | `11462` | single source of truth (matches `start.sh`) |
| `FUSION_SCIENCE_API_KEY` / `FUSION_SCIENCE_API_KEYS_FILE` | provisioned | multi-key RBAC; file enables live rotation |
| `FUSION_SCIENCE_SESSION_STORE` | `postgres` | selects `PostgresSessionStore` |
| `FUSION_SCIENCE_SESSION_DSN` | `postgresql://user:pass@pg:5432/fusion_science` | shared by all workers |
| `FUSION_SCIENCE_AUDIT_SINK_URL` | `https://siem/ingest` | central audit fan-out (optional) |
| `FUSION_SCIENCE_ENGINE_BASE_URL` | `http://mlx:11432/v1` | shared fusion-mlx via gateway |

Install with the HA extra on each worker:

```bash
pip install -e ".[api,ha,oidc]"
```

## 4. Readiness vs Liveness

Two distinct probes (see `api/routes/health.py`):

- **`GET /api/v1/health`** — *liveness*. Process is up; reports MLX/disk/session
  status as `ok`/`degraded` but always returns 200 while the process runs. A
  transient dependency blip must NOT get the pod killed and restarted, so
  liveness stays permissive.
- **`GET /api/v1/ready`** — *readiness*. Hard dependencies are reachable. For
  Postgres this is a real `SELECT 1` ping (`PostgresSessionStore.ping`). Returns
  **503** when the store is down so the LB pulls the node from rotation instead
  of serving 500s; returns 200 when ready.

Recommended probe wiring:

```yaml
livenessProbe:
  httpGet: { path: /api/v1/health, port: 11462 }
readinessProbe:
  httpGet: { path: /api/v1/ready, port: 11462 }
```

## 5. Session Concurrency

`PostgresSessionStore.save` uses optimistic locking on a `version` column
(unchanged contract from `SQLiteSessionStore`): an update succeeds only if the
row's version matches the in-memory copy, else returns `False` and the caller
reloads. This prevents lost updates when two nodes edit the same session
concurrently without distributed locks. `SessionManager`'s per-session asyncio
lock serializes access within a node; the version check serializes across nodes.

## 6. Audit Aggregation

Each `TraceRecorder.record()` appends to the local tamper-evident hash chain
(SHA-256, source of truth) AND, when `FUSION_SCIENCE_AUDIT_SINK_URL` is set,
forwards the entry as one NDJSON line to the collector on a daemon thread
(fire-and-forget). A collector outage degrades to local-file-only audit — never
fails the request, never loses the local chain. The pull-based
`GET /sessions/{id}/audit/export` (SIEM export from PR #26) remains for
single-node; the sink adds push-based fan-out for HA.

## 7. Operator-Side Items (Out of Code Scope)

These remain the operator's responsibility and are NOT shipped in code:

- **TLS termination** — the API is HTTP; terminate TLS at the LB/Ingress or via a
  reverse proxy (Caddy/nginx) in front of the workers.
- **Postgres HA** — run Postgres itself with replication/failover (Patroni,
  RDS, Cloud SQL). The store connects to a DSN; it does not manage the DB.
- **LB configuration** — session affinity, TLS certs, probe thresholds.
- **Backups** — Postgres PITR + the existing `SQLiteSessionStore.backup` for
  single-node. Audit files are append-only and pruned by retention policy.

## 8. Acceptance Criteria

- [x] `PostgresSessionStore` implements `SessionStore` ABC, JSONB, optimistic lock
- [x] `session_store="postgres"` selectable via config + env
- [x] `GET /api/v1/ready` distinct from `/health`, 503 when store down
- [x] Central audit sink via `FUSION_SCIENCE_AUDIT_SINK_URL` (fire-and-forget)
- [x] `[ha]` optional extra (psycopg), default install unaffected
- [x] This topology document
- [ ] Postgres integration test against a live DB (tracked: needs CI Postgres
      service — local tests use a stubbed connection pool)
