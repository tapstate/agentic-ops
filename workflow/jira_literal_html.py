#!/usr/bin/env python3
"""已确认字面评论的 HTML 展示与回读；无网络、无状态写入，不解析任意富文本。"""
from __future__ import annotations

# 产品入口：先取得租约，再重新加载本版脚本；导入库模块不启动 CLI。
if __name__ == "__main__" and not globals().get("_AO_PRODUCT_ENTRY"):
    from pathlib import Path as _AoPath
    _ao_loader = _AoPath(__file__).resolve().parents[1] / "bootstrap/product_access.py"
    _ao_namespace = {"__file__": str(_ao_loader), "_AO_LOADER_SOURCE": _ao_loader.read_bytes()}
    exec(compile(_ao_namespace["_AO_LOADER_SOURCE"], str(_ao_loader), "exec"), _ao_namespace)
    raise SystemExit(_ao_namespace["python_entry"](__file__))


import argparse
import html
from html.parser import HTMLParser
import json
from pathlib import Path


class RepresentationError(ValueError):
    """只含固定、不带敏感原文的协议诊断。"""


def literal_text(body):
    if not isinstance(body, str) or not body.strip() or '\x00' in body or '\r' in body.replace('\r\n', ''):
        raise RepresentationError('正文必须为非空字面文本，不能含 NUL 或孤立 CR')
    return '\n'.join(line.rstrip() for line in body.replace('\r\n', '\n').split('\n')).strip()


def render(body):
    """只增加标题、段落和标签强调；代码、链接和 Markdown 语法均保持字面。"""
    blocks = literal_text(body).split('\n\n')
    result = []
    for index, block in enumerate(blocks):
        tag = 'h3' if index == 0 and '\n' not in block else 'p'
        escaped = html.escape(block, quote=True).replace('\n', '<br>')
        # 标签只作用于短的首行前缀，不重排、不归并正文或隐藏风险。
        label, colon, rest = escaped.partition('：')
        if tag == 'p' and colon and len(label) <= 12 and '<br>' not in label:
            escaped = '<strong>' + label + colon + '</strong>' + rest
        result.append('<' + tag + '>' + escaped + '</' + tag + '>')
    return ''.join(result)


class LiteralDocument(HTMLParser):
    """仅接受本展示合同的节点；未知结构、属性、链接、媒体均拒绝。"""
    def __init__(self, plain=False):
        super().__init__(convert_charrefs=True)
        self.plain = plain
        self.stack = []
        self.blocks = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        if any(key != 'data-local-id' for key, _ in attrs):
            raise RepresentationError('不支持的 HTML 属性；不能忽略链接或其它语义')
        if tag in ('p', 'h3') and not self.stack:
            self.current = [tag, []]
            self.stack.append(tag)
        elif tag == 'strong' and self.stack in (['p'], ['h3']):
            self.current[1].append(['strong', ''])
            self.stack.append(tag)
        elif tag == 'br' and self.stack in (['p'], ['h3']):
            self.current[1].append(['text', '\n'])
        else:
            raise RepresentationError('不支持的 HTML 结构；保留待核验')

    def handle_startendtag(self, tag, attrs):
        if tag != 'br':
            raise RepresentationError('不支持的自闭合节点')
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if not self.stack or tag != self.stack[-1]:
            raise RepresentationError('HTML 节点未正确闭合')
        self.stack.pop()
        if not self.stack:
            self.blocks.append(self.current)
            self.current = None

    def handle_data(self, data):
        if not self.stack:
            if data.strip():
                raise RepresentationError('HTML 块外存在正文')
            return
        if self.plain and any(char in data for char in '\r\n'):
            raise RepresentationError('纯文本换行必须由 p 或 br 表达，不能将 HTML 源码空白当作换行')
        kind = 'strong' if self.stack[-1] == 'strong' else 'text'
        parts = self.current[1]
        if parts and parts[-1][0] == kind:
            parts[-1][1] += data
        else:
            parts.append([kind, data])

    def handle_comment(self, data):
        raise RepresentationError('不接纳 HTML 注释')

    def handle_decl(self, decl):
        raise RepresentationError('不接纳 HTML 文档声明')

    def unknown_decl(self, data):
        raise RepresentationError('不接纳 HTML 声明')

    def handle_pi(self, data):
        raise RepresentationError('不接纳 HTML 处理指令')


def document(value, plain=False):
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise RepresentationError('缺少有效 HTML 正文')
    parser = LiteralDocument(plain=plain)
    parser.feed(value)
    parser.close()
    if parser.stack or not parser.blocks:
        raise RepresentationError('HTML 正文未闭合或没有段落')
    return parser.blocks


def readback_metadata(readback, site, issue_key, comment_id):
    """仅核验原生材料的目标与来源；原操作及作者绑定仍由所属接口检查。"""
    if not isinstance(readback, dict):
        raise RepresentationError('回读必须为原生来源对象')
    for key, value in (('site', site), ('issue_key', issue_key), ('comment_id', comment_id)):
        if not isinstance(value, str) or not value or readback.get(key) != value:
            raise RepresentationError('回读目标或评论 ID 不匹配')
    if not isinstance(readback.get('source_ref'), str) or not readback['source_ref'].strip():
        raise RepresentationError('回读缺少原生来源')
    return {'site': site, 'issue_key': issue_key, 'comment_id': comment_id,
            'source_ref': readback['source_ref'],
            **({'author_account_id': readback['author_account_id']} if 'author_account_id' in readback else {})}


def compare(body, expected_html, readback, site, issue_key, comment_id):
    """比对保存的 HTML 原意图及正文；不把文本相同视为任意结构相同。"""
    metadata = readback_metadata(readback, site, issue_key, comment_id)
    expected = document(expected_html)
    plain = '\n\n'.join(''.join(part[1] for part in block[1]) for block in expected)
    if literal_text(plain) != literal_text(body):
        raise RepresentationError('HTML 意图与原确认正文不一致')
    if expected != document(readback.get('body')):
        raise RepresentationError('HTML 回读正文或结构不匹配，保持待核对，不得重发')
    # 转换结果仅供已有 readback 接口核验原操作/作者等绑定；不声称 verified。
    return {'body': literal_text(body), 'body_representation': 'exact-text', **metadata}


def compare_plain(body, readback, site, issue_key, comment_id):
    """只核验原确认纯文本的无格式原生段落；不重建旧 HTML 发送意图。"""
    metadata = readback_metadata(readback, site, issue_key, comment_id)
    actual = document(readback.get('body'), plain=True)
    if any(tag != 'p' or any(kind != 'text' for kind, _ in parts) for tag, parts in actual):
        raise RepresentationError('纯文本回读只接受无格式段落和换行；展示或富文本必须核对原 HTML 意图')
    plain = '\n\n'.join(''.join(value for _, value in parts) for _, parts in actual)
    if literal_text(plain) != literal_text(body):
        raise RepresentationError('原生段落与原确认正文不一致，保持待核对，不得重发')
    return {'body': literal_text(body), 'body_representation': 'exact-text', **metadata}


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise RepresentationError('回读 JSON 字段重复')
        value[key] = item
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    render_parser = sub.add_parser('render', help='展示已确认的字面正文；将输出保存为原发送 HTML 意图')
    render_parser.add_argument('--body-file', required=True)
    compare_parser = sub.add_parser('compare', help='核对原 HTML 意图和原生回读；输出已有 readback 所需文本')
    compare_parser.add_argument('--expected-html-file', required=True)
    plain_parser = sub.add_parser('compare-plain', help='核验原确认纯文本的无格式原生段落；不生成 HTML 意图')
    for command_parser in (compare_parser, plain_parser):
        for name in ('body-file', 'readback', 'site', 'issue-key', 'comment-id'):
            command_parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    try:
        body = Path(args.body_file).read_bytes().decode('utf-8')
        if args.action == 'render':
            print(render(body))
        else:
            readback = json.loads(Path(args.readback).read_text(encoding='utf-8'), object_pairs_hook=unique_object)
            if args.action == 'compare-plain':
                result = compare_plain(body, readback, args.site, args.issue_key, args.comment_id)
            else:
                result = compare(body, Path(args.expected_html_file).read_text(encoding='utf-8'),
                                 readback, args.site, args.issue_key, args.comment_id)
            print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, OSError, TypeError) as error:
        # 不回显正文或底层带本机路径的异常。
        print(json.dumps({'ok': False, 'reason': str(error) if isinstance(error, RepresentationError) else 'Jira HTML 输入不可读取或格式无效',
                          'guidance': '核对原正文、HTML 意图、目标与原生回读；不自动重发'}, ensure_ascii=False))
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
