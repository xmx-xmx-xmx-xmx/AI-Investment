"""今日市场快报 —— 用 akshare 拉美股+ A 股 ETF 行情（国内可用），喂给大模型生成快报。"""

# ============================================================
# 必须在所有第三方库引入前，从三个层面彻底禁用系统代理
# 防止 Clash / V2Ray 等代理工具干扰国内站点访问
# ============================================================

# 层面一：清空环境变量（拦截 requests 等库）
import os
os.environ["http_proxy"] = ""
os.environ["https_proxy"] = ""
os.environ["HTTP_PROXY"] = ""
os.environ["HTTPS_PROXY"] = ""
os.environ["all_proxy"] = ""
os.environ["ALL_PROXY"] = ""

# 层面二：强制覆盖全局 urllib 代理处理器（拦截纯 urllib 库）
import urllib.request
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))

# 层面三：拦截 getproxies()（这是 urllib3 / requests 在 macOS 上读取系统代理的唯一入口）
_original_getproxies = urllib.request.getproxies
def _empty_proxies():
    return {}
urllib.request.getproxies = _empty_proxies

import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

import akshare as ak

logger = logging.getLogger(__name__)

FETCH_TIMEOUT = 15
MAX_RETRIES = 2

CN_ETF_NAMES = {
    "510500": "中证500 ETF",
    "512890": "红利低波 ETF",
}


# ---------- 工具函数 ----------

def _call_with_timeout(fn, timeout):
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(fn).result(timeout=timeout)


# ---------- 美股 ETF ----------

def _try_stock_us_daily(ticker: str):
    df = ak.stock_us_daily(symbol=ticker, adjust="")
    if len(df) < 2:
        raise RuntimeError("数据不足")
    return {
        "ticker": ticker,
        "label": "美股—纳斯达克100 ETF (QQQ)" if ticker == "QQQ" else "商品—黄金ETF (GLD)",
        "close": round(float(df["close"].iloc[-1]), 2),
        "change_pct": round(
            (float(df["close"].iloc[-1]) - float(df["close"].iloc[-2]))
            / float(df["close"].iloc[-2])
            * 100,
            2,
        ),
    }


def _try_stock_us_hist(ticker: str):
    """东方财富源（备选）"""
    df = ak.stock_us_hist(symbol=ticker, period="daily", adjust="")
    if len(df) < 2:
        raise RuntimeError("数据不足")
    return {
        "ticker": ticker,
        "label": "美股—纳斯达克100 ETF (QQQ)" if ticker == "QQQ" else "商品—黄金ETF (GLD)",
        "close": round(float(df["收盘"].iloc[-1]), 2),
        "change_pct": round(
            (float(df["收盘"].iloc[-1]) - float(df["收盘"].iloc[-2]))
            / float(df["收盘"].iloc[-2])
            * 100,
            2,
        ),
    }


def fetch_us_etf(ticker: str) -> dict | None:
    """拉取美股 ETF 行情，多源 fallback + 超时 + 重试。"""
    sources = [
        ("新浪", lambda: _try_stock_us_daily(ticker)),
        ("东方财富", lambda: _try_stock_us_hist(ticker)),
    ]
    for attempt in range(MAX_RETRIES):
        for name, fn in sources:
            try:
                logger.debug("[%s] 尝试 %s 源...", ticker, name)
                return _call_with_timeout(fn, FETCH_TIMEOUT)
            except (FutureTimeoutError, TimeoutError):
                logger.debug("[%s] %s 源超时", ticker, name)
            except Exception as e:
                logger.debug("[%s] %s 源失败: %s", ticker, name, e)
        if attempt < MAX_RETRIES - 1:
            logger.debug("[%s] 所有源均失败，%d 秒后重试...", ticker, attempt + 2)
            time.sleep(attempt + 2)
    logger.warning("[%s] 全部尝试失败，跳过该品种", ticker)
    return None


# ---------- A 股 ETF ----------

def _try_cn_etf_sina(code: str, name: str):
    """新浪源 — 列名为英文，需手算涨跌幅。"""
    symbol = f"sh{code}"
    df = ak.fund_etf_hist_sina(symbol=symbol)
    if len(df) < 2:
        raise RuntimeError("数据不足")
    prev = float(df["close"].iloc[-2])
    today = float(df["close"].iloc[-1])
    return {
        "code": code,
        "label": f"A股—{name} ({code})",
        "close": round(today, 2),
        "change_pct": round((today - prev) / prev * 100, 2),
    }


def _try_cn_etf_em(code: str, name: str):
    """东方财富源（备选）— 自带涨跌幅列。"""
    df = ak.fund_etf_hist_em(symbol=code, period="daily", adjust="")
    if len(df) < 2:
        raise RuntimeError("数据不足")
    return {
        "code": code,
        "label": f"A股—{name} ({code})",
        "close": round(float(df["收盘"].iloc[-1]), 2),
        "change_pct": round(float(df["涨跌幅"].iloc[-1]), 2),
    }


def fetch_cn_etf(code: str) -> dict | None:
    """拉取国内沪深 ETF 行情，多源 fallback + 超时 + 重试。"""
    name = CN_ETF_NAMES.get(code, code)
    sources = [
        ("新浪", lambda: _try_cn_etf_sina(code, name)),
        ("东方财富", lambda: _try_cn_etf_em(code, name)),
    ]
    for attempt in range(MAX_RETRIES):
        for src_name, fn in sources:
            try:
                logger.debug("[%s] 尝试 %s 源...", name, src_name)
                return _call_with_timeout(fn, FETCH_TIMEOUT)
            except (FutureTimeoutError, TimeoutError):
                logger.debug("[%s] %s 源超时", name, src_name)
            except Exception as e:
                logger.debug("[%s] %s 源失败: %s", name, src_name, e)
        if attempt < MAX_RETRIES - 1:
            logger.debug("[%s] 所有源均失败，%d 秒后重试...", name, attempt + 2)
            time.sleep(attempt + 2)
    logger.warning("[%s] 全部尝试失败，跳过该品种", name)
    return None


# ---------- 快报生成 ----------

def build_prompt(items: list[dict]) -> str:
    """根据实际获取成功的数据动态构建 prompt。"""
    lines = "\n".join(
        f"- {d['label']}：收盘 {d['close']}，涨跌幅 {d['change_pct']:+.2f}%"
        for d in items
    )
    return f"""你是一名专业的财经快评写手。请根据以下今日行情数据，用中文写一段 150 字以内的"今日市场快报"。

要求：
- 语气专业但轻松，像一位懂行的朋友在聊天
- 综合点评已提供品种的整体表现，给出一句简评
- 严格控制在 150 字以内（含标点）

数据：
{lines}

请直接输出快报正文，不要带任何前缀或说明。"""


def gen_brief(prompt: str) -> str:
    from src.llm import get_llm_client, get_llm_model
    client = get_llm_client()
    if client is None:
        return "LLM 未配置"
    resp = client.chat.completions.create(
        model=get_llm_model(),
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content.strip()


# ---------- 主流程 ----------

def main():
    print("📡 正在获取行情数据...\n")

    qqq = fetch_us_etf("QQQ")
    gld = fetch_us_etf("GLD")
    zz500 = fetch_cn_etf("510500")
    hongli = fetch_cn_etf("512890")

    items = [d for d in (qqq, gld, zz500, hongli) if d is not None]

    if not items:
        print("\n❌ 所有品种数据获取均失败，无法生成快报")
        return

    prompt = build_prompt(items)

    print("\n🤖 正在生成快报...\n")
    brief = gen_brief(prompt)

    print("=" * 50)
    print("           今日市场快报")
    print("=" * 50)
    print()
    print(brief)
    print()
    summary = "  |  ".join(
        f"{d.get('ticker', d.get('code', '?'))} {d['close']} ({d['change_pct']:+.2f}%)"
        for d in items
    )
    print(f"📊 {summary}")


if __name__ == "__main__":
    main()
