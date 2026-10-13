"""清理结果的有界展示，不生成第二套清理判定。"""
import json
import os
from pathlib import Path


def safe_describe(base, task=None, plan=None, operation=None, problems=None):
    result = (operation or {}).get("cleanup_result", {})
    return {"released": result.get("released", False), "complete": result.get("complete", False),
            "residuals": result.get("residuals", (plan or {}).get("residuals", [])), "problems": problems or []}


def emit(value, details=False, output=None, base=None):
    if output:
        path = Path(output)
        if not path.is_absolute() or path.is_symlink() or (base and Path(base).resolve() in path.resolve().parents):
            raise ValueError("完整结果必须输出到工位外的新文件")
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False)
    if not details:
        def bounded(item):
            if isinstance(item, dict):
                result = {}
                for key, child in item.items():
                    if key == 'cleanup_plan':
                        result['object_count'] = len(child.get('objects', []))
                    elif isinstance(child, list):
                        result[key + '_count'] = len(child)
                        result[key] = [bounded(row) for row in child[:50]]
                    else:
                        result[key] = bounded(child)
                return result
            return item
        value = bounded(value)
    print(json.dumps(value, ensure_ascii=False))
