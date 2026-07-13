"""
飞书群机器人日报推送 —— 完整版。

职责：
- 第一张卡片：数据速览（偏离度表格 + 持仓盈亏 + 操作要点）
- 第二张卡片：AI 深度分析报告（宏观解读 + 偏离度诊断 + 持仓安抚 + 操作指令）
- HMAC 签名格式复用自开源项目 feishu_sender.py

用法：
    python -m src.notify                    # 完整推送（数据卡 + AI 报告）
    python -m src.notify --data-only        # 仅数据卡，不调 LLM
    python -m src.notify --dry-run          # 只打印，不发
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta

import requests

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# 飞书推送器
# ═══════════════════════════════════════════════════════════════

class FeishuPusher:
    """飞书群机器人 Webhook 消息推送。"""

    def __init__(self, webhook_url: str = "", secret: str = ""):
        self.url = webhook_url or os.environ.get("FEISHU_WEBHOOK_URL", "")
        self.secret = secret or os.environ.get("FEISHU_WEBHOOK_SECRET", "")

    def is_configured(self) -> bool:
        return bool(self.url)

    def _build_sign(self) -> dict:
        if not self.secret:
            return {}
        ts = str(int(time.time()))
        string_to_sign = f"{ts}\n{self.secret}"
        sign = base64.b64encode(
            hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
        ).decode("utf-8")
        return {"timestamp": ts, "sign": sign}

    def send_card(self, title: str, content: str) -> bool:
        if not self.url:
            logger.warning("飞书 Webhook 未配置")
            return False
        payload = {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {"title": {"tag": "plain_text", "content": title}},
                "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": content}}],
            },
        }
        payload.update(self._build_sign())
        try:
            resp = requests.post(self.url, json=payload, timeout=30)
            if resp.status_code == 200 and resp.json().get("code") == 0:
                logger.info("飞书消息发送成功")
                return True
            logger.error("飞书返回错误: %s", resp.text[:200])
            return False
        except Exception as e:
            logger.error("飞书推送异常: %s", e)
            return False


# 注：_make_data_card() / _make_ai_analysis_card() / run_full_notify() 已于
# 2026-07-07 删除（死代码，全项目无调用者）。notify.py 仅保留 FeishuPusher 类，
# 供 briefing.py 的 _push() 使用。
#
# 🔥 2026-07-13 保留 notify 入口：供飞书机器人 @AI投顾 后续按需推送使用。
# 当前推送 closing 简报（仓位健康 + 市值 + 偏离度），不额外开发轻量版。


# ═══════════════════════════════════════════════════════════════
# CLI 入口（供 workflow_dispatch notify 模式调用）
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    print()
    print("=" * 50)
    print("   📨 量化日报推送（notify 模式）")
    print("=" * 50)
    print()

    # 直接复用 closing 简报——它包含了仓位健康、市场基准、持仓市值，
    # 是"看一眼就知道今天该不该动"的最佳轻量推送。
    from src.briefing import _build_closing
    try:
        result = _build_closing()
        if result and result != "SKIP":
            pusher = FeishuPusher()
            if pusher.is_configured():
                pusher.send_card(
                    title=f"📊 量化日报 {datetime.now(timezone(timedelta(hours=8))).strftime('%m-%d %H:%M')}",
                    content=result,
                )
            else:
                print("⚠️  FEISHU_WEBHOOK_URL 未设置，仅打印：")
                print(result)
        else:
            print("⏭️  今日休市，无推送")
    except Exception as e:
        logger.error("notify 推送失败: %s", e)
        sys.exit(1)

    print()
    print("=" * 50)
    print("   ✅ 推送完成")
    print("=" * 50)
