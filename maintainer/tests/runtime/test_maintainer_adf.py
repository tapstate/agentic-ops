from __future__ import annotations

import unittest

from ao_maint.jira.adf import adf_to_markdown, markdown_to_adf


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
        self.assertEqual("paragraph", paragraph["type"])
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

    def test_inline_marks_inside_list_items(self) -> None:
        doc = markdown_to_adf("- **加粗项**\n- 普通项\n")
        list_item = doc["content"][0]["content"][0]
        marks = list_item["content"][0]["content"][0].get("marks", [])
        self.assertEqual("strong", marks[0]["type"])
        self.assertEqual("加粗项", list_item["content"][0]["content"][0]["text"])

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

    def test_adf_readback_preserves_supported_structure(self) -> None:
        source = (
            "# 标题\n\n"
            "- **粗体** 与 [链接](https://example.test)\n"
            "- [x] 已完成\n\n"
            "```\n代码\n```\n"
        )
        rendered = adf_to_markdown(markdown_to_adf(source))
        self.assertTrue(rendered.complete)
        self.assertEqual((), rendered.unsupported_node_types)
        self.assertIn("# 标题", rendered.markdown)
        self.assertIn("**粗体**", rendered.markdown)
        self.assertIn("[链接](https://example.test)", rendered.markdown)
        self.assertIn("- [x] 已完成", rendered.markdown)
        self.assertIn("```\n代码\n```", rendered.markdown)
        self.assertIn("标题", rendered.plain_text)

    def test_adf_readback_marks_unknown_node_incomplete(self) -> None:
        document = {
            "type": "doc",
            "version": 1,
            "content": [{"type": "panel", "content": [{"type": "text", "text": "保留"}]}],
        }
        rendered = adf_to_markdown(document)
        self.assertFalse(rendered.complete)
        self.assertEqual(("node:panel",), rendered.unsupported_node_types)
        self.assertIn("不支持的 ADF node: panel", rendered.markdown)
        self.assertEqual("保留", rendered.plain_text)

    def test_adf_readback_marks_unknown_mark_incomplete(self) -> None:
        document = {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": "文字",
                            "marks": [{"type": "backgroundColor"}],
                        }
                    ],
                }
            ],
        }
        rendered = adf_to_markdown(document)
        self.assertFalse(rendered.complete)
        self.assertEqual(("mark:backgroundColor",), rendered.unsupported_node_types)
        self.assertIn("文字", rendered.markdown)


if __name__ == "__main__":
    unittest.main()
