# D1 全球行情直连 —— 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `src/market_data.py` 新增美股三大指数和美债收益率抓取函数，精简 VIX 数据源，不修改任何消费端。

**Architecture:** 所有新函数遵循 `market_data.py` 现有模式 —— 独立函数、多源 fallback、统一返回 `Optional[dict]`。yfinance 为海外主源，akshare 为国内兜底。

**Tech Stack:** Python 3.11+, yfinance, akshare, pytest + monkeypatch

## Global Constraints

- 不新增 pip 依赖（yfinance、akshare 已存在于 pyproject.toml）
- 不动消费端代码（price_updater.py、briefing.py、advisor.py、notify.py）
- 不动飞书表结构
- 遵循现有代码风格：`Optional[dict]` 返回、fallback 链、logger 日志级别
- 所有函数失败返回 `None`，不抛异常

---

### Task 1: 创建测试文件骨架 + `fetch_us_index` 全部失败测试

**Files:**
- Create: `tests/test_market_data.py`

**Interfaces:**
- Produces: `test_fetch_us_index_all_sources_fail`（依赖 Task 2 实现 `fetch_us_index` 后通过）

- [ ] **Step 1: 创建测试文件并写入 `fetch_us_index` 全部失败的测试**

```python
# -*- coding: utf-8 -*-
"""market_data.py 单元测试 —— D1 全球行情直连"""

from __future__ import annotations

import pytest
import pandas as pd

# 将要测试的模块（Task 2 实现后 import 成功）
# from src import market_data


class TestFetchUsIndex:
    """fetch_us_index() 美股三大指数抓取"""

    def test_all_sources_fail_returns_none(self, monkeypatch):
        """yfinance 和 akshare 均失败时返回 None"""
        # 模拟 yfinance Ticker.history 抛异常
        def mock_yf_history_fail(self, period="5d"):
            raise RuntimeError("yfinance network error")

        # 模拟 akshare index_us_stock_sina 抛异常
        def mock_ak_sina_fail(symbol):
            raise RuntimeError("akshare network error")

        # 注入 mock
        import yfinance as yf
        monkeypatch.setattr(yf.Ticker, "history", mock_yf_history_fail)

        import akshare as ak
        monkeypatch.setattr(ak, "index_us_stock_sina", mock_ak_sina_fail)

        from src import market_data
        result = market_data.fetch_us_index("^DJI")
        assert result is None
```

- [ ] **Step 2: 运行测试确认失败（函数尚未定义）**

```bash
cd /Users/chenyimin/Documents/AI-Investment && uv run pytest tests/test_market_data.py::TestFetchUsIndex::test_all_sources_fail_returns_none -v
```

预期：`FAILED` — `AttributeError: module 'src.market_data' has no attribute 'fetch_us_index'`

- [ ] **Step 3: 提交骨架**

```bash
git add tests/test_market_data.py
git commit -m "test: add fetch_us_index failure test (expect fail until Task 2)"
```

---

### Task 2: 实现 `fetch_us_index` + `US_INDEX_MAP` + `fetch_us_indices`

**Files:**
- Modify: `src/market_data.py`（在 L66-L74 US_ETF_MAP 之后追加注册表；在 L233 `fetch_us_etfs` 之后追加新函数）
- Test: `tests/test_market_data.py`（追加更多测试）

**Interfaces:**
- Consumes: 无
- Produces:
  - `US_INDEX_MAP: dict[str, str]` — `{"^DJI": "道琼斯工业指数", "^GSPC": "标普500指数", "^IXIC": "纳斯达克综合指数"}`
  - `fetch_us_index(ticker: str) -> Optional[dict]` — 返回 `{"ticker", "name", "market", "close", "change_pct", "source"}`
  - `fetch_us_indices(tickers: list[str]) -> list[dict]` — 批量版

- [ ] **Step 1: 在 `market_data.py` 中添加 `US_INDEX_MAP` 注册表**

在 L74（`US_ETF_MAP` 闭合 `}` 后）追加：

```python

US_INDEX_MAP = {
    "^DJI":  "道琼斯工业指数",
    "^GSPC": "标普500指数",
    "^IXIC": "纳斯达克综合指数",
}

# akshare sina 源符号映射（去掉 ^ 前缀，sina 用 . 前缀）
_AKSHARE_INDEX_SYMBOL = {
    "^DJI":  ".DJI",
    "^GSPC": ".INX",
    "^IXIC": ".IXIC",
}
```

- [ ] **Step 2: 在 `fetch_us_etfs()` 之后添加 `fetch_us_index()` 函数**

在 L233（`fetch_us_etfs` return 后）追加：

```python

# ═══════════════════════════════════════════════════════════════
# 美股三大指数（yfinance 优先 → akshare sina 兜底）
# ═══════════════════════════════════════════════════════════════

def fetch_us_index(ticker: str) -> Optional[dict]:
    """抓取单只美股指数最新行情。

    数据源优先级：yfinance → akshare index_us_stock_sina

    Args:
        ticker: 美股指数代码，如 "^DJI"

    Returns:
        {"ticker": "^DJI", "name": "道琼斯工业指数", "market": "美股",
         "close": 42000.0, "change_pct": +0.35, "source": "yfinance"}
        失败返回 None
    """
    name = US_INDEX_MAP.get(ticker, ticker)
    ak_symbol = _AKSHARE_INDEX_SYMBOL.get(ticker, ticker)

    # 策略 1: yfinance（海外网络直连）
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        df = t.history(period="5d")
        if len(df) >= 2:
            prev = float(df["Close"].iloc[-2])
            today = float(df["Close"].iloc[-1])
            return {
                "ticker": ticker, "name": name, "market": "美股",
                "close": round(today, 2),
                "change_pct": round((today - prev) / prev * 100, 2),
                "source": "yfinance",
            }
    except Exception:
        logger.debug("[%s] yfinance 源失败", ticker)

    # 策略 2: akshare 新浪源（国内可用）
    try:
        import akshare as ak
        df = ak.index_us_stock_sina(symbol=ak_symbol)
        if len(df) >= 2:
            prev = float(df["close"].iloc[-2])
            today = float(df["close"].iloc[-1])
            return {
                "ticker": ticker, "name": name, "market": "美股",
                "close": round(today, 2),
                "change_pct": round((today - prev) / prev * 100, 2),
                "source": "akshare_sina",
            }
    except Exception:
        logger.debug("[%s] akshare_sina 源失败", ticker)

    logger.warning(f"[{ticker}] {name} 所有数据源均失败")
    return None


def fetch_us_indices(tickers: list[str]) -> list[dict]:
    """批量抓取多只美股指数。"""
    results = []
    for t in tickers:
        data = fetch_us_index(t)
        if data:
            results.append(data)
        time.sleep(0.2)
    return results
```

- [ ] **Step 3: 追加 `fetch_us_index` 正常返回 + fallback 测试**

在 `tests/test_market_data.py` 的 `TestFetchUsIndex` 类中追加：

```python

    def test_yfinance_success_returns_data(self, monkeypatch):
        """yfinance 正常返回时直接使用"""
        import pandas as pd

        def mock_history(self, period="5d"):
            return pd.DataFrame({
                "Close": [100.0, 101.0, 102.0, 103.0, 104.0],
            })

        import yfinance as yf
        monkeypatch.setattr(yf.Ticker, "history", mock_history)

        from src import market_data
        result = market_data.fetch_us_index("^DJI")
        assert result is not None
        assert result["ticker"] == "^DJI"
        assert result["name"] == "道琼斯工业指数"
        assert result["market"] == "美股"
        assert result["close"] == 104.0
        assert result["change_pct"] == pytest.approx(0.9708, rel=1e-2)  # (104-103)/103*100
        assert result["source"] == "yfinance"

    def test_yfinance_fails_falls_back_to_akshare(self, monkeypatch):
        """yfinance 失败时回退到 akshare sina 源"""
        import pandas as pd

        def mock_yf_history_fail(self, period="5d"):
            raise RuntimeError("fail")

        def mock_ak_sina(symbol):
            return pd.DataFrame({
                "close": [200.0, 201.0, 202.0, 203.0, 204.0],
            })

        import yfinance as yf
        monkeypatch.setattr(yf.Ticker, "history", mock_yf_history_fail)

        import akshare as ak
        monkeypatch.setattr(ak, "index_us_stock_sina", mock_ak_sina)

        from src import market_data
        result = market_data.fetch_us_index("^DJI")
        assert result is not None
        assert result["close"] == 204.0
        assert result["source"] == "akshare_sina"

    def test_insufficient_data_returns_none(self, monkeypatch):
        """不够 2 行数据时返回 None"""
        import pandas as pd

        def mock_history_one_row(self, period="5d"):
            return pd.DataFrame({"Close": [100.0]})

        import yfinance as yf
        monkeypatch.setattr(yf.Ticker, "history", mock_history_one_row)

        from src import market_data
        result = market_data.fetch_us_index("^DJI")
        assert result is None
```

- [ ] **Step 4: 运行所有 `fetch_us_index` 相关测试**

```bash
cd /Users/chenyimin/Documents/AI-Investment && uv run pytest tests/test_market_data.py::TestFetchUsIndex -v
```

预期：4 个测试全部 PASS

- [ ] **Step 5: 追加 `fetch_us_indices` 批量测试**

在 `tests/test_market_data.py` 中追加新类：

```python

class TestFetchUsIndices:
    """fetch_us_indices() 批量抓取"""

    def test_batch_success_all(self, monkeypatch):
        """全部成功返回完整列表"""
        import pandas as pd

        called_symbols = []

        def mock_history(self, period="5d"):
            # 通过 ticker 区分返回
            called_symbols.append(self.ticker)
            return pd.DataFrame({"Close": [100.0, 101.0]})

        import yfinance as yf
        monkeypatch.setattr(yf.Ticker, "history", mock_history)
        # 让 Ticker 构造器记录 ticker
        original_init = yf.Ticker.__init__

        def mock_init(self, ticker):
            self.ticker = ticker
            original_init(self, ticker)

        monkeypatch.setattr(yf.Ticker, "__init__", mock_init)

        from src import market_data
        results = market_data.fetch_us_indices(["^DJI", "^GSPC", "^IXIC"])
        assert len(results) == 3
        assert all(r["close"] == 101.0 for r in results)

    def test_batch_partial_failure(self, monkeypatch):
        """部分失败不影响其他"""
        import pandas as pd

        def mock_history(self, period="5d"):
            if self.ticker == "^GSPC":
                raise RuntimeError("fail")
            return pd.DataFrame({"Close": [100.0, 101.0]})

        import yfinance as yf
        monkeypatch.setattr(yf.Ticker, "history", mock_history)
        original_init = yf.Ticker.__init__

        def mock_init(self, ticker):
            self.ticker = ticker
            original_init(self, ticker)

        monkeypatch.setattr(yf.Ticker, "__init__", mock_init)

        from src import market_data
        results = market_data.fetch_us_indices(["^DJI", "^GSPC", "^IXIC"])
        assert len(results) == 2  # ^GSPC 失败被跳过

    def test_all_fail_returns_empty(self, monkeypatch):
        """全部失败返回空列表"""
        def mock_history_all_fail(self, period="5d"):
            raise RuntimeError("fail")

        import yfinance as yf
        monkeypatch.setattr(yf.Ticker, "history", mock_history_all_fail)

        # 也 mock akshare 失败
        import akshare as ak
        monkeypatch.setattr(ak, "index_us_stock_sina", lambda symbol: (_ for _ in ()).throw(RuntimeError("fail")))

        from src import market_data
        results = market_data.fetch_us_indices(["^DJI", "^GSPC"])
        assert results == []
```

- [ ] **Step 6: 运行批量测试**

```bash
cd /Users/chenyimin/Documents/AI-Investment && uv run pytest tests/test_market_data.py::TestFetchUsIndices -v
```

预期：3 个测试全部 PASS

- [ ] **Step 7: 提交**

```bash
git add src/market_data.py tests/test_market_data.py
git commit -m "feat: add fetch_us_index + fetch_us_indices for US major indices"
```

---

### Task 3: 实现 `fetch_us_treasury()` + 测试

**Files:**
- Modify: `src/market_data.py`（在 Task 2 新增代码之后追加）
- Test: `tests/test_market_data.py`（追加测试类）

**Interfaces:**
- Consumes: 无
- Produces: `fetch_us_treasury() -> Optional[dict]` — 返回 `{"date", "us_2y", "us_10y", "us_10y2y_spread", "source"}`

- [ ] **Step 1: 先追加 `fetch_us_treasury` 的失败测试**

在 `tests/test_market_data.py` 末尾追加：

```python

class TestFetchUsTreasury:
    """fetch_us_treasury() 美债收益率"""

    def test_all_sources_fail_returns_none(self, monkeypatch):
        """akshare 异常时返回 None"""
        import akshare as ak
        monkeypatch.setattr(ak, "bond_zh_us_rate", lambda: (_ for _ in ()).throw(RuntimeError("fail")))

        from src import market_data
        result = market_data.fetch_us_treasury()
        assert result is None
```

- [ ] **Step 2: 运行确认失败**

```bash
cd /Users/chenyimin/Documents/AI-Investment && uv run pytest tests/test_market_data.py::TestFetchUsTreasury::test_all_sources_fail_returns_none -v
```

预期：`FAILED` — `AttributeError: module 'src.market_data' has no attribute 'fetch_us_treasury'`

- [ ] **Step 3: 在 `market_data.py` 的 `fetch_us_indices` 之后追加实现**

```python

# ═══════════════════════════════════════════════════════════════
# 美债收益率（akshare 单源）
# ═══════════════════════════════════════════════════════════════

def fetch_us_treasury() -> Optional[dict]:
    """抓取最新美国国债收益率（2Y / 10Y / 10Y-2Y 利差）。

    数据源：akshare bond_zh_us_rate（中美债券收益率全期限表）

    Returns:
        {"date": "2026-06-18", "us_2y": 4.19, "us_10y": 4.46,
         "us_10y2y_spread": 0.27, "source": "akshare_bond"}
        失败返回 None
    """
    _ensure_no_proxy()

    try:
        import akshare as ak
        df = ak.bond_zh_us_rate()
        if df.empty:
            logger.warning("美债收益率数据为空")
            return None

        last = df.iloc[-1]
        return {
            "date": str(last["日期"]),
            "us_2y": float(last["美国国债收益率2年"]),
            "us_10y": float(last["美国国债收益率10年"]),
            "us_10y2y_spread": float(last["美国国债收益率10年-2年"]),
            "source": "akshare_bond",
        }
    except Exception as e:
        logger.warning("美债收益率获取失败: %s", e)
        return None
```

- [ ] **Step 4: 追加正常返回测试**

在 `TestFetchUsTreasury` 类中追加：

```python

    def test_success_returns_data(self, monkeypatch):
        """正常返回完整数据结构"""
        import pandas as pd

        def mock_bond_rate():
            return pd.DataFrame([{
                "日期": "2026-06-18",
                "美国国债收益率2年": 4.19,
                "美国国债收益率10年": 4.46,
                "美国国债收益率10年-2年": 0.27,
            }])

        import akshare as ak
        monkeypatch.setattr(ak, "bond_zh_us_rate", mock_bond_rate)

        from src import market_data
        result = market_data.fetch_us_treasury()
        assert result is not None
        assert result["date"] == "2026-06-18"
        assert result["us_2y"] == 4.19
        assert result["us_10y"] == 4.46
        assert result["us_10y2y_spread"] == 0.27
        assert result["source"] == "akshare_bond"

    def test_empty_dataframe_returns_none(self, monkeypatch):
        """空 DataFrame 返回 None"""
        import pandas as pd

        import akshare as ak
        monkeypatch.setattr(ak, "bond_zh_us_rate", lambda: pd.DataFrame())

        from src import market_data
        result = market_data.fetch_us_treasury()
        assert result is None
```

- [ ] **Step 5: 运行测试**

```bash
cd /Users/chenyimin/Documents/AI-Investment && uv run pytest tests/test_market_data.py::TestFetchUsTreasury -v
```

预期：3 个测试全部 PASS

- [ ] **Step 6: 提交**

```bash
git add src/market_data.py tests/test_market_data.py
git commit -m "feat: add fetch_us_treasury for US bond yields (2Y/10Y/spread)"
```

---

### Task 4: 精简 `fetch_vix()` — 删除 Yahoo v8 API 直连层

**Files:**
- Modify: `src/market_data.py` L252-L296（`fetch_vix` 函数体）
- Test: `tests/test_market_data.py`（追加测试类）

**Interfaces:**
- Consumes: 现有 `fetch_vix()` 签名不变
- Produces: 返回结构不变（`{"vix", "level", "source"}`），仅数据源从三变二

- [ ] **Step 1: 先追加 VIX 新版行为测试**

在 `tests/test_market_data.py` 末尾追加：

```python

class TestFetchVix:
    """fetch_vix() VIX 恐慌指数（D1 精简后双源 fallback）"""

    def test_yfinance_primary_source(self, monkeypatch):
        """yfinance 为第一优先源"""
        import pandas as pd

        def mock_history(self, period="5d"):
            return pd.DataFrame({"Close": [18.0, 19.0, 20.0, 21.0, 22.0]})

        import yfinance as yf
        monkeypatch.setattr(yf.Ticker, "history", mock_history)

        from src import market_data
        result = market_data.fetch_vix()
        assert result is not None
        assert result["vix"] == 22.0
        assert result["level"] == "谨慎"  # VIX 22.0 → 20-25 区间
        assert result["source"] == "yfinance"

    def test_yfinance_fails_falls_back_to_akshare(self, monkeypatch):
        """yfinance 失败回退到 akshare"""
        import pandas as pd

        def mock_yf_fail(self, period="5d"):
            raise RuntimeError("fail")

        def mock_ak_index(symbol):
            return pd.DataFrame({"收盘": [15.0, 16.0, 17.0, 18.0, 19.0]})

        import yfinance as yf
        monkeypatch.setattr(yf.Ticker, "history", mock_yf_fail)

        import akshare as ak
        monkeypatch.setattr(ak, "index_global_hist_em", mock_ak_index)

        from src import market_data
        result = market_data.fetch_vix()
        assert result is not None
        assert result["vix"] == 19.0
        assert result["source"] == "akshare_em"

    def test_all_sources_fail_returns_none(self, monkeypatch):
        """全部失败返回 None"""
        def mock_all_fail(self, period="5d"):
            raise RuntimeError("fail")

        import yfinance as yf
        monkeypatch.setattr(yf.Ticker, "history", mock_all_fail)

        import akshare as ak
        monkeypatch.setattr(ak, "index_global_hist_em",
                            lambda symbol: (_ for _ in ()).throw(RuntimeError("fail")))

        from src import market_data
        result = market_data.fetch_vix()
        assert result is None

    def test_vix_level_boundaries(self):
        """测试 _vix_level 分级边界（间接通过 fetch_vix 验证）"""
        from src.market_data import _vix_level
        assert _vix_level(35.0) == "极度恐慌"
        assert _vix_level(30.0) == "极度恐慌"
        assert _vix_level(25.0) == "恐慌"
        assert _vix_level(20.0) == "谨慎"
        assert _vix_level(15.0) == "正常"
        assert _vix_level(10.0) == "极度平静"
```

- [ ] **Step 2: 运行确认新测试失败（新版 VIX 尚未实现）**

```bash
cd /Users/chenyimin/Documents/AI-Investment && uv run pytest tests/test_market_data.py::TestFetchVix::test_yfinance_primary_source -v
```

预期：PASS（因为 yfinance 第一源已在旧版中存在，但不排除因新 mock 导致的导入问题）——如果失败也没关系，Step 3 会修复。

- [ ] **Step 3: 修改 `fetch_vix()` — 删除 Yahoo v8 API 层，yfinance 提为第一源**

打开 `src/market_data.py`，找到 `def fetch_vix() -> Optional[dict]:`（约 L252）。

**删除** 策略 1 整个代码块（Yahoo v8 API 直连，原 L261-L271）：
```python
    # 策略 1: Yahoo Finance v8 chart API（直连，国内可用）
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?interval=1d&range=5d"
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        data = resp.json()
        result = data["chart"]["result"][0]
        quotes = result["indicators"]["quote"][0]["close"]
        valid = [(ts, q) for ts, q in zip(result["timestamp"], quotes) if q is not None]
        if valid:
            vix = round(float(valid[-1][1]), 2)
            return {"vix": vix, "level": _vix_level(vix), "source": "yahoo_api"}
    except Exception:
        logger.debug("VIX yahoo_api 源失败")
```

**将原策略 2（yfinance）的注释改为策略 1**，但保留代码不变：
```python
    # 策略 1: yfinance（海外网络直连）
    try:
        ...
        return {"vix": vix, "level": _vix_level(vix), "source": "yfinance"}
    except Exception:
        logger.debug("VIX yfinance 源失败")
```

**将原策略 3（akshare）的注释改为策略 2**，但保留代码不变：
```python
    # 策略 2: akshare 全球指数（东方财富源）
    try:
        ...
        return {"vix": vix, "level": _vix_level(vix), "source": "akshare_em"}
    except Exception:
        logger.debug("VIX akshare_em 源失败")
```

同时更新函数 docstring 中的 `三源` → `双源`。

- [ ] **Step 4: 运行 VIX 相关测试**

```bash
cd /Users/chenyimin/Documents/AI-Investment && uv run pytest tests/test_market_data.py::TestFetchVix -v
```

预期：4 个测试全部 PASS

- [ ] **Step 5: 检查 `import requests` 是否还在其他地方使用**

在 `market_data.py` 中确认：删掉 Yahoo v8 API 代码块后，`import requests` 仅剩 VIX 原策略 1 使用。现在 VIX 不再直连 Yahoo API，如果没有其他地方用 `requests`，应移除该 import。

检查：`fetch_cn_etf`、`fetch_us_etf`、`fetch_hk_stock`、`fetch_us_index`、`fetch_us_treasury` 均不 import requests。确认后删除 L38 的 `import requests`。

- [ ] **Step 6: 运行全部测试确认无回归**

```bash
cd /Users/chenyimin/Documents/AI-Investment && uv run pytest tests/test_market_data.py -v
```

预期：全部 PASS（~14 个测试）

- [ ] **Step 7: 提交**

```bash
git add src/market_data.py tests/test_market_data.py
git commit -m "refactor: simplify fetch_vix to dual-source (yfinance→akshare), remove Yahoo v8 API"
```

---

### Task 5: 更新 `snapshot()` + 测试

**Files:**
- Modify: `src/market_data.py` L396-L417（`snapshot` 函数体）
- Test: `tests/test_market_data.py`（追加测试类）

**Interfaces:**
- Consumes: `fetch_us_indices`, `fetch_us_treasury`（Task 2, 3 产出）
- Produces: `snapshot() -> dict` — 新增 `us_indices` 和 `us_treasury` 两个 key

- [ ] **Step 1: 追加 `snapshot` 测试**

在 `tests/test_market_data.py` 末尾追加：

```python

class TestSnapshot:
    """snapshot() 一键快照（D1 新增字段）"""

    def test_new_fields_present(self, monkeypatch):
        """确认 snapshot 返回中包含 us_indices 和 us_treasury"""
        import pandas as pd

        # Mock 所有外部数据源，让 snapshot 顺利跑完
        def mock_cn_etf(code):
            import time as _time
            _time.sleep(0.01)
            return {"code": code, "name": "test", "market": "A股",
                    "close": 1.0, "change_pct": 0.0, "source": "test"}

        def mock_us_etf(ticker):
            import time as _time
            _time.sleep(0.01)
            return {"ticker": ticker, "name": "test", "market": "美股",
                    "close": 100.0, "change_pct": 0.0, "source": "test"}

        def mock_hk_stock(code):
            import time as _time
            _time.sleep(0.01)
            return {"code": code, "name": "test", "market": "港股",
                    "close": 50.0, "change_pct": 0.0, "source": "test"}

        def mock_vix():
            return {"vix": 20.0, "level": "谨慎", "source": "test"}

        def mock_us_index(ticker):
            import time as _time
            _time.sleep(0.01)
            return {"ticker": ticker, "name": "test", "market": "美股",
                    "close": 5000.0, "change_pct": 0.0, "source": "test"}

        def mock_treasury():
            return {"date": "2026-06-18", "us_2y": 4.0, "us_10y": 4.5,
                    "us_10y2y_spread": 0.5, "source": "test"}

        monkeypatch.setattr("src.market_data.fetch_cn_etf", mock_cn_etf)
        monkeypatch.setattr("src.market_data.fetch_cn_etfs",
                            lambda codes: [mock_cn_etf(c) for c in codes])
        monkeypatch.setattr("src.market_data.fetch_us_etf", mock_us_etf)
        monkeypatch.setattr("src.market_data.fetch_us_etfs",
                            lambda tickers: [mock_us_etf(t) for t in tickers])
        monkeypatch.setattr("src.market_data.fetch_hk_stock", mock_hk_stock)
        monkeypatch.setattr("src.market_data.fetch_hk_stocks",
                            lambda codes: [mock_hk_stock(c) for c in codes])
        monkeypatch.setattr("src.market_data.fetch_vix", mock_vix)
        monkeypatch.setattr("src.market_data.fetch_us_index", mock_us_index)
        monkeypatch.setattr("src.market_data.fetch_us_indices",
                            lambda tickers: [mock_us_index(t) for t in tickers])
        monkeypatch.setattr("src.market_data.fetch_us_treasury", mock_treasury)

        from src import market_data
        result = market_data.snapshot()

        assert "us_indices" in result
        assert "us_treasury" in result
        assert result["ok"] is True
        # 验证 us_indices 结构
        assert len(result["us_indices"]) == 3
        assert result["us_indices"][0]["close"] == 5000.0
        # 验证 us_treasury 结构
        assert result["us_treasury"]["us_2y"] == 4.0
        assert result["us_treasury"]["us_10y"] == 4.5
```

- [ ] **Step 2: 运行确认测试失败（新字段尚未加入）**

```bash
cd /Users/chenyimin/Documents/AI-Investment && uv run pytest tests/test_market_data.py::TestSnapshot -v
```

预期：PASS（`snapshot` 当前返回中已有 `us_indices`？不，还没改）→ 预期 FAIL，因为 `result["us_indices"]` 会 KeyError。

- [ ] **Step 3: 更新 `snapshot()` 函数**

修改 `src/market_data.py` 中 `snapshot()` 函数（约 L396-L417），在 `result["vix"]` 之后追加两行：

```python
def snapshot() -> dict:
    """一键获取当前持仓相关所有品种的价格快照。"""
    result = {"cn_etfs": [], "us_etfs": [], "hk_stocks": [],
              "vix": None, "us_indices": [], "us_treasury": None, "ok": False}

    result["cn_etfs"] = fetch_cn_etfs(["515080", "513100", "159941", "518880"])
    result["us_etfs"] = fetch_us_etfs(["QQQ", "GLD"])
    result["hk_stocks"] = fetch_hk_stocks(["00700", "09988"])
    result["vix"] = fetch_vix()
    result["us_indices"] = fetch_us_indices(["^DJI", "^GSPC", "^IXIC"])
    result["us_treasury"] = fetch_us_treasury()

    result["ok"] = len(result["cn_etfs"]) > 0 or len(result["us_etfs"]) > 0
    return result
```

- [ ] **Step 4: 运行全部测试**

```bash
cd /Users/chenyimin/Documents/AI-Investment && uv run pytest tests/test_market_data.py -v
```

预期：全部 PASS（~15 个测试）

- [ ] **Step 5: 确认现有测试不受影响**

```bash
cd /Users/chenyimin/Documents/AI-Investment && uv run pytest tests/ -v
```

预期：`test_strategy.py` 和 `test_macro_calendar.py` 照常 PASS

- [ ] **Step 6: 提交**

```bash
git add src/market_data.py tests/test_market_data.py
git commit -m "feat: add us_indices and us_treasury to snapshot()"
```

---

### Task 6: 最终验证 + 更新 TODO.md

**Files:**
- Modify: `TODO.md`（标记 D1 完成）

- [ ] **Step 1: 运行全部测试最后确认**

```bash
cd /Users/chenyimin/Documents/AI-Investment && uv run pytest tests/ -v
```

预期：全部 PASS

- [ ] **Step 2: 手动验证 `snapshot()` 可 import 且不抛异常**

```bash
cd /Users/chenyimin/Documents/AI-Investment && python -c "from src.market_data import fetch_us_index, fetch_us_indices, fetch_us_treasury, snapshot; print('All imports OK'); print('snapshot keys:', list(snapshot().keys()))"
```

预期：输出包含 `us_indices` 和 `us_treasury`

- [ ] **Step 3: 更新 TODO.md 标记 D1 完成**

将 `TODO.md` 中：
```markdown
- [ ] **D1. 全球行情直连**
  - [ ] yfinance 全量激活：美股三大指数/VIX/美债收益率，替代国内易超时备用源
  - [ ] 底仓路由实盘验证：在飞书加入 A 股 ETF、港股、美股 ETF 各一只，验证 price_updater 智能路由
  - [x] 🛡️ `pending_resolver.py` 已有 FUND_NAME_MAPPING 映射字典（已完成）
```

改为：
```markdown
- [x] **D1. 全球行情直连**
  - [x] yfinance 全量激活：美股三大指数(`^DJI`/`^GSPC`/`^IXIC`) + VIX + 美债收益率(2Y/10Y/利差)，yfinance 优先/akshare 兜底
  - [ ] 底仓路由实盘验证：在飞书加入 A 股 ETF、港股、美股 ETF 各一只，验证 price_updater 智能路由
  - [x] 🛡️ `pending_resolver.py` 已有 FUND_NAME_MAPPING 映射字典（已完成）
```

（底仓路由实盘验证是操作任务，代码层面已完成）

- [ ] **Step 4: 最终提交**

```bash
git add TODO.md
git commit -m "docs: mark D1 global market data as complete"
```

---

## 变更文件汇总

| 文件 | 操作 | Tasks |
|------|------|-------|
| `src/market_data.py` | 修改 | Task 2, 3, 4, 5 |
| `tests/test_market_data.py` | 新建 | Task 1, 2, 3, 4, 5 |
| `TODO.md` | 修改 | Task 6 |
