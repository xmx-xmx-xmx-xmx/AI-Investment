# 给不可控的第三方库调用套外层超时

## 问题

第三方库（典型：akshare / yfinance 这类爬取型数据源）**内部的 requests 无法注入 timeout**。
库里某个请求挂死时，你的整个进程会一起挂住，而这不是你的代码能修的。

日志特征：进度条（库内部用 tqdm 打的）走到某一格后**静默数分钟到十几分钟**，零输出，然后被上层强杀。

## 对策：daemon 线程 + join(timeout)

（下面是本项目 `src/timeout_guard.py` 的真实实现，照抄口径，含它的取舍）

```python
import functools, logging, threading
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

def with_timeout(seconds: float, fallback: Optional[Any] = None):
    """返回一个「装饰器」。⚠️ 返回的是装饰器，不是已包装的函数。"""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            result = [fallback]
            exception = [None]

            def target():
                try:
                    result[0] = func(*args, **kwargs)
                except Exception as e:      # ← 吞掉，不重抛
                    exception[0] = e

            t = threading.Thread(target=target, daemon=True)   # ← daemon 是关键
            t.start()
            t.join(timeout=seconds)

            if t.is_alive():
                logger.warning("⏰ %s() 超过 %ds 硬超时，降级为 fallback",
                               getattr(func, "__name__", str(func)), seconds)
                return fallback
            if exception[0] is not None:
                logger.debug("%s() 异常: %s，降级为 fallback",
                             getattr(func, "__name__", str(func)), str(exception[0])[:100])
                return fallback
            return result[0]
        return wrapper
    return decorator
```

用法（两种写法都对，注意区分）：

```python
# 链式：with_timeout(...) 先得到装饰器，作用到函数上得到 wrapper，再调用 wrapper
data = with_timeout(10, fallback=None)(some_fetch_func)("AAPL")

# 分两步（更易读，推荐）
guarded = with_timeout(15, fallback=None)(third_party_fn)
result = guarded(arg1, arg2)
```

### ⚠️ 行为特征：**异常也被静默降级**

上面第 5 点是关键取舍：**函数内部抛异常时也返回 `fallback`，不重抛**。
好处是不让单个数据源的小毛病中断整个任务；代价是**任何 bug 都会被伪装成"数据源不可用"**。

> 这正是下文那个装饰器误用 bug 能潜伏很久的原因：`TypeError` 被这里吞掉，
> 表现只是"结果莫名其妙来自兜底源"，而不是报错。

因此：**只加 warning（或 debug）日志是不够的**，必须靠**测试**锁住行为，别靠日志观察。

## ⚠️ 最大的坑：业务参数被喂给了装饰器

```python
# ❌ 错：args/kwargs 被当成 decorator(...) 的额外参数
result = with_timeout(15, fallback=None)(self._t.history, *args, **kwargs)
# → with_timeout 返回的 decorator 只接受 1 个参数 → TypeError
# → 被上面的 except 吞掉 → 静默返回 fallback → 误走兜底源（真实网络）→ 极难发现

# ✅ 对：先作用到函数得到 wrapper，再调用 wrapper
guarded = with_timeout(15, fallback=None)(self._t.history)
result = guarded(*args, **kwargs)
```

必须写回归测试锁住**参数能透传**：

```python
def test_history_passes_arguments():
    """业务参数不能被装饰器吃掉。"""
    seen = {}
    def fake_history(*args, **kwargs):
        seen["args"], seen["kwargs"] = args, kwargs
        return "ok"
    guarded = with_timeout(15, fallback=None)(fake_history)
    assert guarded("1d", interval="1h") == "ok"
    assert seen["args"] == ("1d",)
    assert seen["kwargs"] == {"interval": "1h"}
```

## ⚠️ 为什么不用 ThreadPoolExecutor

`ThreadPoolExecutor` 的 worker 线程**不是 daemon**，且 `atexit` 会 join 它们。
于是挂死的任务不只卡住你的业务逻辑，还会把**解释器的正常退出**一起卡死——
比单纯超时更糟（CI 里表现为任务结束后进程不退出，继续吃时间直到外部强杀）。

超时场景必须用 daemon 线程，让挂死的线程在进程退出时被直接丢弃。

## 规模化：模块级代理

若同一个库有几十个调用点，逐个改容易漏。用**模块级代理**一次覆盖整个文件、且**未来新增的调用自动受保护**：

```python
class _GuardedModule:
    def __init__(self, module, default_timeout):
        object.__setattr__(self, "_m", module)
        object.__setattr__(self, "_d", default_timeout)

    def __getattr__(self, name):
        # ⚠️ 必须每次实时取（而非缓存），否则测试里 monkeypatch 真实模块会失效
        attr = getattr(self._m, name)
        if name.startswith("__") or not callable(attr):
            return attr
        return _wrap(attr, self._d, f"{self._m.__name__}.{name}")

def import_ak(seconds=None):
    import akshare as _m
    return _GuardedModule(_m, seconds or 30.0)
```

要点：
- **`__getattr__` 里实时 resolve**，不要缓存属性 —— 缓存会让 `monkeypatch.setattr(akshare, "x", ...)` 失效，测试全废。
- 保留 `ImportError` 语义（工厂函数内 import），否则上游的"库没装"降级分支会变成别的异常。
- 留**逃生阀**：`NET_GUARD_DISABLED=1` 整体关闭；超时值走环境变量；对确实慢的接口（如全市场名称表）用 override 表单独放行。
- 代理返回 None 时**打 warning**，别静默 —— 否则又是一个看不见的降级。

## 审计：确认没有漏网

替换完要验证，别靠肉眼。用 AST 扫：

```python
# 模块级被包装的名字
wrapped = set(re.findall(r'^(\w+)\s*=\s*_tw\(', src, re.M))
# 含原生 import 的函数
# → 私有 helper 的调用者是否都在 wrapped 内
```

⚠️ 一种容易误判的情形：**模块级重绑定式的包装**（`fetch_x = _tw(N, fb)(fetch_x)`）。
此时函数**定义体**里仍留着原生 import，看起来像"漏网"，但模块级**绑定**已经指向包装版 —— 调用方拿到的是安全的那个。
判断依据必须是"调用者是否在包装名单内"，而不是"函数体里有没有原生 import"。
