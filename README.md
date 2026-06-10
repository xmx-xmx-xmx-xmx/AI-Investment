# AI 量化投资系统

> 不预测市场，只执行纪律。飞书多维表格 = 唯一数据库 + 展示看板。GitHub Actions 白嫖运行。

## 核心原则

1. **极简前端**：100% 依赖飞书多维表格（Bitable）作为数据库和展示看板
2. **铁血纪律**：只算资产大类偏离度，抛弃所有技术指标玄学
3. **情绪中枢**：暴跌时结合仓位大小提供心理安抚，宏观事件翻译成大白话
4. **零成本运行**：完全依靠 GitHub Actions 定时触发

## 项目结构

```
.
├── main.py                     # 总入口
├── requirements.txt            # 依赖清单
├── .env.example                # 环境变量模板
├── src/                        # 核心业务代码
│   ├── advisor.py              # 资产偏离度计算 + AI 再平衡建议
│   ├── market_brief.py         # 市场快报生成
│   ├── auto_bill_parser.py     # 基金票据 OCR → 飞书 Bitable
│   └── prompts.md              # Prompt 参考
├── data_provider/              # 多源行情数据适配层（从 daily_stock_analysis 提取）
├── .github/workflows/
│   └── daily-run.yml           # 定时任务：工作日 18:00 自动运行
└── _legacy_backup/             # 旧代码备份（开源项目 + 旧截图）
```

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env
# 编辑 .env 填入真实 API Key

# 运行
python main.py                    # 完整流程
python main.py --brief            # 仅市场快报
python main.py --deviation-only   # 仅偏离度计算
python main.py --parse-bill xxx.png  # 解析基金截图
```

## 飞书多维表格配置

在 GitHub Repo → Settings → Secrets 中配置：

| Secret | 说明 |
|--------|------|
| `DEEPSEEK_API_KEY` | DeepSeek 大模型 API Key |
| `FEISHU_APP_ID` | 飞书应用 App ID |
| `FEISHU_APP_SECRET` | 飞书应用 Secret |
| `FEISHU_BITABLE_TOKEN` | 多维表格 token |
| `FEISHU_TABLE_ID` | 表格 ID |
| `SILICONFLOW_API_KEY` | 硅基流动（票据 OCR） |
| `FEISHU_WEBHOOK_URL` | 飞书群机器人（可选） |

## 投资纪律

| 资产大类 | 目标权重 |
|----------|----------|
| 美股资产 | 40% |
| A股资产 | 20% |
| 港股资产 | 20% |
| 避险商品 | 20% |

- 偏离度红线：±3%，超配止盈，低配加仓
- 长期底仓标的无论浮亏多少，严禁割肉
