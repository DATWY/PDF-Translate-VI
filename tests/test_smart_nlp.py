from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pymupdf
from pdf2zh.text_utils import (
    cleanup_vietnamese_typography,
    dehyphenate_text,
    protect_glossary,
    protect_links,
    restore_glossary,
    restore_links,
)
from scripts.translate_pdf import inspect_pdf


class SmartNLPTests(unittest.TestCase):
    def test_dehyphenate_broken_words(self):
        text = "This is an inter-\npretation of the approxi-\n  mately correct model."
        expected = "This is an interpretation of the approximately correct model."
        self.assertEqual(dehyphenate_text(text), expected)

    def test_dehyphenate_preserves_compound_words_on_same_line(self):
        text = "This is a state-of-the-art model and real-time processing."
        self.assertEqual(dehyphenate_text(text), text)

    def test_cleanup_vietnamese_typography(self):
        dirty = "Dưới đây là một số từ , ví dụ ( như thế này ) và “ từ trong ngoặc kép ” . Thử nghiệm !"
        expected = "Dưới đây là một số từ, ví dụ (như thế này) và “từ trong ngoặc kép”. Thử nghiệm!"
        self.assertEqual(cleanup_vietnamese_typography(dirty), expected)

    def test_protect_and_restore_links_and_urls(self):
        text = "Available online: http://www.ti.com/product/AWR1243 (accessed on 11 September 2020)."
        protected, lmap = protect_links(text)
        self.assertIn("{u0}", protected)
        self.assertNotIn("http://www.ti.com", protected)

        # Simulate translated sentence
        translated = "Có sẵn trực tuyến: {u0} (truy cập vào ngày 11 tháng 9 năm 2020)."
        cleaned = cleanup_vietnamese_typography(translated)
        restored = restore_links(cleaned, lmap)
        self.assertEqual(
            restored,
            "Có sẵn trực tuyến: http://www.ti.com/product/AWR1243 (truy cập vào ngày 11 tháng 9 năm 2020).",
        )

    def test_protect_and_restore_citation_badges(self):
        text = "J. Crit. Care 2012, 27, 424.e7-424.e13. [CrossRef] [PubMed]"
        protected, lmap = protect_links(text)
        self.assertIn("{u0}", protected)
        self.assertIn("{u1}", protected)
        self.assertNotIn("CrossRef", protected)
        self.assertNotIn("PubMed", protected)

        # Simulate translation with preserved badges
        translated = "J. Phê bình. Chăm sóc 2012, 27, 424.e7-424.e13. {u0} {u1}"
        cleaned = cleanup_vietnamese_typography(translated)
        restored = restore_links(cleaned, lmap)
        self.assertEqual(
            restored,
            "J. Phê bình. Chăm sóc 2012, 27, 424.e7-424.e13. [CrossRef] [PubMed]",
        )

    def test_protect_complex_urls_and_dois(self):
        text = (
            "Radar 60 GHz. Link: https://siliconradar.com/products/single-product/60-ghz-4tx4tr-mimo/ "
            "or doi:10.1016/j.critcare.2012.04.013 [Google Scholar]."
        )
        protected, lmap = protect_links(text)
        self.assertEqual(len(lmap), 3)

        translated = (
            "Radar 60 GHz. Liên kết: {u1} hoặc {u2} {u0}."
        )
        cleaned = cleanup_vietnamese_typography(translated)
        restored = restore_links(cleaned, lmap)
        self.assertIn("https://siliconradar.com/products/single-product/60-ghz-4tx4tr-mimo/", restored)
        self.assertIn("doi:10.1016/j.critcare.2012.04.013", restored)
        self.assertIn("[Google Scholar]", restored)

    def test_protect_and_restore_glossary(self):
        text = "The Transformer model uses Multi-Head Attention and Backpropagation to optimize the loss function."
        terms = ["Transformer", "Multi-Head Attention", "Backpropagation"]

        protected, gmap = protect_glossary(text, terms)
        self.assertIn("{g0}", protected)
        self.assertIn("{g1}", protected)
        self.assertIn("{g2}", protected)
        self.assertNotIn("Transformer", protected)

        # Simulate translated string with preserved tokens
        translated = "Mô hình {g0} sử dụng {g1} và {g2} để tối ưu hóa hàm mất mát."
        restored = restore_glossary(translated, gmap)
        self.assertIn("Transformer", restored)
        self.assertIn("Multi-Head Attention", restored)
        self.assertIn("Backpropagation", restored)

    def test_restore_glossary_with_inserted_spaces(self):
        """Handle case where machine translator adds spaces like { g 0 }."""
        text = "Text with term"
        _, gmap = protect_glossary("Text with Transformer", ["Transformer"])
        simulated = "Văn bản với { g0 } và { G 0 }"
        restored = restore_glossary(simulated, gmap)
        self.assertEqual(restored, "Văn bản với Transformer và Transformer")


class InspectPdfTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_inspect_text_pdf(self):
        pdf_path = self.root / "sample.pdf"
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((50, 50), "Hello world, this is a test document.")
        doc.save(str(pdf_path))
        doc.close()

        info = inspect_pdf(pdf_path)
        self.assertEqual(info["pages"], 1)
        self.assertTrue(info["has_text"])
        self.assertFalse(info["is_scan"])
        self.assertGreater(info["size_mb"], 0)


if __name__ == "__main__":
    unittest.main()
