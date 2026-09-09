from __future__ import annotations

import re
import unittest
from html.parser import HTMLParser
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TUTORIAL_ROOT = PROJECT_ROOT / "tutorial"


class TutorialParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.textareas: list[tuple[dict[str, str | None], str]] = []
        self._active_textarea: dict[str, str | None] | None = None
        self._textarea_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        self.tags.append((tag, attributes))
        if tag == "textarea":
            self._active_textarea = attributes
            self._textarea_text = []

    def handle_data(self, data: str) -> None:
        if self._active_textarea is not None:
            self._textarea_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "textarea" and self._active_textarea is not None:
            self.textareas.append(
                (self._active_textarea, "".join(self._textarea_text))
            )
            self._active_textarea = None
            self._textarea_text = []


class TutorialStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (TUTORIAL_ROOT / "index.html").read_text(encoding="utf-8")
        cls.css = (TUTORIAL_ROOT / "styles.css").read_text(encoding="utf-8")
        cls.javascript = (TUTORIAL_ROOT / "app.js").read_text(encoding="utf-8")
        cls.guide = (TUTORIAL_ROOT / "read-code.html").read_text(encoding="utf-8")
        cls.parser = TutorialParser()
        cls.parser.feed(cls.html)

    def test_assets_are_local_and_dependency_free(self) -> None:
        for name in ("index.html", "read-code.html", "styles.css", "app.js"):
            self.assertTrue((TUTORIAL_ROOT / name).is_file())

        combined = "\n".join((self.html, self.guide, self.css, self.javascript))
        self.assertIsNone(re.search(r"https?://", combined))
        self.assertIsNone(
            re.search(r'(?:src|href)=["\']//', self.html, flags=re.IGNORECASE)
        )
        self.assertNotIn("@import", self.css)
        self.assertNotIn("url(", self.css)
        self.assertNotIn("fetch(", self.javascript)
        self.assertNotIn("XMLHttpRequest", self.javascript)
        self.assertIn('href="./styles.css"', self.html)
        self.assertIn('src="./app.js"', self.html)

    def test_fixed_trace_teaches_required_concepts(self) -> None:
        required_copy = (
            "Inclusive prefix scan",
            "processing element",
            "分块",
            "local prefix",
            "carry",
            "Invariant",
            "瓶颈",
            "west → east",
        )
        for phrase in required_copy:
            self.assertIn(phrase, self.html + self.javascript)

        self.assertIn("[3, -1, 4, 2, 5, -2, 1, 3]", self.javascript)
        self.assertIn("[3, 2, 6, 8]", self.javascript)
        self.assertIn("[5, 3, 4, 7]", self.javascript)
        self.assertIn("[13, 11, 12, 15]", self.javascript)
        self.assertEqual(self.html.count('role="tab"'), 6)

    def test_boundary_is_visible_and_truthful(self) -> None:
        self.assertIn("不是 WSE simulator 输出", self.html)
        self.assertIn("固定数据的教学演示，不调用 SDK", self.html)
        self.assertIn(
            "This is a fixed teaching trace, not a second prefix-scan implementation.",
            self.javascript,
        )
        self.assertNotIn("hardware performance", self.html.lower())

    def test_answers_are_blank_and_not_auto_scored(self) -> None:
        self.assertEqual(len(self.parser.textareas), 4)
        expected_ids = {
            "answer-partition",
            "answer-carry",
            "answer-invariant",
            "answer-bottleneck",
        }
        self.assertEqual(
            {attributes.get("id") for attributes, _ in self.parser.textareas},
            expected_ids,
        )
        for _attributes, initial_text in self.parser.textareas:
            self.assertEqual(initial_text.strip(), "")

        self.assertIn("不会自动判断你是否理解", self.html)
        self.assertIn("网页未自动验证理解", self.javascript)
        self.assertNotIn("correctAnswer", self.javascript)
        self.assertNotIn("scoreAnswer", self.javascript)

    def test_accessibility_and_keyboard_contract(self) -> None:
        self.assertIn("viewport-fit=cover", self.html)
        self.assertIn('class="skip-link"', self.html)
        self.assertIn('aria-live="polite"', self.html)
        self.assertIn('aria-selected="true"', self.html)
        self.assertIn(":focus-visible", self.css)
        self.assertIn("prefers-reduced-motion: reduce", self.css)
        self.assertIn("safe-area-inset", self.css)
        for key in ("ArrowLeft", "ArrowRight", "Home", "End"):
            self.assertIn(key, self.javascript)

    def test_copy_requires_four_nonempty_drafts(self) -> None:
        self.assertIn('id="copy-answers"', self.html)
        self.assertRegex(self.html, r'id="copy-answers"[^>]*disabled')
        self.assertIn(
            "count !== elements.answers.length",
            self.javascript,
        )
        self.assertIn("writeToClipboard(buildSelfExplanation())", self.javascript)

    def test_code_reading_and_practice_flow(self) -> None:
        self.assertIn("不会自动保存或发送", self.html)
        for phrase in (
            "SdkRuntime", "H2D", "D2H", "CSL kernel", "C++ oracle",
            "make simulator", "valid_count", "padding",
            "unblock_cmd_stream()", "固定随机 seed", "int32",
            "复写", "不是一条魔法全局 barrier",
        ):
            self.assertIn(phrase, self.html + self.guide)

    def test_local_navigation_targets_exist(self) -> None:
        for filename in ("index.html", "read-code.html"):
            source = (TUTORIAL_ROOT / filename).read_text(encoding="utf-8")
            parser = TutorialParser()
            parser.feed(source)
            ids = [attrs["id"] for _, attrs in parser.tags if "id" in attrs]
            self.assertEqual(len(ids), len(set(ids)), filename)
            for tag, attrs in parser.tags:
                if tag != "a" or not attrs.get("href"):
                    continue
                path, _, fragment = attrs["href"].partition("#")
                destination = TUTORIAL_ROOT / path if path else TUTORIAL_ROOT / filename
                self.assertTrue(destination.is_file(), attrs["href"])
                if fragment:
                    target = TutorialParser()
                    target.feed(destination.read_text(encoding="utf-8"))
                    target_ids = {item.get("id") for _, item in target.tags}
                    self.assertIn(fragment, target_ids, attrs["href"])

    def test_guide_is_usable_without_javascript(self) -> None:
        parser = TutorialParser()
        parser.feed(self.guide)
        self.assertFalse(any(tag == "script" for tag, _ in parser.tags))
        self.assertIn('id="rewrite"', self.guide)
        self.assertIn('id="run-sdk"', self.guide)
        self.assertIn("实际运行结果", self.guide)
        self.assertIn("没有真实 WSE 硬件参与", self.guide)


if __name__ == "__main__":
    unittest.main()
