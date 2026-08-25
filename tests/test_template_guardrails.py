import tempfile
import unittest
from pathlib import Path

from src.literature_agent import template_guardrails as tg
from src.literature_agent import writing_workspace as ww


class TemplateGuardrailsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="sa-template-guardrails-")
        self.original_output_dir = ww.DEFAULT_OUTPUT_DIR
        ww.DEFAULT_OUTPUT_DIR = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        ww.DEFAULT_OUTPUT_DIR = self.original_output_dir
        self.temp_dir.cleanup()

    def test_load_guardrails_generates_memory_yaml(self) -> None:
        project = ww.create_project(
            {
                "project_id": "project-guardrail-yaml",
                "title": "Guardrails YAML",
                "files": [
                    {
                        "path": "main.tex",
                        "content": (
                            "\\documentclass{article}\n"
                            "\\begin{document}\n"
                            "\\section{引言}\n"
                            "正文。\n"
                            "\\section{参考文献}\n"
                            "\\end{document}\n"
                        ),
                    }
                ],
            }
        )
        guardrails = tg.load_guardrails(project["project_id"])
        self.assertEqual(guardrails["schema_version"], 1)
        self.assertTrue((ww._memory_dir(project["project_id"]) / "guardrails.yaml").exists())
        self.assertGreaterEqual(len(guardrails.get("sections") or []), 1)

    def test_strip_illegal_content_restores_title_and_removes_unknown_section(self) -> None:
        guardrails = {
            "defaults": {"top_level_heading": r"\section"},
            "sections": [
                {
                    "id": "intro",
                    "title": "引言",
                    "heading": r"\section",
                    "title_immutable": True,
                    "allow_subsections": True,
                    "allow_subsubsections": False,
                },
                {
                    "id": "refs",
                    "title": "参考文献",
                    "heading": r"\section",
                    "title_immutable": True,
                },
            ],
        }
        existing = "\\begin{document}\n\\section{引言}\nOld.\n\\section{参考文献}\n\\end{document}\n"
        new = "\\begin{document}\n\\section{背景介绍}\nNew.\n\\section{实验设置}\nMore.\n\\section{参考文献}\n\\end{document}\n"
        sanitized, violations = tg.strip_illegal_content(new, existing, guardrails, section_id="intro")
        self.assertIn("\\section{引言}", sanitized)
        self.assertNotIn("\\section{实验设置}", sanitized)
        self.assertGreaterEqual(len(violations), 2)

    def test_save_project_file_reports_guardrail_violations(self) -> None:
        project = ww.create_project(
            {
                "project_id": "project-guardrail-save",
                "title": "Guardrails Save",
                "files": [
                    {
                        "path": "main.tex",
                        "content": (
                            "\\documentclass{article}\n"
                            "\\begin{document}\n"
                            "\\section{引言}\n"
                            "Old.\n"
                            "\\section{参考文献}\n"
                            "\\end{document}\n"
                        ),
                    }
                ],
            }
        )
        saved = ww.save_project_file(
            {
                "project_id": project["project_id"],
                "path": "main.tex",
                "content": (
                    "\\begin{document}\n"
                    "\\section{错误标题}\n"
                    "Updated.\n"
                    "\\section{新章节}\n"
                    "Oops.\n"
                    "\\section{参考文献}\n"
                    "\\end{document}\n"
                ),
                "preserve_structure": True,
            }
        )
        self.assertTrue(saved["guardrails"]["violations"])
        self.assertIn("\\section{引言}", saved["content"])
        self.assertNotIn("\\section{新章节}", saved["content"])

    def test_project_guardrails_yaml_roundtrip(self) -> None:
        project = ww.create_project(
            {
                "project_id": "project-guardrail-roundtrip",
                "title": "Guardrails Roundtrip",
                "files": [
                    {
                        "path": "main.tex",
                        "content": (
                            "\\documentclass{article}\n"
                            "\\begin{document}\n"
                            "\\section{引言}\n"
                            "正文。\n"
                            "\\end{document}\n"
                        ),
                    }
                ],
            }
        )
        original = tg.read_project_guardrails_yaml(project["project_id"])
        updated = original + "\nmetadata:\n  note: test\n"
        payload = tg.save_project_guardrails_yaml(project["project_id"], updated)
        self.assertEqual(payload["metadata"]["note"], "test")
        roundtrip = tg.read_project_guardrails_yaml(project["project_id"])
        self.assertIn("note: test", roundtrip)

    def test_analyze_project_template_guardrails_falls_back_without_api_key(self) -> None:
        project = ww.create_project(
            {
                "project_id": "project-guardrail-analyze",
                "title": "Guardrails Analyze",
                "files": [
                    {
                        "path": "main.tex",
                        "content": (
                            "\\documentclass{article}\n"
                            "\\begin{document}\n"
                            "\\section{背景}\n"
                            "\\section{方法}\n"
                            "\\section{参考文献}\n"
                            "\\end{document}\n"
                        ),
                    }
                ],
            }
        )
        payload = tg.analyze_project_template_guardrails(project["project_id"], api_key="")
        titles = [item["title"] for item in payload.get("sections") or []]
        self.assertIn("背景", titles)
        self.assertIn("方法", titles)


if __name__ == "__main__":
    unittest.main()
