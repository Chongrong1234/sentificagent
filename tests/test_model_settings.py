import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.literature_agent import chat


class ProviderApiKeyTests(unittest.TestCase):
    def test_save_provider_api_key_writes_kimi_secret_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            key_file = Path(tmp) / "kimi_api_key.txt"
            with mock.patch.object(chat, "DEFAULT_KIMI_KEY_FILE", key_file):
                saved = chat.save_provider_api_key("kimi", "sk-test-1234567890")
                self.assertTrue(saved)
                self.assertEqual(key_file.read_text(encoding="utf-8"), "sk-test-1234567890")

    def test_save_provider_api_key_normalizes_deepseek_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            key_file = Path(tmp) / "deepseek_api_key.txt"
            with mock.patch.object(chat, "DEFAULT_DEEPSEEK_KEY_FILE", key_file):
                saved = chat.save_provider_api_key("deepseek", " ds-key ")
                self.assertTrue(saved)
                self.assertEqual(key_file.read_text(encoding="utf-8"), "ds-key")

    def test_save_provider_api_key_ignores_empty_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            key_file = Path(tmp) / "kimi_api_key.txt"
            key_file.write_text("existing", encoding="utf-8")
            with mock.patch.object(chat, "DEFAULT_KIMI_KEY_FILE", key_file):
                self.assertFalse(chat.save_provider_api_key("kimi", "   "))
                self.assertEqual(key_file.read_text(encoding="utf-8"), "existing")


class ProviderApiBaseTests(unittest.TestCase):
    def test_custom_api_base_overrides_default_and_can_reset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base_file = Path(tmp) / "kimi_api_base.txt"
            with mock.patch.object(chat, "DEFAULT_KIMI_API_BASE_FILE", base_file):
                chat.save_provider_api_base("kimi", "https://proxy.example.com/v1/")
                self.assertEqual(chat.provider_api_base("kimi"), "https://proxy.example.com/v1")
                chat.save_provider_api_base("kimi", "")
                self.assertEqual(chat.provider_api_base("kimi"), chat.KIMI_API_BASE)
                self.assertFalse(base_file.exists())

    def test_default_api_base_per_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(chat, "DEFAULT_KIMI_API_BASE_FILE", Path(tmp) / "kimi_api_base.txt"), \
                 mock.patch.object(chat, "DEFAULT_DEEPSEEK_API_BASE_FILE", Path(tmp) / "deepseek_api_base.txt"):
                self.assertEqual(chat.provider_api_base("kimi"), chat.KIMI_API_BASE)
                self.assertEqual(chat.provider_api_base("ds"), chat.DEEPSEEK_API_BASE)


class MaskApiKeyTests(unittest.TestCase):
    def test_masks_long_keys(self) -> None:
        self.assertEqual(chat.mask_api_key("sk-abcdef123456"), "sk-a...3456")

    def test_masks_short_keys_fully(self) -> None:
        self.assertEqual(chat.mask_api_key("short"), "***")

    def test_empty_key_masks_to_empty(self) -> None:
        self.assertEqual(chat.mask_api_key(""), "")
        self.assertEqual(chat.mask_api_key(None), "")


if __name__ == "__main__":
    unittest.main()
