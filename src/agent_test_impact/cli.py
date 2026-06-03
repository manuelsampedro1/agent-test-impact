from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence


SOURCE_EXTENSIONS = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".go",
    ".rs",
    ".rb",
    ".java",
    ".kt",
    ".swift",
    ".cs",
}

DOC_EXTENSIONS = {".md", ".rst", ".txt", ".adoc"}
CONFIG_NAMES = {
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "pyproject.toml",
    "requirements.txt",
    "setup.py",
    "setup.cfg",
    "go.mod",
    "go.sum",
    "Cargo.toml",
    "Cargo.lock",
    "Gemfile",
    "Gemfile.lock",
    "Makefile",
}


@dataclass(frozen=True)
class ChangedFile:
    path: str
    kind: str
    language: str
    stem: str


@dataclass(frozen=True)
class SourceImpact:
    source: str
    language: str
    status: str
    related_tests: list[str]
    packet_checks: list[str]
    likely_test_paths: list[str]
    suggested_checks: list[str]


@dataclass(frozen=True)
class ProofPacketAudit:
    path: str
    status: str
    verdict: str
    changed_files: list[str]
    checks: list[str]
    issues: list[dict[str, str]]


@dataclass(frozen=True)
class ImpactReport:
    score: int
    status: str
    changed_sources: list[SourceImpact]
    changed_tests: list[str]
    ignored_files: list[str]
    proof_packets: list[ProofPacketAudit]
    summary: dict[str, int]


def normalize_path(path: str) -> str:
    path = path.strip()
    if path in {"/dev/null", ""}:
        return ""
    if path.startswith("a/") or path.startswith("b/"):
        path = path[2:]
    return path.replace("\\", "/")


def parse_changed_paths(diff_text: str) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    current_new = ""
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                candidate = normalize_path(parts[3])
                if candidate:
                    current_new = candidate
        elif line.startswith("+++ "):
            candidate = normalize_path(line[4:])
            if candidate:
                current_new = candidate
                if candidate not in seen:
                    paths.append(candidate)
                    seen.add(candidate)
        elif line.startswith("rename to "):
            candidate = normalize_path(line[len("rename to ") :])
            if candidate and candidate not in seen:
                paths.append(candidate)
                seen.add(candidate)
    if not paths and current_new:
        paths.append(current_new)
    return paths


def language_for(path: str) -> str:
    suffix = PurePosixPath(path).suffix.lower()
    return {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".go": "go",
        ".rs": "rust",
        ".rb": "ruby",
        ".java": "java",
        ".kt": "kotlin",
        ".swift": "swift",
        ".cs": "csharp",
    }.get(suffix, "unknown")


def clean_stem(path: str) -> str:
    name = PurePosixPath(path).name
    suffix = PurePosixPath(path).suffix
    stem = name[: -len(suffix)] if suffix else name
    for marker in (".test", ".spec", "_test", "Tests", "Test"):
        if stem.endswith(marker):
            stem = stem[: -len(marker)]
    if stem.startswith("test_"):
        stem = stem[len("test_") :]
    return re.sub(r"[^a-z0-9]+", "", stem.lower())


def is_test_path(path: str) -> bool:
    normalized = path.lower()
    name = PurePosixPath(normalized).name
    parts = normalized.split("/")
    if any(part in {"tests", "test", "__tests__", "spec", "specs"} for part in parts):
        return True
    return any(
        (
            name.startswith("test_"),
            name.endswith("_test.py"),
            name.endswith("_test.go"),
            ".test." in name,
            ".spec." in name,
            name.endswith("tests.swift"),
            name.endswith("test.swift"),
            name.endswith("tests.cs"),
            name.endswith("test.cs"),
        )
    )


def classify_path(path: str) -> ChangedFile:
    name = PurePosixPath(path).name
    suffix = PurePosixPath(path).suffix.lower()
    if is_test_path(path):
        kind = "test"
    elif suffix in SOURCE_EXTENSIONS and name not in CONFIG_NAMES:
        kind = "source"
    elif suffix in DOC_EXTENSIONS:
        kind = "docs"
    elif name in CONFIG_NAMES or path.startswith(".github/"):
        kind = "config"
    else:
        kind = "other"
    return ChangedFile(path=path, kind=kind, language=language_for(path), stem=clean_stem(path))


def likely_tests_for(path: str, language: str) -> list[str]:
    posix = PurePosixPath(path)
    suffix = posix.suffix
    stem = posix.stem
    parent = str(posix.parent)
    parent = "" if parent == "." else parent

    candidates: list[str] = []
    if language == "python":
        candidates.extend(
            [
                f"tests/test_{stem}.py",
                f"tests/{stem}_test.py",
                f"{parent}/test_{stem}.py" if parent else f"test_{stem}.py",
            ]
        )
        if parent.startswith("src/"):
            candidates.append(f"tests/{parent[4:]}/test_{stem}.py")
    elif language in {"javascript", "typescript"}:
        candidates.extend(
            [
                f"{parent}/{stem}.test{suffix}" if parent else f"{stem}.test{suffix}",
                f"{parent}/{stem}.spec{suffix}" if parent else f"{stem}.spec{suffix}",
                f"tests/{stem}.test{suffix}",
                f"__tests__/{stem}.test{suffix}",
            ]
        )
        if parent.startswith("src/"):
            candidates.append(f"tests/{parent[4:]}/{stem}.test{suffix}")
    elif language == "go":
        candidates.append(f"{parent}/{stem}_test.go" if parent else f"{stem}_test.go")
    elif language == "rust":
        candidates.extend(
            [
                f"tests/{stem}.rs",
                f"{parent}/{stem}_test.rs" if parent else f"{stem}_test.rs",
            ]
        )
    elif language == "ruby":
        candidates.extend([f"spec/{stem}_spec.rb", f"test/{stem}_test.rb"])
    elif language in {"java", "kotlin"}:
        candidates.extend(
            [
                f"src/test/{stem}Test{suffix}",
                f"src/test/{stem}Tests{suffix}",
            ]
        )
    elif language == "swift":
        candidates.extend([f"Tests/{stem}Tests.swift", f"{parent}/{stem}Tests.swift"])
    elif language == "csharp":
        candidates.extend([f"Tests/{stem}Tests.cs", f"{parent}/{stem}Tests.cs"])
    return dedupe(c for c in candidates if c)


def suggested_checks_for(source: str, language: str, likely_paths: Sequence[str]) -> list[str]:
    base_stem = PurePosixPath(source).stem
    if language == "python":
        path_hint = likely_paths[0] if likely_paths else f"tests/test_{base_stem}.py"
        return [f"python3 -m unittest discover -s tests", f"python3 -m pytest {path_hint}"]
    if language in {"javascript", "typescript"}:
        return [f"npm test -- {base_stem}", "npm test"]
    if language == "go":
        package_dir = str(PurePosixPath(source).parent)
        return [f"go test ./{package_dir}", "go test ./..."]
    if language == "rust":
        return [f"cargo test {base_stem}", "cargo test"]
    if language == "ruby":
        return ["bundle exec rspec", "ruby -Itest"]
    if language in {"java", "kotlin"}:
        return ["./gradlew test", "mvn test"]
    if language == "swift":
        return ["swift test"]
    if language == "csharp":
        return ["dotnet test"]
    return ["run the nearest targeted test for this source change"]


def dedupe(values: Iterable[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            output.append(value)
            seen.add(value)
    return output


def normalize_proof_path(path: str) -> str:
    normalized = normalize_path(path)
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def proof_issue(severity: str, code: str, message: str, evidence: str, recommendation: str) -> dict[str, str]:
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "evidence": evidence,
        "recommendation": recommendation,
    }


def audit_proof_packet(path: Path, diff_paths: Sequence[str]) -> ProofPacketAudit:
    issues: list[dict[str, str]] = []
    verdict = ""
    changed_files: list[str] = []
    checks: list[str] = []

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        return ProofPacketAudit(
            str(path),
            "fail",
            "",
            [],
            [],
            [
                proof_issue(
                    "high",
                    "proof_packet_unreadable",
                    f"Proof packet could not be read: {error}",
                    str(path),
                    "Pass a readable agent-proof-packet.v1 JSON file.",
                )
            ],
        )
    except json.JSONDecodeError as error:
        return ProofPacketAudit(
            str(path),
            "fail",
            "",
            [],
            [],
            [
                proof_issue(
                    "high",
                    "proof_packet_invalid_json",
                    f"Proof packet is not valid JSON: {error}",
                    str(path),
                    "Regenerate the proof packet as valid JSON.",
                )
            ],
        )

    if payload.get("schema_version") != "agent-proof-packet.v1":
        issues.append(
            proof_issue(
                "high",
                "proof_packet_wrong_schema",
                "Proof packet schema_version is not agent-proof-packet.v1.",
                str(path),
                "Use an agent-proof-packet.v1 JSON proof packet.",
            )
        )

    verdict = str(payload.get("verdict", "")).strip()
    if verdict != "complete":
        issues.append(
            proof_issue(
                "high",
                "proof_packet_incomplete",
                f"Proof packet verdict is {verdict or 'missing'}, not complete.",
                str(path),
                "Resolve missing evidence before using the packet as test-impact evidence.",
            )
        )

    raw_changed_files = payload.get("changed_files")
    if not isinstance(raw_changed_files, list) or not raw_changed_files:
        issues.append(
            proof_issue(
                "high",
                "proof_packet_missing_changed_files",
                "Proof packet has no changed-file evidence.",
                str(path),
                "Regenerate the packet from the actual diff.",
            )
        )
    else:
        for item in raw_changed_files:
            if isinstance(item, dict) and isinstance(item.get("path"), str) and item["path"].strip():
                changed_files.append(normalize_proof_path(item["path"]))
            else:
                issues.append(
                    proof_issue(
                        "high",
                        "proof_packet_invalid_changed_file",
                        "Proof packet contains an invalid changed_files entry.",
                        str(path),
                        "Keep changed_files entries shaped as objects with a path.",
                    )
                )

    raw_checks = payload.get("checks")
    check_statuses: list[str] = []
    if not isinstance(raw_checks, list) or not raw_checks:
        issues.append(
            proof_issue(
                "high",
                "proof_packet_missing_checks",
                "Proof packet has no checks.",
                str(path),
                "Include at least one passing check before using packet evidence.",
            )
        )
    else:
        for item in raw_checks:
            if not isinstance(item, dict):
                issues.append(
                    proof_issue(
                        "high",
                        "proof_packet_invalid_check",
                        "Proof packet contains an invalid check entry.",
                        str(path),
                        "Keep checks shaped as JSON objects.",
                    )
                )
                continue
            name = str(item.get("name", "")).strip()
            status = str(item.get("status", "")).strip()
            detail = str(item.get("detail", "")).strip()
            if not name or not status:
                issues.append(
                    proof_issue(
                        "high",
                        "proof_packet_invalid_check",
                        "Proof packet contains a nameless or statusless check.",
                        str(path),
                        "Keep checks shaped as objects with name and status.",
                    )
                )
                continue
            checks.append(f"{status}: {name}" + (f" - {detail}" if detail else ""))
            check_statuses.append(status)

    if check_statuses and any(status == "fail" for status in check_statuses):
        issues.append(
            proof_issue(
                "high",
                "proof_packet_failing_checks",
                "Proof packet includes failing checks.",
                str(path),
                "Do not use failing packet checks as test-impact evidence.",
            )
        )
    if not any(status == "pass" for status in check_statuses):
        issues.append(
            proof_issue(
                "high",
                "proof_packet_no_passing_checks",
                "Proof packet has no passing checks.",
                str(path),
                "Add passing verification evidence before using the packet.",
            )
        )

    missing_evidence = payload.get("missing_evidence")
    if isinstance(missing_evidence, list) and missing_evidence:
        issues.append(
            proof_issue(
                "high",
                "proof_packet_missing_evidence",
                "Proof packet still has missing evidence.",
                ", ".join(str(item) for item in missing_evidence[:5]),
                "Resolve missing evidence before using packet checks.",
            )
        )

    open_questions = payload.get("open_questions")
    if isinstance(open_questions, list) and open_questions:
        issues.append(
            proof_issue(
                "medium",
                "proof_packet_open_questions",
                "Proof packet still has open questions.",
                ", ".join(str(item) for item in open_questions[:5]),
                "Carry open questions into test-impact review instead of treating the packet as complete proof.",
            )
        )

    diff_file_set = set(diff_paths)
    packet_file_set = set(changed_files)
    if diff_file_set and packet_file_set and diff_file_set != packet_file_set:
        issues.append(
            proof_issue(
                "high",
                "proof_packet_diff_mismatch",
                "Proof packet changed files do not match the provided diff.",
                f"diff={sorted(diff_file_set)} packet={sorted(packet_file_set)}",
                "Regenerate the packet from the exact diff before using it as test-impact evidence.",
            )
        )

    status = "fail" if any(issue["severity"] == "high" for issue in issues) else "pass"
    return ProofPacketAudit(str(path), status, verdict, changed_files, checks, issues)


def passing_test_packet_checks(source: ChangedFile, packets: Sequence[ProofPacketAudit]) -> list[str]:
    checks: list[str] = []
    for packet in packets:
        if packet.status != "pass" or packet.verdict != "complete":
            continue
        if source.path not in packet.changed_files:
            continue
        for check in packet.checks:
            normalized = check.lower()
            if "pass:" in normalized and ("test" in normalized or "smoke" in normalized or "ci" in normalized):
                checks.append(f"{packet.path}: {check}")
    return dedupe(checks)


def related_tests(source: ChangedFile, tests: Sequence[ChangedFile]) -> list[str]:
    likely = set(likely_tests_for(source.path, source.language))
    related: list[str] = []
    source_parts = set(re.findall(r"[a-z0-9]+", source.path.lower()))
    for test in tests:
        test_parts = set(re.findall(r"[a-z0-9]+", test.path.lower()))
        if test.path in likely:
            related.append(test.path)
        elif source.stem and test.stem and source.stem == test.stem:
            related.append(test.path)
        elif source.stem and source.stem in test.path.replace("-", "_").lower():
            related.append(test.path)
        elif source_parts and len(source_parts & test_parts) >= 2:
            related.append(test.path)
    return dedupe(related)


def build_report(paths: Sequence[str], proof_packets: Sequence[ProofPacketAudit] = ()) -> ImpactReport:
    files = [classify_path(path) for path in paths]
    sources = [file for file in files if file.kind == "source"]
    tests = [file for file in files if file.kind == "test"]
    ignored = [file.path for file in files if file.kind not in {"source", "test"}]

    impacts: list[SourceImpact] = []
    summary = {"covered": 0, "partial": 0, "missing": 0}
    for source in sources:
        likely = likely_tests_for(source.path, source.language)
        related = related_tests(source, tests)
        packet_checks = passing_test_packet_checks(source, proof_packets)
        if related:
            status = "covered"
        elif tests:
            status = "partial"
        elif packet_checks:
            status = "partial"
        else:
            status = "missing"
        summary[status] += 1
        impacts.append(
            SourceImpact(
                source=source.path,
                language=source.language,
                status=status,
                related_tests=related,
                packet_checks=packet_checks,
                likely_test_paths=likely,
                suggested_checks=suggested_checks_for(source.path, source.language, likely),
            )
        )

    if not sources:
        score = 100
    else:
        score = max(0, 100 - (summary["missing"] * 35) - (summary["partial"] * 15))
    status = "pass" if summary["missing"] == 0 else "attention"
    return ImpactReport(
        score=score,
        status=status,
        changed_sources=impacts,
        changed_tests=[file.path for file in tests],
        ignored_files=ignored,
        proof_packets=list(proof_packets),
        summary=summary,
    )


def render_markdown(report: ImpactReport) -> str:
    lines = [
        "# Agent Test Impact",
        "",
        f"Status: {report.status}",
        f"Score: {report.score}/100",
        "",
        "## Summary",
        "",
        f"- Covered source changes: {report.summary['covered']}",
        f"- Partial source changes: {report.summary['partial']}",
        f"- Missing source test evidence: {report.summary['missing']}",
        f"- Changed test files: {len(report.changed_tests)}",
        "",
    ]
    if report.changed_sources:
        lines.extend(["## Source Impact", ""])
        for item in report.changed_sources:
            lines.append(f"### {item.source}")
            lines.append("")
            lines.append(f"- Status: {item.status}")
            lines.append(f"- Language: {item.language}")
            if item.related_tests:
                lines.append(f"- Related changed tests: {', '.join(item.related_tests)}")
            if item.packet_checks:
                lines.append(f"- Packet-backed checks: {'; '.join(item.packet_checks)}")
            if not item.related_tests and not item.packet_checks:
                likely = ", ".join(item.likely_test_paths[:4]) or "none inferred"
                lines.append(f"- Likely test paths: {likely}")
            checks = "; ".join(item.suggested_checks[:3])
            lines.append(f"- Suggested checks: {checks}")
            lines.append("")
    else:
        lines.extend(["## Source Impact", "", "No source-code changes detected.", ""])

    if report.changed_tests:
        lines.extend(["## Changed Tests", ""])
        lines.extend(f"- {path}" for path in report.changed_tests)
        lines.append("")
    if report.ignored_files:
        lines.extend(["## Ignored Changed Files", ""])
        lines.extend(f"- {path}" for path in report.ignored_files)
        lines.append("")
    if report.proof_packets:
        lines.extend(["## Proof Packets", ""])
        for packet in report.proof_packets:
            lines.append(
                f"- `{packet.status}` `{packet.path}`: verdict `{packet.verdict or 'missing'}`, "
                f"{len(packet.changed_files)} files, {len(packet.checks)} checks"
            )
            for issue in packet.issues:
                lines.append(f"  - `{issue['severity']}` `{issue['code']}`: {issue['message']}")
                lines.append(f"    Evidence: {issue['evidence']}")
                lines.append(f"    Next: {issue['recommendation']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def load_diff(args: argparse.Namespace) -> str:
    if args.diff:
        with open(args.diff, "r", encoding="utf-8") as handle:
            return handle.read()

    command = ["git", "diff", "--no-ext-diff", "--unified=3"]
    if args.base:
        command.append(args.base)
    try:
        result = subprocess.run(
            command,
            cwd=args.repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError as exc:
        raise SystemExit("git is required when no diff path is provided") from exc
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or "git diff failed")
    return result.stdout


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Map coding-agent diffs to likely test coverage gaps.",
    )
    parser.add_argument("diff", nargs="?", help="Path to a unified diff file.")
    parser.add_argument("--repo", default=".", help="Repository path for git diff fallback.")
    parser.add_argument("--base", help="Optional git diff base when no diff path is provided.")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    parser.add_argument("--min-score", type=int, default=0, help="Fail when score is below this value.")
    parser.add_argument("--fail-on-missing", action="store_true", help="Fail when any source change has no related test evidence.")
    parser.add_argument(
        "--proof-packet",
        action="append",
        default=[],
        help="agent-proof-packet.v1 JSON file to validate against the diff and use as packet-backed test evidence. Can be repeated.",
    )
    return parser


def main(argv: Sequence[str] = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    if args.diff and not os.path.exists(args.diff):
        parser.error(f"diff file not found: {args.diff}")
    diff_text = load_diff(args)
    paths = parse_changed_paths(diff_text)
    proof_packets = [audit_proof_packet(Path(path), paths) for path in args.proof_packet]
    report = build_report(paths, proof_packets)

    if args.format == "json":
        print(json.dumps(asdict(report), indent=2, sort_keys=True))
    else:
        sys.stdout.write(render_markdown(report))

    failed = report.score < args.min_score
    failed = failed or (args.fail_on_missing and report.summary["missing"] > 0)
    failed = failed or any(
        issue["severity"] == "high"
        for packet in report.proof_packets
        for issue in packet.issues
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
