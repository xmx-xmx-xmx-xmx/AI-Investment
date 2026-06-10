# ⚠️ 兼容性说明

## 当前状态

`data_provider/` 目录是从 [daily_stock_analysis](https://github.com/anthropics/daily_stock_analysis) 开源项目中**完整提取**的多源行情数据适配层。

## 重要提示

**该目录中的 `base.py`（DataFetcherManager 调度器）目前无法直接运行**，因为它在深度上依赖开源项目的 `src/` 基础设施：

- `src.data.stock_index_loader` — 股票名称索引
- `src.data.stock_mapping` — 代码映射表
- `src.services.run_diagnostics` — 诊断记录
- `src.config` — 完整配置系统

## 使用方式

1. **作为参考代码库**：每个独立的 fetcher 模块（如 `akshare_fetcher.py`、`yfinance_fetcher.py`）可以单独阅读和使用
2. **逐文件迁移**：当需要使用某个特定数据源的 fallback 逻辑时，从对应文件中提取需要的函数即可
3. **当前生产代码不使用它**：`src/advisor.py` 和 `src/market_brief.py` 直接通过 `yfinance` 和 `akshare` pip 包获取数据

## 未来计划

如果需要启用多源 fallback 和自动调度能力，需要：
1. 从开源项目提取 `src/data/` 索引模块
2. 从开源项目提取 `src/services/run_diagnostics.py`
3. 创建轻量级 `config.py` 兼容层

在此之前，各 fetcher 文件中的具体数据获取逻辑仍然有价值，可以作为代码参考。
