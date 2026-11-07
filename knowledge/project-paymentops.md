# Project — PaymentOps Workbench (personal project, 2026)
Sources: README of github.com/nitinpalleti-swe/paymentops-workbench; portfolio/index.html (Featured work).

## Overview
PaymentOps Workbench (Payment Investigation Workbench) is a personal project Nitin designed and built independently in 2026. It is a local-first, synthetic-data-only workbench for investigating payment exceptions and evaluating AI-assisted review behavior before promotion. It uses Python, Java and TypeScript across three services (React, Spring Boot, FastAPI). Source: github.com/nitinpalleti-swe/paymentops-workbench.

## The problem it addresses
"Payment investigation is an evidence problem, not a text-generation problem." An operator needs recorded event history, the policy revision in force at the time, missing information, and a clear human-review boundary. The project makes those facts deterministic first, then places any AI behavior behind typed contracts, source-ID validation, timeouts and an evaluation gate. It does not connect to payment rails or move money.

## What is built
- Investigation workbench: React case browser with 10 synthetic cases, ordered timelines, rule flags and versioned policies.
- Business API: Spring Boot tenant-scoped case and policy APIs, PostgreSQL transactions, request budgets and structured failures.
- AI boundary: FastAPI schema validation with a deterministic mock by default; an optional Bedrock adapter is disabled unless separately configured.
- Evidence retrieval: keyword and offline-only vector-fixture paths with policy provenance, tenant/effective-date filters and source citations.
- Approval workflow: versioned synthetic review drafts, optimistic concurrency and one durable approval audit event.
- Eval Gate: versioned development / held-out / adversarial fixtures, reusable offline reports, metrics and a release-evaluation provenance block.
- Operations: Docker Compose, private production service ports, Caddy plan, backup/rollback preparation and a read-only release runbook.

## Architecture
React operator UI → Spring Boot business API (with PostgreSQL + pgvector) → FastAPI AI boundary (deterministic mock by default; optional Bedrock Converse adapter, not configured or called). The browser never provides evidence to the AI service: Spring re-reads the synthetic case, ordered events, rule flags and eligible policy text, then FastAPI validates both request and response source IDs.

## Measured local results
- make test: React build, 10 Spring tests and 7 FastAPI tests passed (17 service tests).
- make evals-check: 30 development, 20 held-out and 10 adversarial cases, separated.
- make eval-gate-check: two offline fixture endpoints passed expected checks.
- make performance-check: 60 mock requests at concurrency 4; cached immutable context p50/p95 of 0.0150 / 0.0217 ms (mock-only, not provider capacity).
- Release-eval preflight: PENDING by design, because the held-out set is not run through the label-aware mock.

## Key design decisions
1. Facts before AI: timelines, flags, policy eligibility and citations are deterministic and visible without a model.
2. Fail closed at service boundaries: unknown source IDs, malformed outputs, provider deadlines and rate limits become explicit review-required states, never synthetic success.
3. Hold out the holdout: the release preflight fingerprints 20 held-out and 10 adversarial cases without consuming their one-time run, until an independent adapter is ready.

## Known limitations
- A local synthetic demonstration, not a payment system, production tenancy implementation or automated financial decision maker.
- Tenant selection is caller-controlled and is not authentication or authorization.
- Vector retrieval uses a deterministic offline fixture, not a semantic embedding-quality claim.
- Bedrock, a public AWS demo and the held-out evaluation remain unverified and blocked on documented access, budget, independent-adapter and human-review gates.
- The local recovery rehearsal has one recorded table-name blocker, so restore/rollback is not yet verified.

## Publication status
Source is public on GitHub; verified main history was pushed on 2026-10-31. No v1.0.0 tag yet, no AWS deployment and no public demo URL.
