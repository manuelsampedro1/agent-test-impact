from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
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
    likely_test_paths: list[str]
    suggested_checks: list[str]


@dataclass(frozen=True)
class ImpactReport:
    score: int
    status: str
    changed_sources: list[SourceImpact]
    changed_tests: list[str]
    ignored_files: list[str]
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


def build_report(paths: Sequence[str]) -> ImpactReport:
    files = [classify_path(path) for path in paths]
    sources = [file for file in files if file.kind == "source"]
    tests = [file for file in files if file.kind == "test"]
    ignored = [file.path for file in files if file.kind not in {"source", "test"}]

    impacts: list[SourceImpact] = []
    summary = {"covered": 0, "partial": 0, "missing": 0}
    for source in sources:
        likely = likely_tests_for(source.path, source.language)
        related = related_tests(source, tests)
        if related:
            status = "covered"
        elif tests:
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
            else:
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
    return parser


def main(argv: Sequence[str] = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    if args.diff and not os.path.exists(args.diff):
        parser.error(f"diff file not found: {args.diff}")
    diff_text = load_diff(args)
    paths = parse_changed_paths(diff_text)
    report = build_report(paths)

    if args.format == "json":
        print(json.dumps(asdict(report), indent=2, sort_keys=True))
    else:
        sys.stdout.write(render_markdown(report))

    failed = report.score < args.min_score
    failed = failed or (args.fail_on_missing and report.summary["missing"] > 0)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
