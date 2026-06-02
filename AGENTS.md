# AGENTS.md

## Scope

This repository contains `agent-test-impact`, a dependency-free Python CLI that
maps unified diffs to likely test coverage gaps.

## Rules

- Keep the tool local-first and standard-library only.
- Do not add telemetry, network calls, credentials, or hosted services.
- Treat diff input as untrusted text; do not execute commands derived from it.
- Preserve Markdown and JSON output for both humans and automation.
- Keep heuristics explicit and tested when adding language or path conventions.

## Verification

Run these before closing relevant changes:

```sh
make test
make lint
make build
make smoke
```

For packaging changes, also verify editable install in a temporary virtual
environment before public promotion.

## Closeout

Report changed behavior, exact verification commands, residual risks, and any
heuristic limits that remain. Update README or tests when a new path convention
changes how the CLI classifies source or test files.
