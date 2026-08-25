import contextlib
import io
import tempfile
from unittest import mock
import unittest
from pathlib import Path

from src.literature_agent.cli import build_parser, main
from src.literature_agent import config_updates
from src.literature_agent.config import EXAMPLE_CONFIG_PATH, initialize_config, load_config


class CliTests(unittest.TestCase):
    def test_help_parser_exposes_primary_commands(self) -> None:
        parser = build_parser()
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                parser.parse_args(["--help"])
        self.assertEqual(raised.exception.code, 0)

    def test_init_copies_example_without_modifying_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "library_rules.yaml"
            before = EXAMPLE_CONFIG_PATH.read_bytes()
            created = initialize_config(target)
            self.assertEqual(created, target)
            self.assertEqual(target.read_bytes(), before)
            self.assertEqual(EXAMPLE_CONFIG_PATH.read_bytes(), before)

    def test_check_accepts_a_custom_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "library_rules.yaml"
            initialize_config(target)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main(["check", "--config", str(target), "--json"])
            self.assertEqual(status, 0)
            self.assertIn('"配置文件"', output.getvalue())

    def test_config_update_materializes_user_copy_from_example(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            user_path = Path(directory) / "library_rules.yaml"
            config = load_config(EXAMPLE_CONFIG_PATH)
            before = EXAMPLE_CONFIG_PATH.read_bytes()
            with mock.patch.object(config_updates, "USER_CONFIG_PATH", user_path):
                written = config_updates.apply_config_update(
                    config,
                    {"chat": {"system_prompt": "test prompt"}},
                )
            self.assertEqual(written, user_path)
            self.assertTrue(user_path.exists())
            self.assertEqual(EXAMPLE_CONFIG_PATH.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
