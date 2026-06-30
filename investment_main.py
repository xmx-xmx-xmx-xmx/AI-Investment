#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===================================
AI 量化投资系统 —— 总入口
===================================

核心理念：
1. 不预测市场，只做量化纪律执行
2. 按资产大类（美股40%/A股20%/港股20%/商品20%）管理目标权重
3. 通过偏离度阈值触发定投或分批止盈
4. 飞书多维表格 = 唯一数据库 + 展示看板
5. GitHub Actions 定时触发，零成本运行

使用方式：
    python main.py                    # 完整流程
    python main.py --brief            # 仅生成市场快报
    python main.py --deviation-only   # 仅计算偏离度并写入飞书
    python main.py --parse-bill <img> # 解析基金账单截图
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AI 量化投资系统 —— 资产标签化再平衡",
    )
    parser.add_argument(
        "--brief", action="store_true",
        help="仅生成今日市场快报"
    )
    parser.add_argument(
        "--deviation-only", action="store_true",
        help="仅计算偏离度并写入飞书多维表格"
    )
    parser.add_argument(
        "--parse-bill", type=str, default=None, metavar="IMAGE_PATH",
        help="解析基金交易截图并写入飞书表格"
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="启用调试日志"
    )
    return parser.parse_args()


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

def main() -> int:
    args = parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    tz_cn = timezone(timedelta(hours=8))
    now = datetime.now(tz_cn)
    logger.info("=" * 56)
    logger.info("  AI 量化投资系统 —— 启动")
    logger.info("  时间: %s", now.strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("=" * 56)

    try:
        # ── 模式 1: 票据解析 ──
        if args.parse_bill:
            logger.info("模式: 基金票据 OCR 解析")
            from src.auto_bill_parser import main as bill_main
            bill_main(image_path=args.parse_bill)
            return 0

        # ── 模式 2: 仅市场快报 ──
        if args.brief:
            logger.info("模式: 市场快报")
            from src.market_brief import main as brief_main
            brief_main()
            return 0

        # ── 模式 3: 仅偏离度计算 ──
        if args.deviation_only:
            logger.info("模式: 偏离度稽查")
            from src.advisor import main as advisor_main
            advisor_main()
            return 0

        # ── 模式 4: 完整流程（默认） ──
        logger.info("模式: 完整日报流程")

        # Step 1: 偏离度稽查 + AI 建议
        logger.info("[1/3] 资产大类偏离度稽查...")
        from src.advisor import main as advisor_main
        advisor_main()

        # Step 2: 市场快报
        logger.info("[2/3] 生成市场快报...")
        from src.market_brief import main as brief_main
        brief_main()

        # Step 3: 飞书多维表格同步（如果配置了票据解析路径则自动执行）
        logger.info("[3/3] 流程完成")

        return 0

    except KeyboardInterrupt:
        logger.info("用户中断")
        return 130
    except Exception as e:
        logger.exception("执行失败: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
