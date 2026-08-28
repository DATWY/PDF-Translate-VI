"""Smart NLP and typography utilities for document translation."""

from __future__ import annotations

import re


def dehyphenate_text(text: str) -> str:
    """Join words broken across lines by a trailing hyphen.

    Example:
        'inter-\\npretation' -> 'interpretation'
        'approxi- \\n mately' -> 'approximately'
        'state-of-the-art' -> 'state-of-the-art' (preserved)
    """
    if not text or "-" not in text:
        return text

    # Match lowercase word followed by hyphen, newline/whitespace, then lowercase continuation
    pattern = re.compile(r"([a-zA-Z]{2,})-\s*\n\s*([a-zA-Z]{2,})")
    return pattern.sub(r"\1\2", text)


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

    # Ensure single space after punctuation if followed by a letter/number
    text = re.sub(r"([,.:;!?])([^\s0-9,.:;!?/\"'”’])", r"\1 \2", text)

    # Clean up spaces inside parentheses, square brackets, curly braces
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)
    text = re.sub(r"\[\s+", "[", text)
    text = re.sub(r"\s+\]", "]", text)

    # Clean up spaces inside curly and straight quotes
    text = re.sub(r"“\s+", "“", text)
    text = re.sub(r"\s+”", "”", text)
    text = re.sub(r'\"\s+([^"]+?)\s+\"', r'"\1"', text)

    # Collapse multiple spaces into one
    text = re.sub(r"[ \t]{2,}", " ", text)

    return text.strip()


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
        current_text = pattern.sub(original_term, current_text)

    return current_text
