from __future__ import annotations

import os
import threading
import unittest
from pathlib import Path
from unittest import mock

from pdf2zh.cache import TranslationCache, close_db
from pdf2zh.translator import (
    DeepLTranslator,
    GoogleTranslator,
    OllamaTranslator,
    ENGINES,
)
from scripts import translate_pdf


class UpgradesV2Tests(unittest.TestCase):
    def test_cache_close_db_and_thread_safety(self):
        cache = TranslationCache("test_engine", {"lang": "vi"})
        cache.set("hello", "xin chào")
        self.assertEqual(cache.get("hello"), "xin chào")

        # Test multi-threaded writes without error
        def writer(idx):
            try:
                cache.set(f"test_{idx}", f"dịch_{idx}")
            finally:
                close_db()

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        for i in range(20):
            self.assertEqual(cache.get(f"test_{i}"), f"dịch_{i}")

        # close_db should execute without exception
        close_db()

    def test_all_engines_registered(self):
        self.assertIn("google", ENGINES)
        self.assertIn("handoff", ENGINES)
        self.assertIn("ollama", ENGINES)
        self.assertIn("deepl", ENGINES)

    def test_ollama_translator_init_and_cancel(self):
        cancel_ev = threading.Event()
        trans = OllamaTranslator(
            "en",
            "vi",
            model="qwen2.5:7b",
            cancellation_event=cancel_ev,
        )
        self.assertEqual(trans.model, "qwen2.5:7b")
        self.assertTrue(trans.endpoint.endswith("/api/generate"))

        # When cancellation is set, do_translate should abort immediately
        cancel_ev.set()
        res = trans.do_translate("Test sentence")
        self.assertEqual(res, "Test sentence")

    def test_deepl_translator_init_and_cancel(self):
        cancel_ev = threading.Event()
        trans = DeepLTranslator(
            "en",
            "vi",
            cancellation_event=cancel_ev,
        )
        self.assertEqual(trans.lang_out, "vi")

        # When cancellation is set, do_translate should abort immediately
        cancel_ev.set()
        res = trans.do_translate("Test sentence")
        self.assertEqual(res, "Test sentence")

    def test_cli_parser_supports_export_and_model(self):
        parser = translate_pdf._parser()
        args = parser.parse_args(["guide.pdf", "--output-dir", "out", "--export", "both", "--engine", "ollama", "--model", "qwen2.5:7b"])
        self.assertEqual(args.export, "both")
        self.assertEqual(args.engine, "ollama")
        self.assertEqual(args.model, "qwen2.5:7b")

    def test_cli_parser_defaults_export_to_mono(self):
        parser = translate_pdf._parser()
        args = parser.parse_args(["guide.pdf", "--output-dir", "out"])
        self.assertEqual(args.export, "mono")
        self.assertEqual(args.engine, "google")
        self.assertIsNone(args.model)


if __name__ == "__main__":
    unittest.main()
