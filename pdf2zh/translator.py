"""Translation adapters for the preservation-focused PDF core."""

from __future__ import annotations

import html
import json
import logging
import re
import socket
import threading
import unicodedata
from typing import Any, ClassVar
from urllib3.util.retry import Retry

import requests

from pdf2zh.cache import TranslationCache

logger = logging.getLogger(__name__)

PLACEHOLDER_PATTERN = re.compile(r"</?b\d+>")


def remove_control_characters(value: str) -> str:
    """Remove control characters that cannot be emitted safely into PDF text."""
    return "".join(character for character in value if unicodedata.category(character)[0] != "C")


class BaseTranslator:
    """Cache-aware translator interface consumed by the PDF converter."""

    name = "base"
    lang_map: ClassVar[dict[str, str]] = {}

    def __init__(
        self,
        lang_in: str,
        lang_out: str,
        model: str | None = None,
        *,
        ignore_cache: bool = False,
        **_: Any,
    ) -> None:
        self.lang_in = self.lang_map.get(lang_in.lower(), lang_in)
        self.lang_out = self.lang_map.get(lang_out.lower(), lang_out)
        self.model = model
        self.ignore_cache = ignore_cache
        self.cache = TranslationCache(
            self.name,
            {
                "lang_in": self.lang_in,
                "lang_out": self.lang_out,
                "model": model,
            },
        )

    def translate(self, text: str, ignore_cache: bool = False) -> str:
        """Translate text, consulting the persistent cache unless bypassed."""
        if not (self.ignore_cache or ignore_cache):
            cached = self.cache.get(text)
            if cached is not None:
                return cached
        translated = self.do_translate(text)
        if not (self.ignore_cache or ignore_cache):
            self.cache.set(text, translated)
        return translated

    def translate_batch(self, texts: list[str], ignore_cache: bool = False) -> list[str]:
        """Translate a batch of text segments, consulting cache and batching where possible."""
        results: list[str | None] = [None] * len(texts)
        missing_indices: list[int] = []
        missing_texts: list[str] = []

        for idx, text in enumerate(texts):
            if not text.strip() or re.match(r"^\{v\d+\}$", text):
                results[idx] = text
                continue
            if not (self.ignore_cache or ignore_cache):
                cached = self.cache.get(text)
                if cached is not None:
                    results[idx] = cached
                    continue
            missing_indices.append(idx)
            missing_texts.append(text)

        if not missing_texts:
            return results  # type: ignore[return-value]

        translated_missing = self.do_translate_batch(missing_texts)

        for idx, orig_text, trans_text in zip(missing_indices, missing_texts, translated_missing):
            results[idx] = trans_text
            if not (self.ignore_cache or ignore_cache):
                self.cache.set(orig_text, trans_text)

        return results  # type: ignore[return-value]

    def do_translate(self, text: str) -> str:
        """Translate one engine-sized text segment."""
        raise NotImplementedError

    def do_translate_batch(self, texts: list[str]) -> list[str]:
        """Default fallback: translate one by one."""
        return [self.do_translate(t) for t in texts]

    def get_rich_text_left_placeholder(self, identifier: int) -> str:
        return f"<b{identifier}>"

    def get_rich_text_right_placeholder(self, identifier: int) -> str:
        return f"</b{identifier}>"

    def get_formular_placeholder(self, identifier: int) -> str:
        return self.get_rich_text_left_placeholder(identifier) + self.get_rich_text_right_placeholder(identifier)


class GoogleTranslator(BaseTranslator):
    """Translate through Google's mobile web endpoint without an API key."""

    name = "google"
    lang_map: ClassVar[dict[str, str]] = {"zh": "zh-CN"}
    DELIMITER = "\n\n_V_SEG_\n\n"
    DELIMITER_REGEX = re.compile(r"\s*_V_SEG_\s*", re.IGNORECASE)

    def __init__(
        self,
        lang_in: str,
        lang_out: str,
        model: str | None = None,
        *,
        ignore_cache: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            lang_in,
            lang_out,
            model,
            ignore_cache=ignore_cache,
            **kwargs,
        )
        self.session = requests.Session()
        retry_strategy = Retry(
            total=5,
            backoff_factor=0.1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
        )
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=512,
            pool_maxsize=512,
            max_retries=retry_strategy,
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        # Pre-resolve DNS and keep TCP alive to eliminate per-request overhead
        self.endpoint = "https://translate.google.com/m"
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
            ),
            "Connection": "keep-alive",
            "Accept-Encoding": "gzip, deflate, br",
        }

    def do_translate(self, text: str) -> str:
        response = self.session.get(
            self.endpoint,
            params={"tl": self.lang_out, "sl": self.lang_in, "q": text[:5000]},
            headers=self.headers,
            timeout=15,
        )
        if response.status_code == 400:
            raise RuntimeError("Google Translate rejected the text segment")
        response.raise_for_status()
        match = re.search(
            r'(?s)class="(?:t0|result-container)">(.*?)<',
            response.text,
        )
        if match is None:
            raise RuntimeError("Google Translate response did not contain a translation result")
        return remove_control_characters(html.unescape(match.group(1)))

    def do_translate_batch(self, texts: list[str]) -> list[str]:
        """Group multiple texts into chunks (< 4800 chars) and translate in parallel HTTP requests."""
        import concurrent.futures

        if not texts:
            return []
        if len(texts) == 1:
            return [self.do_translate(texts[0])]

        chunks: list[list[str]] = []
        current_chunk: list[str] = []
        current_len = 0

        for text in texts:
            t_len = len(text) + len(self.DELIMITER)
            if current_chunk and (current_len + t_len > 3500 or len(current_chunk) >= 15):
                chunks.append(current_chunk)
                current_chunk = [text]
                current_len = len(text)
            else:
                current_chunk.append(text)
                current_len += t_len

        if current_chunk:
            chunks.append(current_chunk)

        def _translate_chunk(chunk: list[str]) -> list[str]:
            if len(chunk) == 1:
                return [self.do_translate(chunk[0])]
            combined = self.DELIMITER.join(chunk)
            
            def fallback():
                with concurrent.futures.ThreadPoolExecutor(max_workers=min(15, len(chunk))) as p:
                    return list(p.map(self.do_translate, chunk))
                    
            try:
                translated_combined = self.do_translate(combined)
                parts = self.DELIMITER_REGEX.split(translated_combined)
                if len(parts) == len(chunk):
                    return parts
                else:
                    logger.debug(
                        "Batch split length mismatch (%d vs %d), falling back to individual translation",
                        len(parts),
                        len(chunk),
                    )
                    return fallback()
            except Exception as e:
                logger.debug("Batch translation failed (%s), falling back to individual translation", e)
                return fallback()

        # Process ALL sub-chunks in PARALLEL instead of sequentially
        if len(chunks) == 1:
            return _translate_chunk(chunks[0])

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(chunks)) as pool:
            chunk_results = list(pool.map(_translate_chunk, chunks))

        results: list[str] = []
        for cr in chunk_results:
            results.extend(cr)
        return results


def placeholders(text: str) -> list[str]:
    """Return the formula placeholder tags in order, e.g. ['<b0>', '</b0>']."""
    return PLACEHOLDER_PATTERN.findall(text)


def load_segment_table(path: str | None) -> dict[str, str]:
    """Load a source-to-translation table from a JSONL file of {"src", "dst"} records.

    Entries whose translation dropped or reordered a formula placeholder are
    skipped, so the next pass re-emits them instead of silently losing a formula.
    """
    if not path:
        return {}
    table: dict[str, str] = {}
    with open(path, encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                source, translation = record["src"], record["dst"]
            except (ValueError, KeyError, TypeError) as error:
                raise ValueError(
                    f"{path} line {number}: expected a JSON object with 'src' and 'dst'"
                ) from error
            if not isinstance(source, str) or not isinstance(translation, str):
                raise ValueError(f"{path} line {number}: 'src' and 'dst' must be strings")
            if not translation:
                continue
            if placeholders(source) != placeholders(translation):
                logger.warning(
                    "%s line %d: formula placeholders differ between src and dst; "
                    "segment left untranslated",
                    path,
                    number,
                )
                continue
            table[source] = translation
    return table


class HandoffTranslator(BaseTranslator):
    """Translate from a table produced outside the pipeline, such as by an agent.

    Two passes: the first runs with no table and records every segment it could
    not translate, the caller fills those in, and the second runs with the filled
    table to emit the real document.
    """

    name = "handoff"

    def __init__(
        self,
        lang_in: str,
        lang_out: str,
        model: str | None = None,
        *,
        ignore_cache: bool = False,
        envs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        # Misses fall through untranslated, so the shared cache must never see them
        # or "translation == original" is memoised for every later run.
        super().__init__(lang_in, lang_out, model, ignore_cache=True, **kwargs)
        envs = envs or {}
        self.table = load_segment_table(envs.get("segments_in"))
        self.misses_path = envs.get("segments_out")
        self._seen: set[str] = set()
        self._lock = threading.Lock()
        if self.misses_path:
            open(self.misses_path, "w", encoding="utf-8").close()

    def do_translate(self, text: str) -> str:
        translation = self.table.get(text)
        if translation is not None:
            return translation
        self._record_miss(text)
        return text

    def _record_miss(self, text: str) -> None:
        """Append one untranslated segment, deduplicated, for the caller to fill in."""
        if not self.misses_path:
            return
        with self._lock:
            if text in self._seen:
                return
            self._seen.add(text)
            with open(self.misses_path, "a", encoding="utf-8") as stream:
                stream.write(json.dumps({"src": text}, ensure_ascii=False) + "\n")


ENGINES: dict[str, type[BaseTranslator]] = {
    engine.name: engine for engine in (GoogleTranslator, HandoffTranslator)
}
