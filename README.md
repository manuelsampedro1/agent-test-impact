# agent-test-impact

Map coding-agent diffs to likely test coverage gaps before a closeout claims the
change was verified.

The tool is intentionally narrow. It does not measure runtime coverage, mutate a
test plan, or inspect private source files. It reads a unified diff, identifies
changed source and test files, then reports whether each source change has
direct, partial, or missing test evidence in the same diff.

Structured `agent-proof-packet.v1` files can be passed with `--proof-packet`.
They are validated against the same diff before their passing test-like checks
can count as packet-backed partial evidence. Packet checks never count as direct
nearby test coverage.

## Why

Coding agents often run broad checks after a change, but reviewers still need to
know whether the changed behavior has a nearby test signal.

Use this before:

- accepting a final answer that says tests passed,
- converting a proof packet into a merge-ready verdict,
- asking another agent to continue from a broad diff,
- publishing a proof repo where the tests should match the actual change.

## Install

```sh
python3 -m pip install --upgrade pip setuptools wheel
python3 -m pip install -e .
```

## Usage

Check a saved diff:

```sh
agent-test-impact examples/mixed-change.diff --min-score 70
```

JSON output for automation:

```sh
agent-test-impact examples/mixed-change.diff --format json --fail-on-missing
```

Use a proof packet as structured broad-check evidence:

```sh
agent-test-impact examples/mixed-change.diff \
  --proof-packet examples/proof-packet.json \
  --fail-on-missing
```

If no diff path is provided, the tool reads `git diff --unified=3` from `--repo`:

```sh
agent-test-impact --repo . --base origin/main --min-score 80
```

## What It Detects

- Changed source files in Python, JavaScript, TypeScript, Go, Rust, Ruby, Java,
  Kotlin, Swift, and C#.
- Changed test files using common conventions such as `tests/`, `__tests__/`,
  `test_*.py`, `*_test.go`, `*.test.ts`, `*.spec.ts`, `*Tests.swift`, and
  `*Tests.cs`.
- Direct test evidence when the changed test name or path matches the changed
  source stem.
- Partial test evidence when tests changed, but not near the changed source.
- Packet-backed partial evidence when a complete, diff-aligned proof packet has
  passing test-like checks for the changed source.
- Missing test evidence when source changed and no related tests changed.
- Suggested targeted checks by ecosystem.

## Output

Markdown output includes:

- source changes grouped by status,
- related changed tests or likely test paths,
- packet-backed checks when `--proof-packet` is used,
- suggested verification commands,
- an overall score and non-zero gate support.

JSON output exposes the same data for CI gates, proof packets, or agent ledgers.

## Limits

- This is not a replacement for coverage tooling.
- This does not prove a test asserts the right behavior.
- Proof-packet checks are broad evidence only; they keep status at `partial`
  unless a related changed test exists in the diff.
- Heuristics are path-based and conservative by design.
- A low score means "review test impact", not "the code is broken".

## Verify

```sh
make test
make lint
make build
make smoke
```
