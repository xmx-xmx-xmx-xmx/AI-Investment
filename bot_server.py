"""
飞书群聊机器人 MVP — FastAPI 后端。

职责：
- 接收飞书事件订阅的 POST 回调
- 处理 URL 握手验证
- 响应群内消息（MVP 阶段固定回复）

部署：Render Web Service，启动命令 `uvicorn bot_server:app --host 0.0.0.0 --port $PORT`
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# Render/本地环境变量兼容
from dotenv import load_dotenv
load_dotenv()

# ═══════════════════════════════════════════════════════════════
# 配置（全部从环境变量读取）
# ═══════════════════════════════════════════════════════════════

FEISHU_APP_ID = os.getenv("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")
FEISHU_VERIFY_TOKEN = os.getenv("FEISHU_VERIFY_TOKEN", "")  # 飞书后台配置的 Verification Token

logger = logging.getLogger("bot_server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

app = FastAPI(title="量化大盘军师 Bot", version="0.1.0")

# ── Token 缓存 ──
_token_cache: dict = {"token": "", "expires_at": 0.0}

MVP_REPLY = "【量化大盘军师 MVP】双向通道已成功拔通！听到你的指令了，后续深度逻辑正在接入中..."


# ═══════════════════════════════════════════════════════════════
# 指令处理
# ═══════════════════════════════════════════════════════════════

def _extract_text(msg_content: str | dict) -> str:
    """从飞书消息 content 中提取纯文本。"""
    try:
        if isinstance(msg_content, str):
            obj = json.loads(msg_content)
        else:
            obj = msg_content
        return (obj.get("text", "") or "").strip()
    except (json.JSONDecodeError, TypeError):
        return str(msg_content).strip()


def _handle_cruise() -> str:
    """执行巡航指令：实时计算仓位健康报告。"""
    try:
        from src.strategy import judge_from_feishu
        verdict = judge_from_feishu()
        health = verdict.get("health_report", "")
        total = verdict.get("total_value", 0)
        if health:
            return f"**📡 实时仓位巡航**\n\n{health}\n\n🔔 总市值 ¥{total:,.2f}"
        return "巡航数据暂时不可用，请稍后再试。"
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logger.error("巡航失败:\n%s", tb)
        return f"巡航计算失败: {e}"


_CMD_PATTERNS = [
    (["巡航", "状态", "仓位", "健康"], _handle_cruise),
]


# ═══════════════════════════════════════════════════════════════
# 飞书 tenant_access_token
# ═══════════════════════════════════════════════════════════════

def _get_tenant_access_token() -> str:
    """获取飞书 tenant_access_token（带内存缓存，过期自动刷新）。"""
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"] - 60:
        return _token_cache["token"]

    if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
        logger.error("FEISHU_APP_ID 或 FEISHU_APP_SECRET 未配置")
        return ""

    try:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
            timeout=10,
        )
        data = resp.json()
        if data.get("code") != 0:
            logger.error("获取 tenant_access_token 失败: %s", data.get("msg", ""))
            return ""
        token = data["tenant_access_token"]
        expire = int(data.get("expire", 7200))
        _token_cache["token"] = token
        _token_cache["expires_at"] = now + expire
        logger.info("tenant_access_token 已刷新（过期时间 %ds）", expire)
        return token
    except Exception as e:
        logger.error("获取 tenant_access_token 异常: %s", e)
        return ""


# ═══════════════════════════════════════════════════════════════
# 消息发送（异步模式：先回 200，后台计算完再推送）
# ═══════════════════════════════════════════════════════════════

def _reply_message(message_id: str, content: str) -> bool:
    """引用回复飞书消息。"""
    token = _get_tenant_access_token()
    if not token:
        return False

    body = {
        "content": json.dumps({"text": content}),
        "msg_type": "text",
    }
    try:
        resp = requests.post(
            f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reply",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=10,
        )
        data = resp.json()
        if data.get("code") != 0:
            logger.error("回复消息失败: %s", data.get("msg", ""))
            return False
        logger.info("消息已回复: %s", message_id)
        return True
    except Exception as e:
        logger.error("回复消息异常: %s", e)
        return False


def _send_message_to_chat(chat_id: str, content: str) -> bool:
    """主动向群聊发送新消息（用于后台异步推送）。"""
    token = _get_tenant_access_token()
    if not token:
        return False

    body = {
        "receive_id": chat_id,
        "msg_type": "text",
        "content": json.dumps({"text": content}),
    }
    try:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=10,
        )
        data = resp.json()
        if data.get("code") != 0:
            logger.error("发送群消息失败: %s", data.get("msg", ""))
            return False
        logger.info("群消息已发送: %s", chat_id)
        return True
    except Exception as e:
        logger.error("发送群消息异常: %s", e)
        return False


def _run_command_async(chat_id: str, text: str):
    """后台线程：执行指令 → 将结果推送到群。"""
    import threading

    def _worker():
        try:
            reply_text = MVP_REPLY
            for keywords, handler in _CMD_PATTERNS:
                if any(kw in text for kw in keywords):
                    reply_text = handler()
                    break
            _send_message_to_chat(chat_id, reply_text)
        except Exception as e:
            logger.error("后台指令执行失败: %s", e)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()


# ═══════════════════════════════════════════════════════════════
# 路由
# ═══════════════════════════════════════════════════════════════

@app.get("/")
async def root():
    return {"status": "ok", "service": "量化大盘军师 Bot"}


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.post("/feishu/webhook")
async def feishu_webhook(request: Request):
    """飞书事件订阅回调入口。

    处理两类请求：
    1. URL 握手验证：body 含 challenge → 原样返回
    2. 事件推送：解析消息事件 → 固定回复
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    logger.info("收到飞书回调: %s", json.dumps(body, ensure_ascii=False)[:500])

    # ── URL 握手验证 ──
    # 旧版事件格式：顶层 type="url_verification"
    if body.get("type") == "url_verification":
        challenge = body.get("challenge", "")
        logger.info("URL 验证（旧版）: challenge=%s", challenge[:20] if challenge else "空")
        return JSONResponse({"challenge": challenge})

    # 新版 v2 事件格式：header.event_type = "url_verification"
    header = body.get("header", {})
    if header.get("event_type") == "url_verification":
        challenge = body.get("event", {}).get("challenge", "")
        logger.info("URL 验证（v2）: challenge=%s", challenge[:20] if challenge else "空")
        return JSONResponse({"challenge": challenge})

    # ── Token 验证（可选，安全加固）──
    if FEISHU_VERIFY_TOKEN and header.get("token") != FEISHU_VERIFY_TOKEN:
        logger.warning("Verification Token 不匹配，忽略请求")
        return JSONResponse({"error": "invalid token"}, status_code=403)

    # ── 事件处理 ──
    event_type = header.get("event_type", "")
    event = body.get("event", {})

    # 旧版兼容：顶层 event_type
    if not event_type:
        event_type = body.get("event_type", "")
        event = body.get("event", body)

    logger.info("事件类型: %s", event_type)

    if event_type == "im.message.receive_v1":
        message = event.get("message", {})
        message_id = message.get("message_id", "")
        chat_type = message.get("chat_type", "")
        chat_id = message.get("chat_id", event.get("chat_id", ""))  # 两种可能的字段名
        if not chat_id:
            chat_id = event.get("chat_id", "")
        msg_content = message.get("content", "{}")

        # 解析消息文本
        try:
            content_obj = json.loads(msg_content) if isinstance(msg_content, str) else msg_content
            text = content_obj.get("text", "")
        except (json.JSONDecodeError, TypeError):
            text = str(msg_content)

        logger.info("收到群消息 | chat=%s | text=%s", chat_type, text[:100])

        if message_id and chat_type == "group":
            # 检查是否是指令 → 异步执行，先回飞书 200 OK
            is_command = any(
                any(kw in text for kw in keywords)
                for keywords, _ in _CMD_PATTERNS
            )
            if is_command and chat_id:
                _reply_message(message_id, "收到，正在调取大盘数据进行分析，请稍候...")
                _run_command_async(chat_id, text)
            elif not is_command:
                _reply_message(message_id, MVP_REPLY)

    # 快速返回 200 OK，所有耗时操作已放入后台线程
    return JSONResponse({"code": 0})


# ═══════════════════════════════════════════════════════════════
# 启动入口
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    logger.info("启动 Bot 服务，监听端口 %d", port)
    uvicorn.run("bot_server:app", host="0.0.0.0", port=port, reload=True)
