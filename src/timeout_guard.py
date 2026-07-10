"""
超时保护工具 —— 为所有外部网络调用提供硬超时熔断。

设计原则：
  - 不改任何业务函数内部逻辑，纯壳式包装
  - 用 threading 实现，跨平台、零外部依赖
  - GitHub Actions 墙外运行，yfinance 10s / akshare 跨墙 15s 足够

用法：
    from src.timeout_guard import with_timeout
    data = with_timeout(10, fallback=None)(some_fetch_func)("AAPL")
"""

from __future__ import annotations

import functools
import logging
import threading
from typing import Any, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def with_timeout(seconds: float, fallback: Optional[Any] = None):
    """给任意函数加硬超时。

    原理：
      1. 在 daemon 子线程执行目标函数
      2. 主线程 join(timeout=seconds)
      3. 超时 → 返回 fallback，子线程自生自灭（GC 回收）
      4. 正常完成 → 返回实际结果
      5. 异常 → 也返回 fallback（静默降级）

    注意：
      - 不能用于同一进程内需严格清理资源的函数（如文件锁）
      - daemon 线程在超时后继续运行但结果被丢弃，适合幂等的 HTTP GET
      - GitHub Actions runner 在 US，yfinance→Yahoo 通常 <2s，
        若 10s 还没回来说明 Yahoo 挂了或限流，降级是正确选择

    Args:
        seconds: 硬超时秒数。行情抓取 10-15s，LLM 调用 180s
        fallback: 超时/异常时返回的兜底值
    """
    def decorator(func: Callable[..., T]) -> Callable[..., Optional[T]]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Optional[T]:
            result: list[Optional[T]] = [fallback]
            exception: list[Optional[Exception]] = [None]

            def target() -> None:
                try:
                    result[0] = func(*args, **kwargs)
                except Exception as e:
                    exception[0] = e

            t = threading.Thread(target=target, daemon=True)
            t.start()
            t.join(timeout=seconds)

            if t.is_alive():
                # 超时了——子线程还在跑，但我们不再等
                # 取函数名用于日志
                func_name = getattr(func, "__name__", str(func))
                logger.warning(
                    "⏰ %s() 超过 %ds 硬超时，降级为 fallback",
                    func_name, seconds,
                )
                return fallback

            if exception[0] is not None:
                logger.debug(
                    "%s() 异常: %s，降级为 fallback",
                    getattr(func, "__name__", str(func)),
                    str(exception[0])[:100],
                )
                return fallback

            return result[0]

        return wrapper

    return decorator
