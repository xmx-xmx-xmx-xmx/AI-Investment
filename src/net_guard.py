# -*- coding: utf-8 -*-
"""给 akshare / yfinance 加统一硬超时保护（2026-09-23）。

背景
----
`src/timeout_guard.with_timeout` 早已存在（2026-07-07），但**只包了 `market_data.py`
里 9 个公开函数**。`briefing.py` / `radar.py` / `price_updater.py` /
`pending_resolver.py` / `classification.py` / `earnings_calendar.py` 里的
akshare 与 yfinance 调用全是**裸调用**（无超时），共约 30 处。

事故证据（GitHub Actions API 实测 + 完整日志）
-------------------------------------------
2026-09-22 12:00 的午间运行：
    04:01:57  25%|██▌  | 2/8        ← 亚太行情段
              （静默 13 分 33 秒，零输出）
    04:15:30  ##[error]The operation was canceled.
`daily-run.yml` 的 `timeout-minutes: 15` 到点，整个 job 被 GitHub 强杀 →
**午间推送整条静默丢失**。同一天 09-07 12:00 也是同一形态（15.3m）。
对照：同一段在成功运行里只要 10 秒。

根因 = 卡死的那段（`briefing._build_asia_pacific_market` 等）用的是**内联裸调用**，
落在 `with_timeout` 的保护圈之外。

做法
----
模块级代理：`ak.sh_xxx(...)` 与 `yf.Ticker(...)` 都自动套上 `with_timeout`。
- 命中 `try/except Exception` 的调用点**无需改动**：超时 → 返回 None → 上游既有的
  降级分支照常走（已逐个核对：全部 49 个调用点都在 try 块内）
- 超时会打 WARNING 日志，便于事后统计"哪些源经常挂"

用法
----
    from src.net_guard import import_ak          # 替代 `import akshare as ak`
    ak = import_ak()

    from src.net_guard import import_yf          # 替代 `import yfinance as yf`
    yf = import_yf()

⚠️ `market_data.py` 不需要改：它的公开函数已经被 `_tw` 在函数级包过一层。
⚠️ `import_ak()` / `import_yf()` 保留 `ImportError` 语义（内部才真正 import），
   因此调用点原有的 `try: import akshare / except ImportError:` 依然有效。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Optional

from src.timeout_guard import with_timeout

logger = logging.getLogger(__name__)

# ── 默认超时 ──
_DEFAULT_AK_TIMEOUT = float(os.getenv("AK_TIMEOUT", "30"))
_DEFAULT_YF_TIMEOUT = float(os.getenv("YF_TIMEOUT", "15"))
# 逃生阀：出问题时设 NET_GUARD_DISABLED=1 可整体退回裸调用
_DISABLED = os.getenv("NET_GUARD_DISABLED", "").strip() not in ("", "0", "false", "False")

# ── 单函数超时覆盖 ──
# fund_name_em 要下载全市场基金名录（上万条），正常就要 10-60s，不能按默认 30s 砍
_AK_TIMEOUT_OVERRIDES = {
    "fund_name_em": 120.0,
    "fund_etf_spot_em": 60.0,
    "fund_open_fund_rank_em": 60.0,
}

# 不包装的成员（非函数 / 会破坏反射的 dunder）
_SKIP_PREFIX = ("__",)


def _wrap(fn: Callable[..., Any], seconds: float, label: str) -> Callable[..., Any]:
    """套 with_timeout，超时返回 None 并留 WARNING 日志。"""
    guarded = with_timeout(seconds, fallback=None)(fn)

    def _call(*args: Any, **kwargs: Any) -> Any:
        out = guarded(*args, **kwargs)
        if out is None:
            # 只有"超时/异常"才会走到这里（正常返回 None 的源本就少见）
            logger.warning("⏰ %s 无返回（超时 %.0fs 或异常），本次降级", label, seconds)
        return out

    return _call


class _GuardedAkshare:
    """akshare 模块代理：每次属性访问都返回带超时的包装函数。"""

    def __init__(self, module: Any, default_timeout: Optional[float] = None) -> None:
        object.__setattr__(self, "_m", module)
        object.__setattr__(self, "_d", default_timeout or _DEFAULT_AK_TIMEOUT)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._m, name)
        if name.startswith(_SKIP_PREFIX) or not callable(attr):
            return attr
        if _DISABLED:
            return attr
        seconds = _AK_TIMEOUT_OVERRIDES.get(name, self._d)
        return _wrap(attr, seconds, f"akshare.{name}")


class _GuardedTicker:
    """yfinance.Ticker 代理：`.info` / `.history()` 都带超时。"""

    def __init__(self, ticker: Any, seconds: float) -> None:
        object.__setattr__(self, "_t", ticker)
        object.__setattr__(self, "_s", seconds)

    @property
    def info(self) -> dict:
        if _DISABLED:
            return self._t.info
        out = with_timeout(self._s, fallback=None)(lambda: self._t.info)()
        if out is None:
            logger.warning("⏰ yfinance.Ticker.info 无返回（超时 %.0fs），本次降级", self._s)
            return {}
        return out

    def history(self, *args: Any, **kwargs: Any) -> Any:
        if _DISABLED:
            return self._t.history(*args, **kwargs)
        # ⚠️ with_timeout(秒数, fallback) 返回的是「装饰器」，必须先作用到函数上再调用，
        # 不能把业务参数直接塞给装饰器（否则 TypeError 静默降级 → 误走兜底源）
        guarded = with_timeout(self._s, fallback=None)(self._t.history)
        out = guarded(*args, **kwargs)
        if out is None:
            logger.warning("⏰ yfinance.Ticker.history 无返回（超时 %.0fs），本次降级", self._s)
        return out

    def __getattr__(self, name: str) -> Any:
        return getattr(self._t, name)


class _GuardedYfinance:
    """yfinance 模块代理：只拦 `Ticker`，其余透传。"""

    def __init__(self, module: Any, default_timeout: Optional[float] = None) -> None:
        object.__setattr__(self, "_m", module)
        object.__setattr__(self, "_d", default_timeout or _DEFAULT_YF_TIMEOUT)

    def Ticker(self, *args: Any, **kwargs: Any) -> Any:  # noqa: N802 (对齐 yfinance 命名)
        real = self._m.Ticker(*args, **kwargs)
        if _DISABLED:
            return real
        return _GuardedTicker(real, self._d)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._m, name)
        if name.startswith(_SKIP_PREFIX) or not callable(attr):
            return attr
        if _DISABLED:
            return attr
        return _wrap(attr, self._d, f"yfinance.{name}")


def import_ak(seconds: Optional[float] = None) -> Any:
    """导入被超时包装的 akshare。

    Args:
        seconds: 覆盖默认超时秒数（测试用）。None = 用 env AK_TIMEOUT / 30s。
    Raises:
        ImportError: akshare 未安装（语义与 `import akshare` 一致）。
    """
    import akshare as _m
    return _GuardedAkshare(_m, seconds)


def import_yf(seconds: Optional[float] = None) -> Any:
    """导入被超时包装的 yfinance。未安装时抛 ImportError。"""
    import yfinance as _m
    return _GuardedYfinance(_m, seconds)
