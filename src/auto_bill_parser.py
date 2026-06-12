import os
import json
import base64
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests
from openai import OpenAI

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── 配置 ──────────────────────────────────────────────────
# 所有密钥从环境变量读取。本地开发：项目根目录 .env 文件（gitignored）
# GitHub Actions：Repo Settings → Secrets 中配置


SILICONFLOW_API_KEY = os.environ.get("SILICONFLOW_API_KEY", "")
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
FEISHU_BITABLE_TOKEN = os.environ.get("FEISHU_BITABLE_TOKEN", "")
FEISHU_TABLE_ID = os.environ.get("FEISHU_TABLE_ID", "tblbnD3uaEdohjji")

# ── 系统提示词 ────────────────────────────────────────────
SYSTEM_PROMPT = """<system_role>
你是一个高精度的金融票据 OCR 自动化解析网关。
</system_role>

<input_context>
你收到的文本不是标准表格，而是通过 iOS 本地快捷指令 OCR 提取的原始粗糙文本。
排版和换行可能已丢失，数字和金额可能混在周围的文字里。
请通过语义理解，在混乱的文本瀑布中精准寻找关键的金额、产品名称和交易日期。
</input_context>

<extraction_rules>
提取以下字段并输出纯 JSON：

1. "order_id": 字符串。严格提取「订单号」或「交易单号」。
2. "product_name": 字符串。提取买入或卖出的产品/基金完整官方名称。
   常见示例：摩根标普500指数(QDII)C、易方达沪深300ETF联接C、建信短债债券C
3. "amount": 浮点数。提取交易总金额，去除「元」字，保留两位小数。
4. "trade_time": 字符串。提取交易时间并转为 "YYYY-MM-DD HH:MM:SS" 格式。
   如仅出现日期（无时分秒），默认补齐为 "YYYY-MM-DD 15:00:00"。
5. "action": 字符串。只能输出 "buy"（买入/申购/定投）或 "sell"（卖出/赎回）。
</extraction_rules>

<constraints>
- 输出必须是一个纯 JSON 对象，以 { 开头，以 } 结尾。
- 严禁使用 ```json 或 ``` 等 Markdown 代码块包裹。
- 严禁任何前缀、后缀、旁白、问候语或解释性文字。
- 如果某项信息完全无法找到，填空字符串 ""。
- JSON 必须合法：键名和字符串用双引号，数字不用引号。
</constraints>

<few_shot_example>
输入文本：
"摩根标普500指数(QDII)C 基金 买入 确认 金额 100.00 元 交易单号 20260612001080 0122046 2026-06-12 14:38"

正确输出：
{"order_id": "202606120010800122046", "product_name": "摩根标普500指数(QDII)C", "amount": 100.00, "trade_time": "2026-06-12 14:38:30", "action": "buy"}

错误输出（严禁）：
```json
{"order_id": "..."}
```
（因为用了 Markdown 代码块包裹 —— 这是违规的）
</few_shot_example>"""

# ── 视觉大模型解析 ────────────────────────────────────────

def _image_to_base64(image_path: str) -> str:
    """将本地图片文件转为 base64 data URL 字符串。"""
    with open(image_path, "rb") as f:
        raw = f.read()
    ext = os.path.splitext(image_path)[1].lower().lstrip(".")
    if ext == "jpg":
        ext = "jpeg"
    mime = f"image/{ext}"
    b64 = base64.b64encode(raw).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def parse_image_to_json(image_path: str) -> dict:
    """调用硅基流动视觉模型，从截图中提取交易结构化 JSON。"""
    client = OpenAI(
        base_url="https://api.siliconflow.cn/v1",
        api_key=SILICONFLOW_API_KEY,
    )

    base64_data = _image_to_base64(image_path)

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": SYSTEM_PROMPT},
                {"type": "image_url", "image_url": {"url": base64_data}},
            ],
        }
    ]

    logger.info("调用视觉模型解析图片: %s", image_path)
    response = client.chat.completions.create(
        model="Qwen/Qwen3-VL-8B-Instruct",
        messages=messages,
    )

    raw = response.choices[0].message.content.strip()
    logger.info("模型原始返回: %s", raw[:200])

    # 清理可能的 markdown 包裹
    raw = raw.replace("```json", "").replace("```", "").strip()
    return json.loads(raw)


# ── 飞书 API 交互 ─────────────────────────────────────────

FEISHU_AUTH_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
FEISHU_LIST_FIELDS_URL = "https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
FEISHU_SEARCH_URL = "https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/search"
FEISHU_CREATE_URL = "https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"

# 你的飞书表格中，界面显示的列名 → 代码内部逻辑名的映射
# 左边是飞书表格实际列名，右边是代码引用 key（固定不变）
FIELD_NAME_MAP = {
    "交易单号": "order_id",
    "产品名称": "product_name",
    "交易金额": "amount",
    "交易时间": "trade_time",
    "买卖方向": "action",
    "状态": "status",
}
# 反向映射：代码内部 key → 飞书界面列名（搜索接口需要列名）
INTERNAL_TO_DISPLAY = {v: k for k, v in FIELD_NAME_MAP.items()}


def get_tenant_access_token() -> str:
    """获取飞书 tenant_access_token。"""
    resp = requests.post(
        FEISHU_AUTH_URL,
        json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("code") != 0:
        raise RuntimeError(f"获取飞书 token 失败: {body}")
    return body["tenant_access_token"]


def fetch_field_id_map(token: str) -> dict:
    """从飞书表格获取 {界面列名: 字段ID} 的映射。

    飞书 API 的搜索和写入都需要用字段 ID（fldxxxx），不能用界面列名。
    这里先获取所有字段，再按 FIELD_NAME_MAP 翻译成代码内部 key。
    """
    resp = requests.get(
        FEISHU_LIST_FIELDS_URL.format(app_token=FEISHU_BITABLE_TOKEN, table_id=FEISHU_TABLE_ID),
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("code") != 0:
        raise RuntimeError(f"获取表格字段列表失败: {body}")

    name_to_field_id = {}
    for item in body.get("data", {}).get("items", []):
        name_to_field_id[item["field_name"]] = item["field_id"]

    logger.info("表格字段映射: %s", json.dumps(name_to_field_id, ensure_ascii=False))

    # 翻译成代码内部 key → field_id
    result = {}
    for display_name, internal_key in FIELD_NAME_MAP.items():
        fid = name_to_field_id.get(display_name)
        if fid:
            result[internal_key] = fid
        else:
            logger.warning("未在表格中找到列「%s」，请在 FIELD_NAME_MAP 中检查列名是否正确", display_name)

    return result


def check_order_exists(order_id: str, token: str, field_map: dict) -> bool:
    """查询飞书多维表格中是否已存在该订单号。"""
    order_field_id = field_map.get("order_id")
    if not order_field_id:
        raise RuntimeError("未找到「订单号」对应的字段 ID，请检查 FIELD_NAME_MAP")

    # 搜索接口的 field_name 需要界面列名，不是字段 ID
    display_name = INTERNAL_TO_DISPLAY.get("order_id", "交易单号")

    resp = requests.post(
        FEISHU_SEARCH_URL.format(app_token=FEISHU_BITABLE_TOKEN, table_id=FEISHU_TABLE_ID),
        json={
            "filter": {
                "conjunction": "and",
                "conditions": [
                    {
                        "field_name": display_name,
                        "operator": "is",
                        "value": [order_id],
                    }
                ],
            }
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("code") != 0:
        raise RuntimeError(f"查询飞书表格失败: {body}")
    items = body.get("data", {}).get("items", [])
    return len(items) > 0


def add_trade_record(data: dict, token: str, field_map: dict) -> None:
    """将交易记录写入飞书多维表格。"""
    fields = {}
    field_pairs = [
        ("order_id", data.get("order_id", "")),
        ("product_name", data.get("product_name", "")),
        ("amount", data.get("amount", 0.0)),
        ("trade_time", data.get("trade_time", "")),
        ("action", data.get("action", "")),
        ("status", "pending"),
    ]
    for internal_key, value in field_pairs:
        # 写入接口 fields 的 key 必须是界面列名，不是字段 ID
        display_name = INTERNAL_TO_DISPLAY.get(internal_key)
        if not display_name:
            logger.warning("未找到字段 %s 的界面列名，跳过写入", internal_key)
            continue
        if internal_key not in field_map:
            logger.warning("字段「%s」不在表格中，跳过写入", display_name)
            continue

        # 日期字段需要毫秒级 Unix 时间戳
        if internal_key == "trade_time" and isinstance(value, str) and value:
            try:
                CST = timezone(timedelta(hours=8))
                dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
                value = int(dt.replace(tzinfo=CST).timestamp() * 1000)
            except ValueError:
                logger.warning("无法解析 trade_time 为时间戳: %s，保留原始值", value)

        fields[display_name] = value

    if not fields:
        raise RuntimeError("没有可写入的字段，请检查 FIELD_NAME_MAP 配置")

    resp = requests.post(
        FEISHU_CREATE_URL.format(app_token=FEISHU_BITABLE_TOKEN, table_id=FEISHU_TABLE_ID),
        json={"fields": fields},
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("code") != 0:
        raise RuntimeError(f"写入飞书表格失败: {body}")


# ── 主控流程 ──────────────────────────────────────────────

def main(image_path: Optional[str] = None):
    if image_path is None:
        image_path = "test.png"

    logger.info("开始处理: %s", image_path)

    # 1. 视觉模型解析
    try:
        data = parse_image_to_json(image_path)
        logger.info("解析结果: %s", json.dumps(data, ensure_ascii=False))
    except json.JSONDecodeError as e:
        logger.error("模型返回内容无法解析为 JSON: %s", e)
        raise
    except Exception as e:
        logger.error("调用视觉模型失败: %s", e)
        raise

    # 2. 校验 order_id
    order_id = data.get("order_id", "").strip()
    if not order_id:
        raise ValueError("截图中未提取到 order_id，解析结果: %s" % json.dumps(data, ensure_ascii=False))

    # 3. 获取飞书 token 并拉取字段映射
    try:
        token = get_tenant_access_token()
        field_map = fetch_field_id_map(token)
    except Exception as e:
        logger.error("获取飞书 token 或字段映射失败: %s", e)
        raise

    try:
        if check_order_exists(order_id, token, field_map):
            logger.info("订单 %s 已存在，跳过写入。", order_id)
            return
    except Exception as e:
        logger.error("查询飞书表格失败: %s", e)
        raise

    # 4. 写入飞书
    try:
        add_trade_record(data, token, field_map)
        logger.info("订单 %s 写入成功。", order_id)
    except Exception as e:
        logger.error("写入飞书表格失败: %s", e)
        raise


if __name__ == "__main__":
    main()
