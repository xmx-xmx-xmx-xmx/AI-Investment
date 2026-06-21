# D6 雷达观测表 —— 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 飞书新建「雷达观测表」+ `src/radar.py`，对标的高波动卫星标的做双向信号检测（抄底+追涨），每日早间/收盘前简报注入信号+LLM轻度确认。

**Architecture:** `radar.py` 为纯计算模块——读飞书雷达表→逐只抓历史价格→算偏离度/趋势/信号→写回飞书→产出简报文本。`briefing.py` 只加一个函数调用。不碰 `strategy.py` 和 `price_updater.py`。

**Tech Stack:** Python 3.11+, yfinance, akshare, pytest + monkeypatch, lark-oapi (飞书 SDK)

## Global Constraints

- 不新增 pip 依赖（所有库已存在于 pyproject.toml）
- 不动 `price_updater.py` / `strategy.py` / 飞书底仓表
- 雷达信号不参与底仓再平衡，仅参考
- 遵循现有代码风格：Optional/dict 返回、try/except、logger 日志级别
- Use `uv run pytest` for running tests
- 雷达表建在现有投资数据库多维表格（`FEISHU_BITABLE_TOKEN`）下

---

### Task 1: 创建测试文件骨架 + 信号算式纯函数测试

**Files:**
- Create: `tests/test_radar.py`

**Interfaces:**
- Produces: `test_detect_trend`、`test_calc_buy_signal`、`test_calc_chase_signal`（纯函数，无外部依赖）

- [ ] **Step 1: 写入测试文件**

```python
# -*- coding: utf-8 -*-
"""radar.py 单元测试 —— D6 雷达观测表"""

from __future__ import annotations

import pytest


# ═══════════════════════════════════════════════════════════════
# 趋势检测
# ═══════════════════════════════════════════════════════════════

class TestDetectTrend:
    """_detect_trend(prices_5d) 趋势方向判定"""

    def test_right_stabilized(self):
        """最近3天连续上涨 → 右侧企稳"""
        from src.radar import _detect_trend
        assert _detect_trend([10.0, 9.8, 9.5, 9.7, 10.0]) == "右侧企稳"

    def test_left_falling(self):
        """5天前高于今天 → 左侧下跌"""
        from src.radar import _detect_trend
        assert _detect_trend([10.0, 9.8, 9.6, 9.5, 9.3]) == "左侧下跌"
        # 即使最后2天微涨，整体仍左侧
        assert _detect_trend([10.0, 9.5, 9.3, 9.4, 9.35]) == "左侧下跌"

    def test_sideways(self):
        """无明显方向 → 横盘震荡"""
        from src.radar import _detect_trend
        assert _detect_trend([10.0, 10.05, 9.95, 10.02, 10.0]) == "横盘震荡"

    def test_too_few_points(self):
        """不足5天 → 空字符串"""
        from src.radar import _detect_trend
        assert _detect_trend([10.0, 10.5, 10.3, 10.7]) == ""
        assert _detect_trend([]) == ""


# ═══════════════════════════════════════════════════════════════
# 抄底信号
# ═══════════════════════════════════════════════════════════════

class TestCalcBuySignal:
    """_calc_buy_signal(change_10d, change_20d, trend) 抄底信号"""

    def test_attention_10d_minus5_trend_right(self):
        """10日跌超5%且右侧企稳 → 🟡 关注"""
        from src.radar import _calc_buy_signal
        assert _calc_buy_signal(-6.0, -2.0, "右侧企稳") == "🟡 关注"

    def test_reversal_20d_minus8_trend_right(self):
        """20日跌超8%且右侧企稳 → 🔵 底部反转"""
        from src.radar import _calc_buy_signal
        assert _calc_buy_signal(-3.0, -9.0, "右侧企稳") == "🔵 底部反转"

    def test_both_hit_stronger_wins(self):
        """两档同时命中 → 🔵 底部反转优先"""
        from src.radar import _calc_buy_signal
        assert _calc_buy_signal(-7.0, -10.0, "右侧企稳") == "🔵 底部反转"

    def test_trend_not_right_no_signal(self):
        """趋势不满足 → 空白，即使跌幅够"""
        from src.radar import _calc_buy_signal
        assert _calc_buy_signal(-6.0, -9.0, "左侧下跌") == ""
        assert _calc_buy_signal(-6.0, -9.0, "横盘震荡") == ""

    def test_no_signal_insufficient_drop(self):
        """跌幅不够 → 空白"""
        from src.radar import _calc_buy_signal
        assert _calc_buy_signal(-3.0, -5.0, "右侧企稳") == ""

    def test_no_signal_positive(self):
        """上涨中 → 空白"""
        from src.radar import _calc_buy_signal
        assert _calc_buy_signal(+2.0, +5.0, "右侧企稳") == ""

    def test_none_values(self):
        """None 输入（数据不足）→ 空白"""
        from src.radar import _calc_buy_signal
        assert _calc_buy_signal(None, -9.0, "右侧企稳") == ""
        assert _calc_buy_signal(-6.0, None, "右侧企稳") == ""
        assert _calc_buy_signal(-6.0, -9.0, "") == ""


# ═══════════════════════════════════════════════════════════════
# 追涨信号
# ═══════════════════════════════════════════════════════════════

class TestCalcChaseSignal:
    """_calc_chase_signal(daily_changes_5d, close, ma20) 追涨信号"""

    def test_chase_all_positive_within_ma20(self):
        """5日全阳 + 现价在20日线103%内 → 🟢 趋势加速"""
        from src.radar import _calc_chase_signal
        assert _calc_chase_signal(
            [0.5, 1.0, 0.8, 1.2, 0.3], close=102.0, ma20=100.0
        ) == "🟢 趋势加速"

    def test_chase_not_all_positive(self):
        """有阴线 → 空白"""
        from src.radar import _calc_chase_signal
        assert _calc_chase_signal(
            [0.5, -0.2, 0.8, 1.2, 0.3], close=102.0, ma20=100.0
        ) == ""

    def test_chase_too_far_above_ma20(self):
        """现价远超20日线（>103%）→ 空白，已飞"""
        from src.radar import _calc_chase_signal
        assert _calc_chase_signal(
            [0.5, 1.0, 0.8, 1.2, 0.3], close=108.0, ma20=100.0
        ) == ""

    def test_chase_exactly_at_103_boundary(self):
        """恰好 103% → 仍然算有效（刚突破）"""
        from src.radar import _calc_chase_signal
        assert _calc_chase_signal(
            [0.5, 0.5, 0.5, 0.5, 0.5], close=103.0, ma20=100.0
        ) == "🟢 趋势加速"

    def test_chase_too_few_days(self):
        """不足5日数据 → 空白"""
        from src.radar import _calc_chase_signal
        assert _calc_chase_signal([0.5, 1.0], close=102.0, ma20=100.0) == ""

    def test_chase_no_ma20(self):
        """无20日线 → 空白"""
        from src.radar import _calc_chase_signal
        assert _calc_chase_signal([0.5, 1.0, 0.8, 1.2, 0.3], close=102.0, ma20=None) == ""
```

- [ ] **Step 2: 运行确认失败（函数尚未定义）**

```bash
cd /Users/chenyimin/Documents/AI-Investment && uv run pytest tests/test_radar.py::TestDetectTrend::test_right_stabilized -v
```

预期：`FAILED` — `ModuleNotFoundError: No module named 'src.radar'`

- [ ] **Step 3: 提交**

```bash
git add tests/test_radar.py
git commit -m "test: add radar signal + trend calculation tests (expect fail until Task 2)"
```

---

### Task 2: 实现信号算式 + 趋势检测纯函数

**Files:**
- Create: `src/radar.py`（信号和趋势函数 + 模块骨架）
- Test: `tests/test_radar.py`（Task 1 已创建）

**Interfaces:**
- Consumes: 无
- Produces:
  - `_detect_trend(prices_5d: list[float]) -> str` — 返回 "右侧企稳" / "左侧下跌" / "横盘震荡" / ""
  - `_calc_buy_signal(change_10d: float|None, change_20d: float|None, trend: str) -> str` — 返回 "🟡 关注" / "🔵 底部反转" / ""
  - `_calc_chase_signal(daily_changes_5d: list[float], close: float, ma20: float|None) -> str` — 返回 "🟢 趋势加速" / ""

- [ ] **Step 1: 创建 `src/radar.py` 模块骨架并实现三个纯函数**

```python
# -*- coding: utf-8 -*-
"""
雷达观测表 —— 隔离区状态机。

对飞书「雷达观测表」中的高波动卫星标的做双向信号检测
（抄底 + 追涨），每日早间/收盘前简报注入信号。

职责：
- 逐只抓取历史价格（yfinance → akshare 双源 fallback）
- 计算 5/10/20 日涨跌幅 + 趋势 + 20 日均线
- 判定抄底/追涨信号
- 写回飞书雷达表
- 产出简报嵌入文本

用法：
    python -m src.radar              # 扫描全部雷达标的
    python -m src.radar --dry-run    # 只算不写
    python -m src.radar --brief      # 仅产出简报文本
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 信号阈值常量
# ═══════════════════════════════════════════════════════════════

THRESHOLD_BUY_SHORT = -0.05   # 10 日跌超 5% → 🟡 关注
THRESHOLD_BUY_LONG = -0.08    # 20 日跌超 8% → 🔵 底部反转
MA20_BREAK_RATIO = 1.03       # 追涨要求现价 ≤ 20 日线 × 1.03


# ═══════════════════════════════════════════════════════════════
# 趋势检测
# ═══════════════════════════════════════════════════════════════

def _detect_trend(prices_5d: list[float]) -> str:
    """用最近 5 个交易日收盘价判断趋势方向。

    Args:
        prices_5d: 最近 5 日收盘价（按时间升序，prices_5d[-1] = 最新）

    Returns:
        "右侧企稳" / "左侧下跌" / "横盘震荡" / ""
    """
    if len(prices_5d) < 5:
        return ""

    last_3 = prices_5d[-3:]
    if all(last_3[i] < last_3[i + 1] for i in range(2)):
        return "右侧企稳"

    if prices_5d[-1] < prices_5d[0]:
        return "左侧下跌"

    return "横盘震荡"


# ═══════════════════════════════════════════════════════════════
# 信号判定
# ═══════════════════════════════════════════════════════════════

def _calc_buy_signal(
    change_10d: float | None,
    change_20d: float | None,
    trend: str,
) -> str:
    """抄底信号：双窗口 + 双档位。

    🟡 关注：10日跌幅 ≤ -5% AND 趋势="右侧企稳"
    🔵 底部反转：20日跌幅 ≤ -8% AND 趋势="右侧企稳"
    两档同时命中 → 🔵 底部反转优先
    """
    if trend != "右侧企稳":
        return ""

    # 从强到弱判定：🔵 优先
    if change_20d is not None and change_20d <= THRESHOLD_BUY_LONG:
        return "🔵 底部反转"
    if change_10d is not None and change_10d <= THRESHOLD_BUY_SHORT:
        return "🟡 关注"

    return ""


def _calc_chase_signal(
    daily_changes_5d: list[float],
    close: float,
    ma20: float | None,
) -> str:
    """追涨信号：连续阳线 AND 未溢价。

    🟢 趋势加速：近5日每日涨 AND 现价 ≤ 20日线 × 1.03
    """
    if len(daily_changes_5d) < 5:
        return ""
    if ma20 is None:
        return ""
    if not all(c > 0 for c in daily_changes_5d):
        return ""
    if close > ma20 * MA20_BREAK_RATIO:
        return ""

    return "🟢 趋势加速"
```

- [ ] **Step 2: 运行全部纯函数测试**

```bash
cd /Users/chenyimin/Documents/AI-Investment && uv run pytest tests/test_radar.py -v
```

预期：16 个测试全部 PASS

- [ ] **Step 3: 提交**

```bash
git add src/radar.py tests/test_radar.py
git commit -m "feat: add radar signal calculation + trend detection pure functions"
```

---

### Task 3: 实现历史价格抓取 + 资产大类推断

**Files:**
- Modify: `src/radar.py`（追加 `_get_asset_class` + `_fetch_historical_prices`）
- Test: `tests/test_radar.py`（追加测试类）

**Interfaces:**
- Consumes: `price_updater._is_cn_etf_code` / `_is_us_code` / `_is_hk_code` / `_is_fund_code` 的路由逻辑（radar 内重新实现或 import）
- Produces:
  - `_get_asset_class(code: str) -> str` — 返回 "A股" / "美股" / "港股" / "基金" / "未知"
  - `_fetch_historical_prices(code: str, days: int = 25) -> dict | None` — 返回 `{"prices": [float, ...], "changes": [float, ...], "source": "..."}`

- [ ] **Step 1: 先追加测试**

在 `tests/test_radar.py` 末尾追加：

```python

# ═══════════════════════════════════════════════════════════════
# 资产大类推断
# ═══════════════════════════════════════════════════════════════

class TestGetAssetClass:
    """_get_asset_class(code) 代码→资产大类"""

    def test_cn_etf(self):
        from src.radar import _get_asset_class
        assert _get_asset_class("515080") == "A股"
        assert _get_asset_class("159941") == "A股"

    def test_cn_fund(self):
        from src.radar import _get_asset_class
        assert _get_asset_class("017093") == "基金"

    def test_us(self):
        from src.radar import _get_asset_class
        assert _get_asset_class("QQQ") == "美股"
        assert _get_asset_class("MU") == "美股"

    def test_hk(self):
        from src.radar import _get_asset_class
        assert _get_asset_class("00700") == "港股"
        assert _get_asset_class("09988") == "港股"

    def test_unknown(self):
        from src.radar import _get_asset_class
        assert _get_asset_class("") == "未知"
        assert _get_asset_class("??") == "未知"


# ═══════════════════════════════════════════════════════════════
# 历史价格抓取
# ═══════════════════════════════════════════════════════════════

class TestFetchHistoricalPrices:
    """_fetch_historical_prices(code, days) 历史行情"""

    def test_cn_etf_yfinance_success(self, monkeypatch):
        """yfinance 正常返回 → 提取 close + change_pct"""
        import pandas as pd

        def mock_history(self, period="1mo"):
            data = {f"day{i}": float(100 + i) for i in range(25)}
            return pd.DataFrame({
                "Close": list(data.values()),
            })

        import yfinance as yf
        monkeypatch.setattr(yf.Ticker, "history", mock_history)

        from src.radar import _fetch_historical_prices
        result = _fetch_historical_prices("515080", days=25)
        assert result is not None
        assert len(result["prices"]) == 25
        assert result["prices"][0] == 100.0
        assert result["prices"][-1] == 124.0
        assert result["source"] in ("yfinance", "akshare_em", "akshare_sina")

    def test_us_ticker_yfinance_fails_fallback(self, monkeypatch):
        """yfinance 失败 → akshare 兜底"""
        import pandas as pd

        def mock_yf_fail(self, period="1mo"):
            raise RuntimeError("fail")

        def mock_ak_us(symbol, period="daily", adjust=""):
            return pd.DataFrame({
                "收盘": [200.0 + i for i in range(25)],
            })

        import yfinance as yf
        monkeypatch.setattr(yf.Ticker, "history", mock_yf_fail)

        import akshare as ak
        monkeypatch.setattr(ak, "stock_us_hist", mock_ak_us)

        from src.radar import _fetch_historical_prices
        result = _fetch_historical_prices("QQQ", days=25)
        assert result is not None
        assert len(result["prices"]) == 25

    def test_all_sources_fail_returns_none(self, monkeypatch):
        """全部失败 → None"""
        import yfinance as yf
        import akshare as ak
        monkeypatch.setattr(yf.Ticker, "history",
                            lambda self, period="1mo": (_ for _ in ()).throw(RuntimeError("fail")))
        monkeypatch.setattr(ak, "fund_etf_hist_em",
                            lambda symbol, period, adjust: (_ for _ in ()).throw(RuntimeError("fail")))
        monkeypatch.setattr(ak, "fund_etf_hist_sina",
                            lambda symbol: (_ for _ in ()).throw(RuntimeError("fail")))

        from src.radar import _fetch_historical_prices
        result = _fetch_historical_prices("515080", days=25)
        assert result is None

    def test_insufficient_data(self, monkeypatch):
        """返回数据不足要求天数 → None"""
        import pandas as pd

        def mock_short(self, period="1mo"):
            return pd.DataFrame({"Close": [100.0, 101.0]})  # 仅2行

        import yfinance as yf
        monkeypatch.setattr(yf.Ticker, "history", mock_short)

        from src.radar import _fetch_historical_prices
        result = _fetch_historical_prices("QQQ", days=25)
        assert result is None  # 不够 25 天，用已有数据算不了
```

- [ ] **Step 2: 运行确认失败（函数尚未定义）**

```bash
cd /Users/chenyimin/Documents/AI-Investment && uv run pytest tests/test_radar.py::TestGetAssetClass::test_cn_etf -v
```

预期：`FAILED` — `AttributeError`

- [ ] **Step 3: 在 `radar.py` 中实现资产大类推断**

在 `radar.py` 的阈值常量之后追加：

```python

# ═══════════════════════════════════════════════════════════════
# 资产大类推断（复用 price_updater 路由逻辑，不 import 以避免循环依赖）
# ═══════════════════════════════════════════════════════════════

def _get_asset_class(code: str) -> str:
    """根据代码格式推断资产大类。

    Returns:
        "A股" / "美股" / "港股" / "基金" / "未知"
    """
    if not code:
        return "未知"

    # 场内ETF: 51/56/58/159/16 开头
    if code.isdigit() and len(code) == 6:
        if code.startswith(("51", "56", "58", "159", "16")):
            return "A股"
        return "基金"  # 其他 6 位数字 = 场外基金

    # 港股: 5 位数字
    if code.isdigit() and len(code) == 5:
        return "港股"

    # 美股: 纯字母
    if code.isalpha():
        return "美股"

    return "未知"
```

- [ ] **Step 4: 在 `radar.py` 中实现历史价格抓取**

在 `_get_asset_class` 之后追加：

```python

# ═══════════════════════════════════════════════════════════════
# 历史价格抓取（yfinance 主 → akshare 兜底）
# ═══════════════════════════════════════════════════════════════

def _fetch_historical_prices(code: str, days: int = 25) -> dict | None:
    """抓取标的最近 N 个交易日的历史收盘价与日涨跌幅。

    数据源优先级：yfinance → akshare（与 market_data.py 一致）

    Args:
        code: 标的代码
        days: 需要的交易日天数（默认 25，覆盖 20 日窗口 + 缓冲）

    Returns:
        {"prices": [p1, p2, ...], "changes": [c1, c2, ...], "source": "yfinance"}
        失败返回 None。prices 和 changes 长度相等，按时间升序排列。
    """
    asset_class = _get_asset_class(code)

    if asset_class in ("A股", "基金"):
        return _fetch_cn_historical(code, days)
    elif asset_class == "美股":
        return _fetch_us_historical(code, days)
    elif asset_class == "港股":
        return _fetch_hk_historical(code, days)
    else:
        logger.warning("[%s] 无法识别资产大类，跳过", code)
        return None


def _fetch_cn_historical(code: str, days: int) -> dict | None:
    """A 股 ETF/基金历史价格。"""
    # 策略 1: yfinance（国内标的也支持 .SS/.SZ 后缀）
    try:
        import yfinance as yf
        prefix = "sz" if code.startswith(("159", "16")) else "sh"
        ticker = yf.Ticker(f"{code}.{prefix.upper()}" if code.isdigit() and len(code) == 6 else code)
        df = ticker.history(period="1mo")
        if len(df) >= days:
            closes = [float(v) for v in df["Close"].tolist()]
            prevs = [closes[0]] + closes[:-1]
            changes = [round((c - p) / p * 100, 2) if p != 0 else 0.0 for c, p in zip(closes, prevs)]
            return {"prices": closes, "changes": changes, "source": "yfinance"}
    except Exception:
        logger.debug("[%s] yfinance CN 历史失败", code)

    # 策略 2: akshare 东方财富源
    try:
        import akshare as ak
        df = ak.fund_etf_hist_em(symbol=code, period="daily", adjust="")
        if len(df) < days:
            return None
        closes = [float(v) for v in df["收盘"].tolist()[-days:]]
        changes = [float(v) for v in df["涨跌幅"].tolist()[-days:]]
        return {"prices": closes, "changes": changes, "source": "akshare_em"}
    except Exception:
        logger.debug("[%s] akshare_em CN 历史失败", code)

    # 策略 3: akshare 新浪源
    try:
        import akshare as ak
        prefix = "sz" if code.startswith(("159", "16")) else "sh"
        df = ak.fund_etf_hist_sina(symbol=f"{prefix}{code}")
        if len(df) < days:
            return None
        closes = [float(v) for v in df["close"].tolist()[-days:]]
        prevs = [closes[0]] + closes[:-1]
        changes = [round((c - p) / p * 100, 2) if p != 0 else 0.0 for c, p in zip(closes, prevs)]
        return {"prices": closes, "changes": changes, "source": "akshare_sina"}
    except Exception:
        logger.debug("[%s] akshare_sina CN 历史失败", code)

    logger.warning("[%s] 所有 CN 数据源均失败", code)
    return None


def _fetch_us_historical(code: str, days: int) -> dict | None:
    """美股历史价格。"""
    # 策略 1: yfinance
    try:
        import yfinance as yf
        df = yf.Ticker(code).history(period="1mo")
        if len(df) >= days:
            closes = [float(v) for v in df["Close"].tolist()]
            prevs = [closes[0]] + closes[:-1]
            changes = [round((c - p) / p * 100, 2) if p != 0 else 0.0 for c, p in zip(closes, prevs)]
            return {"prices": closes, "changes": changes, "source": "yfinance"}
    except Exception:
        logger.debug("[%s] yfinance US 历史失败", code)

    # 策略 2: akshare
    try:
        import akshare as ak
        df = ak.stock_us_hist(symbol=code, period="daily", adjust="")
        if len(df) < days:
            return None
        closes = [float(v) for v in df["收盘"].tolist()[-days:]]
        prevs = [closes[0]] + closes[:-1]
        changes = [round((c - p) / p * 100, 2) if p != 0 else 0.0 for c, p in zip(closes, prevs)]
        return {"prices": closes, "changes": changes, "source": "akshare_em"}
    except Exception:
        logger.debug("[%s] akshare_em US 历史失败", code)

    logger.warning("[%s] 所有 US 数据源均失败", code)
    return None


def _fetch_hk_historical(code: str, days: int) -> dict | None:
    """港股历史价格。"""
    # 策略 1: akshare 东方财富源
    try:
        import akshare as ak
        df = ak.stock_hk_hist_em(symbol=code, period="daily", adjust="")
        if len(df) < days:
            return None
        closes = [float(v) for v in df["收盘"].tolist()[-days:]]
        prevs = [closes[0]] + closes[:-1]
        changes = [round((c - p) / p * 100, 2) if p != 0 else 0.0 for c, p in zip(closes, prevs)]
        return {"prices": closes, "changes": changes, "source": "akshare_em"}
    except Exception:
        logger.debug("[%s] akshare_em HK 历史失败", code)

    # 策略 2: yfinance 兜底
    try:
        import yfinance as yf
        df = yf.Ticker(f"{code}.HK").history(period="1mo")
        if len(df) >= days:
            closes = [float(v) for v in df["Close"].tolist()]
            prevs = [closes[0]] + closes[:-1]
            changes = [round((c - p) / p * 100, 2) if p != 0 else 0.0 for c, p in zip(closes, prevs)]
            return {"prices": closes, "changes": changes, "source": "yfinance"}
    except Exception:
        logger.debug("[%s] yfinance HK 历史失败", code)

    logger.warning("[%s] 所有 HK 数据源均失败", code)
    return None
```

- [ ] **Step 5: 运行测试**

```bash
cd /Users/chenyimin/Documents/AI-Investment && uv run pytest tests/test_radar.py -v
```

预期：全部 PASS（16 个纯函数 + 5 个 `_get_asset_class` + 4 个 `_fetch_historical_prices` = 25 个测试）

- [ ] **Step 6: 提交**

```bash
git add src/radar.py tests/test_radar.py
git commit -m "feat: add historical price fetching + asset class detection to radar"
```

---

### Task 4: 实现 `scan_radar()` 核心循环

**Files:**
- Modify: `src/radar.py`（追加 `scan_radar` 函数）
- Test: `tests/test_radar.py`（追加集成测试）

**Interfaces:**
- Consumes: `feishu_client.FeishuClient`、Task 2/3 的全部纯函数
- Produces: `scan_radar(client=None, dry_run=False) -> dict` — 返回 `{"scanned": 5, "has_signal": 2, "details": [...]}`

- [ ] **Step 1: 先追加 `scan_radar` 集成测试**

在 `tests/test_radar.py` 末尾追加：

```python

# ═══════════════════════════════════════════════════════════════
# scan_radar 集成测试
# ═══════════════════════════════════════════════════════════════

class TestScanRadar:
    """scan_radar() 核心扫描循环"""

    def test_empty_table(self, monkeypatch):
        """雷达表为空 → 返回空结果"""
        class FakeClient:
            def list_records(self, table_name):
                return []

        monkeypatch.setattr("src.radar.FeishuClient", lambda: FakeClient())

        from src.radar import scan_radar
        result = scan_radar(dry_run=True)
        assert result["scanned"] == 0
        assert result["has_signal"] == 0
        assert result["details"] == []

    def test_single_record_with_signal(self, monkeypatch):
        """有信号的标的 → 返回含信号详情"""
        class FakeClient:
            def list_records(self, table_name):
                return [{
                    "_record_id": "rec_001",
                    "标的代码": "515080",
                    "标的名称": "中证红利ETF",
                    "资产大类": "A股",
                    "关联底仓": "",
                    "现价": 0,
                    "10日涨跌幅%": 0,
                    "20日涨跌幅%": 0,
                    "趋势": "",
                    "抄底信号": "",
                    "追涨信号": "",
                    "入库日期": "",
                }]

        # mock 历史价格：20日跌 10%、近5日连续微涨（趋势企稳但不触发追涨）
        def mock_fetch(code, days=25):
            import time as _time
            _time.sleep(0.01)
            prices = [10.0 + i * 0.05 for i in range(20)]  # 从 10.0 涨到 10.95
            prices[0] = 12.0  # 20天前=12.0，当前≈10.95 → 跌约 9%
            prices[-1] = 10.95
            changes = [round((prices[i] - prices[i-1]) / prices[i-1] * 100, 2) if prices[i-1] != 0 else 0.0
                       for i in range(1, len(prices))]
            changes.insert(0, 0.0)
            # 让最后3天微涨
            prices[-3] = 10.88; prices[-2] = 10.92; prices[-1] = 10.95
            changes[-3] = 0.1; changes[-2] = 0.37; changes[-1] = 0.27
            return {"prices": prices, "changes": changes, "source": "test"}

        monkeypatch.setattr("src.radar._fetch_historical_prices", mock_fetch)
        monkeypatch.setattr("src.radar.FeishuClient", lambda: FakeClient())

        from src.radar import scan_radar
        result = scan_radar(dry_run=True)
        assert result["scanned"] == 1
        assert result["has_signal"] == 1
        assert len(result["details"]) == 1
        assert result["details"][0]["code"] == "515080"
        assert result["details"][0]["buy_signal"] != "" or result["details"][0]["chase_signal"] != ""
        # 写入回写字段
        assert len(result["updates"]) == 1
        assert result["updates"][0]["_record_id"] == "rec_001"
        assert "现价" in result["updates"][0]

    def test_record_without_signal(self, monkeypatch):
        """无信号的标的 → details 有记录但信号为空"""
        class FakeClient:
            def list_records(self, table_name):
                return [{
                    "_record_id": "rec_002",
                    "标的代码": "QQQ",
                    "标的名称": "纳斯达克100",
                    "资产大类": "美股",
                    "关联底仓": "",
                    "现价": 0,
                    "10日涨跌幅%": 0,
                    "20日涨跌幅%": 0,
                    "趋势": "",
                    "抄底信号": "",
                    "追涨信号": "",
                    "入库日期": "",
                }]

        def mock_fetch(code, days=25):
            import time as _time
            _time.sleep(0.01)
            prices = [500.0 + i * 2 for i in range(25)]  # 稳步上涨，无信号
            changes = [round((prices[i] - prices[i-1]) / prices[i-1] * 100, 2) if prices[i-1] != 0 else 0.0
                       for i in range(1, len(prices))]
            changes.insert(0, 0.0)
            return {"prices": prices, "changes": changes, "source": "test"}

        monkeypatch.setattr("src.radar._fetch_historical_prices", mock_fetch)
        monkeypatch.setattr("src.radar.FeishuClient", lambda: FakeClient())

        from src.radar import scan_radar
        result = scan_radar(dry_run=True)
        assert result["scanned"] == 1
        assert result["has_signal"] == 0

    def test_partial_fetch_failure(self, monkeypatch):
        """部分标的抓取失败 → 跳过并继续"""
        fetch_calls = []

        def mock_fetch(code, days=25):
            fetch_calls.append(code)
            if code == "BAD":
                return None
            import time as _time
            _time.sleep(0.01)
            prices = [100.0 + i for i in range(25)]
            changes = [0.5] * 25
            return {"prices": prices, "changes": changes, "source": "test"}

        class FakeClient:
            def list_records(self, table_name):
                return [
                    {"_record_id": "r1", "标的代码": "GOOD", "标的名称": "好标的",
                     "资产大类": "美股", "关联底仓": "", "现价": 0, "10日涨跌幅%": 0,
                     "20日涨跌幅%": 0, "趋势": "", "抄底信号": "", "追涨信号": "", "入库日期": ""},
                    {"_record_id": "r2", "标的代码": "BAD", "标的名称": "坏标的",
                     "资产大类": "美股", "关联底仓": "", "现价": 0, "10日涨跌幅%": 0,
                     "20日涨跌幅%": 0, "趋势": "", "抄底信号": "", "追涨信号": "", "入库日期": ""},
                ]

        monkeypatch.setattr("src.radar._fetch_historical_prices", mock_fetch)
        monkeypatch.setattr("src.radar.FeishuClient", lambda: FakeClient())

        from src.radar import scan_radar
        result = scan_radar(dry_run=True)
        assert result["scanned"] == 1  # 只有 GOOD 被扫了
        assert result["failed"] == 1   # BAD 失败
```

- [ ] **Step 2: 运行确认集成测试失败（`scan_radar` 尚未实现）**

```bash
cd /Users/chenyimin/Documents/AI-Investment && uv run pytest tests/test_radar.py::TestScanRadar::test_empty_table -v
```

预期：`FAILED` — `ImportError` or `AttributeError`

- [ ] **Step 3: 实现 `scan_radar`**

在 `radar.py` 末尾追加：

```python

# ═══════════════════════════════════════════════════════════════
# 核心扫描循环
# ═══════════════════════════════════════════════════════════════

def scan_radar(client: "FeishuClient | None" = None, dry_run: bool = False) -> dict:
    """扫描雷达观测表所有标的，计算信号并写回。

    Args:
        client: 飞书客户端。None 时自动创建。
        dry_run: True 时只算不写飞书。

    Returns:
        {"scanned": 5, "has_signal": 2, "failed": 1,
         "updates": [...], "details": [...], "signal_items": [...]}
    """
    import time as _time

    if client is None:
        from src.feishu_client import FeishuClient
        client = FeishuClient()

    records = client.list_records("雷达观测表")
    if not records:
        logger.info("雷达观测表为空，跳过扫描")
        return {"scanned": 0, "has_signal": 0, "failed": 0,
                "updates": [], "details": [], "signal_items": []}

    logger.info("雷达扫描开始，共 %d 只标的", len(records))

    updates = []
    details = []
    signal_items = []
    scanned = 0
    failed = 0

    for rec in records:
        code = rec.get("标的代码", "")
        name = rec.get("标的名称", "未知")
        record_id = rec.get("_record_id", "")
        linked = rec.get("关联底仓", "")
        entry_date = rec.get("入库日期", "")

        if not record_id or not code:
            logger.warning("[%s] 缺少 _record_id 或标的代码，跳过", name)
            continue

        # 1. 抓取历史价格
        logger.info("  扫描 %s (%s)...", name, code)
        hist = _fetch_historical_prices(code, days=25)
        if hist is None:
            logger.warning("    ❌ %s 历史价格抓取失败", name)
            failed += 1
            details.append({"name": name, "code": code, "status": "failed",
                            "buy_signal": "", "chase_signal": "", "linked": linked})
            continue

        scanned += 1
        prices = hist["prices"]
        changes = hist["changes"]
        close = prices[-1]

        # 2. 计算指标
        # 10 日涨跌幅
        change_10d = None
        if len(prices) >= 11:
            change_10d = round((prices[-1] - prices[-11]) / prices[-11] * 100, 2)

        # 20 日涨跌幅
        change_20d = None
        if len(prices) >= 21:
            change_20d = round((prices[-1] - prices[-21]) / prices[-21] * 100, 2)

        # 趋势（5 日）
        trend = _detect_trend(prices[-5:]) if len(prices) >= 5 else ""

        # 20 日均线
        ma20 = None
        if len(prices) >= 20:
            ma20 = round(sum(prices[-20:]) / 20, 2)

        # 5 日每日涨跌幅
        daily_5d = changes[-5:] if len(changes) >= 5 else []

        # 3. 信号判定
        buy_signal = _calc_buy_signal(change_10d, change_20d, trend)
        chase_signal = _calc_chase_signal(daily_5d, close, ma20)

        has_signal = bool(buy_signal or chase_signal)

        # 4. 入库日期（首次扫描时自动填入）
        if not entry_date:
            from datetime import datetime, timezone, timedelta
            tz_cn = timezone(timedelta(hours=8))
            entry_date = datetime.now(tz_cn).strftime("%Y-%m-%d")

        # 5. 日志
        sig_text = f"  {buy_signal}" if buy_signal else ""
        sig_text += f"  {chase_signal}" if chase_signal else ""
        if sig_text:
            sig_text = f"🔔{sig_text}"
        else:
            sig_text = "➖ 无信号"
        logger.info("    %s 现价=%.2f  10日=%s%%  20日=%s%%  趋势=%s",
                     sig_text, close,
                     f"{change_10d:+.2f}" if change_10d is not None else "N/A",
                     f"{change_20d:+.2f}" if change_20d is not None else "N/A",
                     trend or "N/A")

        # 6. 收集回写
        updates.append({
            "_record_id": record_id,
            "现价": close,
            "10日涨跌幅%": change_10d if change_10d is not None else 0,
            "20日涨跌幅%": change_20d if change_20d is not None else 0,
            "趋势": trend,
            "抄底信号": buy_signal,
            "追涨信号": chase_signal,
            "入库日期": entry_date,
        })

        detail = {
            "name": name, "code": code,
            "close": close,
            "change_10d": change_10d, "change_20d": change_20d,
            "trend": trend,
            "buy_signal": buy_signal, "chase_signal": chase_signal,
            "linked": linked, "status": "ok",
        }
        details.append(detail)
        if has_signal:
            signal_items.append(detail)

        _time.sleep(0.3)

    # 7. 写回飞书
    if dry_run:
        logger.info("[DRY RUN] 将更新 %d 条记录，未实际写入", len(updates))
    elif updates:
        logger.info("写回 %d 条记录到雷达观测表...", len(updates))
        count = client.batch_update_records("雷达观测表", updates)
        logger.info("成功更新 %d 条", count)

    return {
        "scanned": scanned,
        "has_signal": len(signal_items),
        "failed": failed,
        "updates": updates,
        "details": details,
        "signal_items": signal_items,
    }
```

- [ ] **Step 4: 运行集成测试**

```bash
cd /Users/chenyimin/Documents/AI-Investment && uv run pytest tests/test_radar.py::TestScanRadar -v
```

预期：4 个集成测试 PASS

- [ ] **Step 5: 提交**

```bash
git add src/radar.py tests/test_radar.py
git commit -m "feat: add scan_radar() core loop with feishu read→compute→write"
```

---

### Task 5: 飞书表关联 + 命令行入口

**Files:**
- Modify: `src/feishu_client.py` L40-L43（`TABLE_MAP` 新增雷达观测表条目）
- Modify: `src/radar.py`（追加 `main()` 入口）

- [ ] **Step 1: 在 `TABLE_MAP` 中新增雷达观测表**

打开 `src/feishu_client.py`，在 `TABLE_MAP` 字典中追加一行：

```python
TABLE_MAP: Dict[str, str] = {
    "交易流水表": "tblbnD3uaEdohjji",
    "底仓表": "tblpiht8ex94bM6x",
    "雷达观测表": "tblRADAR_PLACEHOLDER",  # 需在飞书后台建表后替换为实际 table_id
}
```

**重要**：`tblRADAR_PLACEHOLDER` 是一个占位符。在实际运行时，你需要先在飞书多维表格后台（同一 `FEISHU_BITABLE_TOKEN` 下）手动新建一张「雷达观测表」，然后将它的 table_id 填入这里。表字段需要建好后通过飞书 UI 创建（字段名与 spec 一致），然后替换占位符。

- [ ] **Step 2: 在 `radar.py` 末尾追加 `main()` 入口**

```python

# ═══════════════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="雷达观测表扫描器")
    parser.add_argument("--dry-run", action="store_true", help="只算不写")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    print()
    print("=" * 56)
    print("   📡 雷达观测表扫描器")
    if args.dry_run:
        print("   [DRY RUN 模式 —— 只读不写]")
    print("=" * 56)
    print()

    result = scan_radar(dry_run=args.dry_run)

    print()
    print("── 扫描结果 ──")
    for d in result["details"]:
        if d["status"] == "failed":
            print(f"  ❌ {d['name']} ({d['code']})  抓取失败")
            continue
        sig = ""
        if d["buy_signal"]:
            sig += f"  {d['buy_signal']}"
        if d["chase_signal"]:
            sig += f"  {d['chase_signal']}"
        if not sig:
            sig = "  ➖ 无信号"
        linked = f"  关联: {d['linked']}" if d.get("linked") else ""
        print(f"  {d['name']} ({d['code']})  现价 {d['close']}{linked}{sig}")
    print()
    print(f"  扫描: {result['scanned']} | 有信号: {result['has_signal']} | 失败: {result['failed']}")
    print("=" * 56)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: 验证 import 正常**

```bash
cd /Users/chenyimin/Documents/AI-Investment && uv run python -c "from src.radar import scan_radar, _detect_trend, _calc_buy_signal, _calc_chase_signal; print('All imports OK')"
```

- [ ] **Step 4: 提交**

```bash
git add src/feishu_client.py src/radar.py
git commit -m "feat: add radar table to feishu TABLE_MAP + CLI entry point"
```

---

### Task 6: 简报注入（`briefing.py` + LLM 轻度确认）

**Files:**
- Modify: `src/briefing.py` L207-L252（`_build_morning`）、L278-L291（`_build_closing`）
- Modify: `src/radar.py`（追加 `build_radar_brief()` + `_radar_insight()`）

**Interfaces:**
- Produces: `build_radar_brief(signal_items: list[dict]) -> str` — 返回可嵌入简报的雷达文本块
- Consumes: `news_fetcher.fetch_all_news` / `macro_calendar.calendar_context_for_prompt`

- [ ] **Step 1: 在 `radar.py` 中追加简报输出函数**

在 `radar.py` 的 `scan_radar()` 之后追加：

```python

# ═══════════════════════════════════════════════════════════════
# 简报产出
# ═══════════════════════════════════════════════════════════════

def build_radar_brief(signal_items: list[dict]) -> str:
    """根据有信号的标的生产简报嵌入文本。

    Returns:
        雷达扫描区块的纯文本，直接嵌入 briefing。
        无信号时返回空字符串。
    """
    if not signal_items:
        return ""

    lines = [f"🔭 **雷达扫描（{len(signal_items)} 有信号）**"]
    for s in signal_items:
        name = s["name"]
        code = s["code"]
        close = s["close"]
        linked = f" | 关联: {s['linked']}" if s.get("linked") else ""

        sig_tags = []
        if s["buy_signal"]:
            sig_tags.append(s["buy_signal"])
        if s["chase_signal"]:
            sig_tags.append(s["chase_signal"])
        tag_line = " ".join(sig_tags)

        detail = ""
        if s["buy_signal"] == "🟡 关注" and s.get("change_10d") is not None:
            detail = f"（10日 {s['change_10d']:+.1f}%）"
        elif s["buy_signal"] == "🔵 底部反转" and s.get("change_20d") is not None:
            detail = f"（20日 {s['change_20d']:+.1f}%）"

        lines.append(f"\n· {name} ({code})")
        lines.append(f"  {tag_line} {detail}| 现 ${close:.2f}{linked}")

    return "\n".join(lines)


def _radar_insight(signal_items: list[dict], news_titles: str,
                   macro_context: str = "") -> str:
    """LLM 轻度确认：对每个有信号标的输出一句判断。

    Args:
        signal_items: 有信号的标的信息列表
        news_titles: 当天要闻标题（空格分隔）
        macro_context: 宏观日历上下文

    Returns:
        LLM 输出文本，每行一个标的。失败返回空字符串。
    """
    if not signal_items:
        return ""

    # 构建标的信息
    item_lines = []
    for s in signal_items:
        sig = s["buy_signal"] or s["chase_signal"]
        linked = f"关联底仓 {s['linked']}" if s.get("linked") else "纯观察"
        item_lines.append(
            f"- {s['name']}({s['code']}) | {sig} | 现价 {s['close']:.2f} | {linked}"
        )
    items_text = "\n".join(item_lines)

    macro_block = ""
    if macro_context:
        macro_block = f"\n<macro_calendar>\n{macro_context}\n</macro_calendar>\n"

    prompt = f"""<system_role>
你是一位量化投资顾问。下面列出了雷达扫描中触发信号的标的。
你的任务是对每个信号给出1句简短确认——结合当天新闻判断这信号有没有基本面支撑。
</system_role>

<hard_rules>
- 每个标的只写 1 句，不超过 40 个字
- 如果新闻对该标的偏利好 → 说信号有支撑
- 如果新闻偏利空或宏观不确定 → 提示谨慎观望
- 如果没有直接相关新闻 → 说纯技术信号
- 输出格式：
  🤖 MU: 隔夜存储芯片利好，追涨信号有基本面支撑
  🤖 159509: 纳指修复中但VIX仍在20+，反弹偏弱可观望
- 直接输出，不要前缀，不要多余解释
</hard_rules>
{macro_block}
<radar_signals>
{items_text}
</radar_signals>

<news context="隔夜/盘间要闻">
{news_titles[:800]}
</news>"""

    try:
        from src.llm import get_llm_client, get_llm_model
        client = get_llm_client()
        if client is None:
            return ""
        resp = client.chat.completions.create(
            model=get_llm_model(), max_tokens=200, temperature=0.3,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.warning("雷达 AI 确认生成失败: %s", str(e)[:100])
        return ""
```

- [ ] **Step 2: 修改 `briefing.py` 的 `_build_morning()`**

在 `_build_morning()` 中（约 L239-240），在 `macro_block` 之后 `insight_block` 之前，插入雷达区块：

找到 `_build_morning` 函数中这一行附近：

```python
    macro_block = f"\n{macro_display}\n" if macro_display else ""
```

在其后追加：

```python
    # ── 雷达扫描 ──
    from src.radar import scan_radar, build_radar_brief, _radar_insight
    radar_result = scan_radar(dry_run=False)
    radar_block = ""
    if radar_result["signal_items"]:
        radar_raw = build_radar_brief(radar_result["signal_items"])
        radar_ai = _radar_insight(
            radar_result["signal_items"], titles_only, macro_prompt,
        )
        radar_ai_block = f"\n{radar_ai}\n" if radar_ai else ""
        radar_block = f"\n{radar_raw}\n{radar_ai_block}" if radar_raw else ""
```

- [ ] **Step 3: 修改 `briefing.py` 的 `_build_closing()`**

同样在 `_build_closing()` 中（约 L289，news_block 之后），追加相同的雷达区块代码。

- [ ] **Step 4: 运行全部测试确认无回归**

```bash
cd /Users/chenyimin/Documents/AI-Investment && uv run pytest tests/ -v
```

预期：全部 PASS

- [ ] **Step 5: 提交**

```bash
git add src/radar.py src/briefing.py
git commit -m "feat: inject radar scan into morning + closing briefings with LLM light confirmation"
```

---

### Task 7: 最终验证 + 更新 TODO.md

- [ ] **Step 1: 运行全部测试**

```bash
cd /Users/chenyimin/Documents/AI-Investment && uv run pytest tests/ -v
```

预期：全部 PASS

- [ ] **Step 2: 手动验证所有函数可导入**

```bash
cd /Users/chenyimin/Documents/AI-Investment && uv run python -c "
from src.radar import scan_radar, _detect_trend, _calc_buy_signal, _calc_chase_signal
from src.radar import _get_asset_class, _fetch_historical_prices, build_radar_brief, _radar_insight
print('All imports OK')
"
```

- [ ] **Step 3: `python -m src.radar --dry-run` 语法检查**

```bash
cd /Users/chenyimin/Documents/AI-Investment && uv run python -m src.radar --dry-run 2>&1 | head -20
```

注意：此命令会实际调用飞书 API（读雷达表），如果雷达表为空会打印 "雷达观测表为空"。

- [ ] **Step 4: 更新 TODO.md 标记 D6 完成**

将：
```markdown
- [ ] **D6. 雷达观测表（隔离区状态机）**
```

改为：
```markdown
- [x] **D6. 雷达观测表（隔离区状态机）**
```

同时在可用命令区追加：

```bash
python -m src.radar --dry-run     # 只算不写
python -m src.radar               # 完整扫描 + 写回飞书
```

- [ ] **Step 5: 提交**

```bash
git add TODO.md
git commit -m "docs: mark D6 radar as complete"
```

---

## 飞书表建表说明

在 Task 5 完成后、Task 6 跑通之前，需要在飞书后台手动建表：

1. 打开飞书 → 进入投资数据库多维表格
2. 新建一张表，命名为「雷达观测表」
3. 创建以下字段（字段名必须与代码中的 key 严格一致）：

| 字段名 | 类型 | 说明 |
|--------|------|------|
| 标的代码 | 文本 | 手工填写 |
| 标的名称 | 文本 | 手工填写 |
| 资产大类 | 单选 | 选项：A股、港股、美股、基金 |
| 关联底仓 | 文本 | 可选 |
| 现价 | 数字 | 系统自动填 |
| 10日涨跌幅% | 数字 | 系统自动填 |
| 20日涨跌幅% | 数字 | 系统自动填 |
| 趋势 | 文本 | 系统自动填 |
| 抄底信号 | 文本 | 系统自动填 |
| 追涨信号 | 文本 | 系统自动填 |
| 入库日期 | 文本 | 系统自动填 |
| 状态 | 单选 | 选项：观察中、有信号、已入场、已放弃 |

4. 复制表 ID（飞书 URL 中 `table/` 后的部分），替换 `feishu_client.py` 中 `TABLE_MAP["雷达观测表"]` 的占位符值。

---

## 变更文件汇总

| 文件 | 操作 | Tasks |
|------|------|-------|
| `src/radar.py` | 新建 | Task 2, 3, 4, 5, 6 |
| `tests/test_radar.py` | 新建 | Task 1, 3, 4 |
| `src/feishu_client.py` | 修改 | Task 5 |
| `src/briefing.py` | 修改 | Task 6 |
| `TODO.md` | 修改 | Task 7 |
