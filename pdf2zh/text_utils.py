"""Smart NLP and typography utilities for document translation."""

from __future__ import annotations

import re


def dehyphenate_text(text: str) -> str:
    """Join words broken across lines by a trailing hyphen or spaces.

    Example:
        'inter-\\npretation' -> 'interpretation'
        'inter- pretation' -> 'interpretation'
        'state-of-the-art' -> 'state-of-the-art' (preserved)
    """
    if not text or "-" not in text:
        return text

    # Match lowercase word followed by hyphen, newline/whitespace, then lowercase continuation
    pattern = re.compile(r"([a-zA-Z]{2,})-\s*(?:\n|\s+)\s*([a-zA-Z]{2,})")
    return pattern.sub(r"\1\2", text)


def clean_and_rejoin_urls(text: str) -> str:
    """Rejoin URLs broken across lines by newlines, spaces, hyphens, or soft-hyphens."""
    if not text:
        return text

    # 1. Soft-hyphen (\xad or \ufffd) in URL across newline/space -> remove soft hyphen
    text = re.sub(
        r"(https?://[^\s<>\"\'\)]*?)[\xad\ufffd]\s*(?:\n|\s+)\s*([a-zA-Z0-9_\-./?%&=#+~]+)",
        r"\1\2",
        text,
        flags=re.IGNORECASE,
    )

    # 2. URL with trailing hyphen before line break / space -> keep the hyphen and join
    # e.g. https://siliconradar.com/products/single- product/60-ghz
    text = re.sub(
        r"(https?://[^\s<>\"\'\)]*?-)\s*(?:\n|\s+)\s*([a-zA-Z0-9_\-./?%&=#+~]+)",
        r"\1\2",
        text,
        flags=re.IGNORECASE,
    )

    # 3. URL broken directly with space/newline after dot, slash, query, equals, ampersand, hash
    # e.g. http://www. ti.com/product/AWR1243 or https://example.com/ products/
    text = re.sub(
        r"(https?://[^\s<>\"\'\)]*?[./=&#+~?])\s*(?:\n|\s+)\s*([a-zA-Z0-9_\-./?%&=#+~]+)",
        r"\1\2",
        text,
        flags=re.IGNORECASE,
    )

    # 4. Bare www. domain broken with space/newline (e.g. www. ti.com)
    text = re.sub(
        r"(www\.[a-zA-Z0-9_\-./?%&=#+~]*?[./=&#+~?])\s*(?:\n|\s+)\s*([a-zA-Z0-9_\-./?%&=#+~]+)",
        r"\1\2",
        text,
        flags=re.IGNORECASE,
    )

    return text


def cleanup_vietnamese_typography(text: str) -> str:
    """Normalize Vietnamese punctuation, quotes, brackets, and whitespace.

    Fixes common machine translation artifacts:
    - Extra spaces before punctuation: 'từ , từ' -> 'từ, từ'
    - Spaces inside parentheses: '( chữ )' -> '(chữ)'
    - Spaces inside quotes: '“ từ ”' -> '“từ”'
    - Duplicate spaces: '  ' -> ' '
    """
    if not text:
        return text

    # Remove space before punctuation marks: , . : ; ! ?
    text = re.sub(r"\s+([,.:;!?])", r"\1", text)

    # Ensure single space after commas, semicolons, exclamation marks, question marks
    text = re.sub(r"([,;!?])([^\s,;!?/\"'”’\)])", r"\1 \2", text)

    # Clean up spaces inside parentheses, square brackets, curly braces
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)
    text = re.sub(r"\[\s+", "[", text)
    text = re.sub(r"\s+\]", "]", text)

    # Clean up spaces inside curly and straight quotes
    text = re.sub(r"“\s+", "“", text)
    text = re.sub(r"\s+”", "”", text)
    text = re.sub(r'\"\s+([^"\n]+?)\s+\"', r'"\1"', text)

    # Collapse multiple spaces into one
    text = re.sub(r"[ \t]{2,}", " ", text)

    return text.strip()


CITATION_BADGE_PATTERN = re.compile(
    r"\[\s*(CrossRef|Crossref|PubMed|Pubmed|PubMed\s+Central|PMC\d+|Google\s+Scholar|Scholar|IEEE\s+Xplore|IEEE|Web\s+of\s+Science|Scopus|arXiv(?::\S+)?|bioRxiv(?::\S+)?|medRxiv(?::\S+)?|Preprint|DOI(?::\S+)?)\s*\]",
    re.IGNORECASE,
)

RAW_URL_PATTERN = re.compile(
    r"(?:https?://|ftp://|www\.)[a-zA-Z0-9][-a-zA-Z0-9@:%._+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b(?:[-a-zA-Z0-9()@:%_+.~#?&/=]*)"
    r"|(?:\bdoi:\s*10\.\d{4,9}/[-._;()/:a-zA-Z0-9]+)"
    r"|(?:\b10\.\d{4,9}/[-._;()/:a-zA-Z0-9]+)"
    r"|(?:\barXiv:\s*\d{4}\.\d{4,5}(?:v\d+)?\b)"
    r"|(?:mailto:[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)"
    r"|(?:\b[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+\b)",
    re.IGNORECASE,
)


def trim_url(url: str) -> tuple[str, str]:
    """Trim trailing sentence punctuation from URL, returning (clean_url, trailing_punct)."""
    trailing = ""
    while url and url[-1] in ".,;:!?":
        trailing = url[-1] + trailing
        url = url[:-1]
    if url.endswith(")") and url.count("(") < url.count(")"):
        trailing = ")" + trailing
        url = url[:-1]
    if url.endswith("]") and url.count("[") < url.count("]"):
        trailing = "]" + trailing
        url = url[:-1]
    return url, trailing


def protect_links(text: str) -> tuple[str, dict[str, str]]:
    """Replace URLs, DOIs, email addresses, and academic citation badges with protected tokens {u0}, {u1}.

    Returns:
        (protected_text, mapping_dict)
    """
    if not text:
        return text, {}

    current_text = clean_and_rejoin_urls(text)
    mapping: dict[str, str] = {}
    uid = 0

    # 1. Protect citation badges first
    for m in list(CITATION_BADGE_PATTERN.finditer(current_text)):
        badge = m.group(0)
        tag = f"{{u{uid}}}"
        mapping[f"u{uid}"] = badge
        uid += 1
        current_text = current_text.replace(badge, tag, 1)

    # 2. Protect URLs / DOIs / Emails / arXiv IDs
    for m in list(RAW_URL_PATTERN.finditer(current_text)):
        raw_match = m.group(0)
        clean_url, trailing = trim_url(raw_match)
        if clean_url:
            tag = f"{{u{uid}}}"
            mapping[f"u{uid}"] = clean_url
            uid += 1
            current_text = current_text.replace(raw_match, tag + trailing, 1)

    return current_text, mapping


def restore_links(text: str, mapping: dict[str, str]) -> str:
    """Restore protected {u0}, {u1} tokens back to original URLs and link badges."""
    if not text or not mapping:
        return text

    current_text = text
    for key, original in mapping.items():
        num = key[1:]
        pattern = re.compile(rf"\{{\s*[uU]\s*{num}\s*\}}")
        current_text = pattern.sub(lambda m, orig=original: orig, current_text)

    return current_text


def protect_glossary(text: str, terms: list[str]) -> tuple[str, dict[str, str]]:
    """Replace custom glossary terms with protected tokens {g0}, {g1} before translation.

    Returns:
        (protected_text, mapping_dict)
    """
    if not text or not terms:
        return text, {}

    mapping: dict[str, str] = {}
    current_text = text

    # Sort terms by length descending so longer phrases match before substrings
    cleaned_terms = sorted(
        {t.strip() for t in terms if t and t.strip()},
        key=len,
        reverse=True,
    )

    for idx, term in enumerate(cleaned_terms):
        tag = f"{{g{idx}}}"
        # Word boundary pattern (case-insensitive)
        escaped = re.escape(term)
        pattern = re.compile(rf"\b{escaped}\b", re.IGNORECASE)

        matches = list(pattern.finditer(current_text))
        if matches:
            # Store the original casing of the first match or term
            mapping[f"g{idx}"] = matches[0].group(0)
            current_text = pattern.sub(tag, current_text)

    return current_text, mapping


def restore_glossary(text: str, mapping: dict[str, str]) -> str:
    """Restore protected {g0}, {g1} tokens back to original terms."""
    if not text or not mapping:
        return text

    current_text = text

    for key, original_term in mapping.items():
        # Match {g0}, { g0 }, {G0}, { g 0 }
        num = key[1:]
        pattern = re.compile(rf"\{{\s*[gG]\s*{num}\s*\}}")
        current_text = pattern.sub(lambda m, orig=original_term: orig, current_text)

    return current_text
