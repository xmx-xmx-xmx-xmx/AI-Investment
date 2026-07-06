"""
统一 Prompt 模板 —— D9 核心模块。

所有 LLM 调用点共享以下内容：
  - 投资宪法（公共前置，告诉 LLM 你是谁、什么能做、什么绝对不能做）
  - 输入格式模板（六段式 XML）
  - 思维链指令（强制推理路径，减少废话）
"""

from __future__ import annotations

# ═══════════════════════════════════════════════════════════════
# 投资宪法 —— 所有 LLM 调用的公共前置
# ═══════════════════════════════════════════════════════════════

INVESTMENT_CONSTITUTION = """
<investment_constitution>
## 账户定位
稳健增长型。固收 50% 为压舱石，权益类 50% 争取增长。
允许正常波动，不做短期投机。

## 目标权重
固收 50% / 美股 25% / A股 10% / 港股 5% / 避险商品 10%

## 不可逾越的铁律
1. 长底仓永不卖出（代码锁死）。超配时启用自然稀释：
   停止增投，增量全部转投低配大类，直到偏离回归 ±5% 以内。
   回归后恢复均衡，按权重继续定投（含债基在内的所有低配大类）。

2. 买入必须分批小额定投（每次 100-200 元），禁止一次性梭哈。

3. 左侧下跌时禁止买入（防飞刀），必须等趋势右侧企稳后动手。

## 资金属性
无杠杆、无借贷、无短期套现压力。来源稳定，每周定投节奏可预期。

## 禁止的行为
- 禁止建议重仓押注单一高风险资产
- 禁止建议割肉长底仓标的
- 禁止使用 MACD/均线/布林带等技术术语或玄学术语
- 禁止预测市场方向（"预计反弹""牛市来了"）
- 禁止空洞安抚（"别慌""没事的"）——必须引用真实数据
</investment_constitution>
"""

# ═══════════════════════════════════════════════════════════════
# 思维链指令 —— 所有解读类 prompt 末尾追加
# ═══════════════════════════════════════════════════════════════

CHAIN_OF_THOUGHT = """
<chain_of_thought>
请按以下步骤推理，输出时体现思考过程：

1. 当前大环境基调：VIX 什么水平？宏观日历有什么事件？
   新闻主流方向偏正面还是负面？

2. 逐大类看：每类资产的偏离度、趋势、是否有雷达信号。
   列出关键数据，不止看偏离最大的。

2.5 板块温差：哪些板块在领涨/领跌大盘？
    delta < -2% 且对应持仓低配 → 左侧机会，关注是否企稳。
    delta > +2% 且对应持仓高配 → 警惕追高风险。
    温差最大的 2-3 个板块在结论中明确提及。

3. 交叉判断：新闻/事件对这些持仓有没有直接影响？
   有没有信号和偏离度指向同一个方向（比如低配+雷达信号=高确信度）？
   有没有信号和偏离度方向矛盾（比如高配但雷达信号继续追涨→风险）？

4. 给出结论：综合 1-3，给一个明确的判断。
   如果该动手 → 说动什么、怎么动、力度（如"本周可投 200 元到美股"）。
   如果不动手 → 说清楚为什么不（趋势不对/纪律不允许/数据不足）。
   禁止用"建议关注"这类废话收尾——要么动，要么不动，说清楚原因。
</chain_of_thought>
"""

# ═══════════════════════════════════════════════════════════════
# 六段式输入模板
# ═══════════════════════════════════════════════════════════════

def build_analysis_prompt(
    *,
    role: str,
    holdings_text: str = "",
    macro_text: str = "",
    market_text: str = "",
    news_text: str = "",
    extra_rules: str = "",
    include_constitution: bool = True,
    include_cot: bool = True,
) -> str:
    """组装标准六段式解读 prompt。

    Args:
        role: 系统角色描述（1-2 句）
        holdings_text: 持仓列表/偏离度数据
        macro_text: 宏观日历上下文
        market_text: 市场基准数据（指数/VIX/雷达信号）
        news_text: 待分析资讯（新闻/财报/国际快讯）
        extra_rules: 额外的硬规则（此 prompt 独有）
        include_constitution: 是否前置投资宪法
        include_cot: 是否追加思维链

    Returns:
        完整的 prompt 字符串
    """
    parts = []

    if include_constitution:
        parts.append(INVESTMENT_CONSTITUTION.strip())

    parts.append(f"<system_role>\n{role.strip()}\n</system_role>")

    all_rules = extra_rules.strip() if extra_rules else ""
    if all_rules:
        parts.append(f"<hard_rules>\n{all_rules}\n</hard_rules>")

    if holdings_text.strip():
        parts.append(f"<holdings>\n{holdings_text.strip()}\n</holdings>")

    if macro_text.strip():
        parts.append(f"<macro_calendar>\n{macro_text.strip()}\n</macro_calendar>")

    if market_text.strip():
        parts.append(f"<market_data>\n{market_text.strip()}\n</market_data>")

    if news_text.strip():
        parts.append(f"<news>\n{news_text.strip()}\n</news>")

    if include_cot:
        parts.append(CHAIN_OF_THOUGHT.strip())

    return "\n\n".join(parts)
