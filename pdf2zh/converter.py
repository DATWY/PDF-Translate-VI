import concurrent.futures
import logging
import re
import unicodedata
from enum import Enum
from string import Template
from typing import Dict

import numpy as np
from pdfminer.converter import PDFConverter
from pdfminer.layout import LTChar, LTFigure, LTLine, LTPage
from pdfminer.pdffont import PDFCIDFont, PDFUnicodeNotDefined
from pdfminer.pdfinterp import PDFGraphicState, PDFResourceManager
from pdfminer.utils import apply_matrix_pt, mult_matrix
from pymupdf import Font
from tenacity import retry, stop_after_attempt, wait_exponential

from pdf2zh.rules import BULLET_CHARACTERS, is_formula_font, line_height_for_language
from pdf2zh.text_utils import (
    cleanup_vietnamese_typography,
    dehyphenate_text,
    protect_glossary,
    protect_links,
    restore_glossary,
    restore_links,
)
from pdf2zh.translator import ENGINES, BaseTranslator

log = logging.getLogger(__name__)


class PDFConverterEx(PDFConverter):
    def __init__(
        self,
        rsrcmgr: PDFResourceManager,
    ) -> None:
        PDFConverter.__init__(self, rsrcmgr, None, "utf-8", 1, None)

    def begin_page(self, page, ctm) -> None:
        x0, y0, x1, y1 = page.cropbox
        x0, y0 = apply_matrix_pt(ctm, (x0, y0))
        x1, y1 = apply_matrix_pt(ctm, (x1, y1))
        mediabox = (0, 0, abs(x0 - x1), abs(y0 - y1))
        self.cur_item = LTPage(page.pageno, mediabox)

    def end_page(self, page):
        return self.receive_layout(self.cur_item)

    def begin_figure(self, name, bbox, matrix) -> None:
        self._stack.append(self.cur_item)
        self.cur_item = LTFigure(name, bbox, mult_matrix(matrix, self.ctm))
        self.cur_item.pageid = self._stack[-1].pageid

    def end_figure(self, _: str) -> None:
        fig = self.cur_item
        assert isinstance(self.cur_item, LTFigure), str(type(self.cur_item))
        self.cur_item = self._stack.pop()
        self.cur_item.add(fig)
        return self.receive_layout(fig)

    def render_char(
        self,
        matrix,
        font,
        fontsize: float,
        scaling: float,
        rise: float,
        cid: int,
        ncs,
        graphicstate: PDFGraphicState,
    ) -> float:
        try:
            text = font.to_unichr(cid)
            assert isinstance(text, str), str(type(text))
        except PDFUnicodeNotDefined:
            text = self.handle_undefined_char(font, cid)
        textwidth = font.char_width(cid)
        textdisp = font.char_disp(cid)
        item = LTChar(
            matrix,
            font,
            fontsize,
            scaling,
            rise,
            text,
            textwidth,
            textdisp,
            ncs,
            graphicstate,
        )
        self.cur_item.add(item)
        item.cid = cid
        item.font = font
        return item.adv


class Paragraph:
    def __init__(self, y, x, x0, x1, y0, y1, size, brk, color=None):
        self.y: float = y
        self.x: float = x
        self.x0: float = x0
        self.x1: float = x1
        self.y0: float = y0
        self.y1: float = y1
        self.size: float = size
        self.brk: bool = brk
        self.color = color
        # For weighted-average font size calculation
        self._size_sum: float = size
        self._size_count: int = 1


# fmt: off
class TranslateConverter(PDFConverterEx):
    def __init__(
        self,
        rsrcmgr,
        vfont: str = None,
        vchar: str = None,
        thread: int = 4,
        layout: dict = None,
        lang_in: str = "",
        lang_out: str = "",
        service: str = "",
        noto_name: str = "",
        noto: Font = None,
        envs: Dict = None,
        prompt: Template = None,
        ignore_cache: bool = False,
        translator: BaseTranslator = None,
    ) -> None:
        super().__init__(rsrcmgr)
        self.vfont = vfont
        self.vchar = vchar
        self.thread = max(1, thread or 4)
        self.layout = layout if layout is not None else {}
        self.noto_name = noto_name
        self.noto = noto
        self.translator: BaseTranslator = translator
        self.scanned_pages: set = set()
        self.glossary_terms: list[str] = (envs or {}).get("glossary", [])
        # Segments whose retries ran out; reported as a partial translation.
        self.translation_failures: list[str] = []
        if self.translator is None:
            # e.g. "handoff:model" -> ["handoff", "model"]; model is unused by both engines
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
            self.translator = ENGINES[service_name](
                lang_in,
                lang_out,
                service_model,
                envs=envs,
                prompt=prompt,
                ignore_cache=ignore_cache,
            )

    def receive_layout(self, ltpage: LTPage):
        sstk: list[str] = []
        pstk: list[Paragraph] = []
        vbkt: int = 0
        vstk: list[LTChar] = []
        vlstk: list[LTLine] = []
        vfix: float = 0
        var: list[list[LTChar]] = []
        varl: list[list[LTLine]] = []
        varf: list[float] = []
        vlen: list[float] = []
        lstk: list[LTLine] = []
        xt: LTChar = None
        xt_cls: int = -1
        vmax: float = ltpage.width / 4
        ops: str = ""

        def vflag(font: str, char: str):
            if isinstance(font, bytes):
                try:
                    font = font.decode('utf-8')
                except UnicodeDecodeError:
                    font = ""
            font = font.split("+")[-1]
            if re.match(r"\(cid:", char):
                return True
            if self.vfont:
                if re.match(self.vfont, font):
                    return True
            else:
                if is_formula_font(font):
                    return True
            if self.vchar:
                if re.match(self.vchar, char):
                    return True
            else:
                if (
                    char
                    and char != " "
                    and (
                        unicodedata.category(char[0])
                        in ["Lm", "Mn", "Sk", "Sm", "Zl", "Zp", "Zs"]
                        or ord(char[0]) in range(0x370, 0x400)
                    )
                ):
                    return True
            return False

        # Preserve native PDF content stream order to ensure tall formula numerators, exponents,
        # and fractions are not hoisted into preceding paragraphs.
        sorted_children = list(ltpage)

        all_page_lines = [it for it in sorted_children if isinstance(it, LTLine) and getattr(it, 'linewidth', 0) < 5]

        ############################################################
        for child_idx, child in enumerate(sorted_children):
            if isinstance(child, LTChar):
                cur_v = False
                layout = self.layout[ltpage.pageid]
                h, w = layout.shape
                cx, cy = np.clip(int(child.x0), 0, w - 1), np.clip(int(child.y0), 0, h - 1)
                cls = layout[cy, cx]
                if child.get_text() in BULLET_CHARACTERS:
                    cls = 0

                has_bar_line = any(
                    (abs(l.pts[0][1] - l.pts[1][1]) < 0.8) and
                    (min(l.pts[0][0], l.pts[1][0]) - 2.0 <= (child.x0 + child.x1) / 2.0 <= max(l.pts[0][0], l.pts[1][0]) + 2.0) and
                    (0.0 <= l.pts[0][1] - child.y0 <= child.size * 1.6 or 0.0 <= child.y0 - l.pts[0][1] <= child.size * 0.8)
                    for l in all_page_lines
                )

                is_math_font = vflag(child.fontname, child.get_text())
                is_sub_or_super = (cls == xt_cls and len(sstk[-1].strip()) > 1 and child.size < pstk[-1].size * 0.79)
                is_pure_symbol = (child.matrix[0] == 0 and child.matrix[3] == 0)

                if (
                    cls == 0
                    or has_bar_line
                    or is_sub_or_super
                    or is_math_font
                    or is_pure_symbol
                ):
                    cur_v = True
                if not cur_v and vstk:
                    # Continue accumulating if character is inside formula bounding box / expression
                    v_max_x = max([vch.x1 for vch in vstk] + ([l.pts[1][0] for l in vlstk] if vlstk else [0]))
                    v_min_y = min([vch.y0 for vch in vstk] + ([l.pts[0][1] for l in vlstk] if vlstk else [9999]))
                    v_max_y = max([vch.y1 for vch in vstk] + ([l.pts[0][1] for l in vlstk] if vlstk else [-9999]))
                    p_size = pstk[-1].size if pstk else 10.0
                    ch_txt = child.get_text()
                    is_in_frac = (vlstk and child.x0 >= min(l.pts[0][0] for l in vlstk) - 8 and child.x1 <= max(l.pts[1][0] for l in vlstk) + 8 and child.y0 >= v_min_y - 20 and child.y1 <= v_max_y + 20)
                    is_formula_char = (
                        is_in_frac
                        or (child.size < p_size * 0.85)
                        or (abs(child.y0 - pstk[-1].y0) > p_size * 0.30)
                        or ch_txt in ["(", ")", "[", "]", "{", "}", "+", "-", "=", "±", "*", "/", "<", ">", "≤", "≥", "≠", "≈", "·", "°", "′", "″", "¯", "^", "_"]
                        or (child.x0 <= v_max_x + p_size * 0.8 and ch_txt.isdigit())
                        or (child.x0 <= v_max_x + p_size * 0.8 and len(ch_txt) == 1 and ("Italic" in child.fontname or "italic" in child.fontname) and ch_txt.isalpha())
                    )
                    if is_formula_char and child.x0 <= v_max_x + p_size * 2.0:
                        cur_v = True
                if not cur_v:
                    if vstk and child.get_text() == "(":
                        cur_v = True
                        vbkt += 1
                    if vbkt and child.get_text() == ")":
                        cur_v = True
                        vbkt -= 1
                if (
                    not cur_v
                    or cls != xt_cls
                    or (sstk[-1] != "" and abs(child.x0 - xt.x0) > vmax)
                ):
                    if vstk:
                        if (
                            not cur_v
                            and cls == xt_cls
                            and child.x0 > max([vch.x0 for vch in vstk])
                        ):
                            vfix = vstk[0].y0 - child.y0
                        if sstk[-1] == "":
                            xt_cls = -1
                        sstk[-1] += f"{{v{len(var)}}}"
                        var.append(vstk)
                        varl.append(vlstk)
                        varf.append(vfix)
                        vstk = []
                        vlstk = []
                        vfix = 0
                if not vstk:
                    if cls == xt_cls:
                        prev_text = sstk[-1].strip() if sstk else ""
                        ch_text = child.get_text()
                        ch_color = getattr(getattr(child, 'graphicstate', None), 'ncolor', None)

                        # Robust multi-scenario check: should moving to the next line start a new item?
                        is_new_item = False
                        is_line_wrap = (xt is not None) and (child.x1 < xt.x0 or (child.y0 > xt.y0 + pstk[-1].size * 1.8) or (child.x0 - xt.x1 > 35.0))

                        if is_line_wrap:
                            # Extract next line preview for intelligent boundary detection
                            fwd_chars = []
                            for fwd in sorted_children[child_idx : child_idx + 35]:
                                if isinstance(fwd, LTChar):
                                    fwd_chars.append(fwd.get_text())
                            next_prefix = "".join(fwd_chars)
                            next_prefix_stripped = next_prefix.strip()

                            # 1. Column Jump: cursor jumped upward to top of a new column or horizontally far to the right
                            if child.y0 > xt.y0 + pstk[-1].size * 1.8 or child.x0 - xt.x1 > 35.0:
                                is_new_item = True
                            # 2. Previous line ended with a standalone Page Number (TOC Entry Boundary)
                            elif prev_text and re.search(r"\s+(\d{1,4}|[ivxlcdm]{1,6})\s*$", prev_text, re.IGNORECASE):
                                is_new_item = True
                            # 3. Next line starts with Section / Subsection index (e.g. '4.1 ', '4.2 ', '0.1 ', '1.2.3 ', 'A.1 ')
                            elif re.match(r"^\s*(\d+|[A-Z])(\.\d+)+\s+", next_prefix):
                                is_new_item = True
                            # 4. Next line starts with Numbered Item / Reference / Roman numeral
                            elif re.match(r"^\s*(\d{1,4}\.|\[\d{1,4}\]|\(\d{1,4}\))\s+", next_prefix):
                                if (
                                    not prev_text
                                    or prev_text.endswith(('.', ':', '"', '”', ')', '!', '?', ']'))
                                    or re.search(r"\b\d{1,4}\.?\s*$", prev_text)
                                    or re.search(r"\[(CrossRef|PubMed|Google\s+Scholar|IEEE|arXiv)\]\s*$", prev_text, re.IGNORECASE)
                                ):
                                    is_new_item = True
                            # 5. Next line starts with special TOC / Section keywords
                            elif re.match(
                                r"^\s*(Summary|Tóm tắt|Index|Appendix|Phụ lục|Preface|Lời nói đầu|Acknowledgments|Lời cảm ơn|Contents|Mục lục|PART\s+[IVXLCDM\d]+|CHAPTER\s+\d+|CHƯƠNG\s+\d+)\b",
                                next_prefix,
                                re.IGNORECASE,
                            ):
                                is_new_item = True
                            # 6. Bullet symbols
                            elif ch_text in ['▶', '►', '•', '▪', '■', '◆', '◇', '★', '-']:
                                is_new_item = True
                            # 7. Explicit dot leaders in TOC lines
                            elif re.search(r'(\.\s*){3,}|\.{3,}', prev_text) is not None:
                                is_new_item = True
                            # 8. Significant vertical paragraph gap (extra blank line > 1.55x)
                            elif abs(child.y0 - xt.y0) > pstk[-1].size * 1.55:
                                is_new_item = True
                            # 9. Paragraph indent (>= 5pt indent) after sentence terminator
                            elif child.x0 >= pstk[-1].x0 + 5.0 and (
                                prev_text.endswith(('.', ':', '"', '”', ')', '!', '?')) or re.search(r'(\.|\))\d+\s*$', prev_text) is not None
                            ):
                                is_new_item = True
                            # 10. Color change or major font size change (> 2.5pt) AND not mid-sentence continuation
                            elif (ch_color != pstk[-1].color or abs(child.size - pstk[-1].size) > 2.5):
                                if prev_text.endswith(('.', ':', '"', '”', ')', '!', '?')) or (next_prefix_stripped and next_prefix_stripped[0].isupper()):
                                    is_new_item = True

                        if is_new_item:
                            sstk.append("")
                            pstk.append(Paragraph(child.y0, child.x0, child.x0, child.x0, child.y0, child.y1, child.size, False, ch_color))
                        elif xt is not None and child.x0 > xt.x1 + 1:
                            sstk[-1] += " "
                        elif is_line_wrap:
                            sstk[-1] += " "
                            pstk[-1].brk = True
                    else:
                        # Only create new paragraph if child is meaningful content
                        prev_text = sstk[-1].strip() if sstk else ""
                        ch_text = child.get_text()
                        if sstk and sstk[-1] == "":
                            # Reuse empty slot
                            pstk[-1].x0 = child.x0
                            pstk[-1].y0 = child.y0
                            pstk[-1].x1 = child.x1
                            pstk[-1].y1 = child.y1
                            pstk[-1].size = child.size
                        else:
                            sstk.append("")
                            ch_color = getattr(getattr(child, 'graphicstate', None), 'ncolor', None)
                            pstk.append(Paragraph(child.y0, child.x0, child.x0, child.x0, child.y0, child.y1, child.size, False, ch_color))
                if not cur_v:
                    if child.get_text() != " ":
                        pstk[-1]._size_sum += child.size
                        pstk[-1]._size_count += 1
                        if pstk[-1].color is None:
                            pstk[-1].color = getattr(getattr(child, 'graphicstate', None), 'ncolor', None)
                        if (
                            child.size > pstk[-1].size
                            or len(sstk[-1].strip()) == 1
                        ):
                            pstk[-1].y -= child.size - pstk[-1].size
                            pstk[-1].size = child.size
                    sstk[-1] += child.get_text()
                else:
                    if (
                        not vstk
                        and cls == xt_cls
                        and child.x0 > xt.x0
                    ):
                        vfix = child.y0 - xt.y0
                    vstk.append(child)
                pstk[-1].x0 = min(pstk[-1].x0, child.x0)
                pstk[-1].x1 = max(pstk[-1].x1, child.x1)
                pstk[-1].y0 = min(pstk[-1].y0, child.y0)
                pstk[-1].y1 = max(pstk[-1].y1, child.y1)
                xt = child
                xt_cls = cls
            elif isinstance(child, LTFigure):
                pass
            elif isinstance(child, LTLine):
                layout = self.layout[ltpage.pageid]
                h, w = layout.shape
                cx, cy = np.clip(int(child.x0), 0, w - 1), np.clip(int(child.y0), 0, h - 1)
                cls = layout[cy, cx]
                if vstk and cls == xt_cls:
                    vlstk.append(child)
                else:
                    lstk.append(child)
            else:
                pass
        if vstk:
            sstk[-1] += f"{{v{len(var)}}}"
            var.append(vstk)
            varl.append(vlstk)
            varf.append(vfix)

        # Attach any lines from lstk (e.g. fraction bars, overbars) that belong inside formula bounding boxes
        for id, v in enumerate(var):
            if v:
                f_x0 = min(ch.x0 for ch in v) - 6
                f_x1 = max(ch.x1 for ch in v) + 6
                f_y0 = min(ch.y0 for ch in v) - 15
                f_y1 = max(ch.y1 for ch in v) + 15
                matched_lines = []
                remaining_lines = []
                for l in lstk:
                    lx0 = min(l.pts[0][0], l.pts[1][0])
                    lx1 = max(l.pts[0][0], l.pts[1][0])
                    ly0 = min(l.pts[0][1], l.pts[1][1])
                    ly1 = max(l.pts[0][1], l.pts[1][1])
                    if lx0 >= f_x0 and lx1 <= f_x1 and ly0 >= f_y0 and ly1 <= f_y1 and l.linewidth < 5:
                        matched_lines.append(l)
                    else:
                        remaining_lines.append(l)
                if matched_lines:
                    varl[id].extend(matched_lines)
                    lstk = remaining_lines

        log.debug("\n==========[VSTACK]==========\n")
        var_min_x: list[float] = []
        var_ref_y: list[float] = []
        for id, v in enumerate(var):
            all_x0 = [vch.x0 for vch in v] + [l.pts[0][0] for l in varl[id]] + [l.pts[1][0] for l in varl[id]]
            all_x1 = [vch.x1 for vch in v] + [l.pts[0][0] for l in varl[id]] + [l.pts[1][0] for l in varl[id]]
            min_x = min(all_x0) if all_x0 else 0
            max_x = max(all_x1) if all_x1 else 0
            l = max_x - min_x
            vlen.append(l)
            var_min_x.append(min_x)

            if v:
                max_size = max(vch.size for vch in v)
                main_chars = [vch for vch in v if vch.size >= max_size * 0.85]
                leftmost_main = min(main_chars, key=lambda c: c.x0)
                ref_y = leftmost_main.y0
            elif varl[id]:
                ref_y = varl[id][0].pts[0][1]
            else:
                ref_y = 0
            var_ref_y.append(ref_y)
            log.debug(f'< {l:.1f} {min_x:.1f} {ref_y:.1f} len={len(v)} lines={len(varl[id])} > v{id} = {"".join([ch.get_text() for ch in v])}')

        # 1. Unpack false single-letter formulas (e.g. initial drop caps 'S', 'w', etc. trapped in formula bounding boxes)
        for vid, v in enumerate(var):
            if len(v) == 1 and not varl[vid]:
                vch = v[0]
                ch_txt = vch.get_text()
                if ch_txt.isalpha() and not vflag(vch.fontname, ch_txt):
                    # Replace {v{vid}} with character text in sstk
                    tag = f"{{v{vid}}}"
                    for s_idx in range(len(sstk)):
                        if tag in sstk[s_idx]:
                            sstk[s_idx] = sstk[s_idx].replace(tag, ch_txt)
                    var[vid] = []

        # 2. Forward pass: merge isolated single-letter dropcaps/prefixes into next segment on same line
        fwd_sstk: list[str] = []
        fwd_pstk: list[Paragraph] = []
        i = 0
        while i < len(sstk):
            s = sstk[i].strip()
            p = pstk[i]
            if not s:
                i += 1
                continue

            if i + 1 < len(sstk) and len(s) <= 2 and s.isalpha():
                next_s = sstk[i + 1].strip()
                next_p = pstk[i + 1]
                is_same_line = (abs(next_p.y - p.y) < max(p.size, next_p.size) * 0.85) or (abs(next_p.y1 - p.y1) < max(p.size, next_p.size) * 0.85)
                is_adjacent_x = (next_p.x0 - p.x1) <= p.size * 1.5
                if is_same_line and is_adjacent_x:
                    sstk[i + 1] = s + next_s
                    next_p.x0 = min(p.x0, next_p.x0)
                    next_p.x = next_p.x0
                    next_p.y = max(p.y, next_p.y)
                    next_p.y1 = max(p.y1, next_p.y1)
                    next_p.y0 = min(p.y0, next_p.y0)
                    i += 1
                    continue

            fwd_sstk.append(sstk[i])
            fwd_pstk.append(p)
            i += 1

        sstk = fwd_sstk
        pstk = fwd_pstk

        # 3. Backward pass: merge mid-sentence broken paragraphs
        merged_sstk: list[str] = []
        merged_pstk: list[Paragraph] = []
        for s, p in zip(sstk, pstk):
            s_str = s.strip()
            if not s_str:
                continue
            if not merged_sstk:
                merged_sstk.append(s)
                merged_pstk.append(p)
                continue

            prev_s = merged_sstk[-1].strip()
            prev_p = merged_pstk[-1]

            prev_ends_punct = bool(re.search(r"[\.\!\?\:\”\"]\s*$", prev_s))
            curr_starts_lower = bool(s_str[0].islower()) if s_str else False
            vert_gap = prev_p.y0 - p.y1
            is_vertically_close = -5.0 <= vert_gap <= prev_p.size * 1.6
            is_same_column = abs(prev_p.x0 - p.x0) < 50.0

            if not prev_ends_punct and is_vertically_close and is_same_column and (curr_starts_lower or vert_gap <= prev_p.size * 1.2):
                merged_sstk[-1] = prev_s + " " + s_str
                prev_p.y0 = min(prev_p.y0, p.y0)
                prev_p.x0 = min(prev_p.x0, p.x0)
                prev_p.x1 = max(prev_p.x1, p.x1)
                prev_p.brk = True
                continue

            merged_sstk.append(s)
            merged_pstk.append(p)

        sstk = merged_sstk
        pstk = merged_pstk

        log.debug("\n==========[SSTACK]==========\n")

        # Compute weighted-average font size for each paragraph
        for p in pstk:
            if p._size_count > 2:
                avg = p._size_sum / p._size_count
                # Use average if it's meaningfully different from max
                # (prevents a single large char from inflating the whole paragraph)
                if avg < p.size * 0.85:
                    p.size = round(avg * 1.05, 2)  # slight bias toward larger for readability

        # Pre-process segments with de-hyphenation, TOC title/page separation, and glossary protection
        TOC_LINE_RE = re.compile(
            r"^(.*?)(?:(?:\.\s*){2,}|\.{2,}|\s{2,})\s*(\d{1,4}|[ivxlcdm]{1,6})\s*$",
            re.IGNORECASE,
        )
        TOC_FALLBACK_RE = re.compile(
            r"^(.*?)\s+(\d{1,4}|[ivxlcdm]{1,6})\s*$",
            re.IGNORECASE,
        )
        toc_info: list[tuple[str, str] | None] = []
        prepared: list[tuple[str, dict[str, str], dict[str, str]]] = []
        for s in sstk:
            if not s.strip() or re.match(r"^\{v\d+\}$", s):
                prepared.append((s, {}, {}))
                toc_info.append(None)
            else:
                s_clean = dehyphenate_text(s)
                m = TOC_LINE_RE.match(s_clean)
                if not m and re.search(r"\s+(\d{1,4}|[ivxlcdm]{1,6})\s*$", s_clean, re.IGNORECASE):
                    if (
                        re.match(r"^\s*(\d+|[A-Z])(\.\d+)*\s+", s_clean)
                        or re.match(r"^\s*(Summary|Tóm tắt|Index|Appendix|Phụ lục|Preface|Lời nói đầu|Acknowledgments|Lời cảm ơn|Contents|Mục lục|PART|CHAPTER|CHƯƠNG)\b", s_clean, re.IGNORECASE)
                        or len(s_clean) < 140
                    ):
                        m = TOC_FALLBACK_RE.match(s_clean)
                if m and len(m.group(1).strip()) > 1:
                    title_part = m.group(1).strip()
                    num_part = m.group(2).strip()
                    toc_info.append((title_part, num_part))
                    s_links_prot, l_map = protect_links(title_part)
                    s_protected, g_map = protect_glossary(s_links_prot, self.glossary_terms)
                    prepared.append((s_protected, g_map, l_map))
                else:
                    toc_info.append(None)
                    s_links_prot, l_map = protect_links(s_clean)
                    s_protected, g_map = protect_glossary(s_links_prot, self.glossary_terms)
                    prepared.append((s_protected, g_map, l_map))

        prepared_texts = [p[0] for p in prepared]

        # Chunk prepared texts: small chunks (3) = more parallel batches
        # With 20 segments per page and chunk_size=3, we get ~7 parallel HTTP batches
        # instead of 1 single batch with chunk_size=50
        chunk_size = 3
        chunks = [
            prepared_texts[i : i + chunk_size]
            for i in range(0, len(prepared_texts), chunk_size)
        ]

        def batch_worker(chunk: list[str]) -> list[str]:
            try:
                return self.translator.translate_batch(chunk)
            except Exception as e:
                log.debug("Batch worker error (%s), fallback to item-by-item", e)
                fallback_res: list[str] = []
                
                def _translate_item(s: str) -> str:
                    try:
                        return self.translator.translate(s)
                    except Exception:
                        self.translation_failures.append(s)
                        return s
                        
                with concurrent.futures.ThreadPoolExecutor(max_workers=min(16, len(chunk))) as p:
                    fallback_res = list(p.map(_translate_item, chunk))
                    
                return fallback_res

        # Use class-level persistent executor to avoid create/destroy overhead per page
        if not hasattr(TranslateConverter, '_shared_executor') or TranslateConverter._shared_executor is None:
            TranslateConverter._shared_executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=max(1, self.thread)
            )
        executor = TranslateConverter._shared_executor
        batch_futures = [executor.submit(batch_worker, c) for c in chunks]
        batch_results = [f.result() for f in batch_futures]

        flat_translated = [item for sublist in batch_results for item in sublist]

        # Post-process: restore glossary, clean typography, and restore links
        news: list[str] = []
        is_vi = self.translator.lang_out.lower() == "vi"
        for idx, (translated_text, (_, g_map, l_map)) in enumerate(zip(flat_translated, prepared)):
            if g_map:
                translated_text = restore_glossary(translated_text, g_map)
            if is_vi and translated_text.strip() and not re.match(r"^\{v\d+\}$", translated_text):
                translated_text = cleanup_vietnamese_typography(translated_text)
            if l_map:
                translated_text = restore_links(translated_text, l_map)
            news.append(translated_text)

        ############################################################
        def raw_string(fcur: str, cstk: str):
            if fcur == self.noto_name:
                return "".join(["%04x" % self.noto.has_glyph(ord(c)) for c in cstk])
            font_obj = getattr(self, "fontmap", {}).get(fcur)
            if isinstance(font_obj, PDFCIDFont):
                return "".join(["%04x" % ord(c) for c in cstk])
            else:
                return "".join(["%02x" % ord(c) for c in cstk])

        default_line_height = line_height_for_language(self.translator.lang_out)
        _x, _y = 0, 0
        ops_list = []

        # Draw white rectangles to cover original text in background image (scanned PDFs only)
        white_rects = ""
        if ltpage.pageid in self.scanned_pages:
            pad = 3  # padding to fully cover original text with descenders/ascenders
            for id, new in enumerate(news):
                if new != sstk[id]:  # Only cover areas that were translated
                    rx0 = pstk[id].x0 - pad
                    ry0 = pstk[id].y0 - pad
                    rw = pstk[id].x1 - pstk[id].x0 + pad * 2
                    rh = pstk[id].y1 - pstk[id].y0 + pad * 2
                    white_rects += f"q 1 1 1 rg {rx0:f} {ry0:f} {rw:f} {rh:f} re f Q "
            # Also cover formula areas
            for v in var:
                if v:
                    fx0 = min(ch.x0 for ch in v) - pad
                    fy0 = min(ch.y0 for ch in v) - pad
                    fx1 = max(ch.x1 for ch in v) + pad
                    fy1 = max(ch.y1 for ch in v) + pad
                    white_rects += f"q 1 1 1 rg {fx0:f} {fy0:f} {fx1-fx0:f} {fy1-fy0:f} re f Q "

        def sanitize_color(color):
            if color is None:
                return None
            # If color is pure white or near-white (> 0.95), sanitize to black (None)
            # because in PDFMiner, graphicstate.ncolor often erroneously retains the preceding white background fill
            if isinstance(color, (int, float)) and float(color) >= 0.95:
                return None
            elif isinstance(color, (tuple, list)):
                if len(color) == 1 and float(color[0]) >= 0.95:
                    return None
                elif len(color) == 3 and all(float(c) >= 0.95 for c in color):
                    return None
                elif len(color) == 4 and all(float(c) <= 0.05 for c in color):  # CMYK white is (0,0,0,0)
                    return None
            return color

        def color_to_op(color, is_stroke=False) -> str:
            color = sanitize_color(color)
            cmd_gray = "G" if is_stroke else "g"
            cmd_rgb = "RG" if is_stroke else "rg"
            cmd_cmyk = "K" if is_stroke else "k"
            if color is None:
                return f"0 {cmd_gray} "
            if isinstance(color, (int, float)):
                return f"{float(color):.4f} {cmd_gray} "
            elif isinstance(color, (tuple, list)):
                if len(color) == 1:
                    return f"{float(color[0]):.4f} {cmd_gray} "
                elif len(color) == 3:
                    r, g, b = color
                    return f"{float(r):.4f} {float(g):.4f} {float(b):.4f} {cmd_rgb} "
                elif len(color) == 4:
                    c, m, y, k = color
                    return f"{float(c):.4f} {float(m):.4f} {float(y):.4f} {float(k):.4f} {cmd_cmyk} "
            return f"0 {cmd_gray} "

        def gen_op_txt(font, size, x, y, rtxt, color=None):
            c_op = color_to_op(color, is_stroke=False)
            return f"{c_op}/{font} {size:f} Tf 1 0 0 1 {x:f} {y:f} Tm [<{rtxt}>] TJ "

        def gen_op_line(x, y, xlen, ylen, linewidth, color=None):
            c_op = color_to_op(color, is_stroke=True)
            return f"ET q {c_op}1 0 0 1 {x:f} {y:f} cm [] 0 d 0 J {linewidth:f} w 0 0 m {xlen:f} {ylen:f} l S Q BT "

        for id, new in enumerate(news):
            x: float = pstk[id].x
            y: float = pstk[id].y
            x0: float = pstk[id].x0
            x1: float = pstk[id].x1
            height: float = pstk[id].y1 - pstk[id].y0
            size: float = pstk[id].size
            brk: bool = pstk[id].brk

            col_x1 = x1
            if toc_info[id] is not None:
                pw = ltpage.width if hasattr(ltpage, 'width') and ltpage.width > 0 else 612.0
                if x0 < pw * 0.45:
                    col_x1 = max(x1, pw * 0.47)
                else:
                    col_x1 = max(x1, pw * 0.92)
                x1 = col_x1

            # Auto-scale font size if translation is longer than original.
            # Applied to ALL paragraphs (both single-line and multi-line)
            # to prevent text overflow and overlapping.
            if new != sstk[id]:
                line_width = x1 - x0
                if line_width > 0:
                    if brk:
                        # Multi-line: measure against total available area
                        denom = max(1.0, pstk[id].size * default_line_height)
                        orig_lines = max(1, round(height / denom))
                        total_avail = line_width * orig_lines
                    else:
                        # Single-line: measure against line width only
                        total_avail = line_width
                    # Measure actual width of translated text (excluding formula tags)
                    total_new_width = 0.0
                    tmp_ptr = 0
                    plain_new = new
                    while tmp_ptr < len(plain_new):
                        vm = re.match(r"\{\s*v([\d\s]+)\}", plain_new[tmp_ptr:], re.IGNORECASE)
                        if vm:
                            try:
                                vid_tmp = int(vm.group(1).replace(" ", ""))
                                total_new_width += vlen[vid_tmp]
                            except Exception:
                                pass
                            tmp_ptr += len(vm.group(0))
                        else:
                            ch = plain_new[tmp_ptr]
                            try:
                                if self.fontmap.get("tiro") and self.fontmap["tiro"].to_unichr(ord(ch)) == ch:
                                    total_new_width += self.fontmap["tiro"].char_width(ord(ch)) * pstk[id].size
                                else:
                                    total_new_width += self.noto.char_lengths(ch, pstk[id].size)[0]
                            except Exception:
                                total_new_width += pstk[id].size * 0.5
                            tmp_ptr += 1
                    if total_avail > 0 and total_new_width > total_avail * 1.02:
                        ratio = total_avail / max(0.1, total_new_width)
                        size = pstk[id].size * min(1.0, max(ratio, 0.78))

            # Pre-compute word-boundary line breaks to avoid mid-word splits
            if brk:
                def _measure_char(c):
                    try:
                        if self.fontmap.get("tiro") and self.fontmap["tiro"].to_unichr(ord(c)) == c:
                            return self.fontmap["tiro"].char_width(ord(c)) * size
                    except Exception:
                        pass
                    try:
                        return self.noto.char_lengths(c, size)[0]
                    except Exception:
                        return size * 0.5

                break_positions = set()
                cur_x = x
                last_space_ptr = -1
                last_space_x_after = cur_x
                p2 = 0
                while p2 < len(new):
                    vr2 = re.match(r"\{\s*v([\d\s]+)\}", new[p2:], re.IGNORECASE)
                    if vr2:
                        try:
                            vid_t = int(vr2.group(1).replace(" ", ""))
                            cw = vlen[vid_t]
                        except Exception:
                            cw = 0
                        if cur_x + cw > x1 + 0.1 * size and cur_x > x0 + 0.1 * size:
                            if last_space_ptr >= 0:
                                break_positions.add(last_space_ptr + 1)
                                cur_x = x0 + (cur_x - last_space_x_after)
                                last_space_ptr = -1
                                last_space_x_after = x0
                        cur_x += cw
                        p2 += len(vr2.group(0))
                    else:
                        ch2 = new[p2]
                        cw = _measure_char(ch2)
                        if ch2 == ' ':
                            last_space_ptr = p2
                            last_space_x_after = cur_x + cw
                        if cur_x + cw > x1 + 0.1 * size and cur_x > x0 + 0.1 * size:
                            if last_space_ptr >= 0:
                                break_positions.add(last_space_ptr + 1)
                                cur_x = x0 + (cur_x - last_space_x_after)
                                last_space_ptr = -1
                                last_space_x_after = x0
                        cur_x += cw
                        p2 += 1
                # Replace spaces at break positions with newlines (process in reverse)
                for bp in sorted(break_positions, reverse=True):
                    new = new[:bp - 1] + '\n' + new[bp:]

            cstk: str = ""
            fcur: str = None
            lidx = 0
            tx = x
            fcur_ = fcur
            ptr = 0
            log.debug(f"< {y} {x} {x0} {x1} {size} {brk} > {sstk[id]} | {new}")

            ops_vals: list[dict] = []
            is_pure_layout = bool(re.match(r"^(\{\s*v[\d\s]+\}|\s)+$", new)) and (new == sstk[id])
            
            while ptr < len(new):
                vy_regex = re.match(
                    r"\{\s*v([\d\s]+)\}", new[ptr:], re.IGNORECASE
                )
                mod = 0
                if vy_regex:
                    ptr += len(vy_regex.group(0))
                    try:
                        vid = int(vy_regex.group(1).replace(" ", ""))
                        adv = vlen[vid]
                    except Exception:
                        continue
                    if var[vid][-1].get_text() and unicodedata.category(var[vid][-1].get_text()[0]) in ["Lm", "Mn", "Sk"]:
                        mod = var[vid][-1].width
                else:
                    ch = new[ptr]
                    if ch == '\n':  # Forced line break from word-wrap pre-computation
                        if cstk:
                            ops_vals.append({
                                "type": OpType.TEXT,
                                "font": fcur,
                                "size": size,
                                "x": tx,
                                "dy": 0,
                                "rtxt": raw_string(fcur, cstk),
                                "lidx": lidx
                            })
                            cstk = ""
                        x = x0
                        lidx += 1
                        ptr += 1
                        continue
                    fcur_ = None
                    try:
                        if fcur_ is None and self.fontmap["tiro"].to_unichr(ord(ch)) == ch:
                            fcur_ = "tiro"
                    except Exception:
                        pass
                    if fcur_ is None:
                        fcur_ = self.noto_name
                    if fcur_ == self.noto_name: # FIXME: change to CONST
                        adv = self.noto.char_lengths(ch, size)[0]
                    else:
                        adv = self.fontmap[fcur_].char_width(ord(ch)) * size
                    ptr += 1
                if (
                    fcur_ != fcur
                    or vy_regex
                    or x + adv > x1 + 0.1 * size
                ):
                    if cstk:
                        # Word-wrap: if hitting right boundary, break at last space
                        if brk and x + adv > x1 + 0.1 * size and ' ' in cstk:
                            last_space = cstk.rfind(' ')
                            before = cstk[:last_space]
                            after = cstk[last_space + 1:]
                            if before:
                                ops_vals.append({
                                    "type": OpType.TEXT,
                                    "font": fcur,
                                    "size": size,
                                    "x": tx,
                                    "dy": 0,
                                    "rtxt": raw_string(fcur, before),
                                    "lidx": lidx
                                })
                            # Move remainder to new line
                            lidx += 1
                            x = x0
                            tx = x
                            # Recalculate x for the remaining text
                            for rc in after:
                                if fcur == self.noto_name:
                                    x += self.noto.char_lengths(rc, size)[0]
                                else:
                                    x += self.fontmap[fcur].char_width(ord(rc)) * size
                            cstk = after
                        else:
                            ops_vals.append({
                                "type": OpType.TEXT,
                                "font": fcur,
                                "size": size,
                                "x": tx,
                                "dy": 0,
                                "rtxt": raw_string(fcur, cstk),
                                "lidx": lidx
                            })
                            cstk = ""
                if brk and x + adv > x1 + 0.1 * size:
                    x = x0
                    lidx += 1
                if vy_regex:
                    ref_min_x = var_min_x[vid]
                    ref_base_y = var_ref_y[vid]
                    for vch in var[vid]:
                        vc = chr(vch.cid)
                        font_res_id = getattr(self, "fontid", {}).get(vch.font, self.noto_name)
                        vch_color = getattr(getattr(vch, 'graphicstate', None), 'ncolor', None)
                        
                        vch_x = vch.x0 if is_pure_layout else (x + vch.x0 - ref_min_x)
                        vch_dy = (vch.y0 - y) if is_pure_layout else (vch.y0 - ref_base_y)
                        
                        ops_vals.append({
                            "type": OpType.TEXT,
                            "font": font_res_id,
                            "size": vch.size,
                            "x": vch_x,
                            "dy": vch_dy,
                            "rtxt": raw_string(font_res_id, vc),
                            "lidx": 0 if is_pure_layout else lidx,
                            "color": vch_color,
                        })
                        if log.isEnabledFor(logging.DEBUG):
                            lstk.append(LTLine(0.1, (_x, _y), (x + vch.x0 - ref_min_x, y + vch.y0 - ref_base_y)))
                            _x, _y = x + vch.x0 - ref_min_x, y + vch.y0 - ref_base_y
                    for l in varl[vid]:
                        if l.linewidth < 5:
                            l_color = getattr(l, 'stroking_color', getattr(l, 'non_stroking_color', None))
                            
                            l_x = l.pts[0][0] if is_pure_layout else (l.pts[0][0] + x - ref_min_x)
                            l_dy = (l.pts[0][1] - y) if is_pure_layout else (l.pts[0][1] - ref_base_y)
                            
                            ops_vals.append({
                                "type": OpType.LINE,
                                "x": l_x,
                                "dy": l_dy,
                                "linewidth": l.linewidth,
                                "xlen": l.pts[1][0] - l.pts[0][0],
                                "ylen": l.pts[1][1] - l.pts[0][1],
                                "lidx": 0 if is_pure_layout else lidx,
                                "color": l_color,
                            })
                else:
                    if not cstk:
                        tx = x
                        if x == x0 and ch == " ":
                            adv = 0
                        else:
                            cstk += ch
                    else:
                        cstk += ch
                adv -= mod
                fcur = fcur_
                x += adv
                if log.isEnabledFor(logging.DEBUG):
                    lstk.append(LTLine(0.1, (_x, _y), (x, y)))
                    _x, _y = x, y
            if cstk:
                ops_vals.append({
                    "type": OpType.TEXT,
                    "font": fcur,
                    "size": size,
                    "x": tx,
                    "dy": 0,
                    "rtxt": raw_string(fcur, cstk),
                    "lidx": lidx
                })

            # If this is a TOC/List-of-Figures entry, place dot leaders and page number directly!
            if toc_info[id] is not None:
                _, page_num = toc_info[id]
                num_rtxt = raw_string(self.noto_name, page_num)
                num_w = sum(self.noto.char_lengths(c, size)[0] for c in page_num)
                page_x = col_x1 - num_w
                orig_had_dots = bool(re.search(r"(\.\s*){2,}|\.{2,}", sstk[id]))
                if orig_had_dots and page_x > x + 15.0:
                    dot_w = sum(self.noto.char_lengths(c, size)[0] for c in ". ")
                    if dot_w > 0:
                        num_dots = int((page_x - x - 8.0) / dot_w)
                        if num_dots > 0:
                            dots_str = ". " * num_dots
                            ops_vals.append({
                                "type": OpType.TEXT,
                                "font": self.noto_name,
                                "size": size,
                                "x": x + 4.0,
                                "dy": 0,
                                "rtxt": raw_string(self.noto_name, dots_str),
                                "lidx": lidx,
                                "color": getattr(pstk[id], 'color', None),
                            })
                ops_vals.append({
                    "type": OpType.TEXT,
                    "font": self.noto_name,
                    "size": size,
                    "x": page_x,
                    "dy": 0,
                    "rtxt": num_rtxt,
                    "lidx": lidx,
                    "color": getattr(pstk[id], 'color', None),
                })

            # An inline formula keeps the vertical offsets it had in the source,
            # so a fraction reaches far below its baseline while the prose around
            # it does not. Uniform leading therefore let the next line print
            # straight through the denominator. Measure what each line actually
            # occupies above and below its own baseline, and open up only the
            # gaps that need it.
            ink: dict[int, tuple[float, float]] = {}
            for vals in ops_vals:
                s_ = vals["size"] if vals["type"] == OpType.TEXT else 0.0
                lo = vals["dy"] + min(0.0, vals.get("ylen", 0.0)) - 0.22 * s_
                hi = vals["dy"] + max(0.0, vals.get("ylen", 0.0)) + 0.78 * s_
                plo, phi = ink.get(vals["lidx"], (lo, hi))
                ink[vals["lidx"]] = (min(plo, lo), max(phi, hi))

            line_height = default_line_height

            # Vietnamese accent padding: diacritical marks above (ắ, ấ, ể)
            # and below (ợ, ụ) need extra vertical room.
            accent_pad = 0.0
            if is_vi and lidx > 0:
                accent_pad = size * 0.08  # ~8% extra per line for accents

            # Fit the prose to the box on its own. Charging the formula's extra
            # room to this loop drops the leading for every line in the
            # paragraph, until they collide with each other instead.
            effective_line = line_height + (accent_pad / size if size > 0 else 0)
            while (lidx + 1) * size * effective_line > height and line_height >= 0.75:
                line_height -= 0.04
                effective_line = line_height + (accent_pad / size if size > 0 else 0)

            # If still overflowing after reducing line_height, shrink font slightly (never below 78%)
            if lidx > 0 and (lidx + 1) * size * effective_line > height:
                shrink = height / ((lidx + 1) * size * effective_line)
                shrink = max(shrink, 0.78)  # Keep font size legible and consistent (at least 78%)
                size *= shrink
                accent_pad *= shrink
                for vals in ops_vals:
                    if vals["type"] == OpType.TEXT:
                        vals["size"] *= shrink

            # ponytail: the paragraph's own box is the whole budget, so a
            # formula in an already tight paragraph stays somewhat cramped.
            # Measuring the gap down to the next paragraph would buy the rest.
            offsets = line_offsets(ink, lidx, size, effective_line,
                                   budget=height - (lidx + 1) * size * effective_line)

            for vals in ops_vals:
                vals_color = vals.get("color", pstk[id].color)
                if vals["type"] == OpType.TEXT:
                    ops_list.append(gen_op_txt(vals["font"], vals["size"], vals["x"], vals["dy"] + y - offsets[vals["lidx"]], vals["rtxt"], vals_color))
                elif vals["type"] == OpType.LINE:
                    ops_list.append(gen_op_line(vals["x"], vals["dy"] + y - offsets[vals["lidx"]], vals["xlen"], vals["ylen"], vals["linewidth"], vals_color))

        for l in lstk:
            if l.linewidth < 5:
                l_color = getattr(l, 'stroking_color', getattr(l, 'non_stroking_color', None))
                ops_list.append(gen_op_line(l.pts[0][0], l.pts[0][1], l.pts[1][0] - l.pts[0][0], l.pts[1][1] - l.pts[0][1], l.linewidth, l_color))

        ops = f"{white_rects}BT {''.join(ops_list)}ET "
        return ops


def line_offsets(
    ink: dict[int, tuple[float, float]],
    lines: int,
    size: float,
    line_height: float,
    budget: float | None = None,
) -> list[float]:
    """Distance from a paragraph's first baseline down to each later baseline.

    `ink[i]` is how far line i's glyphs reach below and above its own baseline.
    Prose lines get the usual leading; a line holding a tall inline formula gets
    the extra room its glyphs and its neighbour's need, so a fraction's
    denominator no longer lands on the line underneath. `budget` caps that extra
    at the space the paragraph has left, because spilling onto the paragraph
    below looks worse than a formula that is still a little tight.
    """
    base = size * line_height
    want = [
        max(0.0, (ink.get(i + 1, (0.0, 0.0))[1] - ink.get(i, (0.0, 0.0))[0]) - base)
        for i in range(lines)
    ]
    total = sum(want)
    if budget is not None and total > 0 and total > budget:
        # Not enough slack for every tall formula. Share out what there is
        # rather than growing the paragraph down over the text below it.
        scale = max(0.0, budget) / total
        want = [w * scale for w in want]
    offsets = [0.0]
    for extra in want:
        offsets.append(offsets[-1] + base + extra)
    return offsets


class OpType(Enum):
    TEXT = "text"
    LINE = "line"
