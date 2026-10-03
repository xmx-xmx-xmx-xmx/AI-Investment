"""静态守卫：函数内引用「未导入/未定义」的名字（NameError 只在运行时暴露）。

2026-10-03 事故背景：`_build_sat_morning` 里直接调用了 `get_feishu_client_or_none`
但没做局部导入（briefing.py 惯例是函数内导入，见模块内其他调用点）。
测试全绿，因为**没有任何测试跑过周六 builder**；直到周六首次真跑才炸，
周六简报直接没推出去。

本测试用标准库 symtable 做全量静态扫描，覆盖所有模块的所有作用域，
零依赖、毫秒级，专门堵住「运行时才暴露的未定义名」这一类事故。

判据：某符号在作用域内被引用，但既非本作用域赋值/参数/局部，也不是外层闭包
自由变量，且在模块级符号表与 builtins 里都找不到 → 可疑（必为 NameError）。
"""
from __future__ import annotations

import builtins
import pathlib
import symtable

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
TARGETS = sorted(REPO.glob("src/*.py")) + [REPO / "bot_server.py"]

BUILTINS = set(dir(builtins))

# 白名单：(文件名, 符号名) -> 理由。每条都必须写明为什么是安全的。
WHITELIST: dict[tuple[str, str], str] = {}


def _scan(path: pathlib.Path) -> list[tuple[str, str]]:
    src = path.read_text(encoding="utf-8")
    st = symtable.symtable(src, str(path), "exec")
    module_names = set(st.get_identifiers())
    base = path.name
    found: list[tuple[str, str]] = []

    def walk(table, scope: str) -> None:
        for sym in table.get_symbols():
            name = sym.get_name()
            if not sym.is_referenced():
                continue
            # 本作用域赋值/参数/局部 → 正常
            if sym.is_assigned() or sym.is_parameter() or sym.is_local():
                continue
            # 闭包自由变量（外层函数局部）→ 合法，跳过
            if sym.is_free():
                continue
            # 到这里是当作全局名引用
            if name in module_names or name in BUILTINS:
                continue
            if (base, name) in WHITELIST:
                continue
            found.append((scope, name))
        for child in table.get_children():
            walk(child, f"{scope}.{child.get_name()}()")

    walk(st, "<module>")
    return found


def test_no_undefined_names_in_source():
    """任何模块都不应存在「引用未定义名」——那种错误只在运行时炸。"""
    problems: list[str] = []
    for path in TARGETS:
        for scope, name in _scan(path):
            problems.append(f"{path.relative_to(REPO)} → {scope} 引用了未定义名 `{name}`")
    assert not problems, (
        "发现会在运行时抛 NameError 的引用（通常是漏了 import）：\n  "
        + "\n  ".join(problems)
    )


@pytest.mark.parametrize("path", TARGETS, ids=lambda p: p.name)
def test_all_targets_are_scannable(path: pathlib.Path):
    """守卫扫描器本身：每个目标文件都能正常解析（防止文件被写坏）。"""
    assert _scan(path) is not None
