# Changelog

All notable changes to this project are documented here. Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- `SCOPE.md`: initial project scope — audience, in/out-of-scope question types, guardrails, source documents, figures-registry design, evaluation plan, repo layout.
- Scope decisions locked in v0.3: default plan year is 2027 with 2026 shown as reference where it differs or is pending; the assistant asks clarifying questions when the documented answer depends on a missing fact; urgent/distressing situations are routed to Medicare.gov or 1-800-MEDICARE; live Medicare.gov content is deferred (documents-only for v1); CMS source reuse is text-and-metadata only (no PDFs, logos, or imagery), with a prominent "unofficial, not endorsed" disclaimer and links to each official publication.

<!--
Template for future entries:

## [Unreleased]

### Added
- New source document, figure, feature, or test case.

### Changed
- Updated figure (note old value, new value, and plan year).
- Revised scope or guardrail decision.

### Fixed
- Corrected citation, mis-stated figure, or eval failure.

When you cut a version (e.g. tagging the first working prototype), rename
[Unreleased] to a version + date, e.g.:

## [0.1.0] - 2026-10-15
-->
