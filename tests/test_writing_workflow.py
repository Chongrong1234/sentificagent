import tempfile
import unittest
from pathlib import Path

from src.literature_agent import writing_workspace as ww
from src.literature_agent import writing_workflow as wf


class WritingWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="sa-workflow-tests-")
        self.original_output_dir = ww.DEFAULT_OUTPUT_DIR
        ww.DEFAULT_OUTPUT_DIR = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        ww.DEFAULT_OUTPUT_DIR = self.original_output_dir
        self.temp_dir.cleanup()

    def _create_opening_project(self) -> dict:
        return ww.create_project(
            {
                "project_id": "project-workflow",
                "title": "开题报告测试",
                "goal": "路面病害检测 开题报告",
                "writing_type": "grant",
                "writing_language": "zh",
                "files": [
                    {
                        "path": "main.tex",
                        "content": (
                            "\\documentclass[UTF8,12pt]{ctexart}\n"
                            "\\begin{document}\n"
                            "\\section{课题来源及研究的目的和意义}\n"
                            "\\section{国内外在该方向的研究现状及分析}\n"
                            "\\section{主要研究内容}\n"
                            "\\section{研究方案}\n"
                            "\\section{进度安排与预期达到的目标}\n"
                            "\\section{课题已具备和所需的条件与经费}\n"
                            "\\section{研究过程中可能遇到的困难和问题及解决的措施}\n"
                            "\\section{主要参考文献}\n"
                            "\\end{document}\n"
                        ),
                    }
                ],
                "replace_project": True,
            }
        )

    def test_get_workflow_state_falls_back_to_template_structure(self) -> None:
        project = self._create_opening_project()
        state = wf.get_workflow_state(project["project_id"])
        self.assertEqual(state["stage"], wf.WorkflowStage.EXPLORATION.value)
        self.assertEqual(len(state["sections"]), 8)
        self.assertEqual(state["current_section"]["title"], "课题来源及研究的目的和意义")

    def test_select_topic_advances_to_outline_stage(self) -> None:
        project = self._create_opening_project()
        wf.get_exploration_report(project["project_id"], "路面病害检测 深度学习")
        state = wf.select_exploration_topic(project["project_id"], "面向移动巡检的病害检测")
        self.assertEqual(state["stage"], wf.WorkflowStage.OUTLINE_NEGOTIATION.value)
        self.assertTrue(state["stage_card"]["completed"])
        self.assertIn("面向移动巡检的病害检测", state["stage_card"]["summary"])

    def test_set_writing_order_moves_workflow_into_writing(self) -> None:
        project = self._create_opening_project()
        wf.select_exploration_topic(project["project_id"], "面向移动巡检的病害检测")
        wf.start_outline_negotiation(project["project_id"])
        first = wf.get_workflow_state(project["project_id"])["current_section"]
        wf.negotiate_section(project["project_id"], first["section_id"], "problem_gap_value")
        second = wf.get_workflow_state(project["project_id"])["current_section"]
        wf.negotiate_section(project["project_id"], second["section_id"], "by_tech_stream")
        order = wf.recommend_writing_order(project["project_id"])
        state = wf.set_writing_order(project["project_id"], order["recommended_order"])
        self.assertEqual(state["stage"], wf.WorkflowStage.CHAPTER_WRITING.value)
        self.assertTrue(state["current_section"]["section_id"])
        self.assertEqual(
            state["sections"][0]["write_order"],
            1,
        )

    def test_apply_section_citations_replaces_placeholders(self) -> None:
        project = self._create_opening_project()
        state = wf.get_workflow_state(project["project_id"])
        section_id = state["current_section"]["section_id"]
        wf.save_section_draft(
            project["project_id"],
            section_id,
            "多个研究表明移动巡检方案有效[待引用:1]。",
        )
        workflow = wf.get_workflow_state(project["project_id"])
        pending = workflow["current_section"]["pending_citations"]
        self.assertEqual(len(pending), 1)
        candidates = pending[0]["candidates"]
        if not candidates:
            self.skipTest("local search returned no candidates in test environment")
        result = wf.apply_section_citations(
            project["project_id"],
            section_id,
            {"[待引用:1]": [candidates[0]["bib_key"]]},
        )
        content = result["file"]["content"]
        self.assertIn(r"\cite{", content)
        self.assertNotIn("[待引用:1]", content)


if __name__ == "__main__":
    unittest.main()
