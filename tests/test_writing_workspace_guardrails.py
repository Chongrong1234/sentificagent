import tempfile
import unittest
from pathlib import Path

from src.literature_agent import writing_workspace as ww


class WritingWorkspaceGuardrailsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="sa-writing-tests-")
        self.original_output_dir = ww.DEFAULT_OUTPUT_DIR
        ww.DEFAULT_OUTPUT_DIR = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        ww.DEFAULT_OUTPUT_DIR = self.original_output_dir
        self.temp_dir.cleanup()

    def test_extract_bibliography_tail_ignores_instruction_examples(self) -> None:
        content = (
            "\\documentclass{article}\n"
            "\\begin{document}\n"
            "正文。\n"
            "\\begin{verbatim}\n"
            "\\bibliography{ExampleRefs}\n"
            "\\end{document}\n"
            "\\end{verbatim}\n"
            "\\bibliography{ActualRefs}\\\\\n"
            "\\end{document}\n"
        )
        self.assertEqual(ww._extract_bibliography_tail(content), "\\bibliography{ActualRefs}")

    def test_replace_document_body_preserves_front_matter_and_bibliography(self) -> None:
        existing = (
            "\\documentclass{article}\n"
            "\\title{Guardrails}\n"
            "\\author{Tester}\n"
            "\\begin{document}\n"
            "\\maketitle\n"
            "\\begin{abstract}\n"
            "Old abstract.\n"
            "\\end{abstract}\n"
            "\\section{Intro}\n"
            "Old body.\n"
            "\\bibliographystyle{plain}\n"
            "\\bibliography{refs}\n"
            "\\end{document}\n"
            "% template notes\n"
        )
        new_body = (
            "\\maketitle\n"
            "\\section{Intro}\n"
            "New body.\n"
            "\\bibliography{wrong}\n"
        )
        updated = ww._replace_document_body(existing, new_body)
        self.assertIn("\\maketitle", updated)
        self.assertIn("\\begin{abstract}\nOld abstract.\n\\end{abstract}", updated)
        self.assertIn("\\section{Intro}\nNew body.", updated)
        self.assertIn("\\bibliographystyle{plain}\n\\bibliography{refs}", updated)
        self.assertNotIn("\\bibliography{wrong}", updated)
        self.assertNotIn("\\bibliography{refs}\\\\", updated)
        self.assertTrue(updated.rstrip().endswith("% template notes"))

    def test_save_project_file_preserves_main_tex_and_filters_unknown_citations(self) -> None:
        project = ww.create_project(
            {
                "project_id": "project-guardrails",
                "title": "Guardrails",
                "files": [
                    {
                        "path": "main.tex",
                        "content": (
                            "\\documentclass{article}\n"
                            "\\title{Guardrails}\n"
                            "\\author{Tester}\n"
                            "\\begin{document}\n"
                            "\\maketitle\n"
                            "\\section{Intro}\n"
                            "Old body.\n"
                            "\\bibliographystyle{plain}\n"
                            "\\bibliography{refs}\n"
                            "\\end{document}\n"
                        ),
                    },
                    {
                        "path": "refs.bib",
                        "content": (
                            "@article{known,\n"
                            "  title = {Known Paper},\n"
                            "  year = {2024}\n"
                            "}\n"
                        ),
                    },
                ],
            }
        )
        saved = ww.save_project_file(
            {
                "project_id": project["project_id"],
                "path": "main.tex",
                "content": (
                    "\\maketitle\n"
                    "\\section{Intro}\n"
                    "Updated body with \\cite{known,unknown}.\n"
                    "\\bibliography{other}\n"
                ),
                "preserve_structure": True,
            }
        )
        content = saved["content"]
        self.assertIn("\\maketitle", content)
        self.assertIn("Updated body with \\cite{known}.", content)
        self.assertNotIn("unknown", content)
        self.assertIn("\\bibliographystyle{plain}\n\\bibliography{refs}", content)
        self.assertNotIn("\\bibliography{other}", content)

    def test_save_project_file_adds_fallback_bibliography_tail_when_none_exists(self) -> None:
        project = ww.create_project(
            {
                "project_id": "project-fallback-tail",
                "title": "Fallback Tail",
                "files": [
                    {
                        "path": "main.tex",
                        "content": (
                            "\\documentclass{article}\n"
                            "\\title{Fallback Tail}\n"
                            "\\author{Tester}\n"
                            "\\begin{document}\n"
                            "\\maketitle\n"
                            "\\section{Intro}\n"
                            "Old body.\n"
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
                    "\\section{Intro}\n"
                    "Updated body with \\cite{known}.\n"
                ),
                "preserve_structure": True,
                "bibliography": (
                    "@article{known,\n"
                    "  title = {Known Paper},\n"
                    "  year = {2024}\n"
                    "}\n"
                ),
            }
        )
        content = saved["content"]
        self.assertIn("\\section{Intro}\nUpdated body with \\cite{known}.", content)
        self.assertIn("\\bibliographystyle{plain}\n\\bibliography{reference}", content)

    def test_merge_project_bibliography_prefers_template_referenced_bib_target(self) -> None:
        project = ww.create_project(
            {
                "project_id": "project-bib-target",
                "title": "Bib Target",
                "main_tex": "paper/main.tex",
                "files": [
                    {
                        "path": "paper/main.tex",
                        "content": (
                            "\\documentclass{article}\n"
                            "\\begin{document}\n"
                            "\\section{Intro}\n"
                            "Body.\n"
                            "\\bibliographystyle{plain}\n"
                            "\\bibliography{Bibliography-File}\n"
                            "\\end{document}\n"
                        ),
                    },
                    {
                        "path": "paper/aaai2026.bib",
                        "content": (
                            "@article{existing,\n"
                            "  title = {Existing Paper},\n"
                            "  year = {2024}\n"
                            "}\n"
                        ),
                    },
                ],
            }
        )
        ww.merge_project_bibliography(
            project["project_id"],
            (
                "@article{generated,\n"
                "  title = {Generated Paper},\n"
                "  year = {2025}\n"
                "}\n"
            ),
            bibliography_profile=ww.project_bibliography_profile(project["project_id"]),
        )
        created = ww.read_project_file(project["project_id"], "paper/Bibliography-File.bib")["content"]
        existing = ww.read_project_file(project["project_id"], "paper/aaai2026.bib")["content"]
        self.assertIn("@article{generated,", created)
        self.assertNotIn("generated", existing)


if __name__ == "__main__":
    unittest.main()
