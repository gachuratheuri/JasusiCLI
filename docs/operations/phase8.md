# Phase 8 operations contract

This document describes the reliability and service-control primitives
implemented by `jasusi-service` and exposed by the Rust `ControlService`.

## Deployment profiles

`JASUSI_DEPLOYMENT_PROFILE` accepts `local` (default) or `service`.

Local mode is deliberately conservative: a single durable SQLite queue, bounded
workers, loopback/local-socket serving, and no implied distributed deployment.
Service mode changes admission defaults and is intended for a stateless web
adapter in front of the Rust control plane. It does not provision PostgreSQL,
a durable external queue, an autoscaler, or a service mesh; those are deployment
responsibilities and must be supplied explicitly.

The following bounds are enforced before queue insertion:

| Variable | Default | Meaning |
|---|---:|---|
| `JASUSI_MAX_CONCURRENT_JOBS` | 4 local / 16 service | Worker semaphore capacity |
| `JASUSI_MAX_QUEUE_SIZE` | 16 | Pending + running admission bound |
| `JASUSI_MAX_PROMPT_BYTES` | 65,536 | Prompt byte limit |
| `JASUSI_MAX_EVENT_BYTES` | 262,144 | Persisted event payload limit |
| `JASUSI_GRACEFUL_DRAIN_TIMEOUT_SECONDS` | 30 | Shutdown drain deadline |

Service mode rejects a non-loopback bind unless
`JASUSI_AUTH_BEARER_TOKEN` is configured. Tokens must be injected through a
secret manager or protected process environment; never commit them to config.

## ControlService semantics

The versioned protobuf control surface provides:

- `GetVersion`, `Health`, and `Readiness` for liveness/readiness probes.
- `GetMetrics` for a point-in-time metric snapshot.
- `CheckQuota` and atomic admission quotas by global, user, project, provider,
  and tool scope.
- `SubmitJob` with validated idempotency keys and durable queue insertion.
- `GetJobStatus`, `CancelJob`, and replayable `StreamJobEvents`.
- `Drain`, which stops new submissions and permits existing work to finish
  until the configured deadline.

The local Unix socket is owner-restricted. The non-Unix fallback remains
loopback-only. A public TCP listener must not be added without an authenticated
interceptor and a platform-specific deployment review.

## Telemetry and audit

`MetricsRecorder` emits counters, gauges, and Prometheus-compatible histogram
`+Inf`, `_sum`, and `_count` series. Labels are canonicalized and escaped before
rendering. The tracing boundary emits structured `tracing` spans with service
and version fields and validates W3C `traceparent` identifiers. A deployment
may attach an OpenTelemetry subscriber/exporter at the process edge; the core
crate intentionally does not hard-code a collector endpoint.

`AuditLog` writes redacted JSONL events with monotonic sequence numbers and a
SHA-256 hash chain. `verify()` must pass during health diagnostics and before
exporting an audit bundle. A hash chain detects modification after the fact; it
does not prevent a privileged local user from rewriting both the log and its
verification context.

## Shutdown and recovery procedure

1. Mark the instance draining through `Drain` or the process shutdown signal.
2. Stop accepting submissions; existing jobs remain observable.
3. Wait for the active count to reach zero or the drain deadline.
4. Export metrics and audit evidence, then terminate the process.
5. On restart, reopen the SQLite queue. Pending jobs remain durable; running
   jobs require an explicit recovery policy before being retried.

Operational SLOs must be measured rather than assumed. At minimum, alert on
queue saturation, quota rejection rate, provider failure/fallback rate,
cancellation latency, orphan-process count, readiness loss, audit verification
failure, and recovery/migration failure.

## Explicit limitations

The control plane is now real and durable, but it does not itself attach a
model/tool `JobProcessor`. A deployment must register the canonical Rust engine
worker before treating submitted jobs as executable work. Until that integration
exists, readiness should include a worker probe and remain not-ready for service
traffic that requires execution.
