#!/usr/bin/env python3
"""
===========================================
MVP 冒烟测试 —— 验证数据大动脉是否缝合成功
===========================================

目标：
  1. akshare 抓取 中证红利ETF (515080) + 纳指ETF (159941)
  2. yfinance 抓取 QQQ + GLD + VIX
  3. 终端打印结果

用法：
  python test_mvp.py

注意：此脚本完全不依赖 data_provider/
"""

from __future__ import annotations

import sys
import os

# 确保 src/ 在 Python path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.market_data import fetch_cn_etf, fetch_us_etf, fetch_vix


def main():
    print()
    print("=" * 60)
    print("   🔬 MVP 冒烟测试 —— 数据大动脉验证")
    print("=" * 60)

    all_ok = True

    # ── 测试 1: A 股 ETF ──
    print("\n── [1] A 股 ETF 行情（akshare）──")
    cn_tests = [
        ("515080", "中证红利ETF"),
        ("159941", "纳指ETF"),
    ]

    cn_results = []
    for code, label in cn_tests:
        print(f"  📡 正在获取 {label} ({code})...")
        data = fetch_cn_etf(code)
        if data:
            arrow = "🔺" if data["change_pct"] > 0 else "🔻" if data["change_pct"] < 0 else "➖"
            print(f"     {arrow} {data['name']} | 收盘 ¥{data['close']} | "
                  f"{data['change_pct']:+.2f}% | 源: {data['source']}")
            cn_results.append(data)
        else:
            print(f"     ❌ {label} 获取失败")
            all_ok = False

    # ── 测试 2: 美股 ETF ──
    print("\n── [2] 美股 ETF 行情（yfinance）──")
    us_tests = [
        ("QQQ", "纳斯达克100"),
        ("GLD", "黄金ETF"),
    ]

    us_results = []
    for ticker, label in us_tests:
        print(f"  📡 正在获取 {label} ({ticker})...")
        data = fetch_us_etf(ticker)
        if data:
            arrow = "🔺" if data["change_pct"] > 0 else "🔻" if data["change_pct"] < 0 else "➖"
            print(f"     {arrow} {data['name']} | 收盘 ${data['close']} | "
                  f"{data['change_pct']:+.2f}% | 源: {data['source']}")
            us_results.append(data)
        else:
            print(f"     ❌ {label} 获取失败")
            all_ok = False

    # ── 测试 3: VIX ──
    print("\n── [3] VIX 恐慌指数（yfinance）──")
    print("  📡 正在获取 VIX...")
    vix = fetch_vix()
    if vix:
        emoji = "😱" if vix["vix"] >= 30 else "😰" if vix["vix"] >= 25 else "😐" if vix["vix"] >= 20 else "😌" if vix["vix"] >= 15 else "🥱"
        print(f"     {emoji} VIX = {vix['vix']:.2f}  ({vix['level']})")
    else:
        print("     ⚠️  VIX 获取失败（可能网络问题，不影响核心功能）")

    # ── 汇总 ──
    print("\n" + "=" * 60)
    print(f"   A 股 ETF: {len(cn_results)}/2 成功")
    print(f"   美股 ETF: {len(us_results)}/2 成功")
    print(f"   VIX:      {'✅' if vix else '⚠️'}")

    if all_ok:
        print("\n   ✅ 数据大动脉缝合成功！所有品种获取正常。")
        print("=" * 60)
        return 0
    else:
        print("\n   ⚠️  部分数据获取失败，但系统未崩溃。")
        print("   请检查：网络是否通畅，pip 依赖是否安装完整。")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    sys.exit(main())
