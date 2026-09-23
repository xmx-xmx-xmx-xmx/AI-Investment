# -*- coding: utf-8 -*-
"""net_guard 超时保护测试。

背景（2026-09-23 事故）：
  2026-09-22 12:00 的午间运行卡死在「亚太行情抓取」段，静默 13 分 33 秒后撞破
  daily-run.yml 的 timeout-minutes: 15 被 GitHub 强杀 → 整条午间推送丢失。
  根因是 briefing / radar / price_updater 等文件里的 akshare、yfinance 调用
  全是裸调用（无超时），落在 timeout_guard.with_timeout 的保护圈之外。

本文件锁住三件事：
  1. 代理确实给每个调用套上硬超时（挂死 → 返回 None，不再拖垮整个 run）
  2. 正常返回 / 非函数属性 / 异常降级等行为不变
  3. 调用点原有的打桩方式（monkeypatch 真实模块的属性）依然生效
"""

from __future__ import annotations

import sys
import time

import pytest

from src import net_guard
from src.net_guard import _GuardedAkshare, _GuardedYfinance


# ═══════════════════════════════════════════════════════════════
# 假模块（不碰真实网络）
# ═══════════════════════════════════════════════════════════════

class _FakeAk:
    __version__ = "0.0.0-fake"

    def fast(self, x):
        return {"ok": x}

    def hangs(self, x):
        time.sleep(5)
        return {"never": x}

    def raises(self, x):
        raise RuntimeError("源挂了")

    def returns_none(self, x):
        return None


class _HangingAk:
    """事故现场：所有行情接口都挂死。"""

    def stock_hk_index_spot_sina(self):
        time.sleep(10)

    def stock_zh_index_spot_sina(self):
        time.sleep(10)

    def stock_zh_index_daily_tx(self, symbol):
        time.sleep(10)

    def stock_hk_index_daily_sina(self, symbol):
        time.sleep(10)


class _FakeTickerInstance:
    def __init__(self, ticker):
        self.ticker = ticker

    @property
    def info(self):
        return {"fast": True}

    def history(self, period="1mo"):
        return {"period": period, "rows": 25}


class _HangingTicker:
    def __init__(self, ticker):
        self.ticker = ticker

    @property
    def info(self):
        time.sleep(10)
        return {"never": True}

    def history(self, period="1mo"):
        time.sleep(10)


class _FakeYf:
    __version__ = "0.0.0-fake"

    @staticmethod
    def Ticker(ticker):  # noqa: N802
        return _FakeTickerInstance(ticker)


class _HangingYf:
    @staticmethod
    def Ticker(ticker):  # noqa: N802
        return _HangingTicker(ticker)


def _ak(seconds=0.25):
    return _GuardedAkshare(_FakeAk(), seconds)


def _yf(seconds=0.25, module=None):
    return _GuardedYfinance(module or _FakeYf, seconds)


# ═══════════════════════════════════════════════════════════════
# akshare 代理
# ═══════════════════════════════════════════════════════════════

def test_normal_call_passes_through():
    assert _ak().fast(7) == {"ok": 7}


def test_hanging_call_returns_none_instead_of_blocking(monkeypatch):
    """核心用例：挂死的调用必须在超时后返回 None，不能阻塞。"""
    logs = []
    monkeypatch.setattr(net_guard.logger, "warning",
                        lambda *a, **k: logs.append(a[0] % a[1:]))
    t0 = time.time()
    out = _ak(0.2).hangs(1)
    elapsed = time.time() - t0
    assert out is None
    assert elapsed < 2, f"应 0.2s 左右返回，实际 {elapsed:.2f}s"
    assert any("akshare.hangs" in str(x) for x in logs), "超时必须留 WARNING 日志"


def test_raising_call_degrades_to_none():
    """源抛异常 → 返回 None（沿用 with_timeout 的静默降级语义）"""
    assert _ak().raises(1) is None


def test_source_returning_none_is_preserved():
    assert _ak().returns_none(1) is None


def test_non_callable_attribute_passes_through():
    """非函数属性（版本号等）必须原样返回，不能被包装"""
    assert _ak().__version__ == "0.0.0-fake"


def test_timeout_override_table_applies(monkeypatch):
    """名字在 _AK_TIMEOUT_OVERRIDES 里 → 用覆盖值，而不是默认值"""
    monkeypatch.setitem(net_guard._AK_TIMEOUT_OVERRIDES, "fast", 0.2)
    monkeypatch.setattr(_FakeAk, "fast", lambda self, x: time.sleep(5))
    ak = _ak(30)                        # 默认 30s，但 fast 被覆盖成 0.2s
    t0 = time.time()
    assert ak.fast(1) is None
    assert time.time() - t0 < 2


def test_disabled_flag_bypasses_guard(monkeypatch):
    """逃生阀：NET_GUARD_DISABLED → 直接透传原函数本体"""
    monkeypatch.setattr(net_guard, "_DISABLED", True)
    ak = _ak(0.1)
    assert ak.fast(9) == {"ok": 9}
    assert ak.fast.__name__ == "fast", "未包装时拿到的应是原函数"


def test_monkeypatch_of_real_module_still_takes_effect(monkeypatch):
    """⚠️ 关键兼容性：既有测试靠 monkeypatch 真实 akshare 模块的属性打桩。

    代理必须在**每次属性访问时**去真实模块取属性（而不是提前缓存函数），
    否则所有打桩测试都会失效。
    """
    ak = _ak()                                     # 先拿到代理
    monkeypatch.setattr(_FakeAk, "fast", lambda self, x: {"patched": x})
    assert ak.fast(3) == {"patched": 3}, "代理必须实时解析真实模块的属性"


# ═══════════════════════════════════════════════════════════════
# yfinance 代理
# ═══════════════════════════════════════════════════════════════

def test_yf_ticker_info_normal_passes_through():
    assert _yf().Ticker("TEST").info == {"fast": True}


def test_yf_ticker_info_timeout_returns_empty_dict(monkeypatch):
    """info 超时必须返回 {} 而不是 None —— 调用点会直接 .get()"""
    yf = _GuardedYfinance(_HangingYf, 0.2)
    assert yf.Ticker("TEST").info == {}


def test_yf_ticker_history_passes_arguments():
    """⚠️ 回归锁：history 曾写成 with_timeout(...)(fn, *args, **kwargs) ——
    业务参数被喂给了「装饰器」本身 → TypeError → 静默降级 → 误走兜底源（真实网络）。
    正确写法不能吃掉调用参数。
    """
    t = _yf(2).Ticker("515080")
    assert t.history(period="1mo") == {"period": "1mo", "rows": 25}
    assert t.history(period="5d") == {"period": "5d", "rows": 25}


def test_yf_ticker_history_timeout_returns_none():
    t = _GuardedYfinance(_HangingYf, 0.2).Ticker("515080")
    t0 = time.time()
    assert t.history(period="1mo") is None
    assert time.time() - t0 < 2


def test_yf_ticker_other_attributes_delegate():
    assert _yf(2).Ticker("AAA").ticker == "AAA"


def test_yf_disabled_returns_real_ticker(monkeypatch):
    monkeypatch.setattr(net_guard, "_DISABLED", True)
    assert isinstance(_yf(0.1).Ticker("X"), _FakeTickerInstance)


# ═══════════════════════════════════════════════════════════════
# import_ak / import_yf 的 ImportError 语义
# ═══════════════════════════════════════════════════════════════

def test_import_ak_raises_importerror_when_missing(monkeypatch):
    """必须保留 `try: import akshare / except ImportError:` 的原语义"""
    monkeypatch.setitem(sys.modules, "akshare", None)
    with pytest.raises(ImportError):
        net_guard.import_ak()


def test_import_yf_raises_importerror_when_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "yfinance", None)
    with pytest.raises(ImportError):
        net_guard.import_yf()


def test_import_ak_wraps_real_module(monkeypatch):
    """真实 akshare 走一遍代理，正常调用不受影响"""
    ak = net_guard.import_ak()
    assert isinstance(ak, _GuardedAkshare)
    assert hasattr(ak, "fund_open_fund_info_em")


# ═══════════════════════════════════════════════════════════════
# 端到端：事故现场复现
# ═══════════════════════════════════════════════════════════════

def test_asia_pacific_section_survives_hanging_sources(monkeypatch):
    """复现 2026-09-22 现场：亚太行情段里的 akshare / yfinance 全部挂死。

    修复前：整个 run 卡死 → job 撞 timeout-minutes 被强杀 → 推送丢失。
    修复后：该段快速降级返回，绝不阻塞。
    """
    from src import briefing

    monkeypatch.setattr(net_guard, "import_ak",
                        lambda seconds=None: _GuardedAkshare(_HangingAk(), 0.2))
    monkeypatch.setattr(net_guard, "import_yf",
                        lambda seconds=None: _GuardedYfinance(_HangingYf, 0.2))

    t0 = time.time()
    out = briefing._build_asia_pacific_market()
    elapsed = time.time() - t0

    assert isinstance(out, str), "降级后必须仍返回字符串，不能抛异常"
    assert elapsed < 8, f"挂死的源必须在超时后放弃，实际耗时 {elapsed:.1f}s"


def test_global_market_snapshot_survives_hanging_sources(monkeypatch):
    """同一形态的第二个入口（早间/夜盘用的全球市场快照）。

    该函数还会经 market_data 取美股指数，而 market_data 已被 `_tw` 在函数级包过
    （每个 10s）—— 这里把美股段打桩成"无数据"，让用例聚焦在本次新增保护的
    内联 akshare / yfinance 段上，否则光等 market_data 的自身超时就要 40s。
    """
    from src import briefing, market_data

    monkeypatch.setattr(net_guard, "import_ak",
                        lambda seconds=None: _GuardedAkshare(_HangingAk(), 0.2))
    monkeypatch.setattr(net_guard, "import_yf",
                        lambda seconds=None: _GuardedYfinance(_HangingYf, 0.2))
    monkeypatch.setattr(market_data, "fetch_us_index", lambda ticker: None)
    monkeypatch.setattr(market_data, "fetch_us_etf", lambda ticker: None)

    t0 = time.time()
    out = briefing._build_global_market_snapshot()
    elapsed = time.time() - t0

    assert isinstance(out, str)
    assert elapsed < 10, f"实际耗时 {elapsed:.1f}s"
