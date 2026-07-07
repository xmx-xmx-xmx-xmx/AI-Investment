"""
环境判定 —— 本地开发与生产环境隔离的总开关。

所有涉及飞书 API、LLM 调用的模块，必须通过本模块判断环境。

用法：
    from src.env import is_production, is_dev

    if is_production():
        client = FeishuClient()
    else:
        client = None
"""

import os


def is_production() -> bool:
    """GitHub Actions 环境 = 生产。"""
    return os.environ.get("GITHUB_ACTIONS") == "true"


def is_dev() -> bool:
    """本地开发 / 干跑模式。"""
    return not is_production()
