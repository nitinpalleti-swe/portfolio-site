# Project — Agentic Compass (professional work, in production, 2026)
Sources: Nitin's Agentic Compass project notes and résumé (2026-11-13); production status and employer framing confirmed by Nitin, 2026-11-13. Professional work, not a personal project. Internal names are deliberately omitted for confidentiality.

## Overview
Agentic Compass ("Understand. Route. Build. Prove.") is a human-supervised, understand-first AI agent for GitHub Copilot that Nitin designed and built in his current role as a Senior Software Engineer at MAS Global Consulting, on the JPMorganChase assignment (Offers team). It takes a Jira ticket key and turns it into business context, evidence-backed analysis, a routed implementation plan, real test verification and an auditable history, with every remote write gated by a human. It is professional work, not a personal project.

## Status: in production
Agentic Compass is in production. It went through formal reviews by senior engineering leads, was tested in the UAT environment and was rolled out to production. This applies both to the tool itself, which was rolled out for team use, and to the code changes delivered through it, which passed review and UAT before their production release. The sources state no usage, adoption, time-saved or cost figures.

## Problem it solves
Engineers repeatedly spent time on discovery before coding: reading the ticket, finding predecessor work and pull requests, choosing the right base branch, setting up runtimes and working out which tests prove the acceptance criteria. Generic "Jira-to-code" AI agents jump straight to code, miss hidden dependencies and claim code "should pass" without running anything. The organization already had many specialist AI skills (security remediation, testing, API governance, infrastructure), but no front door to pick the right one and no shared evidence format.

## The router pivot
Nitin started building another Jira-to-code agent, then realized capable specialist skills already existed; the missing pieces were understanding, routing and evidence. He pivoted Agentic Compass into an orchestrator and router that delegates domain work to the existing specialist skills instead of duplicating them. The strictest child-skill guardrail always wins, and the router never inherits the union of its child skills' permissions.

## How it works
The workflow starts from a Jira key. Ask produces a business brief (why, who cares, what changes, what done means, watch-outs, owners) and an acceptance-criteria matrix. Analysis checks repository fit, runtimes and tools, predecessor and branch history, open overlapping pull requests and setup blockers. A deterministic route picks the existing specialist skill or skills. A human types "go" and the engineer picks a verified base branch before local implementation. Tests run in layers (focused, full suite, component or contract, coverage, API checks with Bruno, performance), each stating what it proves and what it does not prove. History is an append-only event log. Push, pull request and evidence are each separately previewed and confirmed by the human, and no command implies the next. Review mode asks zero routine questions; the only mandatory human decision before coding is the base branch.

## Guardrails and governance
- Human approval for every remote write: push, pull request and evidence are each previewed and confirmed by the engineer.
- It never deploys, merges, changes traffic, flips feature flags, transitions Jira tickets or mutates cloud resources.
- Least privilege: the strictest child-skill guardrail wins, and the router never inherits the union of child permissions.
- Every material claim is labeled VERIFIED, INFERRED or UNKNOWN, with file:line or source citations.
- An append-only audit log (events) and COMMANDS.md proof records of each exact command, working directory, exit status, evidence path and proof boundary.
- A loopback-only, read-only dashboard with secret redaction and no write or execute endpoints.
- Zero third-party dependencies and Git-excluded per-ticket workspaces, so nothing pollutes a pull request.
- For security findings, the original scanner is the oracle for closure.
- Formal process: senior engineering lead reviews, then UAT, then production.

## Architecture
Agentic Compass is written in Python 3.11+ using only the standard library (no pip dependencies) plus plain JavaScript, HTML and CSS with no framework or build step, a deliberate choice for zero dependency-scanning overhead, portability and a small attack surface. It is about 5,000 lines across 7 single-responsibility Python modules (deterministic ticket routing, skill resolution, a bounded-retry Jira fallback when the Jira MCP server fails, repository preflight detection, workspace creation and migration, gated state tracking, and the dashboard), a UI and tests. Each ticket gets a resumable, Git-excluded workspace with a summary, command proof records, versioned state, the append-only event log, raw artifacts and Bruno API checks. The routing table has 19 routes, from container and dependency vulnerabilities, static analysis, secrets-in-code and certificate lifecycle to component, contract and performance testing, feature development, API governance, infrastructure-as-code and an unknown route; all 61 referenced specialist skills resolved. The dashboard has five tabs (Ask, Analysis, Implementation, Tests, History) plus Commands, and is scoped per repository so evidence from different applications never mixes.

## Quality and evals
Agentic Compass has 73 unit tests, all passing, covering routing, preflight, state gates, workspace creation and migration, the UI (including redaction and read-only guarantees), the Jira fallback, and documentation and review contracts. It also has two-layer evals: skill-selection prompts that should or should not trigger it (for example, it refuses "deploy to prod"), and routing fixtures that check the right route wins.

## Timeline
Nitin built Agentic Compass in about one week in November 2026. On day 1 he analysed real team workloads and found that discovery, setup, testing and evidence were the biggest repeat costs, and dry-ran it on 2 real tickets. On day 3 he pivoted to the router and understand-first design. On day 5 he renamed it Agentic Compass, simplified the evidence format and made the human base-branch choice mandatory, then validated it across multiple services. It then went through senior engineering lead reviews, UAT and production rollout.

## Validation results
Agentic Compass was validated on 8 real tickets across 5 services. Results are described generically:
- A feature-flagged A/B test feature in a Java/Maven service: a change delivered through Agentic Compass implemented feature-flag routing between the control and the new variant, with a fallback; 357/357 unit tests and 24/24 component tests passed, with 100% coverage of new lines and branches (JaCoCo). Agentic Compass also flagged that a downstream client came from an unstarted dependency ticket and that the flag name wasn't specified.
- A compliance finding in the same Java/Maven service: it showed that the prescribed security fix would only change scanner detection, not remove the underlying exposure, and the issue was escalated to the owners instead of shipping a cosmetic fix.
- A cleanup change in a Java API service: it detected an open overlapping pull request and a release branch that had diverged, recommended wait, parallel or stacked strategies and blocked delivery until the dependency merged; a change delivered through Agentic Compass then changed 14 files with 87.2% module line coverage and 100% changed-line coverage, all local tests passing.
- A CI test-reporting integration in the same Java API service: it correctly stayed analysis-only because required details were missing from the ticket and the linked guide was inaccessible, rather than guessing.
- A static-analysis (SAST) finding in a Python AWS Lambda service: it identified candidate sinks with file:line evidence, a change delivered through Agentic Compass implemented the fix on a feature branch, and the scanner re-scan was treated as the closure oracle.
- An LLM SDK deprecation upgrade in the same Python AWS Lambda service: it found that the ticket's assumption about how the SDK was used was wrong, which raised extra requirements, found an unmerged prior upgrade on a release branch and flagged a 250 MB Lambda package-size risk.
- Component tests for a Java service: it found test-data and assertion gaps in a story already marked "Ready for Review".
- A container-image finding whose closure depended on the deployed image and pipeline, not the repository's code.

## Common pattern
Across these tickets, Agentic Compass repeatedly surfaced hidden risks before coding (wrong ticket assumptions, overlapping pull requests, diverged branches, cosmetic security fixes and missing dependencies) and stopped instead of guessing.

## Engineering principles
Understand acceptance criteria before code; reconstruct predecessor history; never invent production values or branches; keep changes ticket-scoped; distinguish unit, mock, local, deployed and production evidence; run real verification; require explicit human instruction for every remote write; and treat the original scanner as the oracle for security closure.

## Skills demonstrated
Agentic AI and LLM agent design, multi-agent orchestration and skill routing, MCP (Model Context Protocol) integration, GitHub Copilot and Claude agent skills, human-in-the-loop safety design, Python, JavaScript, Java and Maven, test strategy (unit, component, contract, coverage), secure-by-default design (loopback-only, read-only, redaction), developer productivity tooling, DevSecOps (static analysis, secrets and dependency remediation) and technical writing.
