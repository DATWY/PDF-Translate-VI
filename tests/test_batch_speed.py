from __future__ import annotations

import unittest
from unittest import mock

from pdf2zh.cache import TranslationCache
from pdf2zh.translator import GoogleTranslator


class BatchSpeedTests(unittest.TestCase):
    def test_in_memory_ram_cache_hit(self):
        cache = TranslationCache("test_engine", {"lang": "vi"})
        cache.set("hello", "xin chào")

        # Fast RAM hit
        self.assertEqual(cache.get("hello"), "xin chào")
        self.assertIn("hello", cache._mem_cache)

    def test_google_translator_batch_grouping_and_splitting(self):
        translator = GoogleTranslator("en", "vi", ignore_cache=True)

        # Mock do_translate to simulate Google Translate returning translated delimiter
        def fake_do_translate(text: str) -> str:
            if "_V_SEG_" in text:
                parts = text.split("_V_SEG_")
                return "_V_SEG_".join([f"dịch: {p.strip()}" for p in parts])
            return f"dịch: {text}"

        with mock.patch.object(translator, "do_translate", side_effect=fake_do_translate):
            texts = ["First line", "Second line", "Third line"]
            results = translator.translate_batch(texts)
            self.assertEqual(len(results), 3)
            self.assertIn("dịch: First line", results[0])
            self.assertIn("dịch: Second line", results[1])
            self.assertIn("dịch: Third line", results[2])

    def test_google_translator_batch_fallback_on_mismatch(self):
        translator = GoogleTranslator("en", "vi", ignore_cache=True)

        # Simulate Google dropping delimiter
        def fake_do_translate(text: str) -> str:
            if "_V_SEG_" in text:
                return "Google dropped all delimiter markers"
            return f"item: {text}"

        with mock.patch.object(translator, "do_translate", side_effect=fake_do_translate):
            texts = ["Line A", "Line B"]
            results = translator.translate_batch(texts)
            self.assertEqual(len(results), 2)
            self.assertEqual(results[0], "item: Line A")
            self.assertEqual(results[1], "item: Line B")


if __name__ == "__main__":
    unittest.main()
