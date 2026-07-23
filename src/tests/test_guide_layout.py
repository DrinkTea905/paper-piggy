# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import check_guides as G  # noqa: E402


class GuideLayoutTests(unittest.TestCase):
    def test_current_guides_do_not_pin_notes_above_the_first_chapter(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertEqual(G._top_pinned_guide_note_hits(html), [])

    def test_top_pinned_note_is_rejected(self):
        html = """
        <div id="home-guide">
          <div class="ag-guide-body">
            <p class="ag-note">本次新增功能</p>
            <section class="ag-ch"></section>
          </div>
        </div>
        <div id="ag-guide">
          <div class="ag-guide-body">
            <section class="ag-ch"></section>
          </div>
        </div>
        """
        self.assertEqual(
            G._top_pinned_guide_note_hits(html),
            ["#home-guide 标题下、第一章前存在 ag-note"],
        )

    def test_note_inside_a_chapter_is_allowed(self):
        html = """
        <div id="home-guide">
          <div class="ag-guide-body">
            <section class="ag-ch"><p class="ag-note">对应步骤说明</p></section>
          </div>
        </div>
        <div id="ag-guide">
          <div class="ag-guide-body">
            <section class="ag-ch"><p class="ag-note">对应章节说明</p></section>
          </div>
        </div>
        """
        self.assertEqual(G._top_pinned_guide_note_hits(html), [])


if __name__ == "__main__":
    unittest.main()
