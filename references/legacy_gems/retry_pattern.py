"""
从 legacy akshare_fetcher.py 提取的 retry 装饰器模式。

用途：在 market_data.py / radar.py 中为易超时的网络请求（yfinance、
Sina、akshare）添加指数退避重试，替代目前 bare try/except。

依赖：pip install tenacity

使用方法：
    from tenacity import (
        retry,
        stop_after_attempt,
        wait_exponential,
        retry_if_exception_type,
        before_sleep_log,
    )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def fetch_something(code: str):
        ...
"""

# ── tenacity import 模板 ──
# from tenacity import (
#     retry, stop_after_attempt, wait_exponential,
#     retry_if_exception_type, before_sleep_log,
# )

# ── 完整的 rate limiter 模式 ──
class RateLimiter:
    """请求频率控制器 —— 来自 legacy 项目的 DataFetcherManager 模式。

    结合 @retry 装饰器使用：retry 负责失败重试，RateLimiter 负责主动节流。
    """

    def __init__(self, min_interval: float = 2.0, max_jitter: float = 3.0):
        import time
        import random
        self._last_request_time = None
        self.min_interval = min_interval
        self.max_jitter = max_jitter

    def wait(self):
        import time
        import random
        if self._last_request_time is not None:
            elapsed = time.time() - self._last_request_time
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
        jitter = random.uniform(0, self.max_jitter)
        time.sleep(jitter)
        self._last_request_time = time.time()


# ── 使用示例 ──
# limiter = RateLimiter(min_interval=0.3, max_jitter=0.5)
#
# @retry(
#     stop=stop_after_attempt(3),
#     wait=wait_exponential(multiplier=1, min=2, max=30),
#     retry=retry_if_exception_type((ConnectionError, TimeoutError)),
# )
# def fetch_with_retry(code: str):
#     limiter.wait()
#     return some_api_call(code)
