from __future__ import annotations

import math
import unittest
from pathlib import Path
from unittest import mock

from pdfminer.pdfdevice import PDFDevice
from pdfminer.pdfinterp import PDFResourceManager

import pdf2zh
from pdf2zh.pdfinterp import PDFPageInterpreterEx
from pdf2zh.rules import (
    BULLET_CHARACTERS,
    classify_preserved_page,
    is_formula_font,
    line_height_for_language,
)


class CoreImprovementsTests(unittest.TestCase):
    def test_dynamic_submodule_import(self):
        """Verify that __getattr__ allows accessing all submodules."""
        for name in (
            "doclayout",
            "converter",
            "rules",
            "translator",
            "cache",
            "pdfinterp",
            "high_level",
            "text_utils",
        ):
            with self.subTest(submodule=name):
                submod = getattr(pdf2zh, name)
                self.assertIsNotNone(submod)

    def test_invalid_attribute_raises_attribute_error(self):
        with self.assertRaises(AttributeError):
            getattr(pdf2zh, "non_existent_module_xyz")

    def test_vietnamese_rules_contract(self):
        self.assertEqual(line_height_for_language("vi"), 1.35)
        self.assertTrue(is_formula_font("CMMI10"))
        self.assertTrue(is_formula_font("Consolas"))
        self.assertFalse(is_formula_font("Arial"))

    def test_horizontal_rule_detection_tolerance(self):
        """Verify math.isclose handles floating point jitter."""
        y1 = 100.00000000000001
        y2 = 100.00000000000050
        self.assertTrue(math.isclose(y1, y2, abs_tol=1e-3))

    def test_pdf_page_interpreter_ex_handles_ncs_and_scs(self):
        """Verify that PDFPageInterpreterEx does not raise AttributeError on ncs/scs."""
        rsrcmgr = PDFResourceManager()
        device = PDFDevice(rsrcmgr)
        interp = PDFPageInterpreterEx(rsrcmgr, device, None)
        interp.init_state((1, 0, 0, 1, 0, 0))

        # Check property accesses - initialized to DeviceGray
        self.assertIsNotNone(interp.ncs)
        self.assertEqual(interp.ncs.ncomponents, 1)
        self.assertIsNotNone(interp.scs)
        self.assertEqual(interp.scs.ncomponents, 1)

        # Push mock arguments and call do_scn / do_SCN
        interp.push(0.5)
        interp.do_scn()
        self.assertEqual(interp.graphicstate.ncolor, 0.5)

        interp.push(0.8)
        interp.do_SCN()
        self.assertEqual(interp.graphicstate.scolor, 0.8)


if __name__ == "__main__":
    unittest.main()
