# Jasusi-v3 governed inference implementation guide

**Document class:** target implementation plan, not a statement of completed capability

**Repository:** `jasusicli-v3`

**Peer system:** Synaporia (`sfog-v24`)

**Evidence date:** 2026-07-25

**Primary owners:** Jasusi runtime, provider integration, service reliability, and model-routing teams

## 1. Purpose and success condition

This guide defines the Jasusi-owned work required to expose Jasusi as a governed
inference service behind Synaporia. It is deliberately repository-specific:
Synaporia's legal, tenant, entitlement, and workflow controls are inputs to this
service, not features to reimplement here.

The integration is successful only when Jasusi can accept an authenticated,
signed inference authorization; execute or stream one durable inference job;
select only from the authorized candidate set; account for the actual route and
usage; and return verifiable evidence to Synaporia.

This document uses the following evidence labels:

- **CURRENT:** directly evidenced in this repository at the evidence date.
- **TARGET:** required implementation that is not yet established.
- **EXTERNAL:** time-sensitive provider behavior that must be revalidated.

No target item may be represented in product or assurance material as current
until its exit criteria and evidence artefacts have passed review.

## 2. Current-state findings and corrected claims

### 2.1 What is already present

- **CURRENT:** `proto/jasusi.proto` exposes `JasusCoreService` and
  `ControlService`, including service health, readiness, metrics, quotas, durable
  job submission, status, cancellation, event streaming, and drain operations.
- **CURRENT:** `jasusi-service` contains a SQLite-backed queue, quota manager,
  audit log, health model, metrics, deployment profiles, and a `JobProcessor`
  trait.
- **CURRENT:** the queue bounds prompt and event sizes, performs admission under
  a SQLite transaction, and persists jobs and events.
- **CURRENT:** the Rust `api` crate has a native Anthropic client and
  OpenAI-compatible support presently configured for OpenAI and xAI. It is not
  yet a general, catalog-driven implementation of every provider named in
  `settings.json` or the Python package.
- **CURRENT:** ADR-001 selects the Rust engine as the sole target authority and
  makes Python a thin adapter.

### 2.2 Material gaps

1. **No production inference worker is attached.** The Phase 8 operations
   contract explicitly states that the control plane does not register a
   canonical model/tool `JobProcessor`. Queue acceptance therefore does not
   establish inference execution.

2. **Idempotency is globally scoped in storage.** The current schema declares
   `idempotency_key TEXT NOT NULL UNIQUE`. A multi-tenant service instead needs
   an authenticated tenant subject and `UNIQUE (tenant_subject,
   idempotency_key)`. Hashing user/project text into a key is not a substitute
   for a database-enforced tenant boundary.

3. **Prompt content is stored as plaintext.** The queue persists `prompt TEXT`.
   This is acceptable only for explicitly classified local development data.
   Sensitive service workloads require envelope encryption or an encrypted
   content reference, purpose-bound access, retention enforcement, and
   crypto-erasure semantics.

4. **SQLite is a local durability mechanism, not distributed coordination.**
   `service` deployment mode changes admission defaults but does not supply an
   external database, distributed queue, leader election, or multi-instance
   claim protocol. A horizontally scaled deployment must first introduce a
   storage/queue abstraction and prove single-processing semantics.

5. **Provider/configuration authority is split.** `settings.json`,
   `jasusi_cli/config/settings.py`, `jasusi_cli/core/clients.py`, and the Rust
   `api` crate describe different provider sets and environment variables. This
   violates ADR-001 until Python becomes a generated or read-only client of the
   Rust configuration boundary.

6. **The configured routes do not constitute a free product tier.**
   `settings.json` includes paid-looking model identifiers as well as `:free`
   identifiers. Product entitlement must be explicit; it cannot be inferred
   from a role name or fallback list.

7. **The current audit hash chain is tamper-evident only within its verification
   context.** A privileged actor able to rewrite the log and checkpoint can
   rewrite both. Stronger evidence requires signed checkpoints exported to
   Synaporia's independent evidence plane or another external trust anchor.

8. **Current cost output is an estimate, not financial truth.**
   `rust/crates/runtime/src/usage.rs` stores rates and calculated amounts as
   `f64`, selects a price by model-name substring, and applies a default Sonnet
   rate to unknown models. `api::Usage::estimated_cost_usd()` and the Anthropic
   analytics event inherit that behavior. Although cache-create and cache-read
   tokens are separated, the result is suitable only for explicitly labelled
   CLI telemetry. It is not sufficiently versioned, precise, complete, or
   attributable for budget authorization, settlement, invoicing, or revenue.

9. **Usage categories are provider-dependent and incomplete.** The common Rust
   `Usage` type presently represents input, output, cache creation, and cache
   read tokens. OpenAI-compatible responses are normalized only to input/output
   in several paths. Reasoning, image, audio, embeddings, search, storage,
   provisioned capacity, batch pricing, region, and failed billable attempts
   need explicit meters rather than being folded into a total token count.

## 3. Non-negotiable authority boundary

Jasusi owns:

- provider protocol adapters and normalized provider errors;
- the operational model catalog and provider-endpoint facts;
- model execution, streaming, cancellation, and recovery;
- technical admission, queue, provider-rate, and concurrency limits;
- provider health, retry budgets, and circuit breakers;
- deterministic ranking within an authorized candidate set;
- route attempts, provider usage, raw Jasusi platform-resource meters, and
  signed execution receipts;
- provider-price observations and clearly classified operational accruals.

Jasusi does not own:

- user or tenant identity proofing;
- subscription or model entitlement policy;
- legal purpose, data-classification, or residency decisions;
- approval of provider privacy terms or model qualifications;
- authority to exceed a Synaporia cost/token limit;
- authority to execute financial, legal, filesystem, or external side effects
  merely because a model requested them;
- customer rate cards, taxes, invoices, recognized revenue, shared-cost
  allocation, or business-value claims;
- the authoritative regulatory evidence ledger.

The core invariant is:

```text
selected_candidate ∈ signed_authorized_candidates
```

Retries and fallbacks must preserve the same invariant. If the set becomes empty,
Jasusi returns a typed denial/unavailability result. It must not broaden the set.

## 4. Cross-repository protocol contract

### 4.1 Authentication and authorization

Use two independent controls:

1. mTLS or workload identity authenticates the calling Synaporia deployment.
2. A short-lived signed authorization binds the business constraints to the
   inference request.

The signed authorization should include:

- issuer, audience, issued-at, expiry, and unique authorization ID;
- an opaque, stable, non-PII `tenant_subject`;
- principal/workload subject where policy requires it;
- deployment environment and authorized cost-object references: business unit,
  cost centre, vertical, workflow, matter/project/account/compartment,
  operation, and plan;
- purpose, risk class, data class, and residency constraints;
- policy, profile, vertical, and entitlement versions;
- authorized candidate canonical IDs;
- required capabilities;
- maximum input/output tokens and maximum monetary amount;
- operation reservation ID and permitted attempt-allocation semantics;
- permitted credential mode (`MANAGED`, `BYOK_REFERENCE`, or sovereign);
- fallback policy;
- digest of the canonical request constraints.

Jasusi must:

- verify signature, issuer, audience, time bounds, and workload identity;
- compute the request-constraint digest independently;
- reject replay that conflicts with the original idempotent request;
- reject unsigned caller-supplied widening fields;
- retain the authorization digest, not a bearer credential, in evidence.

Do not authenticate a tenant using a truncated tenant hash. Use an opaque subject
issued by the identity/policy authority and bind it cryptographically.

### 4.2 Service shape

Add a versioned `InferenceService`; retain `ControlService` for operational
control rather than expanding `JobSubmit` into an unstable all-purpose message.

```protobuf
service InferenceService {
  rpc ListModels(ModelCatalogQuery) returns (ModelCatalogSnapshot);
  rpc Estimate(InferenceRequest) returns (InferenceEstimate);
  rpc Submit(InferenceRequest) returns (InferenceJobReference);
  rpc GetStatus(InferenceJobReference) returns (InferenceJobStatus);
  rpc Stream(InferenceStreamRequest) returns (stream InferenceEvent);
  rpc GetResult(InferenceJobReference) returns (InferenceResult);
  rpc Cancel(CancelInferenceRequest) returns (InferenceJobStatus);
}
```

`ListModels` must return only the caller-visible projection. Synaporia remains
responsible for deciding which entries a tenant may select.

### 4.3 Request semantics

An `InferenceRequest` needs:

- `schema_version`;
- `correlation_id`, `traceparent`, and tenant-scoped `idempotency_key`;
- signed authorization envelope and canonical authorization digest;
- project/workspace/session references;
- task class and selection mode;
- optional requested canonical model ID;
- authorized candidate IDs;
- required modalities, tools, structured-output, and streaming capabilities;
- prompt messages or an encrypted content reference;
- input/output/token limits;
- deadline;
- maximum `Money` value and currency;
- secret reference, never a raw provider key;
- provider privacy requirements such as data-collection denial or ZDR.

Use an integer/nanounit or decimal money representation with explicit currency.
The existing micro-dollar type may remain as a compatibility field, but reserves
must round conservatively and the ledger must retain the original provider
pricing unit. Floating-point values are prohibited for money.

### 4.4 Result and receipt semantics

The terminal result and signed route receipt must distinguish:

- requested canonical model ID;
- provider request model ID;
- provider-reported resolved model ID;
- provider endpoint/region when disclosed;
- catalog and endpoint snapshots;
- version confidence:
  `IMMUTABLE_VERSION`, `PROVIDER_REVISION`, `MUTABLE_ALIAS`, or `UNKNOWN`;
- route-policy and qualification references;
- considered candidates, exclusion codes, normalized features, scores, and
  stable tie-break;
- every provider/fallback attempt;
- provider request IDs;
- raw provider usage meters, quantities, units, usage time, source, and
  deduplication key, including token subtypes and non-token SKUs;
- provider-reported billed cost when available;
- locally valued list/contracted/accrued cost and price-book snapshot when
  provider cost is unavailable;
- explicit financial `value_type`, currency, precision/scale, cost provenance,
  and reconciliation status;
- operation, attempt, tenant, environment, project/matter/compartment, profile,
  vertical, purpose, data class, route, and fallback attribution references;
- queue, first-token, and completion timing;
- finish/error reason;
- output-schema and safety validation results;
- content or protected result reference;
- canonical receipt digest and signature.

Do not promise exact model reproducibility when a provider exposes only a mutable
alias. High-assurance policies may require `IMMUTABLE_VERSION` or an approved
provider revision and must reject weaker version confidence.

## 5. Target Jasusi domain model

### 5.1 Operational catalog

Create immutable, effective-dated records for:

- `provider`: logical provider, auth scheme, policy flags, and lifecycle;
- `provider_endpoint`: endpoint/region, data handling, ZDR status, health, and
  routing identifier;
- `model`: canonical ID, provider request ID, lifecycle, context/output limits,
  modalities, and supported parameters;
- `model_revision`: provider revision or alias status and evidence provenance;
- `model_price_version`: all billable token/request/media/cache/reasoning units,
  currency, validity interval, source URI, and source digest;
- `catalog_snapshot`: canonical digest, retrieval source, retrieval time,
  approval state, and expiry.

External catalog data is evidence, not automatically trusted policy. Validate
its schema, constrain values, preserve the raw digest, and require approval
before a new or materially changed entry enters an adaptive pool.

### 5.2 Governance projections

Jasusi needs read-only projections of Synaporia-owned decisions:

- qualification ID, state, scope, and expiry;
- provider privacy-assessment ID and permitted data classes;
- entitlement/policy version;
- candidate authorization and fallback rules.

Do not create an independent Jasusi workflow for overriding these decisions.
Changes originate in Synaporia and arrive through signed authorization or a
verified synchronization channel.

### 5.3 Execution and accounting

Add:

- `inference_job`;
- `inference_attempt`;
- `inference_event`;
- `usage_ledger_entry`;
- `provider_health_observation`;
- `route_receipt`;
- `secret_reference_metadata`.

Required constraints:

```sql
UNIQUE (tenant_subject, idempotency_key)
UNIQUE (tenant_subject, job_id)
UNIQUE (provider, provider_request_id) -- where the provider guarantees uniqueness
```

State changes and attempt allocation must use optimistic concurrency or database
locking. Append-only behavior must be enforced by database permissions/triggers
or external immutable storage, not documentation alone.

### 5.4 Financial event contract

Jasusi meters and reports consumption; Synaporia performs enterprise attribution,
allocation, customer rating, invoicing, and revenue recognition. The Jasusi
contract must nevertheless be financially complete enough for those downstream
functions.

#### Usage event

Every provider attempt emits one or more append-only usage events containing:

- stable event, operation, attempt, job, and tenant identifiers;
- provider account/credential reference, provider request ID, service, model,
  endpoint/region, and version-confidence class;
- meter code, quantity, UCUM-compatible or provider-defined unit, and usage time;
- provider-reported versus locally observed status;
- route/fallback/shadow classification;
- price-book eligibility fields such as SKU, context tier, batch/priority class,
  and provisioned-capacity reference;
- source payload digest, adapter/schema version, and deduplication key;
- business attribution references supplied in the signed authorization;
- correction relationship when a provider supplies later or revised usage.

High-cardinality attribution belongs in the event store and receipt, not in
Prometheus labels. Prompt, completion, and tool content are not billing fields.

#### Meter taxonomy

At minimum, support distinct meters for:

```text
provider.request
provider.input.uncached.token
provider.cache.write.token
provider.cache.read.token
provider.output.token
provider.reasoning.token
provider.embedding.token
provider.image
provider.audio.second
provider.search.call
provider.tool.call
provider.cache.storage.byte_second
platform.cpu.second
platform.gpu.second
platform.memory.byte_second
platform.network.byte
platform.storage.byte_second
evidence.record
evidence.byte
evidence.sign_operation
```

Provider documentation determines whether a subtype is included in another
total. Preserve both the provider's raw fields and normalized values so totals
cannot double count cache or reasoning tokens.

#### Cost valuation

For a provider attempt \(a\):

\[
C_a = \sum_{m \in meters(a)} q_{a,m} r_{m,p,t}
\]

where quantity \(q\), rate \(r\), immutable price-book version \(p\), and usage
time \(t\) are explicit. Cache hits are never a divisor. Uncached input,
cache-write, cache-read, cache storage, output, reasoning, media, search, and
other SKUs are separate terms when the provider prices them separately.

Every monetary result has:

- `value_type`: at least `LIST_COST`, `CONTRACTED_COST`,
  `ACCRUED_EFFECTIVE_COST`, or `PROVIDER_REPORTED_BILLED_COST`;
- original currency and exact decimal/fixed-point representation;
- quantity, unit rate, price-book version, and effective interval;
- `ESTIMATED`, `PROVIDER_REPORTED`, or `PROVIDER_RECONCILED` status;
- source and calculation digests.

`PROVIDER_REPORTED_BILLED_COST` does not become a reconciled invoice cost merely
because it appeared in an API response. Synaporia establishes reconciliation
against an authoritative provider statement.

#### Price behavior

- Unknown price never defaults to an unrelated model family for authorization.
- An approved conservative ceiling may be used for reservation, but remains an
  estimate and creates `UNPRICED_USAGE_EXPOSURE`.
- Historical valuation always retains the price-book version effective at usage
  time; a later price change does not rewrite it.
- Free variants use a zero provider rate only when the applicable approved
  price-book entry proves it. Platform and shared costs remain non-zero.
- `f64` may be retained only for non-authoritative display analytics. Financial
  boundaries use checked integer/fixed-point or arbitrary-precision decimal.

#### Reservation handoff

Synaporia owns the commercial budget. Jasusi receives:

- an authorization-level maximum exposure;
- an operation reservation reference;
- optionally an attempt allocation reference for each genuine provider attempt.

An RPC retry must reuse the existing job/attempt and must not create another
hold. A real fallback attempt receives a distinct idempotent attempt allocation
within the operation ceiling. If actual liability exceeds the estimate, Jasusi
records the usage truth and returns an overrun; it must not discard or cap the
observed liability to make the reservation appear valid.

#### External standards

- Map common provider/model/token and duration fields to a pinned OpenTelemetry
  GenAI semantic-convention version only after reviewing each attribute's
  stability. Content-bearing prompt/tool attributes are opt-in and prohibited
  by default for classified workloads.
- Supply a FOCUS-compatible export mapping through Synaporia rather than making
  Jasusi's internal job model depend on FOCUS.

References:

- https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/
- https://focus.finops.org/focus-specification/v1-3/
- https://cloud.google.com/blog/products/ai-machine-learning/vertex-ai-context-caching

## 6. Routing design

### 6.1 Stage A: fail-closed eligibility

Jasusi defensively revalidates operational constraints for every authorized
candidate:

```text
authorized by signed candidate set
AND catalog entry active and unexpired
AND required capabilities supported
AND context and output bounds sufficient
AND endpoint satisfies privacy/residency constraints
AND qualification reference current
AND maximum reserved cost is within authorization
AND credential mode is permitted
AND provider circuit permits an attempt
AND technical quota and deadline permit admission
```

Policy or qualification uncertainty is a denial, not a score penalty.

### 6.2 Stage B: deterministic ranking

Rank only eligible candidates. A suitable versioned objective is:

```text
score =
  w_quality       * quality_evidence
+ w_task_fit      * task_fit
+ w_schema        * structured_output_reliability
+ w_availability * availability
- w_cost          * normalized_expected_cost
- w_latency       * normalized_expected_latency
- w_quota         * quota_pressure
- w_risk          * residual_operational_risk
```

Requirements:

- every feature has a definition, unit, window, missing-data rule, and version;
- weights are fixed by route-policy version, not generated by an LLM;
- normalization uses a frozen candidate snapshot;
- unstable observations have minimum-sample and staleness rules;
- ties use canonical model ID ordering;
- manual selection still passes Stage A;
- replay with the same request and snapshots reproduces the route decision.

### 6.3 Fallback

Fallback may remove candidates but never add them. It must not:

- convert a free request to paid;
- relax privacy, provider, region, ZDR, capability, or version requirements;
- exceed the cost/token/deadline authorization;
- silently change from BYOK to a managed credential;
- treat moderation refusal as infrastructure failure unless policy explicitly
  allows an alternative model to review the same classified content.

## 7. Free-tier implementation, accurately bounded

### 7.1 External facts to revalidate

As of the evidence date, OpenRouter's official documentation states:

- `openrouter/free` filters by requested capabilities and then randomly selects
  an available free model;
- the response identifies the model used;
- a specific available free variant may be requested with `:free`;
- free-model availability, latency, and rate limits may vary;
- the Free plan lists 50 requests/day, while the FAQ says accounts that have
  purchased at least 10 credits receive 1,000 free-model requests/day;
- free models are described as unsuitable for most production use;
- Pay-as-you-go credit purchases carry a 5.5% fee;
- OpenRouter BYOK currently has a separate fee policy after its stated free
  monthly request allowance.

Sources:

- https://openrouter.ai/docs/guides/routing/routers/free-router
- https://openrouter.ai/docs/guides/routing/model-variants/free
- https://openrouter.ai/docs/faq
- https://openrouter.ai/pricing
- https://openrouter.ai/docs/guides/routing/provider-selection

These are external commercial facts, not constants. Record `observed_at`,
source digest, and configuration override. Do not encode them permanently in
Rust or rely on the static RPD values currently in `settings.json`.

### 7.2 Product modes

`FREE_AUTO`

- Uses `openrouter/free`.
- Permitted only for policy-approved public/non-personal, low-risk workloads.
- Cannot meet a policy requiring a prequalified exact model because model choice
  is random after capability filtering.
- Stores the returned model and endpoint information available in the response.

`FREE_CURATED`

- Selects an explicitly cataloged `:free` model from the signed candidate set.
- Still requires endpoint/privacy controls because one model may be served by
  multiple upstream providers.
- For constrained use, configure provider routing to deny data collection or
  require ZDR as authorized, pin/order allowed endpoints, and disable unapproved
  fallbacks.

“Free” means a zero upstream inference price for that route. It does not mean:

- zero Jasusi/Synaporia infrastructure cost;
- unlimited use;
- a provider SLA;
- stable model availability;
- permission to process sensitive data;
- that BYOK creates additional provider capacity.

### 7.3 Required free-tier controls

- explicit entitlement and per-tenant fair-use limits;
- provider-account quota separated from tenant quota;
- no paid fallback;
- no balance auto-top-up caused by a free request;
- response-model verification;
- immediate catalog quarantine when the free variant disappears or changes;
- user-visible no-SLA/availability notice;
- managed-pool abuse controls without storing raw personal data;
- BYOK secret reference and revocation.

Do not hard-code speculative future model names such as unverified Kimi,
DeepSeek, GLM, Qwen, or OpenAI releases. Populate the catalog from official
provider sources and pass each exact entry through qualification.

## 8. Step-by-step implementation programme

### Phase J0 — Baseline and ADR amendment

1. Preserve or commit the existing dirty worktree before implementation.
2. Record the Jasusi and Synaporia baseline commits.
3. Amend ADR-001 with the Synaporia/Jasusi authority matrix.
4. Add data-flow, trust-boundary, and credential-flow diagrams.
5. Create a threat model covering replay, confused deputy, cross-tenant access,
   cost exhaustion, prompt disclosure, provider fallback, and receipt forgery.
6. Create a requirements-to-test traceability table.

**Gate J0:** approved ADR, threat model, data inventory, rollback plan, and
named owner for every authority.

### Phase J1 — Contract-first inference API

1. Add the versioned inference protobuf without breaking `ControlService`.
2. Generate Rust and client bindings deterministically.
3. Define canonical status/error codes.
4. Add compatibility/golden-wire tests.
5. Add signed-authorization verification behind test keys.
6. Add canonical request and receipt serialization.

**Gate J1:** old clients remain compatible; tampered, expired, wrong-audience,
or constraint-mismatched authorizations fail closed.

### Phase J2 — Tenant-safe durable execution

1. Introduce an execution-store trait.
2. Migrate global idempotency to tenant-scoped uniqueness.
3. separate prompt payload storage from job metadata.
4. Add encryption/content-reference lifecycle.
5. Attach a real inference `JobProcessor`.
6. Define recovery for `RUNNING` jobs with attempt leases and fencing tokens.
7. Propagate deadlines and cancellation through provider streams.
8. Make readiness depend on worker and storage readiness.

**Gate J2:** a mock-provider job completes; duplicate tenant requests converge;
two tenants may reuse the same idempotency key; restart recovery and cancellation
are demonstrated; no phantom success is possible.

### Phase J3 — Canonical provider boundary

1. Normalize requests, streams, usage, tool calls, structured output, and errors.
2. Retain dedicated adapters only where protocol semantics genuinely differ.
3. Generalize the OpenAI-compatible adapter through validated endpoint profiles
   rather than copying one connector per marketing model family.
4. Implement OpenRouter and Google behavior needed by the current configuration.
5. Add strict TLS, egress allowlists, redirect policy, response-size bounds, and
   `Retry-After` handling.
6. Move Python provider calls behind the Rust RPC boundary.

**Gate J3:** provider contract tests cover successful, partial-stream, 401, 402,
429, 5xx, timeout, malformed, cancellation, and wrong-model responses.

### Phase J4 — Catalog, pricing, and accounting

1. Implement provider/model/endpoint/price snapshots.
2. Add approval and expiry workflow hooks.
3. Replace static model/RPD assertions with observed facts and configuration.
4. Remove model-substring and default-family pricing from authoritative paths.
5. Replace financial `f64` with checked fixed-point/decimal money and currency.
6. Implement append-only, idempotent usage events for all supported meters.
7. Preserve provider raw usage and normalized quantities without double counting.
8. Estimate maximum cost conservatively before dispatch.
9. Persist provider-reported usage and billed cost where available.
10. Compute list/contracted/accrued cost only when an applicable price-book
    version exists and label provenance.
11. Return reservation utilization, overrun, unpriced exposure, and correction
    events to Synaporia; do not claim provider-invoice reconciliation locally.

**Gate J4:** price changes are effective-dated; historical jobs retain their
price snapshot; rounding, overflow, currency, cache/reasoning inclusion, and
every supported meter have tests; unknown prices fail closed for dispatch or
use an approved ceiling; duplicate/corrected usage cannot duplicate liability;
unexplained valuation differences alarm rather than disappear.

### Phase J5 — Manual and free routing

1. Implement `MANUAL`, `FREE_AUTO`, and `FREE_CURATED`.
2. Enforce signed candidates before model dispatch.
3. Add provider endpoint/privacy filters.
4. Implement strict no-paid-fallback behavior.
5. Verify and record the response model.
6. Add BYOK references and a bounded managed free pool.

**Gate J5:** client fields cannot bypass entitlement; a free request cannot
create a paid attempt; unapproved response models are quarantined; classified
requests cannot use the random free router.

### Phase J6 — Adaptive routing

1. Implement Stage A as pure, property-tested predicates.
2. Implement versioned feature extraction and normalization.
3. Implement deterministic ranking and tie-breaking.
4. Add health/circuit and quota pressure without converting hard policy into
   soft scoring.
5. Record complete route explanations with redaction.
6. Add deterministic replay tooling.

**Gate J6:** property tests prove selection is a subset of authorization and
fallback is monotone; snapshot replay reproduces the selection; all pool entries
have current qualification references.

### Phase J7 — Scale and operational hardening

1. Retain SQLite for local single-instance mode.
2. Select and document a production store/queue through an ADR.
3. Implement leases, fencing, transactional outbox, and event ordering.
4. Add circuit breakers, bulkheads, bounded retries, and backpressure.
5. Add per-tenant and provider-account quotas.
6. Export signed audit checkpoints to Synaporia.
7. Add multi-instance, failover, and chaos tests.

**Gate J7:** no duplicate active attempt under worker failover; quota admission
is atomic; receipt export is replay-safe; drain and recovery meet measured,
approved targets.

### Phase J8 — Qualification and staged release

1. Consume Synaporia qualification approvals.
2. Run provider contract qualification in an isolated environment.
3. Release in order: synthetic/internal, public free, paid manual, low-risk
   adaptive, enterprise constrained.
4. Add automatic rollback/quarantine on drift, privacy, price, or error alarms.
5. Publish only measured SLOs; the free tier has no implied upstream SLA.

**Gate J8:** promotion evidence, rollback rehearsal, security review, operating
runbook, and on-call ownership are approved.

## 9. Verification and scientific evaluation

### 9.1 Mandatory repository checks

Run from `rust/`:

```powershell
cargo fmt --all -- --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
```

When Python adapters change, run the repository's Python tests and static checks
defined by the active project configuration. Do not substitute test count for
coverage of the required properties.

### 9.2 Required test classes

- unit: parsing, constraints, money, quotas, state transitions, and scoring;
- property: authorized-subset selection, fallback monotonicity, deterministic
  replay, non-negative cost, and tenant separation;
- contract: protobuf compatibility and provider payload/stream semantics;
- integration: signed Synaporia authorization to mock provider to receipt;
- financial: raw-to-normalized meter fixtures, price effective dating,
  fixed-point/decimal arithmetic, currency/scale, unknown SKU, reservation
  overrun, corrections, deduplication, and provider-statement fixtures;
- security: replay, tenant confusion, injection, SSRF, credential leakage,
  request smuggling, malicious model IDs, and cost denial-of-service;
- resilience: 429/`Retry-After`, 402, 5xx, deadline, partial stream, restart,
  storage loss, and provider returning a different model;
- performance: queueing, routing overhead, time to first token, completion
  latency, throughput, and saturation by tier;
- audit: signature verification, reconstruction, redaction, and checkpoint
  anchoring.

### 9.3 Model-routing evaluation

For any learned or empirically weighted routing feature:

1. Freeze task taxonomy, dataset, prompts, scoring rubric, and candidate
   versions before the confirmatory run.
2. Separate development/tuning data from confirmatory evaluation.
3. Use paired comparisons because candidates answer the same cases.
4. Report effect sizes and uncertainty, not only means or pass counts.
5. Use Wilson intervals for binary proportions, paired bootstrap intervals for
   continuous/ordinal metrics, and a justified paired test such as McNemar for
   binary disagreement.
6. Correct or bound multiplicity when testing many models/tasks.
7. Predefine non-inferiority or superiority margins from product risk, not from
   observed results.
8. Report subgroup and failure-mode results; do not hide them in an aggregate.
9. Treat human ratings as measurements: blind where feasible, define
   adjudication, and report inter-rater reliability.
10. Expire evidence and monitor drift.

No universal sample size is valid. Determine sample size from the required
precision/power, effect margin, base error rate, pairing, and action risk.

## 10. Release-blocking invariants

- No inference without authenticated workload identity and valid authorization.
- No selected/fallback model outside the signed candidate set.
- No free-to-paid escalation without a new explicit authorization.
- No raw provider secret in requests, logs, database records, or receipts.
- No sensitive prompt in general telemetry.
- No classified payload to an endpoint lacking the required privacy approval.
- No claim of immutable model reproducibility for a mutable alias.
- No cost represented as provider-billed unless the provider supplied it.
- No cost represented as provider-reconciled unless Synaporia matched an
  authoritative statement.
- No untyped field or UI label named simply `cost` at a financial boundary.
- No authoritative financial calculation using `f64`, model-name substring
  pricing, or a default price for an unknown model.
- No cache-hit division; cache read/write/storage are separately metered.
- No observed overrun discarded because it exceeds a reservation.
- No multi-instance claim while SQLite is the coordination authority.
- No readiness for inference until a real worker is attached.
- No model output directly authorizes a tool or side effect.
- No assurance claim based solely on passing unit tests or on test count.

## 11. Definition of done and handoff to Synaporia

Jasusi is ready for the Synaporia production rollout only when it supplies:

1. a versioned inference protocol and compatibility policy;
2. workload and signed-authorization verification;
3. tenant-scoped durable idempotency;
4. protected content storage and retention behavior;
5. a real recoverable inference worker;
6. catalog, price, endpoint, and version-provenance snapshots;
7. manual/free/adaptive selection constrained by authorization;
8. provider usage/cost provenance and valuation status;
9. append-only raw usage events, complete attribution references, financial
   value types, and reservation/overrun/correction semantics;
10. signed route receipts and externally anchored checkpoints;
11. security, resilience, performance, financial-integrity, and qualification
    evidence;
12. rollback, incident, key-rotation, provider-quarantine, and unpriced-usage
    runbooks.

Until then, Synaporia may integrate against mocks or a non-production pilot, but
must not describe Jasusi as a production-governed inference plane.
