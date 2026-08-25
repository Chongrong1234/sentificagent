import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

import yaml

from src.literature_agent.config import load_config
from src.literature_agent import knowledge_sync as ks
from src.literature_agent.library_store import (
    record_discovered_papers,
    record_ranked_items,
    record_run_start,
    record_summarized_items,
)


SAMPLE_PAPER = {
    "title": "Deep Learning for Crop Monitoring: A Survey",
    "abstract": "We survey deep learning methods for crop monitoring.",
    "page_url": "https://example.org/papers/crop-survey",
    "pdf_url": "https://example.org/papers/crop-survey.pdf",
    "doi": "10.1234/crop.2024.001",
    "year": "2024",
    "venue": "Computers and Electronics in Agriculture",
    "authors": ["Alice Zhang", "Bob Li"],
    "keywords": ["deep learning", "crop monitoring"],
}

SECOND_PAPER = {
    "title": "Remote Sensing / Precision Agriculture: Trends?",
    "abstract": "Trends in remote sensing for precision agriculture.",
    "page_url": "https://example.org/papers/rs-trends",
    "pdf_url": "https://example.org/papers/rs-trends.pdf",
    "doi": "",
    "year": "2023",
    "venue": "Remote Sensing of Environment",
    "authors": ["Carol Wang"],
    "keywords": ["remote sensing", "precision agriculture"],
}


def _write_config(directory: str, extra: dict | None = None):
    raw = {
        "storage": {"root_dir": "data", "library_db": "library.sqlite3"},
        "search": {"sources": ["openalex"]},
        "classifier": {},
    }
    if extra:
        raw.update(extra)
    path = Path(directory) / "library_rules.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), "utf-8")
    return load_config(path)


def _populate_library(config) -> None:
    record_run_start(config, "run-1", "attention", "crop", {})
    record_discovered_papers(config, "run-1", [SAMPLE_PAPER, SECOND_PAPER])
    record_ranked_items(
        config,
        "run-1",
        [
            {
                "paper": SAMPLE_PAPER,
                "priority": "high",
                "relevance": {"score": 0.9, "tags": ["topic:deep-learning"]},
            }
        ],
    )
    record_summarized_items(
        config,
        "run-1",
        [
            {
                "paper": SAMPLE_PAPER,
                "priority": "high",
                "summary": {
                    "summary": "本文综述了作物监测中的深度学习方法。",
                    "why_it_matters": "有助于选择田块尺度模型。",
                    "next_actions": ["精读方法章节", "对比无人机数据"],
                },
            }
        ],
        model="test-model",
    )


class ObsidianSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="sa-kb-tests-")
        self.root = Path(self.temp_dir.name)
        self.config = _write_config(str(self.root))
        _populate_library(self.config)
        self.vault = self.root / "vault"
        self.vault.mkdir()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_sync_writes_notes_with_pdf_links_but_no_pdf_files(self) -> None:
        result = ks.sync_obsidian(self.config, vault=str(self.vault))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["papers"], 2)
        self.assertEqual(result["pdf_files_written"], 0)

        base = self.vault / "Literature"
        paper_notes = sorted((base / "papers").glob("*.md"))
        self.assertEqual(len(paper_notes), 2)
        self.assertEqual(list(self.vault.rglob("*.pdf")), [])

        survey_note = (base / "papers" / "Deep Learning for Crop Monitoring A Survey.md")
        self.assertTrue(survey_note.exists())
        content = survey_note.read_text("utf-8")
        self.assertIn("[PDF 下载](https://example.org/papers/crop-survey.pdf)", content)
        self.assertIn("pdf_url: https://example.org/papers/crop-survey.pdf", content)
        self.assertIn("本文综述了作物监测中的深度学习方法。", content)
        self.assertIn("[[DEEP Learning]]", content)

        topic_note = base / "topics" / "DEEP Learning.md"
        self.assertTrue(topic_note.exists())
        self.assertIn(
            "[[Deep Learning for Crop Monitoring A Survey|Deep Learning for Crop Monitoring: A Survey]]",
            topic_note.read_text("utf-8"),
        )
        self.assertTrue((base / "文献库首页.md").exists())

    def test_sync_is_idempotent(self) -> None:
        first = ks.sync_obsidian(self.config, vault=str(self.vault))
        self.assertGreater(first["notes"]["created"], 0)
        second = ks.sync_obsidian(self.config, vault=str(self.vault))
        self.assertEqual(second["notes"]["created"], 0)
        self.assertEqual(second["notes"]["updated"], 0)
        self.assertGreater(second["notes"]["unchanged"], 0)

    def test_illegal_filename_chars_are_sanitized(self) -> None:
        ks.sync_obsidian(self.config, vault=str(self.vault))
        names = [p.name for p in (self.vault / "Literature" / "papers").glob("*.md")]
        for name in names:
            for char in '/\\:*?"<>|#^[]':
                self.assertNotIn(char, name)

    def test_missing_vault_raises_actionable_error(self) -> None:
        with self.assertRaises(ValueError):
            ks.sync_obsidian(self.config, vault=str(self.root / "no-such-vault"))

    def test_detect_obsidian_vault_prefers_open_vault(self) -> None:
        home = self.root / "home"
        registry = home / ".config" / "obsidian"
        registry.mkdir(parents=True)
        old_vault = self.root / "old-vault"
        open_vault = self.root / "open-vault"
        old_vault.mkdir()
        open_vault.mkdir()
        (registry / "obsidian.json").write_text(
            json.dumps(
                {
                    "vaults": {
                        "a": {"path": str(old_vault), "ts": 9999999999999},
                        "b": {"path": str(open_vault), "ts": 1, "open": True},
                    }
                }
            ),
            "utf-8",
        )
        self.assertEqual(ks.detect_obsidian_vault(home), open_vault)
        self.assertIsNone(ks.detect_obsidian_vault(self.root / "no-home"))


class LarkSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="sa-kb-lark-tests-")
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _config_with_lark(self, cli: str):
        return _write_config(
            str(self.root),
            {"knowledge_base": {"lark": {"enabled": True, "cli": cli}}},
        )

    def test_lark_sync_skipped_when_cli_missing(self) -> None:
        config = self._config_with_lark("definitely-missing-lark-cli")
        _populate_library(config)
        result = ks.sync_lark(config)
        self.assertEqual(result["status"], "skipped")
        self.assertIn("npm install -g @larksuite/cli", result["hint"])

    def test_lark_sync_creates_then_overwrites_with_fake_cli(self) -> None:
        log_path = self.root / "lark-cli.log"
        fake_cli = self.root / "lark-cli"
        fake_cli.write_text(
            "#!/bin/bash\n"
            f'echo "$@" >> "{log_path}"\n'
            'token="tok-$(echo "$@" | md5sum | cut -c1-8)"\n'
            'printf \'{"ok": true, "data": {"file_token": "%s"}}\\n\' "$token"\n',
            "utf-8",
        )
        fake_cli.chmod(fake_cli.stat().st_mode | stat.S_IEXEC)

        config = self._config_with_lark(str(fake_cli))
        _populate_library(config)

        first = ks.sync_lark(config)
        self.assertEqual(first["status"], "ok")
        self.assertEqual(first["created"], 2)
        self.assertEqual(first["pdf_files_uploaded"], 0)

        log_lines = log_path.read_text("utf-8").splitlines()
        self.assertEqual(len([line for line in log_lines if "+create" in line]), 2)
        self.assertTrue(all("--folder-token" not in line for line in log_lines))

        second = ks.sync_lark(config)
        self.assertEqual(second["status"], "ok")
        self.assertEqual(second["updated"], 2)
        self.assertEqual(second["created"], 0)
        log_lines = log_path.read_text("utf-8").splitlines()
        overwrite_lines = [line for line in log_lines if "+overwrite" in line]
        self.assertEqual(len(overwrite_lines), 2)
        self.assertTrue(all("--file-token tok-" in line for line in overwrite_lines))

    def test_lark_sync_aborts_on_auth_error(self) -> None:
        fake_cli = self.root / "lark-cli"
        fake_cli.write_text(
            "#!/bin/bash\n"
            'echo \'{"ok": false, "error": {"subtype": "not_configured"}}\' >&2\n'
            "exit 1\n",
            "utf-8",
        )
        fake_cli.chmod(fake_cli.stat().st_mode | stat.S_IEXEC)

        config = self._config_with_lark(str(fake_cli))
        _populate_library(config)
        result = ks.sync_lark(config)
        self.assertEqual(result["status"], "failed")
        self.assertIn("lark-cli config init", result["hint"])


if __name__ == "__main__":
    unittest.main()
