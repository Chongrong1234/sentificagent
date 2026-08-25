import unittest

from src.literature_agent.server import CaptureHandler


class ServerBibliographyFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.handler = CaptureHandler.__new__(CaptureHandler)

    def test_filter_bibtex_to_used_keys_keeps_only_cited_entries(self) -> None:
        bibliography = (
            "@article{used,\n  title = {Used Paper},\n  year = {2024}\n}\n\n"
            "@article{unused,\n  title = {Unused Paper},\n  year = {2023}\n}\n"
        )
        content = "\\section{Intro}\nText with \\cite{used}.\n"
        filtered = self.handler._filter_bibtex_to_used_keys(bibliography, content)
        self.assertIn("@article{used,", filtered)
        self.assertNotIn("unused", filtered)


if __name__ == "__main__":
    unittest.main()
