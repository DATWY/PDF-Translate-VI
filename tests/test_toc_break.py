from __future__ import annotations

import re
import unittest


def should_break_new_item(prev_text: str, next_line_prefix: str, dy: float, dx: float, font_size: float) -> bool:
    """Determine if moving to a new line/position should start a new paragraph/item."""
    # 1. Column Jump: cursor jumped upward significantly or far to the right
    if dy < -font_size * 2.0 or dx > 30.0:  # In PDF coords, y is from bottom, so upward leap has negative dy or large y
        return True

    # 2. Previous line ended with a standalone page number (e.g. 'Transistor Action 124', 'Preface vii', 'Equilibrium 15')
    if prev_text and re.search(r"\s+(\d{1,4}|[ivxlcdm]{1,6})\s*$", prev_text, re.IGNORECASE):
        return True

    # 3. Next line starts with Section / Subsection index (e.g. '4.1 ', '4.2 ', '0.1 ', '1.2.3 ')
    if re.match(r"^\s*(\d+|[A-Z])(\.\d+)+\s+", next_line_prefix):
        return True

    # 4. Next line starts with Numbered Item / Reference / Roman numeral
    if re.match(r"^\s*(\d{1,4}\.|\[\d{1,4}\]|\(\d{1,4}\))\s+", next_line_prefix):
        prev_clean = prev_text.strip() if prev_text else ""
        if (
            not prev_clean
            or prev_clean.endswith(".")
            or prev_clean.endswith("]")
            or prev_clean.endswith(")")
            or prev_clean.endswith(":")
            or re.search(r"\d+$", prev_clean)
        ):
            return True

    # 5. Next line starts with special TOC / Section keywords
    if re.match(
        r"^\s*(Summary|Tóm tắt|Index|Appendix|Phụ lục|Preface|Lời nói đầu|Acknowledgments|Lời cảm ơn|Contents|Mục lục|PART\s+[IVXLCDM\d]+|CHAPTER\s+\d+|CHƯƠNG\s+\d+)\b",
        next_line_prefix,
        re.IGNORECASE,
    ):
        return True

    # 6. Next line starts with bullet / triangle symbols
    if re.match(r"^\s*[►▶•▪■◆◇★\-]\s*", next_line_prefix):
        return True

    return False


class TocItemBreakTests(unittest.TestCase):
    def test_toc_section_number_breaks(self):
        prev = "4.1 Transistor Action 124"
        next_line = "4.2 Static Characteristics of Bipolar Transistors 129"
        self.assertTrue(should_break_new_item(prev, next_line, 10.0, 0.0, 9.0))

    def test_toc_multiline_title_does_not_break_mid_title(self):
        prev = "4.3 Frequency Response and Switching of"
        next_line = "Bipolar Transistors 137"
        self.assertFalse(should_break_new_item(prev, next_line, 10.0, 0.0, 9.0))

    def test_toc_summary_after_section_breaks(self):
        prev = "4.6 Thyristors and Related Power Devices 149"
        next_line = "Summary 155"
        self.assertTrue(should_break_new_item(prev, next_line, 10.0, 0.0, 9.0))

    def test_toc_chapter_header_breaks(self):
        prev = "Summary 155"
        next_line = "► CHAPTER 5"
        self.assertTrue(should_break_new_item(prev, next_line, 10.0, 0.0, 9.0))

    def test_toc_chapter_title_after_page_num_breaks(self):
        prev = "Bipolar Transistors and Related Devices 123"
        next_line = "4.1 Transistor Action 124"
        self.assertTrue(should_break_new_item(prev, next_line, 10.0, 0.0, 9.0))


if __name__ == "__main__":
    unittest.main()
