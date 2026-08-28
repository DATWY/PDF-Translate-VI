from __future__ import annotations

import re
import unittest


def compute_toc_positions(
    page_width: float,
    x0: float,
    x1: float,
    title_width: float,
    num_width: float,
) -> tuple[float, float, float]:
    """Compute (col_x1, title_max_w, page_num_x) for a TOC item."""
    # Determine column boundary in a 2-column or 1-column layout
    if x0 < page_width * 0.45:
        col_x1 = max(x1, page_width * 0.47)
    else:
        col_x1 = max(x1, page_width * 0.92)

    page_num_x = col_x1 - num_width
    avail_title_w = max(50.0, page_num_x - x0 - 10.0)
    return col_x1, avail_title_w, page_num_x


class TocPositionTests(unittest.TestCase):
    def test_left_column_toc_positions(self):
        page_width = 612.0
        x0 = 50.0
        x1 = 180.0
        title_w = 120.0
        num_w = 15.0
        col_x1, avail_w, num_x = compute_toc_positions(page_width, x0, x1, title_w, num_w)
        self.assertGreaterEqual(col_x1, 280.0)
        self.assertEqual(num_x, col_x1 - 15.0)
        self.assertGreater(avail_w, title_w)

    def test_right_column_toc_positions(self):
        page_width = 612.0
        x0 = 310.0
        x1 = 430.0
        title_w = 180.0
        num_w = 20.0
        col_x1, avail_w, num_x = compute_toc_positions(page_width, x0, x1, title_w, num_w)
        self.assertGreaterEqual(col_x1, 550.0)
        self.assertEqual(num_x, col_x1 - 20.0)
        self.assertGreater(avail_w, title_w)


if __name__ == "__main__":
    unittest.main()
