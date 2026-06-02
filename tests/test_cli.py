import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from agent_test_impact import cli


MISSING_DIFF = """diff --git a/src/auth/session.ts b/src/auth/session.ts
--- a/src/auth/session.ts
+++ b/src/auth/session.ts
@@ -1,3 +1,3 @@
-old
+new
"""


COVERED_DIFF = """diff --git a/src/billing/invoice.py b/src/billing/invoice.py
--- a/src/billing/invoice.py
+++ b/src/billing/invoice.py
@@ -1,3 +1,3 @@
-old
+new
diff --git a/tests/test_invoice.py b/tests/test_invoice.py
--- a/tests/test_invoice.py
+++ b/tests/test_invoice.py
@@ -1,3 +1,3 @@
-old
+new
"""


PARTIAL_DIFF = """diff --git a/src/auth/session.ts b/src/auth/session.ts
--- a/src/auth/session.ts
+++ b/src/auth/session.ts
@@ -1,3 +1,3 @@
-old
+new
diff --git a/tests/test_profile.ts b/tests/test_profile.ts
--- a/tests/test_profile.ts
+++ b/tests/test_profile.ts
@@ -1,3 +1,3 @@
-old
+new
"""


DOCS_DIFF = """diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1,2 +1,3 @@
 # Tool
+docs
"""


class TestAgentTestImpact(unittest.TestCase):
    def test_detects_missing_test_evidence(self):
        paths = cli.parse_changed_paths(MISSING_DIFF)
        report = cli.build_report(paths)
        self.assertEqual(report.summary["missing"], 1)
        self.assertEqual(report.status, "attention")
        self.assertLess(report.score, 100)
        self.assertIn("src/auth/session.ts", report.changed_sources[0].source)

    def test_detects_related_changed_test(self):
        paths = cli.parse_changed_paths(COVERED_DIFF)
        report = cli.build_report(paths)
        self.assertEqual(report.summary["covered"], 1)
        self.assertEqual(report.changed_sources[0].related_tests, ["tests/test_invoice.py"])
        self.assertEqual(report.status, "pass")

    def test_detects_partial_test_evidence(self):
        paths = cli.parse_changed_paths(PARTIAL_DIFF)
        report = cli.build_report(paths)
        self.assertEqual(report.summary["partial"], 1)
        self.assertEqual(report.summary["missing"], 0)
        self.assertEqual(report.status, "pass")

    def test_docs_only_scores_clean(self):
        report = cli.build_report(cli.parse_changed_paths(DOCS_DIFF))
        self.assertEqual(report.score, 100)
        self.assertEqual(report.changed_sources, [])
        self.assertEqual(report.ignored_files, ["README.md"])

    def test_json_output_and_fail_on_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            diff_path = Path(tmp) / "change.diff"
            diff_path.write_text(MISSING_DIFF, encoding="utf-8")
            stderr = io.StringIO()
            with self.assertRaises(SystemExit), redirect_stderr(stderr):
                cli.make_parser().parse_args(["--format", "xml"])
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = cli.main([str(diff_path), "--format", "json", "--fail-on-missing"])
            self.assertEqual(code, 1)

    def test_json_shape(self):
        report = cli.build_report(cli.parse_changed_paths(COVERED_DIFF))
        payload = json.loads(json.dumps(cli.asdict(report)))
        self.assertEqual(payload["score"], 100)
        self.assertEqual(payload["changed_sources"][0]["status"], "covered")


if __name__ == "__main__":
    unittest.main()
