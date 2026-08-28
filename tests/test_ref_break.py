from __future__ import annotations

import re
import unittest


def is_new_reference_item(prev_text: str, line_prefix: str) -> bool:
    """Check if a new line starting with line_prefix after prev_text is a new list/reference item."""
    if not line_prefix or not prev_text:
        return False

    # Check if line_prefix starts with a list marker:
    # 1. '1. ', '12. ', '123. '
    # 2. '[1] ', '[12] '
    # 3. '(1) ', '(12) '
    # 4. '• ', '- ', '▶ '
    marker_pattern = re.compile(
        r"^(\d{1,4}\.|\s*\[\d{1,4}\]|\s*\(\d{1,4}\)|[•▪■▶\-])\s*",
        re.IGNORECASE,
    )
    if not marker_pattern.match(line_prefix):
        return False

    # Check if previous text ended with a reasonable sentence or citation boundary
    prev_clean = prev_text.strip()
    if (
        prev_clean.endswith(".")
        or prev_clean.endswith("]")
        or prev_clean.endswith(")")
        or prev_clean.endswith(":")
        or re.search(r"\[(CrossRef|PubMed|Google\s+Scholar|IEEE|arXiv)\]\s*$", prev_clean, re.IGNORECASE)
        or re.search(r"\b\d{4}\.?\s*$", prev_clean)  # year like 2020 or 2020.
        or re.search(r"\b\d{1,5}\.?\s*$", prev_clean) # page number like 424 or 424.
    ):
        return True

    return False


class ReferenceItemTests(unittest.TestCase):
    def test_numbered_reference_break(self):
        prev = "Score. J. Crit. Care 2012, 27, 424.e7–424.e13. [CrossRef] [PubMed]"
        new_line = "2. Weenk, M.; van Goor, H.;"
        self.assertTrue(is_new_reference_item(prev, new_line))

    def test_bracketed_reference_break(self):
        prev = "J. Crit. Care 2012, 27, 424. [1]"
        new_line = "[2] Author, B. (2020)."
        self.assertTrue(is_new_reference_item(prev, new_line))

    def test_normal_text_does_not_break_mid_sentence(self):
        prev = "The experimental results showed that 1."
        new_line = "5 percent increase in accuracy was observed."
        self.assertFalse(is_new_reference_item(prev, new_line))


if __name__ == "__main__":
    unittest.main()
