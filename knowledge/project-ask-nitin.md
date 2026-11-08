# Project — Ask Nitin (the AI assistant on this portfolio site, 2026)
Sources: the Ask Nitin code and README in this site's repository (app.py, knowledge.py, llm.py, tools.py, ops.py, run_evals.py), 2026-11-08; how it was built confirmed by Nitin, 2026-11-08.

## Overview
Ask Nitin is the AI assistant on Nitin's portfolio website, a personal project from 2026. Recruiters can ask questions about his experience and get short answers with citations to his résumé, portfolio and project materials, or paste a job description and get a fit report that checks each requirement against those sources.

## Architecture
A single Python FastAPI backend serves both the static portfolio site and the assistant's API at the same URL. The chat widget is plain JavaScript with no frontend framework or build step. All model calls go through one small module, and the provider and model names are set in configuration, so another model provider can be tried without changing the rest of the code.

## Models
Chat answers use Claude Haiku 5.5 and stream to the browser as they are written. Job-fit reports use Claude Sonnet 5.5 with structured output validated by a Pydantic schema. The system prompts are prompt-cached.

## Retrieval and citations
Nitin's background is stored as small markdown files, one topic each, split into sections with stable ids such as experience-copart#2. Each question retrieves the best-matching sections with BM25 keyword ranking (rank_bm25). The assistant answers only from those sections and cites their ids inline; the widget shows each citation as a numbered chip with the source text, and citations to ids that were not provided are flagged.

## Tool use
The assistant runs a tool-use loop of up to three rounds with three tools: a live list of Nitin's public GitHub repositories (public metadata only, cached for 10 minutes), a lookup of a whole résumé topic, and his public contact details, which the widget shows as buttons. The contact tool never sends anything.

## Job-fit checks
After the model writes a fit report, plain code checks every row: citations to unknown sources become gaps, Python or machine-learning requirements cannot be marked as a full match unless a supported alternative is named, years-of-experience matches need a source that states enough years, a job that cannot sponsor visas gets a work-authorization gap, and the overall rating is capped by the rows. The job description is treated as untrusted data, so instructions hidden inside it are ignored.

## Guardrails and cost controls
Off-topic requests get a fixed polite refusal, and prompt-injection attempts are resisted. Inputs are length-limited, each visitor is rate-limited per hour, identical first questions are answered from a one-hour cache, and a daily spend cap switches the widget to a "come back tomorrow" message. Requests are logged with token counts and estimated cost; visitor IP addresses are stored only as salted hashes, and job descriptions are never stored.

## Evaluation
An evaluation suite runs factual, negative, off-topic, prompt-injection and job-fit cases against the app. Answers are checked with simple rules plus an LLM judge (Claude Sonnet 5.5) that asks whether every claim is supported by the cited source, and a headless browser test checks that the widget sends a typed question. The suite fails below a 90% pass rate so it can gate deploys, and a separate holdout set that was never used for tuning guards against overfitting.

## The portfolio site
This portfolio website is also a personal project from 2026: a static site (HTML, CSS and plain JavaScript, with no build step) that presents his experience and featured work, with citation markers that show the evidence behind claims. It is served by the same FastAPI app as Ask Nitin, at the same URL.

## How it was built
Nitin built the portfolio site and Ask Nitin with AI coding agents (Claude Code) in a leader/worker setup. He wrote the step-by-step specifications and ground rules; one agent implemented each step, a second agent independently tested and reviewed it, and Nitin approved every commit. An evaluation suite gated changes, and the reviews caught real issues before they shipped, for example a rate-limit bypass through a spoofed X-Forwarded-For header and a broken Enter-to-send in the chat box. The code was written by the AI agents under his direction and review, not typed entirely by hand.
