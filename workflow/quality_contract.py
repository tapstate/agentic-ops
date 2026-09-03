"""质量契约的有限 JSON Schema 校验器，仅实现本产品使用的关键字。

不联网解析引用，不接受契约目录以外的文件；未知关键字失败关闭。
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "contracts"
KEYWORDS = {"$schema", "$id", "$defs", "$ref", "title", "description", "anyOf",
            "type", "const", "enum", "properties", "required", "additionalProperties",
            "items", "minimum", "minLength", "pattern"}


def validate(value, schema, document=None, path="$", root=ROOT):
    if isinstance(schema, str):
        file = (root / schema).resolve()
        if file.parent != root.resolve():
            raise ValueError("质量契约引用越界")
        schema = json.loads(file.read_text(encoding="utf-8"))
        document = schema
    document = document or schema
    if set(schema) - KEYWORDS:
        raise ValueError("质量契约含未支持关键字")
    if "$ref" in schema:
        ref = schema["$ref"]
        if ref.startswith("#/$defs/"):
            return validate(value, document["$defs"][ref[8:]], document, path, root)
        return validate(value, ref, path=path, root=root)
    if "anyOf" in schema:
        for option in schema["anyOf"]:
            try:
                validate(value, option, document, path, root)
                return
            except ValueError:
                pass
        raise ValueError("%s 不符合质量操作契约；请核对 action/payload、字段及类型" % path)
    types = {"object": dict, "array": list, "string": str, "integer": int}
    if "type" in schema and type(value) is not types[schema["type"]]:
        raise ValueError("%s 类型错误" % path)
    if "const" in schema and (type(value) is not type(schema["const"]) or value != schema["const"]):
        raise ValueError("%s 版本或固定值不支持" % path)
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError("%s 枚举值无效" % path)
    if isinstance(value, dict):
        props = schema.get("properties", {})
        if set(schema.get("required", [])) - set(value):
            raise ValueError("%s 缺少必需字段" % path)
        extra = schema.get("additionalProperties", True)
        for key, item in value.items():
            if key in props:
                validate(item, props[key], document, path + "." + key, root)
            elif extra is False:
                raise ValueError("%s 含未声明字段 %s" % (path, key))
            elif isinstance(extra, dict):
                validate(item, extra, document, path + "." + key, root)
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            validate(item, schema["items"], document, "%s[%s]" % (path, i), root)
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or ("pattern" in schema and not re.search(schema["pattern"], value)):
            raise ValueError("%s 字符串为空或格式无效" % path)
    if type(value) is int and value < schema.get("minimum", value):
        raise ValueError("%s 数值低于下限" % path)
