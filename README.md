# Medicare Q&A Tool

A question-and-answer tool for Medicare beneficiaries and caregivers, built on Claude, with a document-grounded pipeline and an evaluation harness on top of it.

**Unofficial project.** Not affiliated with or endorsed by CMS, Medicare, or the U.S. government. Not legal, financial, or medical advice. See [SCOPE.md](./SCOPE.md) for the full reasoning behind these boundaries.

## What this is

- Answers general Medicare questions (coverage, enrollment timing, costs, penalties) in plain language, cited to the source CMS publication and page.
- Labels every dollar figure with its plan year, and says plainly when a figure (like the 2027 Part B premium) hasn't been announced yet, rather than guessing or mixing years.
- Declines to recommend a specific plan; explains the documented trade-offs and points to a SHIP counselor or 1-800-MEDICARE instead.
- Runs locally with your own Anthropic API key — no hosted service, no shared cost.

## Why

Full scope, guardrails, source documents, and the evaluation plan live in [SCOPE.md](./SCOPE.md). Short version: this is both a useful tool and a case study in building a small RAG pipeline with an eval harness — the pipeline, the evals, and the product decisions are all part of the deliverable, not just the code.

## Status

Early scaffolding. See [CHANGELOG.md](./CHANGELOG.md) for what's landed.

## Setup

```bash
cp .env.example .env
# add your own ANTHROPIC_API_KEY to .env
```

(Nothing to run yet — the system prompt, figures registry, and eval set come next.)
