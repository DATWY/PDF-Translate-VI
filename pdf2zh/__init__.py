import logging

log = logging.getLogger(__name__)

__version__ = "2.0.0"
__ruleset__ = "code4life-preservation-v1"
__author__ = "Byaidu"
__all__ = ["translate", "translate_stream"]


def __getattr__(name):
    if name in {"translate", "translate_stream"}:
        from pdf2zh.high_level import translate, translate_stream

        return {"translate": translate, "translate_stream": translate_stream}[name]
    if name in {"doclayout", "converter", "rules", "translator", "cache", "pdfinterp", "high_level", "text_utils"}:
        import importlib
        return importlib.import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

