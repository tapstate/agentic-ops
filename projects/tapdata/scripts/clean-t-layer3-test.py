#!/usr/bin/env python3
"""仅清理 t-layer3-test 仓库明确生成的 target/Python 缓存，不处理数据库或容器。"""
import argparse
from pathlib import Path
import shutil
import subprocess

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--repository', required=True)
args = p.parse_args()
root = Path(args.repository).resolve()
if root.name != 't-layer3-test' or not (root / '.git').is_dir():
    raise SystemExit('必须显式指定 t-layer3-test 独立仓库')
paths = sorted({item for pattern in ('target', '**/target', '__pycache__', '**/__pycache__', '.pytest_cache')
                for item in root.glob(pattern)}, key=lambda item: len(item.parts))
selected = []
for path in paths:
    relative = path.relative_to(root)
    if '.git' in relative.parts or any(parent in selected for parent in path.parents):
        continue
    for part in [path, *path.parents]:
        if part == root:
            break
        if part.is_symlink() or part.is_mount():
            raise SystemExit('生成目录含链接或挂载，停止清理')
    tracked = subprocess.run(['git', '-C', str(root), 'ls-files', '-z', '--', str(relative)], capture_output=True, check=True)
    if tracked.stdout or not path.is_dir():
        raise SystemExit('候选包含源码或不是生成目录，停止清理')
    for child in path.rglob('*'):
        if child.name == '.git' or child.is_symlink() or child.is_mount():
            raise SystemExit('生成目录包含链接、挂载或嵌套 Git，停止清理')
    selected.append(path)
for path in selected:
    shutil.rmtree(path)
