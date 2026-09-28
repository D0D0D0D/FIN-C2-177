# FIN-C2-177 — Financial Outsourcing Due-Diligence Evidence Brief Agent

> **Category**: Cat 2 (domain workflow)
> **Industry**: FIN

## Overview

This agent organises the evidence an outsourcing provider submits — questionnaires, audit
reports, BCP documents — against an approved control catalogue and perspective pack, and returns
a source-linked brief: what each control requirement is supported by, what is missing, what
contradicts, and what still needs to be asked. Where a prior assessment of the same vendor
exists, it checks the two are comparable before showing what changed.

It suits the reviewing step in third-party risk work: an outsourcing manager sorting a new or
renewed submission by control requirement, a procurement lead preparing follow-up questions with
source IDs attached, or a re-assessment that needs the delta against last time.

The boundary between the agent and the reviewer is deliberately sharp:

- **The catalogue and the perspective pack are pinned by the caller, not chosen by the agent.**
  Each is fixed by `ref`, `version` and content hash at invocation time. The agent never edits
  them and never generates assessment criteria of its own — without a pin there is no basis to
  assess against, and the call does not proceed.
- **Every finding carries a support level from a closed set** — `supported`, `insufficient`,
  `contradicted`, `undetermined`. There is no `compliant` value to reach, because compliance is
  not the agent's call. A requirement whose level is anything other than `undetermined` must
  name the sources behind it.
- **Source references are machine-verified for existence and location, not for meaning.** The
  agent confirms a cited source exists, is eligible, and that the quoted span is where it says it
  is. Whether that source actually satisfies the control requirement is the reviewer's judgment.
- **The output schema is closed and recursively validated.** Both the envelope and the brief must
  match their key sets exactly; anything else fails the output gate rather than being returned.
- **Raw evidence text never enters the graph state.** Submitted documents go into a short-lived
  payload store and the state carries only an opaque reference, so the checkpoint that HITL
  requires cannot accumulate provider evidence.

The agent does not decide vendor suitability, regulatory compliance, materiality, exception
acceptance or contractual adequacy, and it is not a substitute for legal, compliance, risk or
audit judgment.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Integration

Provide:

- an AgentCore runtime;
- a `BaseLLM` implementation injected through `config["llm"]` — no provider client is bundled and
  no model is named. Left unwired, the assessment stage falls back to a deterministic path,
  returns `undetermined` support levels and states why in `degradation_reason`; it does not
  fabricate an assessment;
- authentication middleware that establishes caller identity and scope — `caller_id`,
  `tenant_id`, `org_id` and `case_ref` are all required, and a caller carrying verified-external
  or internal trust without them is refused;
- your own approved control catalogue and perspective pack. The repository ships **entirely
  fictitious** sample pins and evidence under `config/*.sample.json` so the Marketplace adapter
  has something to validate at start-up; replace them before any real use;
- the backing stores named in the manifest, if you use them: `DD_DB_DSN` (cases, case evidence
  and the feedback audit trail) and `DD_KB_DSN` (case evidence and the same-vendor prior-assessment
  projection), plus `LLM_API_KEY` for the injected model.

Runtime parameters read from `config/config.yaml` (defaults in parentheses):

| Key | Meaning |
|---|---|
| `hitl.enabled` (`true`) / `hitl.max_hitl` (`8`) | Human review is part of the pipeline; `max_hitl` caps the review cycles per session |
| `memory_enabled` (`true`) | Required for pause/resume. The entry adapter compiles the outer and inner graphs with **separate** saver instances — neither setting creates a checkpointer on its own |
| `evidence_confidence_threshold` (`0.8`) | Below this, a claim-to-source match goes to human review rather than into the brief |
| `kb_top_k` (`8`) | Retrieval breadth, set high because control evidence runs long |
| `payload_ttl_s` (`900`) | Lifetime of the transient evidence payload store |
| `max_evidence_documents` (`20`) / `max_aggregate_evidence_chars` (`400000`) | Submission caps enforced before processing |
| `llm_temperature` (`0.0`) | Deterministic bias for a regulated, fact-matching task |
| `max_retry` (`3`) / `timeout_s` (`60`) | Graph backbone tunables |

The shipped saver is in-memory: a restart loses pending review sessions. A deployment that needs
review to survive a restart supplies a durable checkpointer.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent runs inside the platform runtime and takes its secrets from the platform secret provider. Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Supplied by the platform's own package registry at build time. It is not published on public PyPI, so `pip install -e .` alone does not fetch it. |
| Python | >=3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run on AGENTIC STAR, and the agent does not fall back to a degraded
mode when it is absent. Without the framework package installed, `src/api/server.py` cannot be
imported at all — it imports `framework.*` at module level, so start-up ends in `ImportError`
rather than in a partially working server. With the framework present but no model injected, the
server starts and answers, but the assessment stage runs its deterministic fallback and every
brief comes back `undetermined` with `degradation_reason` set. The test suite runs without a
platform connection.

## Input

Four routes, each with a strict body — an unexpected field is rejected outright:

| Route | Body | Purpose |
|---|---|---|
| `POST /invoke` | `input`, `evidence`, `session_id` | Assess a submission against the pins named in `input` |
| `POST /ingest` | `evidence`, optional `input`, `session_id` | Take in case evidence |
| `POST /feedback` | `input`, `session_id` | Record reviewer feedback against an assessment |
| `POST /resume` | `thread_id`, `feedback` | Resume a paused human-review session |
| `GET /health` | — | Liveness |

Authentication is required on every route and failure is a **403**. Verified middleware identity
is the normal path, and it must supply `caller_id`, `tenant_id`, `org_id` and `case_ref` at
verified-external or internal trust. The standalone `INVOKE_AUTH_TOKEN` bearer is accepted on
`/invoke` **only** — `/ingest`, `/feedback` and `/resume` need real middleware identity, and a
caller wearing the standalone token's synthetic identity is rejected on those routes. `/resume`
additionally checks that the caller owns the checkpoint being resumed, so one tenant cannot
resume another's review session.

```bash
curl -X POST http://127.0.0.1:8000/invoke \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer ${INVOKE_AUTH_TOKEN}" \
  -d '{
    "session_id": "dd-demo-001",
    "input": {
      "control_catalogue_ref": "SAMPLE-CATALOGUE",
      "control_catalogue_version": "SAMPLE-2026.1",
      "control_catalogue_content_hash": "<hash of the registered record>",
      "perspective_pack_ref": "SAMPLE-PERSPECTIVE-PACK",
      "perspective_pack_version": "SAMPLE-2026.1",
      "perspective_pack_content_hash": "<hash of the registered record>",
      "vendor_type": "sample_cloud_service_provider"
    },
    "evidence": {
      "documents": [
        {
          "text": "...",
          "control_ids": ["SAMPLE-CTL-001"],
          "document_version": "SAMPLE-DOC-2026.1-A",
          "content_hash": "<hash of this document>"
        }
      ]
    }
  }'
```

Pin fields are flat in `input`, one `_ref` / `_version` / `_content_hash` triple per pinned
artefact. The hash must match the registered record and the record must be approved and inside
its effective period, or the call is refused with **400** (unavailable) or **403** (not usable).

Evidence must be text; other file types are refused by the ingest sanitiser rather than parsed.

## Output

The response envelope carries exactly eight keys — `evidence_brief`, `evidence_map`,
`current_evidence_basis`, `applied_pins`, `assessment_ref`, `requires_human_review`,
`degradation_reason` and `mode`. `evidence_brief` in turn carries exactly seven —
`control_requirements`, `evidence_sources`, `gaps`, `contradictions`, `verification_questions`,
`prior_finding` and `changed_since_last`. Both key sets are checked exactly: a missing or extra
key fails the output gate.

While a review is pending, the response is the short form — `mode`, `requires_human_review` and
`degradation_reason` only — so an unreviewed draft cannot be mistaken for a finished brief.

Raw evidence text and internal file paths are never returned; the brief cites source IDs and
spans.

## Security and Limitations

Callers are authenticated and scoped before anything runs, and case evidence and same-vendor
prior assessments are fetched under separate scoped predicates so one cannot be used to reach the
other. Submitted documents live in a transient store with a short TTL; the graph state and its
checkpoint hold only an opaque reference. Output is validated against a closed recursive schema.

What that does *not* buy you:

- **A prohibited-word list does not guarantee non-conclusiveness.** Free text can still read more
  definitively than intended. The controls that matter are the fixed support-level vocabulary,
  the mandatory source links and the human review — not the lexicon.
- **Source verification is existence, eligibility and span.** Whether the cited passage actually
  supports the control requirement is a judgment the reviewer makes.
- **Pending review is not durable by default.** With the shipped in-memory saver, a restart loses
  sessions awaiting review.

Output is an AI-generated working draft. Vendor suitability, regulatory compliance, legal and
contractual judgment, and exception acceptance are decisions for an authorised person, made
against the applicable internal policies, contracts and regulations.

## Release

- Version: 1.0.0
- Category: fixed multi-step domain workflow (Cat 2), inner workflow graph in the `main` slot
- Generation mode: `llm`
- HITL: **enabled** (`max_hitl: 8`), with `/resume`
- Persistence: checkpointing enabled; the shipped saver is in-memory. Evidence text lives in a
  transient store, not in state
- Secrets: `LLM_API_KEY`, `DD_DB_DSN`, `DD_KB_DSN`
- External API clients: deployment-injected; none bundled

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection, using fakes for the model and the stores. Running the
agent itself needs the framework package; `src/api/server.py` loads `config/config.yaml`, builds
the transient payload store and pin resolver, and compiles the graph with in-memory savers.

## Documentation

`docs/02_design.md` carries the stage pipeline, the state schema, the pinning and scope
boundaries, the HITL design and the closed output contract. `docs/03_test_spec.md` records the
test specification and the verification results, including a live-model run.

## Project Structure

```
src/          agent implementation (nodes, services, schemas)
tests/        unit, integration and boundary tests
config/       agent configuration
docs/         design and test specifications
```

See `docs/02_design.md` for the design and `docs/03_test_spec.md` for the test specification.

## Customising

1. Adjust `config/` for your own environment and policies.
2. Replace the knowledge sources and sample data with your own.
3. Review the node implementations under `src/nodes/` for domain-specific logic.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.
