#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 AST 找未使用的 import。没有装 linter 时够用。"""

from __future__ import annotations

import ast
import sys
from pathlib import Path


def unused_imports(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[0]
                imported[name] = node.lineno
        elif isinstance(node, ast.ImportFrom):
            if node.module == "__future__":
                continue  # 编译器指令,不是真实依赖
            for alias in node.names:
                if alias.name == "*":
                    continue
                name = alias.asname or alias.name
                imported[name] = node.lineno

    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            current = node
            while isinstance(current, ast.Attribute):
                current = current.value
            if isinstance(current, ast.Name):
                used.add(current.id)
    # 字符串注解里出现也算用到
    text = source
    report = []
    for name, lineno in imported.items():
        if name in used:
            continue
        if f'"{name}' in text or f"'{name}" in text or f"[{name}" in text:
            continue
        report.append(f"{path.name}:{lineno}: unused import {name}")
    return report


def main(targets: list[str]) -> int:
    findings: list[str] = []
    for target in targets:
        root = Path(target)
        paths = [root] if root.is_file() else sorted(root.rglob("*.py"))
        for path in paths:
            # 跳过缓存和内置第三方代码(不是我写的,不改也不检查)
            if "__pycache__" in str(path) or "/vendor/" in path.as_posix():
                continue
            findings.extend(unused_imports(path))
    for line in findings:
        print(line)
    print(f"\n{len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["bilifocus", "tests"]))
