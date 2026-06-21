# D1 全球行情直连 —— 设计文档

> 日期：2026-06-21
> 状态：已确认
> 关联：[[TODO.md]] D1 项

## 概述

在 `src/market_data.py` 新增三大美股指数和美债收益率抓取函数，精简 VIX 数据源，不修改任何消费端。

---

## 一、新增函数

### 1.1 `fetch_us_index(ticker)` — 美股三大指数

**返回格式**（与现有 `fetch_us_etf()` 一致）：

```python
{"ticker": "^DJI", "name": "道琼斯工业指数", "market": "美股",
 "close": 42000.0, "change_pct": +0.35, "source": "yfinance"}
```

**注册表**：

```python
US_INDEX_MAP = {
    "^DJI":  "道琼斯工业指数",
    "^GSPC": "标普500指数",
    "^IXIC": "纳斯达克综合指数",
}
```

**数据源优先级**：

| 层级 | 数据源 | 说明 |
|------|--------|------|
| 1 | `yfinance.Ticker(ticker).history(period="5d")` | 主源，海外网络直连 |
| 2 | `akshare.index_global_hist_em(symbol)` | 兜底，国内可用 |

失败返回 `None`，与现有函数行为一致。

**批量函数** `fetch_us_indices(tickers: list[str]) -> list[dict]`：循环调用
`fetch_us_index()`，每只间隔 0.2s 限速。

### 1.2 `fetch_us_treasury()` — 美债收益率

**数据源**：`akshare.bond_zh_us_rate()` 单源（接口稳定，9281 行历史数据覆盖至
2026-06-18）。

**返回格式**：

```python
{"date": "2026-06-18",
 "us_2y": 4.19, "us_10y": 4.46, "us_10y2y_spread": 0.27,
 "source": "akshare_bond"}
```

无 fallback——该接口取中美两国所有期限债券数据，一行调用即可拿到 2Y/10Y/
预计算利差。

---

## 二、VIX 精简

**现状**：三源 fallback（Yahoo v8 API → yfinance → akshare），其中 Yahoo API 和
yfinance 本质是同一后端的不同访问方式，冗余。

**改后**：两源 fallback

| 层级 | 数据源 |
|------|--------|
| 1 | `yfinance.Ticker("^VIX").history()` |
| 2 | `akshare.index_global_hist_em("VIX")` |

删除 `requests` 直连 Yahoo v8 API 的代码块，简化 ~15 行。

---

## 三、`snapshot()` 更新

新增两个字段，其余不变（向后兼容）：

```python
result["us_indices"] = fetch_us_indices(["^DJI", "^GSPC", "^IXIC"])
result["us_treasury"] = fetch_us_treasury()
```

返回结构：

```python
{
    "cn_etfs": [...],      # 不变
    "us_etfs": [...],      # 不变
    "hk_stocks": [...],    # 不变
    "vix": {...},          # 数据源精简，字段不变
    "us_indices": [...],   # 新增：三大指数
    "us_treasury": {...},  # 新增：美债收益率
    "ok": True/False,
}
```

---

## 四、消费者不变

| 消费者 | 当前用途 | D1 后 |
|--------|---------|-------|
| `price_updater.py` | 路由用 `fetch_cn_etf` / `fetch_us_etf` / `fetch_hk_stock` | 不变 |
| `briefing.py` | 调用 `fetch_vix()`、`fetch_us_etf("SPY")`、`fetch_us_etf("QQQ")` | 不变 |
| `advisor.py` | 调用 `fetch_vix()` | 不变 |
| `notify.py` | 调用 `fetch_vix()` | 不变 |

后续简报/周报升级时再注入三大指数和美债数据。本期只铺数据管道。

---

## 五、测试

新增 `tests/test_market_data.py`，使用 `pytest` + `monkeypatch` 模拟外部依赖：

- `fetch_us_index`：两源正常返回、第一源失败走 fallback、全部失败返回 None
- `fetch_us_treasury`：正常返回、akshare 异常返回 None
- `fetch_vix`：新版两源返回、第一源失败走 fallback、全部失败返回 None
- `fetch_us_indices`：批量正常、部分失败不影响其他
- `snapshot`：新增 `us_indices` / `us_treasury` 字段存在性检查

---

## 六、变更文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/market_data.py` | 修改 | 新增 `fetch_us_index` / `fetch_us_indices` / `fetch_us_treasury`；精简 `fetch_vix`；更新 `snapshot()` |
| `tests/test_market_data.py` | 新建 | 单元测试 |

---

## 七、不在范围

- 不新增 pip 依赖（yfinance、akshare 已在 pyproject.toml 中）
- 不动消费端代码
- 不动飞书表结构
- 不涉及 `pending_resolver.py` FUND_NAME_MAPPING（TODO 已标记完成）
