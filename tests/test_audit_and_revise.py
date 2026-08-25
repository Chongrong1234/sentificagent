"""Comprehensive tests for audit→revise pipeline, citation fixes, and template comprehension."""

import tempfile
import unittest
from pathlib import Path

from src.literature_agent.writing_audit import (
    AuditIssue,
    AuditReport,
    audit_fix_prompt,
)
from src.literature_agent.writing_workspace import (
    _remove_unknown_citations,
    _SUPPORTED_CITE_COMMANDS,
)


class CitationRemovalTests(unittest.TestCase):
    """Tests for _remove_unknown_citations behavior."""

    def test_unknown_citation_returns_comment_placeholder(self) -> None:
        result = _remove_unknown_citations(
            r"revealed by~\cite{unknownkey}",
            {"validkey"},
        )
        self.assertIn("%[cite:", result)
        self.assertIn("unknownkey", result)
        self.assertNotIn("~", result, "orphaned tilde should be cleaned")

    def test_partially_valid_citation_keeps_good_keys(self) -> None:
        result = _remove_unknown_citations(
            r"\citep{good,bad}",
            {"good"},
        )
        self.assertIn(r"\citep{good}", result)
        self.assertNotIn("bad", result)

    def test_all_valid_citations_preserved(self) -> None:
        result = _remove_unknown_citations(
            r"\cite{key1,key2}",
            {"key1", "key2"},
        )
        self.assertIn(r"\cite{key1,key2}", result)

    def test_empty_allowed_keys_passes_through_unchanged(self) -> None:
        original = r"\cite{anything}"
        result = _remove_unknown_citations(original, set())
        self.assertEqual(result, original)

    def test_multiple_cite_commands_in_one_text(self) -> None:
        result = _remove_unknown_citations(
            r"\cite{good} and \citep{bad} explained",
            {"good"},
        )
        self.assertIn(r"\cite{good}", result)
        self.assertIn("%[cite:", result)
        self.assertIn("bad", result)

    def test_parenthesized_unknown_citations_cleaned(self) -> None:
        result = _remove_unknown_citations(
            r"(\cite{bad1,bad2})",
            {"valid"},
        )
        self.assertIn("%[cite:", result)
        self.assertNotIn("(%[cite:", result)
        self.assertNotIn("()", result)

    def test_parenthesized_citation_no_orphaned_parens(self) -> None:
        result = _remove_unknown_citations(
            r"see (\cite{unk}) for details",
            {"known"},
        )
        self.assertNotIn("()", result)
        self.assertNotIn("(%[cite:", result)
        self.assertIn("%[cite:", result)

    def test_footcitetext_before_footcite_in_tuple(self) -> None:
        idx_ft = _SUPPORTED_CITE_COMMANDS.index("footcitetext")
        idx_fc = _SUPPORTED_CITE_COMMANDS.index("footcite")
        self.assertLess(idx_ft, idx_fc, "footcitetext must precede footcite")


class AuditReportTests(unittest.TestCase):
    """Tests for AuditReport and audit_fix_prompt."""

    def test_empty_audit_report_returns_accept_message(self) -> None:
        report = AuditReport(
            project_id="test",
            version="1",
            verdict="ACCEPT",
            issues=[],
            scores={},
            overall_score=95.0,
        )
        prompt = audit_fix_prompt(report)
        self.assertIn("无需修改", prompt)

    def test_audit_report_with_errors_includes_all_sections(self) -> None:
        issues = [
            AuditIssue(
                mode="D",
                severity="error",
                location="main.tex:42",
                category="citation",
                description="Citation key 'unk1' not found in .bib",
                fix_suggestion="Add entry for 'unk1' or remove citation",
            ),
            AuditIssue(
                mode="C",
                severity="warning",
                location="main.tex:100",
                category="syntax",
                description="Unbalanced braces",
                fix_suggestion="Add closing brace",
            ),
            AuditIssue(
                mode="E",
                severity="info",
                location="",
                category="quality",
                description="Section could be more detailed",
                fix_suggestion="Expand with more evidence",
            ),
        ]
        report = AuditReport(
            project_id="test-123",
            version="3",
            verdict="REVISE",
            issues=issues,
            scores={"scientific_depth": 72, "writing_clarity": 85},
            overall_score=74.5,
        )
        prompt = audit_fix_prompt(report)

        # Should contain all required sections
        self.assertIn("test-123", prompt)
        self.assertIn("REVISE", prompt)
        self.assertIn("必须修复（error）", prompt)
        self.assertIn("建议修复（warning）", prompt)
        self.assertIn("改进提示（info）", prompt)
        self.assertIn("unk1", prompt)
        self.assertIn("Unbalanced braces", prompt)
        self.assertIn("72/100", prompt)
        self.assertIn("74.5/100", prompt)
        self.assertIn("修复指示", prompt)

    def test_audit_report_issue_count_correct(self) -> None:
        report = AuditReport(
            project_id="t", version="1", verdict="REVISE",
            issues=[
                AuditIssue("A", "error", "f:1", "cat", "desc", "fix"),
                AuditIssue("B", "error", "f:2", "cat", "desc", "fix"),
                AuditIssue("C", "warning", "f:3", "cat", "desc", "fix"),
            ],
            scores={}, overall_score=0,
        )
        prompt = audit_fix_prompt(report)
        self.assertIn("3 (2 错误, 1 警告, 0 提示)", prompt)


class AuditIssueTests(unittest.TestCase):
    """Tests for AuditIssue dataclass."""

    def test_issue_creation_all_fields(self) -> None:
        issue = AuditIssue(
            mode="D",
            severity="error",
            location="refs.bib:15",
            category="citation",
            description="Missing required field 'year'",
            fix_suggestion="Add year = {2024}",
        )
        self.assertEqual(issue.mode, "D")
        self.assertEqual(issue.severity, "error")
        self.assertEqual(issue.location, "refs.bib:15")
        self.assertEqual(issue.category, "citation")


class CommentDetectionTests(unittest.TestCase):
    """Tests for bibliography comment detection fix."""

    def test_double_backslash_percent_is_comment(self) -> None:
        import re

        line_prefix = r"\\% \bibliography{refs}"
        # Old pattern would miss this
        old_match = bool(re.search(r"(?<!\\)%", line_prefix))
        # New pattern should detect it
        new_match = bool(re.search(r"(?<!\\)(?:\\\\)*%", line_prefix))
        self.assertFalse(old_match, "old pattern incorrectly treats \\\\% as escaped")
        self.assertTrue(new_match, "new pattern should detect \\\\% as comment")

    def test_escaped_percent_not_comment(self) -> None:
        import re

        line_prefix = r"100\% \bibliography{refs}"
        new_match = bool(re.search(r"(?<!\\)(?:\\\\)*%", line_prefix))
        self.assertFalse(new_match, r"\% should NOT be detected as comment")

    def test_simple_percent_is_comment(self) -> None:
        import re

        line_prefix = r"text % \bibliography{refs}"
        new_match = bool(re.search(r"(?<!\\)(?:\\\\)*%", line_prefix))
        self.assertTrue(new_match, "plain % should be detected as comment")


if __name__ == "__main__":
    unittest.main()
