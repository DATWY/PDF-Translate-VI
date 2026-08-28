from __future__ import annotations

import re
import unittest


def clean_and_rejoin_urls(text: str) -> str:
    """Rejoin URLs broken across lines (with space or newline)."""
    if not text:
        return text

    # 1. URL broken with trailing hyphen across line break / space
    # e.g. https://siliconradar.com/products/single- product/60-ghz
    text = re.sub(
        r"(https?://[^\s<>\"\'\)]*?-)\s+([a-zA-Z0-9_\-./?%&=#+~]+)",
        r"\1\2",
        text,
        flags=re.IGNORECASE,
    )

    # 2. URL broken directly with space after dot, slash, query, equals, ampersand, hash
    # e.g. http://www. ti.com/product/AWR1243 or https://example.com/ products/
    text = re.sub(
        r"(https?://[^\s<>\"\'\)]*?[./=&#+~?])\s+([a-zA-Z0-9_\-./?%&=#+~]+)",
        r"\1\2",
        text,
        flags=re.IGNORECASE,
    )

    # 3. Bare www. domain broken with space (e.g. www. ti.com)
    text = re.sub(
        r"(www\.[a-zA-Z0-9_\-./?%&=#+~]*?[./=&#+~?])\s+([a-zA-Z0-9_\-./?%&=#+~]+)",
        r"\1\2",
        text,
        flags=re.IGNORECASE,
    )

    return text


class RejoinUrlTests(unittest.TestCase):
    def test_rejoin_url_after_dot(self):
        text = "Available online: http://www. ti.com/product/AWR1243 (accessed on 11 September 2020)."
        rejoined = clean_and_rejoin_urls(text)
        self.assertEqual(
            rejoined,
            "Available online: http://www.ti.com/product/AWR1243 (accessed on 11 September 2020).",
        )

    def test_rejoin_url_after_hyphen(self):
        text = "Available online: https://siliconradar.com/products/single- product/60-ghz-4tx4tr-mimo/ (accessed on 11 September 2020)."
        rejoined = clean_and_rejoin_urls(text)
        self.assertEqual(
            rejoined,
            "Available online: https://siliconradar.com/products/single-product/60-ghz-4tx4tr-mimo/ (accessed on 11 September 2020).",
        )

    def test_rejoin_url_after_slash(self):
        text = "Available online: https://example.com/products/ single-product/test.html"
        rejoined = clean_and_rejoin_urls(text)
        self.assertEqual(
            rejoined,
            "Available online: https://example.com/products/single-product/test.html",
        )


if __name__ == "__main__":
    unittest.main()
