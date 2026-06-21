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


# ═══════════════════════════════════════════════════════════════
# 日报内容
# ═══════════════════════════════════════════════════════════════

def _make_data_card(portfolio: list[dict], rebalance: dict) -> str:
    """构建数据速览卡片（偏离度表格 + 持仓盈亏 + 操作要点）。

    这笔始终不变——不管有没有开 AI，这张数据卡都推。
    """
    from src.market_data import fetch_vix

    tz_cn = timezone(timedelta(hours=8))
    today = datetime.now(tz_cn).strftime("%Y-%m-%d")
    total_value = rebalance["total_value"]

    # VIX
    vix_data = fetch_vix()
    vix_line = "获取失败"
    if vix_data and vix_data.get("vix"):
        vix_line = f"**{vix_data['vix']:.2f}**（{vix_data['level']}）"

    # 偏离度表格
    rows = []
    for d in rebalance["deviation_report"]:
        emoji = {"超配": "🔺", "低配": "🔻", "正常": "✅"}.get(d["status"], "➖")
        rows.append(
            f"| {d['asset_class']} | {d['target_weight_pct']} | {d['actual_weight_pct']} "
            f"| {d['deviation_pct']} | {emoji} {d['status']} |"
        )

    # 持仓盈亏
    position_lines = []
    for p in rebalance["positions"]:
        tag = " 🏷️" + "长底" if "长期底仓" in p.get("tags", []) else ""
        arrow = "🔺" if p["pnl_pct"] > 0 else "🔻" if p["pnl_pct"] < 0 else "➖"
        position_lines.append(
            f"| {p['name']}{tag} | {p['asset_class']} "
            f"| ¥{p['market_value']:,.0f} | {arrow} {p['pnl_pct']:+.1f}% |"
        )

    return f"""📅 **{today} 量化日报**

**总市值：¥{total_value:,.2f}**　|　**VIX 恐惧温度计：{vix_line}**

---

📊 **大类偏离度**

| 资产大类 | 目标 | 实际 | 偏离 | 状态 |
|--------|------|------|------|------|
{chr(10).join(rows)}

---

💼 **持仓盈亏**

| 标的 | 大类 | 市值 | 盈亏 |
|------|------|------|------|
{chr(10).join(position_lines)}

---

> 📐 策略详情见第二张卡「AI 深度分析」｜ 🏷️ 长底 = 永不割肉 ｜ 向上滑动查看 👆"""


def _make_ai_analysis_card(report_text: str) -> str:
    """把 LLM 报告原文截断为适合飞书卡片的长度，加标题和声明。"""
    tz_cn = timezone(timedelta(hours=8))
    now = datetime.now(tz_cn)

    # 飞书卡片单条消息大约支持约 15KB，我们的报告一般在 3-5KB 范围，直接全贴
    # 安全起见加个截断
    max_chars = 12000
    if len(report_text) > max_chars:
        report_text = report_text[:max_chars] + "\n\n...（报告过长，已截断。完整版请运行 python -m src.advisor）"

    return f"""🤖 **AI 深度分析**　|　{now.strftime('%H:%M')}

{report_text}

---

> ⚠️ 以上建议由 AI 基于量化纪律生成，不构成投资建议。投资有风险，操作请结合自身判断。"""


# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════

def run_full_notify(data_only: bool = False, dry_run: bool = False):
    """完整推送：数据卡 + AI 分析卡。

    Args:
        data_only: 仅数据卡，不调 LLM
    """
    from src.advisor import load_portfolio, calculate_rebalance, build_prompt
    from src import news_fetcher
    from src.market_data import fetch_vix

    pusher = FeishuPusher()
    if not pusher.is_configured() and not dry_run:
        print("⚠️  FEISHU_WEBHOOK_URL 未设置")
        return

    # ── 1. 加载数据 ──
    logger.info("加载持仓 + 抓取价格...")
    portfolio = load_portfolio()
    rebalance = calculate_rebalance(portfolio)
    vix_data = fetch_vix()
    if vix_data is None:
        vix_data = {"vix": None, "level": "unknown"}

    # ── 2. 数据卡（始终发） ──
    data_card = _make_data_card(portfolio, rebalance)
    if dry_run:
        print("═══ 数据速览卡 ═══")
        print(data_card)
    else:
        logger.info("发送数据速览卡...")
        pusher.send_card(
            title=f"📊 量化日报 {datetime.now(timezone(timedelta(hours=8))).strftime('%m-%d')}",
            content=data_card,
        )
        time.sleep(0.5)

    if data_only:
        logger.info("--data-only 模式，跳过 AI 分析")
        return

    # ── 3. 新闻 ──
    logger.info("搜索财经新闻...")
    news_articles = news_fetcher.fetch_portfolio_news(portfolio, max_per_query=2)

    # ── 4. AI 分析 ──
    logger.info("调用 LLM 生成分析报告...")
    prompt = build_prompt(rebalance, vix_data, news_articles=news_articles)

    from src.llm import get_llm_client, get_llm_model
    client = get_llm_client()
    if client is None:
        report = "AI 分析生成失败: API Key 未配置"
    else:
        try:
            resp = client.chat.completions.create(
                model=get_llm_model(),
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
            )
            report = resp.choices[0].message.content.strip()
        except Exception as e:
            logger.error("LLM 调用失败: %s", e)
            report = f"AI 分析生成失败: {e}"

    # ── 5. AI 分析卡 ──
    ai_card = _make_ai_analysis_card(report)
    if dry_run:
        print()
        print("═══ AI 分析卡 ═══")
        print(ai_card)
    else:
        logger.info("发送 AI 分析卡...")
        pusher.send_card(
            title=f"🤖 AI 深度分析 {datetime.now(timezone(timedelta(hours=8))).strftime('%m-%d %H:%M')}",
            content=ai_card,
        )


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    data_only = "--data-only" in sys.argv
    dry = "--dry-run" in sys.argv

    print()
    print("=" * 50)
    print("   📨 量化日报推送" + (" [数据模式]" if data_only else " [完整模式]"))
    if dry:
        print("   [DRY RUN —— 不实际发送]")
    print("=" * 50)
    print()

    run_full_notify(data_only=data_only, dry_run=dry)

    print()
    print("=" * 50)
    if dry:
        print("   [DRY RUN 完成]")
    else:
        print("   ✅ 推送完成")
    print("=" * 50)
