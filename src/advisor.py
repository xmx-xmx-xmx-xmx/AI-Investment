"""
AI 投资顾问核心分析脚本 —— 基于"资产标签化"管理体系。

核心理念：
- 不预测市场，只做量化纪律执行
- 按资产大类管理目标权重，偏离度驱动再平衡
- VIX 锚定全球风险偏好
- 暴跌时提供情绪安抚，宏观事件翻译成大白话

数据来源：
- 持仓：飞书多维表格「底仓表」
- 行情：src.market_data（akshare + yfinance 多源 fallback）
- AI：DeepSeek（通过 OpenAI 兼容接口）
"""

from __future__ import annotations

import os
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

from openai import OpenAI

from src.feishu_client import FeishuClient
from src import market_data
from src import news_fetcher
from src import strategy

# ═══════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════

API_KEY = os.environ.get("SILICONFLOW_API_KEY", "")
API_BASE_URL = os.environ.get("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
MODEL_NAME = os.environ.get("SILICONFLOW_MODEL", "Qwen/Qwen3-30B-A3B-Instruct-2507")

# 资产大类目标权重 —— 修改你的投资纪律就改这里
TARGET_WEIGHTS = {
    "美股资产": 0.35,
    "A股资产": 0.15,
    "港股资产": 0.20,
    "避险商品": 0.15,
    "固收资产": 0.15,
}

# 偏离度红线
DEVIATION_THRESHOLD = 0.03  # ±3% 触发再平衡

# 恐慌安抚阈值 —— 单只标的浮亏超过这个比例时触发情绪安抚
PANIC_THRESHOLD = -0.05  # -5%

# 资产大类 → 对应的行情抓取方式
# 场外基金无法通过 yfinance/akshare 直接抓净值，目前先用手动更新的现价
# 未来可扩展：用基金代码通过天天基金等 API 抓净值


# ═══════════════════════════════════════════════════════════════
# 0. 字段值归一化 —— SDK 单选返回 str，多选返回 list
# ═══════════════════════════════════════════════════════════════

def _unwrap_select(value):
    """单选字段：str → str；list → 取首元素。"""
    if isinstance(value, list):
        return value[0] if value else ""
    return value or ""


def _unwrap_select_list(value):
    """多选字段：保证返回 list[str]。"""
    if isinstance(value, list):
        return [str(v) for v in value if v]
    if value:
        return [str(value)]
    return []


# ═══════════════════════════════════════════════════════════════
# 1. 从飞书读取持仓
# ═══════════════════════════════════════════════════════════════

def load_portfolio(client: Optional[FeishuClient] = None) -> list[dict]:
    """
    从飞书底仓表读取最新持仓。

    底仓表字段 → 内部字段映射：
      标的名称 → name
      标的代码 → code
      资产大类 → asset_class（列表取首元素）
      持仓份额 → shares
      成本均价 → cost
      现价     → latest_price
      结算货币 → currency
      标签     → tags

    Returns:
        [{name, code, asset_class, shares, cost, latest_price, currency, tags, record_id}, ...]
    """
    if client is None:
        client = FeishuClient()

    raw = client.list_records("底仓表")
    portfolio = []

    for rec in raw:
        fields = rec  # fields 已被展平到 record dict 里

        # SDK 返回值：单选字段 → str，多选字段 → list[str]
        asset_class = _unwrap_select(fields.get("资产大类"))
        tags = _unwrap_select_list(fields.get("标签", []))
        currency = _unwrap_select(fields.get("结算货币")) or "CNY"

        # 如果现价为空（新加的标的还没更新过），用成本价兜底
        latest_price = fields.get("现价") or 0.0
        cost = fields.get("成本均价") or 0.0

        portfolio.append({
            "name": fields.get("标的名称", "未知"),
            "code": fields.get("标的代码", ""),
            "asset_class": asset_class,
            "shares": float(fields.get("持仓份额", 0)),
            "cost": float(cost),
            "latest_price": float(latest_price),
            "currency": currency,
            "tags": tags,
            "trend": fields.get("趋势", ""),      # 自动趋势检测（price_updater 写入）
            "record_id": fields.get("_record_id", ""),
        })

    return portfolio


# ═══════════════════════════════════════════════════════════════
# 2. 偏离度计算（核心引擎 —— 逻辑不变）
# ═══════════════════════════════════════════════════════════════

def calculate_rebalance(portfolio: list[dict]) -> dict:
    """按资产大类汇总持仓，计算偏离度。"""
    positions = []
    total_value = 0.0

    for item in portfolio:
        shares = item["shares"]
        cost = item["cost"]
        latest = item["latest_price"]

        market_value = shares * latest
        cost_value = shares * cost
        pnl = market_value - cost_value
        pnl_pct = (pnl / cost_value * 100) if cost_value > 0 else 0.0

        positions.append({
            "name": item["name"],
            "code": item["code"],
            "asset_class": item["asset_class"],
            "shares": shares,
            "cost": cost,
            "latest_price": latest,
            "market_value": round(market_value, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "currency": item.get("currency", "CNY"),
            "tags": item.get("tags", []),
            "record_id": item.get("record_id", ""),
        })
        total_value += market_value

    total_value = round(total_value, 2)

    # 按大类汇总
    class_summary = {}
    for pos in positions:
        cls = pos["asset_class"]
        if cls not in class_summary:
            class_summary[cls] = {"market_value": 0.0, "positions": []}
        class_summary[cls]["market_value"] += pos["market_value"]
        class_summary[cls]["positions"].append(pos)

    # 偏离度分析
    deviation_report = []
    for cls, target_weight in TARGET_WEIGHTS.items():
        actual_value = class_summary.get(cls, {}).get("market_value", 0.0)
        actual_weight = (actual_value / total_value) if total_value > 0 else 0.0
        deviation = actual_weight - target_weight
        positions_in_class = class_summary.get(cls, {}).get("positions", [])
        total_class_pnl = sum(p["pnl"] for p in positions_in_class)

        if deviation > DEVIATION_THRESHOLD:
            status = "超配"
            action_hint = "盈利标的应停止定投、适度止盈"
        elif deviation < -DEVIATION_THRESHOLD:
            status = "低配"
            action_hint = "亏损标的应加大买入"
        else:
            status = "正常"
            action_hint = "维持现有定投节奏"

        deviation_report.append({
            "asset_class": cls,
            "target_weight": target_weight,
            "target_weight_pct": f"{target_weight * 100:.0f}%",
            "actual_value": round(actual_value, 2),
            "actual_weight": round(actual_weight, 6),
            "actual_weight_pct": f"{actual_weight * 100:.2f}%",
            "deviation": round(deviation, 6),
            "deviation_pct": f"{deviation * 100:+.2f}%",
            "status": status,
            "action_hint": action_hint,
            "positions": positions_in_class,
            "total_pnl": round(total_class_pnl, 2),
        })

    return {
        "positions": positions,
        "total_value": total_value,
        "class_summary": {k: round(v["market_value"], 2) for k, v in class_summary.items()},
        "deviation_report": deviation_report,
    }


# ═══════════════════════════════════════════════════════════════
# 3. Prompt 组装 —— 融合「纪律执行 + 情绪安抚 + 大白话」
# ═══════════════════════════════════════════════════════════════

def build_prompt(
    rebalance_data: dict,
    vix_data: dict,
    news_articles: list[dict] | None = None,
    verdict: dict | None = None,
) -> str:
    """组装发送给大模型的上下文 Prompt。

    Args:
        rebalance_data: calculate_rebalance() 的返回结果
        vix_data: market_data.fetch_vix() 的返回结果
        news_articles: news_fetcher.fetch_portfolio_news() 的返回结果（可选）
    """
    report = rebalance_data["deviation_report"]
    positions = rebalance_data["positions"]
    total_value = rebalance_data["total_value"]
    vix = vix_data.get("vix")
    vix_level = vix_data.get("level", "unknown")

    # ── 持仓明细 ──
    position_lines = []
    panic_cases = []  # 需要安抚的标的
    for p in positions:
        tags_str = f" [{', '.join(p['tags'])}]" if p["tags"] else ""
        currency_symbol = {"CNY": "¥", "HKD": "HK$", "USD": "$"}.get(p["currency"], "¥")
        position_lines.append(
            f"  - {p['name']} ({p['code']}){tags_str} | {p['asset_class']} | "
            f"市值 {currency_symbol}{p['market_value']:,.2f} | "
            f"盈亏 {p['pnl']:+,.2f} ({p['pnl_pct']:+.2f}%) | "
            f"份额 {p['shares']} | 成本 {p['cost']} | 现价 {p['latest_price']}"
        )
        # 检测需要安抚的情况
        if p["pnl_pct"] / 100 < PANIC_THRESHOLD:
            panic_cases.append(p)

    # ── 大类偏离度 ──
    deviation_lines = []
    for d in report:
        deviation_lines.append(
            f"  【{d['status']}】{d['asset_class']}："
            f"目标 {d['target_weight_pct']}，实际 {d['actual_weight_pct']}，"
            f"偏离 {d['deviation_pct']}，"
            f"市值 ¥{d['actual_value']:,.2f}，"
            f"该类总盈亏 {d['total_pnl']:+,.2f}"
        )

    # ── 特殊纪律 ──
    special_constraints = []
    for p in positions:
        if "长期底仓" in p.get("tags", []):
            special_constraints.append(
                f"  ⚠️ 「{p['name']}」标记为【长期底仓】，无论浮亏多少，严禁建议割肉，"
                f"只可建议观望或逢低加仓。当前盈亏 {p['pnl']:+,.2f} ({p['pnl_pct']:+.2f}%)"
            )
        if "观察仓" in p.get("tags", []):
            special_constraints.append(
                f"  🔍 「{p['name']}」标记为【观察仓】，仓位较轻，"
                f"可继续持有观察，暂不需大动作。"
            )

    # ── 情绪安抚段落 ──
    comfort_section = ""
    if panic_cases:
        comfort_lines = []
        for p in panic_cases:
            # 按仓位占比判断安抚强度
            weight_in_portfolio = p["market_value"] / total_value * 100 if total_value > 0 else 0
            intensity = "重度" if weight_in_portfolio > 20 else "中度" if weight_in_portfolio > 5 else "轻度"
            comfort_lines.append(
                f"  - {p['name']}：浮亏 {p['pnl_pct']:+.2f}%，占总仓位 {weight_in_portfolio:.1f}%，安抚强度：{intensity}"
            )
        comfort_section = f"""
【💚 情绪安抚要求】
以下标的当前浮亏较大，你需要在报告的「操作建议」部分为这些持有者提供心理安抚：
{chr(10).join(comfort_lines)}

安抚原则：
- 仓位占比大的亏损标的：强调「资产大类配置逻辑未变、长期纪律优于短期波动」
- 仓位占比小的亏损标的：提醒「仓位很轻、即使继续下跌对整体影响有限」
- 禁止使用「别慌」「没事的」等空洞安慰，要用数据说话（如：VIX水平、历史偏离度恢复周期）
- 标有「长期底仓」的标的：强调「本就不该卖，浮亏是定投的好朋友」
"""

    # ── 资讯流 ──
    news_section = ""
    if news_articles:
        news_lines = []
        for a in news_articles[:8]:  # 最多 8 条，控制 prompt 长度
            snippet = (a.get("snippet") or "")[:80]
            news_lines.append(f"  · {a['title']} — {snippet}")
        if news_lines:
            news_section = f"""
【📰 今日要闻】
{chr(10).join(news_lines)}

你要把这些新闻翻译成大白话，结合到你的分析中：
- 如果新闻涉及某类资产（如「纳指下跌」），要在该类资产的偏离度诊断中提到
- 如果某条新闻暗含风险（如「央行加息」「地缘冲突」），要在安抚段落中提醒
- 禁止复制新闻原文，必须用自己的话总结
"""

    # ── 思想钢印：Python 策略中枢的死结论 ──
    steel_seal = ""
    if verdict:
        seal_lines = []
        for s in verdict["signals"]:
            extra = ""
            if s.get("override"):
                # 取覆盖信息的第一行（最关键的那句）
                first_line = s["override"].split("\n")[0][:80]
                extra += f" ⚠️覆盖：{first_line}"
            if s.get("cooldown_status"):
                extra += f" ⏳{s['cooldown_status'][:60]}"
            seal_lines.append(
                f"  · {s['signal_label']} {s['asset_class']}："
                f"偏离 {s['deviation_pct']} → {s['explanation']}{extra}"
            )
        steel_seal = f"""
【⚠️ 系统硬核裁定——你必须完全接受，严禁反驳】

{verdict['command']}

Python 策略中枢逐类判定（不可推翻）：
{chr(10).join(seal_lines)}

【💚 心理防御数据——基于以下事实安抚，不得脱离依据】
{verdict['psyche_facts']}

---
"""

    prompt = f"""{steel_seal}你是一位量化纪律执行官 + 投资心理教练。你有两个身份：

身份一【纪律执行官】：基于上方 Python 系统的硬核裁定，用大白话解释当前的偏离度状况。你无权修改裁定结论。

身份二【心理教练】：当持仓出现浮亏时，用数据和逻辑安抚投资者，帮助坚守纪律。

【核心约束】
1. 严禁推翻、弱化或质疑上方「系统硬核裁定」中的任何结论——那是 Python 算出来的绝对正确结果。
2. 不预测市场趋势，不使用「预计将反弹」「牛市来了」等主观判断。
3. 所有宏观概念（VIX、偏离度、再平衡）必须用大白话解释。
4. 你的工作是翻译 Python 的判定结果，不是重新判定。

【🌐 宏观环境】
- VIX 恐慌指数：{vix if vix is not None else '获取失败'}
- 风险等级：{vix_level}
- 大白话解释：VIX 就是市场的「恐惧温度计」。数字越高 = 大家越害怕。
  · VIX > 30：极度恐慌（别人恐惧我贪婪，是加仓低估值资产的好时机）
  · VIX 20-30：比较紧张（正常定投，不要追高）
  · VIX 15-20：情绪正常（按纪律执行即可）
  · VIX < 15：过度平静（小心，别在市场最乐观的时候冲进去）
{news_section}
【📊 资产大类偏离度稽查】
总市值：¥{total_value:,.2f}

持仓明细：
{chr(10).join(position_lines)}

大类偏离度（红线 ±3%）：
{chr(10).join(deviation_lines)}

【🔴 红线纪律】
- 偏离度 > +3%：该类别下的盈利标的 → 停止定投，适度止盈
- 偏离度 < -3%：该类别下的亏损标的 → 加大买入（别人恐惧我贪婪）
- 偏离度在 ±3% 以内 → 维持节奏
- 固收资产（债券基金等）：波动天然比股票小，偏离度通常会比较稳定。但正因如此，不要因为债基涨得慢就卖掉追股票。固收是压舱石。

【🛡️ 特殊纪律】
{chr(10).join(special_constraints) if special_constraints else '（无特殊约束标的）'}
{comfort_section}
【输出格式】
严格按以下四段式输出：

### 🌐 宏观解读（大白话版）
用 2-3 句大白话解释：当前 VIX 水平意味着什么？现在市场情绪怎么样？
禁用「波动率指数」「隐含波动率」等专业术语。用「恐惧温度计」「市场情绪」等通俗说法。

### 📊 偏离度稽查
以表格列出每个大类：目标权重、实际权重、偏离度、状态、操作方向。
表格后加一行总结：哪个大类该加仓、哪个该止盈。

### 💚 持仓安抚（如有需要）
如果上面标注了需要安抚的标的，针对每只给出基于数据（而非鸡汤）的安抚话术。

### 🎯 操作指令
针对每只持仓，给出明确指令（买入/卖出/持有/观望）+ 操作逻辑 + 参考力度。
严格遵守特殊标签的纪律约束。

直接输出报告，不要前缀后缀。"""

    return prompt


# ═══════════════════════════════════════════════════════════════
# 4. 主控流程
# ═══════════════════════════════════════════════════════════════

def main(vix_override: Optional[float] = None, skip_ai: bool = False):
    """
    主流程：读飞书 → 抓行情 → 算偏离度 → AI 建议 → 打印。

    Args:
        vix_override: 手动指定 VIX 值（测试用）
        skip_ai: 跳过 AI 调用，只算偏离度（离线调试用）
    """
    print("=" * 62)
    print("   🔬 AI 量化投资顾问 —— 资产标签化再平衡系统")
    print("=" * 62)
    print()

    # ── Step 1: 读飞书底仓表 ──
    print("[1/6] 从飞书底仓表读取持仓...")
    client = None
    try:
        client = FeishuClient()
        if not client.is_configured():
            print("      ⚠️  飞书未配置，使用本地 mock 数据")
            portfolio = _get_fallback_portfolio()
        else:
            portfolio = load_portfolio(client)
    except Exception as e:
        print(f"      ⚠️  飞书读取失败 ({e})，使用本地 mock 数据")
        portfolio = _get_fallback_portfolio()

    print(f"      共 {len(portfolio)} 只持仓标的")
    for p in portfolio:
        print(f"        {p['name']} | {p['asset_class']} | "
              f"份额 {p['shares']} | 成本 {p['cost']} | 现价 {p['latest_price']}")
    print()

    # ── Step 2: 宏观情绪 ──
    print("[2/6] 获取宏观情绪指标 (VIX)...")
    vix_data = market_data.fetch_vix()
    if vix_data is None:
        vix_data = {"vix": None, "level": "unknown"}
    if vix_override is not None:
        vix_data["vix"] = vix_override
    if vix_data.get("vix"):
        print(f"      VIX = {vix_data['vix']:.2f}  ({vix_data['level']})")
    else:
        print(f"      VIX 获取失败，将以无 VIX 数据继续")
    print()

    # ── Step 2.5: 资讯搜索 ──
    print("[2.5/6] 搜索相关财经新闻...")
    news_articles = news_fetcher.fetch_portfolio_news(portfolio, max_per_query=2)
    print(f"      获取 {len(news_articles)} 条相关资讯")
    print()

    # ── Step 3: 计算偏离度 ──
    print("[3/6] 计算大类偏离度...")
    rebalance_data = calculate_rebalance(portfolio)
    print(f"      总市值: ¥{rebalance_data['total_value']:,.2f}")
    for d in rebalance_data["deviation_report"]:
        print(f"      {d['asset_class']}: {d['actual_weight_pct']} "
              f"(目标 {d['target_weight_pct']}) 偏离 {d['deviation_pct']}  [{d['status']}]")
    print()

    # ── Step 3.5: 策略中枢硬核判定 ──
    print("[3.5/6] Python 策略中枢硬核判定...")
    try:
        verdict = strategy.judge(portfolio, client=client)
    except Exception:
        verdict = None
    if verdict:
        print(f"      全局判定: {verdict['overall_verdict']}  优先买入: {verdict['priority_target']}")
    print()

    # ── Step 4: AI 分析 ──
    if skip_ai:
        print("[4/6] 跳过 AI 分析（--skip-ai）")
        print()
    else:
        print("[4/6] 调用大模型生成投资建议...")
        prompt = build_prompt(rebalance_data, vix_data, news_articles=news_articles, verdict=verdict)

        if not API_KEY:
            print("      ⚠️  SILICONFLOW_API_KEY 未设置，跳过 AI 分析")
        else:
            try:
                client_ai = OpenAI(api_key=API_KEY, base_url=API_BASE_URL)
                resp = client_ai.chat.completions.create(
                    model=MODEL_NAME,
                    max_tokens=2048,
                    messages=[{"role": "user", "content": prompt}],
                )
                report = resp.choices[0].message.content.strip()
                print()
                print(report)
            except Exception as e:
                print(f"      ❌ AI 调用失败: {e}")

    # ── Step 5: 写回现价（如果有新数据）─
    print()
    print("[6/6] 同花顺基金净值已更新...")
    print("      💡 现价由 price_updater.py 每日自动更新，市值由飞书公式自动重算")

    print()
    print("=" * 62)
    print("   ⚠️ 以上建议由 AI 基于量化规则生成，不构成投资建议")
    print("   投资有风险，操作请结合自身判断")
    print("=" * 62)


def _get_fallback_portfolio() -> list[dict]:
    """当飞书不可用时，用硬编码 mock 数据兜底（开发用）。"""
    return [
        {"name": "标普500场外基金", "code": "096001", "asset_class": "美股资产",
         "shares": 1000, "cost": 1.20, "latest_price": 1.45,
         "currency": "CNY", "tags": [], "trend": "", "record_id": ""},
        {"name": "中证500ETF", "code": "510500", "asset_class": "A股资产",
         "shares": 5000, "cost": 2.50, "latest_price": 2.30,
         "currency": "CNY", "tags": [], "trend": "", "record_id": ""},
        {"name": "小米集团", "code": "01810", "asset_class": "港股资产",
         "shares": 200, "cost": 20.0, "latest_price": 17.5,
         "currency": "HKD", "tags": ["长期底仓"], "trend": "", "record_id": ""},
        {"name": "黄金ETF", "code": "518880", "asset_class": "避险商品",
         "shares": 2000, "cost": 4.80, "latest_price": 5.00,
         "currency": "CNY", "tags": [], "trend": "", "record_id": ""},
    ]


if __name__ == "__main__":
    main()
