"""Market data block builders (VIX, US futures, Asia-Pacific, Global markets, DST reminder)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)
tz_cn = timezone(timedelta(hours=8))


def _build_vix_block() -> str:
    """独立 VIX 恐慌指数区块。"""
    from src import briefing
    md = getattr(briefing, "market_data", None)
    if md is None:
        from src import market_data as md
    try:
        vix = md.fetch_vix()
        if vix and vix.get("vix"):
            return f"😨 **VIX 恐慌指数**: {vix['vix']:.1f}（{vix['level']}）"
    except Exception:
        pass
    return ""


def _build_us_futures_block() -> str:
    """美股期货实时行情区块。14:30 盘前风向 + 21:00 实时期货。"""
    from src import briefing
    md = getattr(briefing, "market_data", None)
    if md is None:
        from src import market_data as md
    lines = []
    for sym, name in [("NQ", "纳指期货"), ("ES", "标普期货")]:
        try:
            data = md.fetch_nq_futures(sym)
            if data and data.get("change_pct") is not None:
                a = "🔺" if data["change_pct"] > 0 else "🔻" if data["change_pct"] < 0 else "➖"
                time_str = f" {data['time']}" if data.get("time") else ""
                lines.append(f"· {name}: {data['price']:,.2f}　{a}{data['change_pct']:+.2f}%{time_str}")
        except Exception:
            pass
    if not lines:
        return ""
    return "\n".join(lines)


def _build_asia_pacific_market() -> str:
    """亚太市场中午 12:00 实时快照（compact 精简三行 / full 完整点位）。"""
    from src import briefing
    is_compact_fn = getattr(briefing, "_is_compact_style", None)
    compact = is_compact_fn() if is_compact_fn else False
    lines = ["**🌏 亚太午盘**"]

    # ── A 股（11:30 上午盘收盘，用新浪实时数据）──
    cn_items = []
    try:
        import os as _os
        for _k in ('http_proxy','https_proxy','HTTP_PROXY','HTTPS_PROXY','all_proxy','ALL_PROXY'):
            _os.environ.pop(_k, None)
        # 2026-09-23 超时保护：见 src/net_guard.py
        from src.net_guard import import_ak
        _ak = import_ak()
        df = _ak.stock_zh_index_spot_sina()
        target_names = [('上证指数', '上证'), ('深证成指', '深证'), ('创业板指', '创业板')]
        if '名称' in df.columns:
            for name, short in target_names:
                rows = df[df['名称'] == name]
                if len(rows) > 0:
                    r = rows.iloc[0]
                    price = float(r['最新价'])
                    pct = float(r['涨跌幅'])
                    arrow = "🔺" if pct > 0 else "🔻" if pct < 0 else "➖"
                    if compact:
                        cn_items.append(f"{short} {arrow}{pct:+.2f}%")
                    else:
                        cn_items.append(f"· {name}: {price:,.2f}　{arrow}{pct:+.2f}%")
    except Exception:
        pass
    if cn_items:
        if compact:
            lines.append("· A股：" + " ｜ ".join(cn_items))
        else:
            lines.append("\n**A 股（上午盘收盘）**")
            lines.extend(cn_items)

    # ── 港股（12:00 上午盘收盘，用新浪实时数据）──
    # 🔥 2026-09-23：港股休市时新浪 spot 接口会返回**上一交易日**的值，
    #    配上「上午盘收盘」的标题会让读者误以为是今天的行情 → 休市日改为显式标注。
    #    ⚠️ 港股休市日 ≠ A股休市日（耶稣受难节 / 佛诞 / 圣诞 等 A股反而开市）。
    hk_open_fn = getattr(briefing, "is_hk_market_open", None)
    if hk_open_fn is None:
        from src.holiday_gate import is_hk_market_open as hk_open_fn

    if not hk_open_fn():
        lines.append("· **港股**：今日休市（无当日行情）" if compact else "\n**港股**：今日休市（无当日行情）")
    else:
        hk_items = []
        try:
            # 2026-09-23 超时保护：见 src/net_guard.py
            from src.net_guard import import_ak
            _ak = import_ak()
            df = _ak.stock_hk_index_spot_sina()
            target_names = [('恒生指数', '恒指'), ('恒生科技指数', '恒科')]
            if '名称' in df.columns:
                for name, short in target_names:
                    rows = df[df['名称'] == name]
                    if len(rows) > 0:
                        r = rows.iloc[0]
                        price = float(r['最新价'])
                        pct = float(r['涨跌幅'])
                        arrow = "🔺" if pct > 0 else "🔻" if pct < 0 else "➖"
                        if compact:
                            hk_items.append(f"{short} {arrow}{pct:+.2f}%")
                        else:
                            hk_items.append(f"· {name}: {price:,.2f}　{arrow}{pct:+.2f}%")
        except Exception:
            pass
        if hk_items:
            if compact:
                lines.append("· 港股：" + " ｜ ".join(hk_items))
            else:
                lines.append("\n**港股（上午盘收盘）**")
                lines.extend(hk_items)

    # ── 日经/KOSPI/台湾（优先 .info 实时价 → 日线兜底）──
    apac_items = []
    for ticker, full_name, short in [
        ('^N225', '日经225', '日经'),
        ('^KS11', '韩国KOSPI', '韩国'),
        ('^TWII', '台湾加权', '台湾'),
    ]:
        try:
            # 2026-09-23 超时保护：见 src/net_guard.py
            from src.net_guard import import_yf
            yf = import_yf()
            t = yf.Ticker(ticker)
            info = t.info
            now_price = info.get('regularMarketPrice') or info.get('currentPrice')
            prev_close = info.get('previousClose') or info.get('regularMarketPreviousClose')
            if now_price and prev_close:
                pct = round((now_price-prev_close)/prev_close*100,2)
                arrow = "🔺" if pct > 0 else "🔻" if pct < 0 else "➖"
                if compact:
                    apac_items.append(f"{short} {arrow}{pct:+.2f}%")
                else:
                    apac_items.append(f"· {full_name}: {now_price:,.2f}　{arrow}{pct:+.2f}%（实时）")
        except Exception:
            try:
                df = yf.Ticker(ticker).history(period='5d')
                if len(df) >= 2:
                    prev = float(df['Close'].iloc[-2])
                    today = float(df['Close'].iloc[-1])
                    pct = round((today-prev)/prev*100,2)
                    arrow = "🔺" if pct > 0 else "🔻" if pct < 0 else "➖"
                    if compact:
                        apac_items.append(f"{short} {arrow}{pct:+.2f}%")
                    else:
                        apac_items.append(f"· {full_name}: {today:,.2f}　{arrow}{pct:+.2f}%（收盘）")
            except Exception:
                pass
    if apac_items:
        if compact:
            lines.append("· 日韩台：" + " ｜ ".join(apac_items))
        else:
            lines.append("\n**亚太其他（实时）**")
            lines.extend(apac_items)

    if len(lines) == 1:
        return ""
    return "\n".join(lines)


def _build_global_market_snapshot(prefix: str = '') -> str:
    """全球市场快照。prefix=''时为各时段默认标签。"""
    from src import briefing
    md = getattr(briefing, "market_data", None)
    if md is None:
        from src import market_data as md

    hk_open_fn = getattr(briefing, "is_hk_market_open", None)
    if hk_open_fn is None:
        from src.holiday_gate import is_hk_market_open as hk_open_fn

    lines = ["**📊 全球市场**"]

    # ── 亚太收盘 ──
    apac_lines = []
    try:
        import os as _os
        for _k in ('http_proxy','https_proxy','HTTP_PROXY','HTTPS_PROXY','all_proxy','ALL_PROXY'):
            _os.environ.pop(_k, None)
        # 2026-09-23 超时保护：见 src/net_guard.py
        from src.net_guard import import_ak
        _ak = import_ak()
        for sym, name in [('sh000001','上证指数'), ('sz399001','深证成指'), ('sz399006','创业板指')]:
            try:
                df = _ak.stock_zh_index_daily_tx(symbol=sym)
                if len(df) >= 2:
                    prev = float(df['close'].iloc[-2])
                    today = float(df['close'].iloc[-1])
                    pct = round((today-prev)/prev*100,2)
                    arrow = "🔺" if pct > 0 else "🔻" if pct < 0 else "➖"
                    apac_lines.append(f"· {name}: {today:,.2f}　{arrow}{pct:+.2f}%")
            except Exception:
                pass
    except Exception:
        pass
    # 🔥 2026-09-23：港股休市时 spot / 日线取到的都是**上一交易日**的数据，
    #    小标题写的是「亚太收盘」（读起来像今日），加标注避免误读。
    _hk_stale = "" if hk_open_fn() else "（上一交易日）"
    for sym, name in [('HSI','恒生指数'), ('HSTECH','恒生科技')]:
        try:
            # 2026-09-23 超时保护：见 src/net_guard.py
            from src.net_guard import import_ak
            _ak = import_ak()
            # 优先用实时 spot（晚间/午间都是当前盘面）
            df = _ak.stock_hk_index_spot_sina()
            rows = df[df['名称']==name]
            if len(rows)>0:
                r = rows.iloc[0]
                price = float(r['最新价'])
                pct = float(r['涨跌幅'])
                arrow = "🔺" if pct > 0 else "🔻" if pct < 0 else "➖"
                apac_lines.append(f"· {name}: {price:,.2f}　{arrow}{pct:+.2f}%{_hk_stale}")
                continue
        except Exception:
            pass
        try:
            df = _ak.stock_hk_index_daily_sina(symbol=sym)
            if len(df) >= 2:
                prev = float(df['close'].iloc[-2])
                today = float(df['close'].iloc[-1])
                pct = round((today-prev)/prev*100,2)
                arrow = "🔺" if pct > 0 else "🔻" if pct < 0 else "➖"
                apac_lines.append(f"· {name}: {today:,.2f}　{arrow}{pct:+.2f}%{_hk_stale}")
        except Exception:
            pass
    for ticker, name in [('^N225','日经225'), ('^KS11','韩国KOSPI'), ('^TWII','台湾加权')]:
        try:
            # 2026-09-23 超时保护：见 src/net_guard.py
            from src.net_guard import import_yf
            yf = import_yf()
            t = yf.Ticker(ticker)
            info = t.info
            now_price = info.get('regularMarketPrice') or info.get('currentPrice')
            prev_close = info.get('previousClose') or info.get('regularMarketPreviousClose')
            if now_price and prev_close:
                pct = round((now_price-prev_close)/prev_close*100,2)
                arrow = "🔺" if pct > 0 else "🔻" if pct < 0 else "➖"
                apac_lines.append(f"· {name}: {now_price:,.2f}　{arrow}{pct:+.2f}%")
        except Exception:
            try:
                df = yf.Ticker(ticker).history(period='5d')
                if len(df) >= 2:
                    prev = float(df['Close'].iloc[-2])
                    today = float(df['Close'].iloc[-1])
                    pct = round((today-prev)/prev*100,2)
                    arrow = "🔺" if pct > 0 else "🔻" if pct < 0 else "➖"
                    apac_lines.append(f"· {name}: {today:,.2f}　{arrow}{pct:+.2f}%")
            except Exception:
                pass
    if apac_lines:
        lines.append("\n**亚太收盘**")
        lines.extend(apac_lines)

    # ── 美股 ──
    us_lines = []
    for ticker, name in [('^GSPC','标普500'), ('^IXIC','纳斯达克')]:
        try:
            data = md.fetch_us_index(ticker)
            if data:
                a = "🔺" if data['change_pct'] > 0 else "🔻" if data['change_pct'] < 0 else "➖"
                us_lines.append(f"· {name}: {data['close']:,.2f}　{a}{data['change_pct']:+.2f}%")
        except Exception:
            pass
    for ticker, name in [('^DJI','道琼斯'), ('^SOX','费城半导体')]:
        try:
            data = md.fetch_us_index(ticker)
            if data:
                a = "🔺" if data['change_pct'] > 0 else "🔻" if data['change_pct'] < 0 else "➖"
                us_lines.append(f"· {name}: {data['close']:,.2f}　{a}{data['change_pct']:+.2f}%")
        except Exception:
            pass
    if us_lines:
        lines.append("\n**美股收盘**")
        lines.extend(us_lines)

    if len(lines) == 1:
        return ""
    return "\n".join(lines)


def _get_us_dst_reminder(now_cn: datetime | None = None) -> str:
    """美股夏令时/冬令时双向换季防忘提醒。

    机理：
    - 夏令时期间（美东 EDT，美股 21:30 开盘）：期望夜盘在 21:00 左右触发。
      若检测到运行时刻晚于 21:40（例如用户依然保留冬令时的 22:00 定时），提醒调回 21:00。
    - 冬令时期间（美东 EST，美股 22:30 开盘）：期望夜盘在 22:00 左右触发。
      若检测到运行时刻早于 21:20（例如用户依然保留夏令时的 21:00 定时），提醒调整为 22:00。
    - 匹配正常时返回空串（零打扰）。
    """
    from zoneinfo import ZoneInfo
    tz_ny = ZoneInfo("America/New_York")
    if now_cn is None:
        now_cn = datetime.now(tz_cn)

    try:
        now_ny = now_cn.astimezone(tz_ny)
        is_dst = bool(now_ny.dst())  # True=夏令时, False=冬令时
        t = now_cn.time()

        if is_dst:
            # 夏令时下，若在 21:40 之后运行（例如依然保留冬令时 22:00 定时）
            if t.hour >= 22 or (t.hour == 21 and t.minute >= 40):
                return "> 💡 **换季提醒**：美股已切换为夏令时（21:30 开盘）。若希望在开盘前半小时收到前瞻，可将飞书夜盘定时器调回 21:00。\n"
        else:
            # 冬令时下，若在 21:20 之前运行（例如依然保留夏令时 21:00 定时）
            if t.hour == 21 and t.minute < 20:
                return "> 💡 **换季提醒**：美股已切换为冬令时（22:30 开盘）。建议将飞书夜盘定时器调整为 22:00，净值公布更全、盘前情绪更准。\n"
    except Exception as e:
        logger.debug("计算美股 DST 换季提醒异常: %s", e)

    return ""
