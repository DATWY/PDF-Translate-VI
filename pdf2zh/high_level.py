"""Functions that can be used for the most common use-cases for pdf2zh.six"""

import asyncio
import concurrent.futures
import io
import logging
import os
import re
import sys
import tempfile
from asyncio import CancelledError
from pathlib import Path
from string import Template
from typing import Any, BinaryIO, Dict, List, Optional

import numpy as np
import pikepdf
import tqdm
from babeldoc.assets.assets import get_font_and_metadata
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdfexceptions import PDFValueError
from pdfminer.pdfinterp import PDFResourceManager
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser
from pymupdf import Document, Font

from pdf2zh.converter import TranslateConverter
from pdf2zh.doclayout import OnnxModel
from pdf2zh.pdfinterp import PDFPageInterpreterEx
from pdf2zh.rules import classify_preserved_page, is_scanned_page
from pdf2zh.translator import ENGINES

NOTO_NAME = "noto"

logger = logging.getLogger(__name__)

noto_list = [
    "am",  # Amharic
    "ar",  # Arabic
    "bn",  # Bengali
    "bg",  # Bulgarian
    "chr",  # Cherokee
    "el",  # Greek
    "gu",  # Gujarati
    "iw",  # Hebrew
    "hi",  # Hindi
    "kn",  # Kannada
    "ml",  # Malayalam
    "mr",  # Marathi
    "ru",  # Russian
    "sr",  # Serbian
    "ta",  # Tamil
    "te",  # Telugu
    "th",  # Thai
    "ur",  # Urdu
    "uk",  # Ukrainian
]


def check_files(files: List[str]) -> List[str]:
    files = [
        f for f in files if not f.startswith("http://")
    ]  # exclude online files, http
    files = [
        f for f in files if not f.startswith("https://")
    ]  # exclude online files, https
    missing_files = [file for file in files if not os.path.exists(file)]
    return missing_files


def translate_patch(
    inf: BinaryIO,
    pages: Optional[list[int]] = None,
    vfont: str = "",
    vchar: str = "",
    thread: int = 0,
    doc_zh: Document = None,
    lang_in: str = "",
    lang_out: str = "",
    service: str = "",
    noto_name: str = "",
    noto: Font = None,
    callback: object = None,
    cancellation_event: asyncio.Event = None,
    model: OnnxModel = None,
    envs: Dict = None,
    prompt: Template = None,
    ignore_cache: bool = False,
    **kwarg: Any,
) -> None:
    # 1. Initialize shared translator (shared connection pool & RAM cache)
    param = service.split(":", 1)
    service_name = param[0]
    service_model = param[1] if len(param) > 1 else None
    if not envs:
        envs = {}
    if service_name not in ENGINES:
        supported = ", ".join(sorted(ENGINES))
        raise ValueError(
            f"Unsupported translation service {service_name!r}; supported: {supported}"
        )
    shared_translator = ENGINES[service_name](
        lang_in,
        lang_out,
        service_model,
        envs=envs,
        prompt=prompt,
        ignore_cache=ignore_cache,
    )

    parser = PDFParser(inf)
    doc = PDFDocument(parser)

    rsrcmgr = PDFResourceManager()
    layout = {}
    scanned_pages = set()
    device = TranslateConverter(
        rsrcmgr,
        vfont,
        vchar,
        thread,
        layout,
        lang_in,
        lang_out,
        service,
        noto_name,
        noto,
        envs,
        prompt,
        ignore_cache,
        translator=shared_translator,
    )

    assert device is not None
    obj_patch = {}
    interpreter = PDFPageInterpreterEx(rsrcmgr, device, obj_patch)
    if pages:
        total_pages = len(pages)
    else:
        total_pages = doc_zh.page_count

    # ================================================================
    # PHASE 1: Pre-compute ALL page layouts (CPU-intensive ONNX inference)
    # This separates CPU-bound layout detection from I/O-bound translation
    # ================================================================
    vcls = ["abandon", "figure", "table", "isolate_formula", "formula_caption"]
    pre_layouts = {}
    pre_scanned = set()
    pre_preservations = {}

    # Collect all page objects from pdfminer
    all_pages_enum = list(enumerate(PDFPage.create_pages(doc)))
    target_pageids = []
    for pageno, _page in all_pages_enum:
        if pages and (pageno not in pages):
            continue
        target_pageids.append(pageno)

    logger.info("Phase 1: Pre-computing layouts for %d pages...", len(target_pageids))
    with tqdm.tqdm(total=total_pages * 2) as progress:
        progress.set_description("Phase 1: Layout")
        for pageno in target_pageids:
            if cancellation_event and cancellation_event.is_set():
                raise CancelledError("task cancelled")

            page_rect = doc_zh[pageno].rect
            page_area = page_rect.width * page_rect.height
            page_blocks = doc_zh[pageno].get_text("dict")["blocks"]
            if is_scanned_page(page_blocks, page_area):
                pre_scanned.add(pageno)

            pix = doc_zh[pageno].get_pixmap()
            image = np.frombuffer(pix.samples, np.uint8).reshape(
                pix.height, pix.width, 3
            )[:, :, ::-1]
            target_imgsz = min(max(int(pix.height / 32) * 32, 640), 768)
            page_layout = model.predict(image, imgsz=target_imgsz)[0]
            box = np.ones((pix.height, pix.width))
            h, w = box.shape
            non_vcls_boxes = [
                (i, d) for i, d in enumerate(page_layout.boxes)
                if page_layout.names[int(d.cls)] not in vcls
            ]
            for i, d in reversed(non_vcls_boxes):
                x0, y0, x1, y1 = d.xyxy.squeeze()
                x0, y0, x1, y1 = (
                    np.clip(int(x0 - 1), 0, w - 1),
                    np.clip(int(h - y1 - 1), 0, h - 1),
                    np.clip(int(x1 + 1), 0, w - 1),
                    np.clip(int(h - y0 + 1), 0, h - 1),
                )
                box[y0:y1, x0:x1] = i + 2
            for i, d in enumerate(page_layout.boxes):
                if page_layout.names[int(d.cls)] in vcls:
                    x0, y0, x1, y1 = d.xyxy.squeeze()
                    x0, y0, x1, y1 = (
                        np.clip(int(x0 - 1), 0, w - 1),
                        np.clip(int(h - y1 - 1), 0, h - 1),
                        np.clip(int(x1 + 1), 0, w - 1),
                        np.clip(int(h - y0 + 1), 0, h - 1),
                    )
                    box[y0:y1, x0:x1] = 0

            page_text = doc_zh[pageno].get_text("text")
            preservation = classify_preserved_page(page_text)
            pre_layouts[pageno] = box
            if preservation is not None:
                pre_preservations[pageno] = preservation
                
            progress.update()
            if callback:
                callback(progress)

        logger.info("Phase 1 complete: all layouts pre-computed.")

        # ================================================================
        # PHASE 2: Process pages with pre-computed layouts
        # PDF parsing + translation with full thread parallelism
        # ================================================================
        logger.info("Phase 2: Processing pages with translation...")
        progress.set_description("Phase 2: Translate")
        for pageno, page in all_pages_enum:
                if cancellation_event and cancellation_event.is_set():
                    raise CancelledError("task cancelled")
                if pages and (pageno not in pages):
                    continue
                progress.update()
                if callback:
                    callback(progress)
                page.pageno = pageno

                if pageno in pre_preservations:
                    pres = pre_preservations[pageno]
                    logger.info(
                        "Page %s detected as %s (%s)",
                        pageno + 1,
                        pres.kind,
                        pres.detail,
                    )

                layout[page.pageno] = pre_layouts[pageno]
                if pageno in pre_scanned:
                    device.scanned_pages.add(pageno)
                page.page_xref = doc_zh.get_new_xref()
                doc_zh.update_object(page.page_xref, "<<>>")
                doc_zh.update_stream(page.page_xref, b"")
                doc_zh[page.pageno].set_contents(page.page_xref)
                interpreter.process_page(page)

        device.close()
    return obj_patch, device.translation_failures


def translate_stream(
    stream: bytes,
    pages: Optional[list[int]] = None,
    lang_in: str = "",
    lang_out: str = "",
    service: str = "",
    thread: int = 0,
    vfont: str = "",
    vchar: str = "",
    callback: object = None,
    cancellation_event: asyncio.Event = None,
    model: OnnxModel = None,
    envs: Dict = None,
    prompt: Template = None,
    skip_subset_fonts: bool = False,
    ignore_cache: bool = False,
    **kwarg: Any,
):
    font_list = [("tiro", None)]

    font_path = download_remote_fonts(lang_out.lower())
    noto_name = NOTO_NAME
    noto = Font(noto_name, font_path)
    font_list.append((noto_name, font_path))

    doc_en = Document(stream=stream)
    stream = io.BytesIO()
    doc_en.save(stream)
    doc_zh = Document(stream=stream)
    page_count = doc_zh.page_count
    # font_list = [("GoNotoKurrent-Regular.ttf", font_path), ("tiro", None)]
    font_id = {}
    for page in doc_zh:
        for font in font_list:
            font_id[font[0]] = page.insert_font(font[0], font[1])
    xreflen = doc_zh.xref_length()
    for xref in range(1, xreflen):
        for label in ["Resources/", ""]:
            try:
                font_res = doc_zh.xref_get_key(xref, f"{label}Font")
                target_key_prefix = f"{label}Font/"
                if font_res[0] == "xref":
                    resource_xref_id = re.search("(\\d+) 0 R", font_res[1]).group(1)
                    xref = int(resource_xref_id)
                    font_res = ("dict", doc_zh.xref_object(xref))
                    target_key_prefix = ""

                if font_res[0] == "dict":
                    for font in font_list:
                        target_key = f"{target_key_prefix}{font[0]}"
                        font_exist = doc_zh.xref_get_key(xref, target_key)
                        if font_exist[0] == "null":
                            doc_zh.xref_set_key(
                                xref,
                                target_key,
                                f"{font_id[font[0]]} 0 R",
                            )
            except Exception:
                pass

    fp = io.BytesIO()

    doc_zh.save(fp)
    obj_patch, translation_failures = translate_patch(fp, **locals())

    for obj_id, ops_new in obj_patch.items():
        # ops_old=doc_en.xref_stream(obj_id)
        # print(obj_id)
        # print(ops_old)
        # print(ops_new.encode())
        doc_zh.update_stream(obj_id, ops_new.encode())

    doc_en.insert_file(doc_zh)
    for id in range(page_count):
        doc_en.move_page(page_count + id, id * 2 + 1)
    if not skip_subset_fonts:
        doc_zh.subset_fonts(fallback=True)
        doc_en.subset_fonts(fallback=True)
    return (
        doc_zh.write(deflate=True, garbage=3, use_objstms=1),
        doc_en.write(deflate=True, garbage=3, use_objstms=1),
        translation_failures,
    )


def convert_to_pdfa(input_path, output_path):
    """
    Convert PDF to PDF/A format

    Args:
        input_path: Path to source PDF file
        output_path: Path to save PDF/A file
    """
    from pikepdf import Dictionary, Name, Pdf

    # Open the PDF file
    pdf = Pdf.open(input_path)

    # Add PDF/A conformance metadata
    metadata = {
        "pdfa_part": "2",
        "pdfa_conformance": "B",
        "title": pdf.docinfo.get("/Title", ""),
        "author": pdf.docinfo.get("/Author", ""),
        "creator": "PDF Math Translate",
    }

    with pdf.open_metadata() as meta:
        meta.load_from_docinfo(pdf.docinfo)
        meta["pdfaid:part"] = metadata["pdfa_part"]
        meta["pdfaid:conformance"] = metadata["pdfa_conformance"]

    # Create OutputIntent dictionary
    output_intent = Dictionary(
        {
            "/Type": Name("/OutputIntent"),
            "/S": Name("/GTS_PDFA1"),
            "/OutputConditionIdentifier": "sRGB IEC61966-2.1",
            "/RegistryName": "http://www.color.org",
            "/Info": "sRGB IEC61966-2.1",
        }
    )

    # Add output intent to PDF root
    if "/OutputIntents" not in pdf.Root:
        pdf.Root.OutputIntents = [output_intent]
    else:
        pdf.Root.OutputIntents.append(output_intent)

    # Save as PDF/A
    pdf.save(output_path, linearize=True)
    pdf.close()


def translate(
    files: list[str],
    output: str = "",
    pages: Optional[list[int]] = None,
    lang_in: str = "",
    lang_out: str = "",
    service: str = "",
    thread: int = 0,
    vfont: str = "",
    vchar: str = "",
    callback: object = None,
    compatible: bool = False,
    cancellation_event: asyncio.Event = None,
    model: OnnxModel = None,
    envs: Dict = None,
    prompt: Template = None,
    skip_subset_fonts: bool = False,
    ignore_cache: bool = False,
    **kwarg: Any,
):
    if not files:
        raise PDFValueError("No files to process.")

    missing_files = check_files(files)

    if missing_files:
        print("The following files do not exist:", file=sys.stderr)
        for file in missing_files:
            print(f"  {file}", file=sys.stderr)
        raise PDFValueError("Some files do not exist.")

    result_files = []

    for file in files:
        source_path = Path(file).resolve()
        if source_path.suffix.lower() != ".pdf":
            raise PDFValueError(f"Only PDF input is supported: {source_path}")
        filename = source_path.stem
        processing_path = source_path
        temporary_paths: list[Path] = []

        try:
            pikepdf.open(source_path).close()
        except Exception:
            logger.warning(
                "PDF structure issue detected in %s; translating a repaired temporary copy",
                source_path,
            )
            try:
                with tempfile.NamedTemporaryFile(suffix="-fixed.pdf", delete=False) as temporary:
                    fixed_path = Path(temporary.name)
                with pikepdf.open(source_path, suppress_warnings=True) as fixed_pdf:
                    fixed_pdf.save(fixed_path)
                processing_path = fixed_path
                temporary_paths.append(fixed_path)
            except Exception as error:
                raise PDFValueError(f"Could not repair PDF structure: {source_path}") from error

        if compatible:
            with tempfile.NamedTemporaryFile(suffix="-pdfa.pdf", delete=False) as temporary:
                pdfa_path = Path(temporary.name)
            convert_to_pdfa(processing_path, pdfa_path)
            processing_path = pdfa_path
            temporary_paths.append(pdfa_path)

        s_raw = processing_path.read_bytes()
        for temporary_path in temporary_paths:
            temporary_path.unlink(missing_ok=True)

        try:
            s_mono, _s_dual, translation_failures = translate_stream(
                s_raw,
                **locals(),
            )
            if translation_failures:
                logger.warning(
                    "%d of the segments in %s could not be translated and were left "
                    "in the source language",
                    len(translation_failures),
                    source_path,
                )
            file_mono = Path(output) / f"{filename}-mono.pdf"
            with open(file_mono, "wb") as doc_mono:
                doc_mono.write(s_mono)
            result_files.append((str(file_mono), len(translation_failures)))
        except Exception as error:
            raise PDFValueError(f"Failed to translate {source_path}") from error

    return result_files


def download_remote_fonts(lang: str):
    lang = lang.lower()
    LANG_NAME_MAP = {
        **{la: "GoNotoKurrent-Regular.ttf" for la in noto_list},
        **{
            la: f"SourceHanSerif{region}-Regular.ttf"
            for region, langs in {
                "CN": ["zh-cn", "zh-hans", "zh"],
                "TW": ["zh-tw", "zh-hant"],
                "JP": ["ja"],
                "KR": ["ko"],
            }.items()
            for la in langs
        },
    }

    # Use Times New Roman for Vietnamese if available
    if lang == "vi":
        windir = os.environ.get("WINDIR", "C:/Windows")
        candidates = [
            Path(windir) / "Fonts" / "times.ttf",
            Path(windir) / "Fonts" / "TIMES.TTF",
            Path("C:/Windows/Fonts/times.ttf"),
        ]
        for times_path in candidates:
            if times_path.exists():
                logger.info(f"use font: {times_path.as_posix()}")
                return times_path.as_posix()

    font_name = LANG_NAME_MAP.get(lang, "GoNotoKurrent-Regular.ttf")

    # docker
    font_path = os.environ.get("NOTO_FONT_PATH", Path("/app", font_name).as_posix())
    if not Path(font_path).exists():
        font_path, _ = get_font_and_metadata(font_name)
        font_path = font_path.as_posix()

    logger.info(f"use font: {font_path}")

    return font_path
