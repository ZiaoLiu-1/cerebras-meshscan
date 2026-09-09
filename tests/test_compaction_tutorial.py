from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unittest
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TUTORIAL_ROOT = PROJECT_ROOT / "tutorial"


@dataclass
class Element:
    tag: str
    attrs: dict[str, str | None]
    text: list[str] = field(default_factory=list)

    @property
    def content(self) -> str:
        return " ".join(self.text).strip()


class PageParser(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, html: str) -> None:
        super().__init__()
        self.elements: list[Element] = []
        self.stack: list[Element] = []
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        element = Element(tag, dict(attrs))
        self.elements.append(element)
        if tag not in self.VOID:
            self.stack.append(element)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index].tag == tag:
                self.stack = self.stack[:index]
                break

    def handle_data(self, data: str) -> None:
        for element in self.stack:
            element.text.append(data)

    def with_attr(self, attribute: str) -> list[Element]:
        return [element for element in self.elements if attribute in element.attrs]


def integers(text: str | None) -> list[int]:
    if not text:
        return []
    return [int(part) for part in text.split(",")]


class CompactionTutorialTests(unittest.TestCase):
    binary = PROJECT_ROOT / "build" / "meshcompact-reference"

    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (TUTORIAL_ROOT / "compaction.html").read_text(encoding="utf-8")
        cls.css = (TUTORIAL_ROOT / "compaction.css").read_text(encoding="utf-8")
        cls.shared_css = (TUTORIAL_ROOT / "styles.css").read_text(encoding="utf-8")
        cls.page = PageParser(cls.html)
        cls.fixture = cls.page.with_attr("data-input")[0]
        args = cls.fixture.attrs
        process = subprocess.run(
            [str(cls.binary), "--pes", str(args["data-width"]), "--chunk-size", str(args["data-chunk-size"]),
             "--threshold", str(args["data-threshold"]), "--values", str(args["data-input"])],
            check=True, capture_output=True, text=True, timeout=15,
        )
        cls.oracle = json.loads(process.stdout)

    def test_semantics_and_accessible_headings(self) -> None:
        tags = [element.tag for element in self.page.elements]
        self.assertEqual(tags.count("main"), 1)
        self.assertEqual(tags.count("h1"), 1)
        self.assertEqual(tags.count("header"), 1)
        self.assertIn('<html lang="zh-CN">', self.html)
        self.assertIn('class="skip-link" href="#main-content"', self.html)
        ids = [element.attrs["id"] for element in self.page.with_attr("id")]
        self.assertEqual(len(ids), len(set(ids)))
        for element in self.page.with_attr("aria-labelledby"):
            for identifier in str(element.attrs["aria-labelledby"]).split():
                self.assertIn(identifier, ids)
        self.assertTrue(any(element.tag == "summary" for element in self.page.elements))

    def test_static_page_has_no_javascript_or_exfiltration_surfaces(self) -> None:
        forbidden = {"script", "form", "iframe", "object", "embed", "base", "input", "textarea", "button"}
        self.assertFalse(any(element.tag in forbidden for element in self.page.elements))
        for element in self.page.elements:
            for key, value in element.attrs.items():
                self.assertFalse(key.lower().startswith("on"), key)
                self.assertNotIn(key.lower(), {"ping", "srcdoc", "action", "formaction"})
                if key in {"href", "src", "poster", "data"}:
                    target = urlsplit(value or "")
                    self.assertEqual(target.scheme, "")
                    self.assertEqual(target.netloc, "")
            self.assertNotEqual(str(element.attrs.get("http-equiv", "")).lower(), "refresh")
        self.assertNotRegex(self.css, r"(?i)url\s*\(|@import|https?://")

    def test_styles_are_local_and_existing_assets_preserved(self) -> None:
        styles = [element.attrs.get("href") for element in self.page.elements if element.tag == "link"]
        self.assertEqual(styles, ["./styles.css", "./compaction.css"])
        for href in styles:
            self.assertTrue((TUTORIAL_ROOT / str(href)).is_file())
        self.assertIn("var(--paper", self.css)
        self.assertIn("var(--signal-deep)", self.css)
        self.assertNotIn("@font-face", self.css)

    def test_navigation_and_cross_chapter_links(self) -> None:
        for filename in ("index.html", "read-code.html", "compaction.html"):
            content = (TUTORIAL_ROOT / filename).read_text(encoding="utf-8")
            parser = PageParser(content)
            for element in parser.elements:
                if element.tag != "a":
                    continue
                href = str(element.attrs["href"])
                path, _, fragment = href.partition("#")
                destination = TUTORIAL_ROOT / (path or filename)
                self.assertTrue(destination.is_file(), href)
                if fragment:
                    target = PageParser(destination.read_text(encoding="utf-8"))
                    self.assertIn(fragment, {item.attrs["id"] for item in target.with_attr("id")}, href)
            if filename != "compaction.html":
                self.assertIn('href="./compaction.html"', content)

    def test_authored_example_metadata_matches_cpp(self) -> None:
        self.assertEqual(len(self.page.with_attr("data-input")), 1)
        for attribute, key in (("data-input", "input"), ("data-output", "output"), ("data-indices", "indices")):
            self.assertEqual(integers(self.fixture.attrs[attribute]), self.oracle[key])
        self.assertEqual(self.oracle["width"], 4)
        self.assertEqual(self.oracle["chunk_size"], 3)
        self.assertEqual(self.oracle["threshold"], 5)
        self.assertEqual(self.oracle["output"], [5, 5, 9, 7])
        self.assertEqual(self.oracle["indices"], [2, 3, 5, 7])

    def test_rendered_input_records_and_predicate(self) -> None:
        records = self.page.with_attr("data-index")
        self.assertEqual(len(records), len(self.oracle["input"]))
        for index, element in enumerate(records):
            value = self.oracle["input"][index]
            self.assertEqual(int(str(element.attrs["data-index"])), index)
            self.assertEqual(int(str(element.attrs["data-value"])), value)
            selected = index in self.oracle["indices"]
            self.assertEqual(element.attrs["data-kept"], str(selected).lower())
            visible = element.content.replace("−", "-")
            self.assertRegex(visible, rf"^\s*{value}\s+@{index}")
            self.assertIn("保留" if selected else "去掉", visible)

    def test_rendered_output_records_and_stability(self) -> None:
        records = self.page.with_attr("data-rank")
        self.assertEqual(len(records), self.oracle["selected_count"])
        for rank, element in enumerate(records):
            value, index = self.oracle["output"][rank], self.oracle["indices"][rank]
            self.assertEqual(int(str(element.attrs["data-rank"])), rank)
            self.assertEqual(int(str(element.attrs["data-output-value"])), value)
            self.assertEqual(int(str(element.attrs["data-source-index"])), index)
            self.assertRegex(element.content, rf"^\s*{value}\s+@{index} → 位置 {rank}")

    def test_count_flow_matches_cpp_per_pe_offsets(self) -> None:
        nodes = self.page.with_attr("data-count-pe")
        self.assertEqual(len(nodes), 4)
        for node, expected in zip(nodes, self.oracle["pes"]):
            for attribute, key in (("data-count-pe", "pe"), ("data-local-count", "local_count"),
                                   ("data-offset", "offset"), ("data-prefix-count", "prefix_count")):
                self.assertEqual(int(str(node.attrs[attribute])), expected[key])
            self.assertIn(f"PE {expected['pe']}", node.content)
            self.assertIn(f"本地 {expected['local_count']}", node.content)
            self.assertIn(f"累计 {expected['prefix_count']}", node.content)
        self.assertIn("offset = [0,0,2,3]", self.html)

    def test_westbound_outputs_and_hop_counts_match_cpp(self) -> None:
        nodes = self.page.with_attr("data-output-pe")
        self.assertEqual(len(nodes), 4)
        for node, expected in zip(nodes, self.oracle["pes"]):
            self.assertEqual(int(str(node.attrs["data-output-pe"])), expected["pe"])
            self.assertEqual(integers(node.attrs["data-output-values"]), expected["output_values"])
            self.assertEqual(integers(node.attrs["data-output-indices"]), expected["output_indices"])
            self.assertEqual(int(str(node.attrs["data-received"])), expected["received_count"])
            self.assertEqual(int(str(node.attrs["data-forwarded"])), expected["forwarded_count"])
            visible_values = ",".join(map(str, expected["output_values"])) or "空"
            self.assertIn(visible_values, node.content)
        self.assertIn("[3,2,1,0]", self.html)
        self.assertIn("[0,3,2,1]", self.html)

    def test_record_journey_and_protocol_are_concrete(self) -> None:
        for text in ("7@7", "rank = 3 + 0 = 3", "floor(3 / 3) = 1", "rank=3, value=7, index=7",
                     "PE 3 → PE 2：1 条", "PE 2 → PE 1：2 条", "PE 1 → PE 0：3 条",
                     "0 条记录只发包头", "2 + 2 = 1 + 3", "poison padding", "blocking launch",
                     "rank = offset + j", "owner = floor(rank / 3)"):
            self.assertIn(text, self.html)

    def test_limits_and_run_instructions_are_truthful(self) -> None:
        for text in ("不是 WSE simulator 输出", "本页不调用 SDK", "固定手算示例",
                     "O(P×N)", "没有 host 往返", "bounded batch",
                     "make compaction", "make test-compaction", "make simulator-compaction",
                     "--prepare-only", "不代表 simulator 通过", "localhost 不能从手机远程访问"):
            self.assertIn(text, self.html)
        for text in (".sif", "/private-runs/", "csl-extras", "152", "device launches passed"):
            self.assertNotIn(text, self.html)

    def test_mobile_and_focus_contract(self) -> None:
        self.assertIn("viewport-fit=cover", self.html)
        self.assertNotIn("user-scalable=no", self.html)
        self.assertIn("safe-area-inset", self.shared_css)
        self.assertIn(":focus-visible", self.shared_css)
        self.assertIn("prefers-reduced-motion: reduce", self.shared_css)
        self.assertIn("min-height: 2.75rem", self.css)
        self.assertIn("repeat(4, minmax(0, 1fr))", self.css)
        self.assertIn("flex-wrap: wrap", self.css)
        self.assertNotRegex(self.css, r"(?i)@media[^\{]*max-width")
        self.assertNotRegex(self.css, r"(?i)(?:animation|transition)\s*:")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--binary", type=Path, default=CompactionTutorialTests.binary)
    arguments, remaining = parser.parse_known_args()
    sys.argv[1:] = remaining
    CompactionTutorialTests.binary = arguments.binary.resolve()
    unittest.main()
