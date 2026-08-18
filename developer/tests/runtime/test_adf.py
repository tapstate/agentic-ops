from __future__ import annotations

import unittest

from ao_work.jira.adf import markdown_to_adf


class MarkdownToAdfTest(unittest.TestCase):
    def test_headings_and_lists_preserved(self) -> None:
        doc = markdown_to_adf("# 标题\n\n- 项一\n- 项二\n")
        types = [node["type"] for node in doc["content"]]
        self.assertEqual(["heading", "bulletList"], types)
        self.assertEqual(1, doc["content"][0]["attrs"]["level"])
        self.assertEqual(2, len(doc["content"][1]["content"]))

    def test_inline_bold_italic_code_and_link(self) -> None:
        doc = markdown_to_adf(
            "**粗体** 与 *斜体* 与 `代码` 与 [链接](https://example.test)\n"
        )
        paragraph = doc["content"][0]
        nodes = paragraph["content"]
        by_mark = {
            node.get("marks", [{}])[0].get("type"): node
            for node in nodes
            if node.get("type") == "text" and node.get("marks")
        }
        self.assertEqual("粗体", by_mark["strong"]["text"])
        self.assertEqual("斜体", by_mark["em"]["text"])
        self.assertEqual("代码", by_mark["code"]["text"])
        self.assertEqual("链接", by_mark["link"]["text"])
        self.assertEqual(
            "https://example.test", by_mark["link"]["marks"][0]["attrs"]["href"]
        )

    def test_fenced_code_block(self) -> None:
        doc = markdown_to_adf("```\nline1\nline2\n```\n")
        code_block = doc["content"][0]
        self.assertEqual("codeBlock", code_block["type"])
        self.assertEqual("line1\nline2", code_block["content"][0]["text"])

    def test_plain_paragraph_when_empty(self) -> None:
        doc = markdown_to_adf("")
        self.assertEqual([{"type": "paragraph"}], doc["content"])

    def test_ordered_list(self) -> None:
        doc = markdown_to_adf("1. 第一\n2. 第二\n")
        ordered = doc["content"][0]
        self.assertEqual("orderedList", ordered["type"])
        self.assertEqual(1, ordered["attrs"]["order"])
        self.assertEqual(2, len(ordered["content"]))

    def test_task_list(self) -> None:
        doc = markdown_to_adf("- [ ] 待办\n- [x] 已完成\n")
        task_list = doc["content"][0]
        self.assertEqual("taskList", task_list["type"])
        self.assertEqual("TODO", task_list["content"][0]["attrs"]["state"])
        self.assertEqual("DONE", task_list["content"][1]["attrs"]["state"])

    def test_strike_underline_subsup(self) -> None:
        doc = markdown_to_adf("~~删除~~ +下划线+ ^上标^ ~下标~\n")
        paragraph = doc["content"][0]
        nodes = paragraph["content"]
        subsup_types = [
            node["marks"][0]["attrs"]["type"]
            for node in nodes
            if node.get("type") == "text"
            and node.get("marks")
            and node["marks"][0]["type"] == "subsup"
        ]
        self.assertEqual(["sup", "sub"], subsup_types)
        by_mark = {
            node.get("marks", [{}])[0].get("type"): node
            for node in nodes
            if node.get("type") == "text" and node.get("marks")
        }
        self.assertEqual("删除", by_mark["strike"]["text"])
        self.assertEqual("下划线", by_mark["underline"]["text"])

    def test_rule(self) -> None:
        doc = markdown_to_adf("---\n")
        self.assertEqual("rule", doc["content"][0]["type"])


if __name__ == "__main__":
    unittest.main()
