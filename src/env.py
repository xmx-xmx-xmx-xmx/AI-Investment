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
    """生产环境 = GitHub Actions 或 Render（两个部署平台）。

    - GITHUB_ACTIONS == "true" → GitHub Actions 定时简报运行器
    - RENDER == "true"         → Render Web Service（飞书机器人 bot_server）
      （Render 自动为所有服务注入 RENDER=true，见 render.com/docs/environment-variables）

    两者都不是 → 本地开发（禁止调飞书 API，见 CLAUDE.md 1.1 节）。
    """
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return True
    if os.environ.get("RENDER") == "true":
        return True
    return False


def is_dev() -> bool:
    """本地开发 / 干跑模式。"""
    return not is_production()
