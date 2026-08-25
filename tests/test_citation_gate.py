import unittest

from src.literature_agent import citation_gate as cg


class CitationGateTests(unittest.TestCase):
    def test_detect_citation_need_finds_placeholders(self) -> None:
        content = "多个研究表明模型有效[待引用:1]。此外，部署延迟可显著下降[待引用:2]。"
        points = cg.detect_citation_need(content, "methods")
        self.assertEqual(len(points), 2)
        self.assertEqual(points[0].placeholder, "[待引用:1]")
        self.assertIn("多个研究表明模型有效", points[0].claim)

    def test_search_candidates_scores_overlap(self) -> None:
        items = [
            {
                "citation_key": "hinton2015distill",
                "title": "Distilling the Knowledge in a Neural Network",
                "authors": ["Geoffrey Hinton"],
                "year": "2015",
                "venue": "NIPS Workshop",
                "abstract": "Knowledge distillation compresses neural networks for deployment.",
            },
            {
                "citation_key": "smith2020other",
                "title": "An unrelated study",
                "authors": ["John Smith"],
                "year": "2020",
                "venue": "Journal",
                "abstract": "This paper discusses a different topic entirely.",
            },
        ]
        candidates = cg.search_candidates("knowledge distillation compresses neural networks", items, min_strength=2)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].bib_key, "hinton2015distill")
        self.assertGreaterEqual(candidates[0].strength, 2)

    def test_apply_citations_replaces_placeholders(self) -> None:
        content = "知识蒸馏可压缩参数量[待引用:1]。"
        updated = cg.apply_citations(content, {"[待引用:1]": ["hinton2015distill", "gou2021survey"]})
        self.assertIn(r"\cite{hinton2015distill,gou2021survey}", updated)
        self.assertNotIn("[待引用:1]", updated)

    def test_extract_bibtex_for_decisions_keeps_selected_entries(self) -> None:
        candidates = [
            {
                "bib_key": "key1",
                "bibtex": "@article{key1,\n  title = {Paper One},\n  year = {2020},\n}",
            },
            {
                "bib_key": "key2",
                "bibtex": "@article{key2,\n  title = {Paper Two},\n  year = {2021},\n}",
            },
        ]
        bibtex = cg.extract_bibtex_for_decisions(candidates, {"[待引用:1]": ["key2"]})
        self.assertIn("@article{key2", bibtex)
        self.assertNotIn("@article{key1", bibtex)


if __name__ == "__main__":
    unittest.main()
