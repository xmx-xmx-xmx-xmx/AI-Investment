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

import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# Render/本地环境变量兼容
from dotenv import load_dotenv
load_dotenv()

from src.env import is_production

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

# ── 事件去重（防止飞书超时重试导致刷屏）──
# 🔥 2026-09-30 #7 加固：set + 「超 1 万条全清」→ 清空后旧事件可被重复放行，且无时效。
# 改为 TTL dict：event_id → 首次见到的时间；超过 _EVENT_TTL 的条目在插入时惰性清理。
# TTL 取 24h：飞书的重试窗口远小于此，而内存占用 1 万条 ≈ 1MB 级，无压力。
_processed_events: dict[str, float] = {}
_EVENT_TTL_SECONDS = 24 * 3600
_EVENT_DEDUP_MAX = 10000  # 超过则触发一轮过期清理（正常远达不到）


def _seen_event(event_id: str) -> bool:
    """事件去重：首次见到返回 False 并登记；已见（且未过期）返回 True。"""
    now = time.time()
    # 清理判定用 >=：在**插入前**就触发，保证登记后字典不会超过阈值 +1
    if len(_processed_events) >= _EVENT_DEDUP_MAX:
        expired = [k for k, t in _processed_events.items() if now - t > _EVENT_TTL_SECONDS]
        for k in expired:
            del _processed_events[k]
        # 兜底：全是最新的导致清不掉 → 只丢最旧的一半，绝不 clear 全清
        if len(_processed_events) >= _EVENT_DEDUP_MAX:
            for k in sorted(_processed_events, key=_processed_events.get)[: len(_processed_events) // 2]:
                del _processed_events[k]
    if event_id in _processed_events:
        return True
    _processed_events[event_id] = now
    return False

# ═══════════════════════════════════════════════════════════════
# 指令处理
# ═══════════════════════════════════════════════════════════════

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


def _verify_callback_token(header: dict) -> tuple[bool, str]:
    """校验飞书回调的 Verification Token。

    Returns:
        (allowed, reason)

    策略（#7 加固）：
    - 配置了 FEISHU_VERIFY_TOKEN → 强制校验，不匹配直接 403。
      这是防伪造回调的唯一闸门：回调能触发 GitHub 推送、消耗 LLM 配额。
    - 未配置 → 本地放行（开发方便）；生产打 ERROR（不拦，防止 Render 上
      未配置环境变量时 bot 整个静默失聪）。真正的强制需要在 Render 补配
      FEISHU_VERIFY_TOKEN，见 docs/MANUAL_OPS.md #7b；/version 暴露开启状态。
    """
    configured = bool(FEISHU_VERIFY_TOKEN)
    supplied = header.get("token", "")
    if configured:
        if supplied == FEISHU_VERIFY_TOKEN:
            return True, "token ok"
        return False, "token mismatch"
    if is_production():
        logger.error("⚠️ 生产环境未配置 FEISHU_VERIFY_TOKEN —— Token 校验未生效，"
                     "伪造回调可触发推送/耗 LLM 配额（补配步骤见 docs/MANUAL_OPS.md #7b）")
    return True, "token 未配置，跳过校验"


# ═══════════════════════════════════════════════════════════════
# 智能投顾问答（LLM + 持仓上下文 + 新闻搜索）
# ═══════════════════════════════════════════════════════════════

def _fetch_qa_context(question: str) -> dict:
    """组装 LLM 所需的完整上下文：底仓 + 雷达 + 宏观 + 新闻。

    Returns:
        {"holdings": "...", "market": "...", "news": "...", "macro": ""}
    """
    result = {"holdings": "", "market": "", "news": "", "macro": ""}
    # 🔥 2026-09-05 P0 改造：本地开发不调飞书 API
    from src.feishu_client import get_feishu_client_or_none
    client = get_feishu_client_or_none()
    if client is None:
        return result
    try:
        from src.advisor import load_portfolio
        pf = load_portfolio(client)
        if pf:
            from src.advisor import calculate_rebalance
            rb = calculate_rebalance(pf)
            total = rb.get("total_value", 0)
            lines = [f"总市值 ¥{total:,.0f}"]
            for d in rb.get("deviation_report", []):
                lines.append(
                    f"{d['asset_class']}: 实占{d['actual_weight_pct']} "
                    f"（目标{d['target_weight_pct']}）偏离{d['deviation_pct']}"
                )
            result["holdings"] = "\n".join(lines)
            # 雷达信号
            try:
                from src.radar import scan_radar
                radar = scan_radar(client=client, dry_run=True)
                if radar["signal_items"]:
                    sig_lines = ["当前雷达信号:"]
                    for s in radar["signal_items"]:
                        sig = s.get("buy_signal") or s.get("chase_signal") or ""
                        sig_lines.append(
                            f"  · {s['name']}({s['code']}): {sig}"
                        )
                    result["market"] = "\n".join(sig_lines[:10])
            except Exception:
                pass
            # 宏观日历
            try:
                from src.macro_calendar import fetch_today_calendar, calendar_context_for_prompt
                events = fetch_today_calendar(min_impact="Medium")
                if events:
                    result["macro"] = calendar_context_for_prompt(events, pf)
            except Exception:
                pass
    except Exception:
        pass
    # 新闻
    try:
        from src.news_fetcher import fetch_all_news, _filter_by_keywords
        articles = fetch_all_news(max_results=20)
        # 提取关键词
        q_lower = question.lower()
        keywords = set()
        for kw in ["半导体","芯片","AI","储","利率","美联储","CPI","财报",
                    "新能源","黄金","原油","降息","加息","就业","PMI",
                    "美光","MU","英伟达","NVDA","台积电","TSMC","苹果","AAPL",
                    "纳指","标普","恒指","A股","上证","深证"]:
            if kw.lower() in q_lower:
                keywords.add(kw)
        if keywords:
            from src.news_fetcher import _KEYWORDS_MAP
            scored = [(a, sum(1 for kw in keywords if kw in (a.get("title","")+a.get("snippet","")))) for a in articles]
            scored.sort(key=lambda x: x[1], reverse=True)
            top = [a for a, s in scored if s > 0][:6]
        else:
            top = articles[:6]
        lines = [f"· {a['title'][:100]} _{a.get('source','')}_" for a in top]
        result["news"] = "\n".join(lines[:8])
    except Exception:
        pass
    return result


def _handle_qa(question: str) -> str:
    """LLM 问答：组装上下文 → D9 prompt → 推理 → 回复。"""
    try:
        ctx = _fetch_qa_context(question)
        if not ctx["holdings"] and not ctx["news"]:
            return "当前无法获取持仓数据或新闻，请稍后再试。"

        from src.prompt_templates import build_analysis_prompt
        from src.llm import get_llm_client, get_llm_model

        extra = (
            "根据用户问题，结合持仓、雷达、宏观日历和新闻给出 2-3 句大白话回复。\n"
            "如果用户问题中包含\"建议\"\"该不该\"\"能不能\"这类词，必须在回复末尾\n"
            "明确说出你的判断和理由（买/不买/等/加/减）。禁止\"建议关注\"这类废话。\n"
            "直接输出正文，不要前缀。"
        )
        prompt = build_analysis_prompt(
            role=f"用户提问: {question[:200]}。请结合下面所有持仓、雷达、新闻数据，给出回答。",
            holdings_text=ctx["holdings"],
            market_text=ctx["market"],
            news_text=ctx["news"],
            macro_text=ctx["macro"],
            extra_rules=extra,
            include_constitution=True,
            include_cot=True,
        )

        client = get_llm_client()
        if client is None:
            return "LLM 服务暂不可用，请稍后重试。"

        resp = client.chat.completions.create(
            model=get_llm_model(), max_tokens=500, temperature=0.3,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        import traceback
        logger.error("问答失败:\n%s", traceback.format_exc())
        return f"军师思考时出了点问题: {e}"


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


# ── 指令路由表（未来新增指令只需加一行）──
_CMD_PATTERNS: list[tuple[list[str], str]] = [
    (["巡航", "状态", "仓位", "健康"], "cruise"),
    # 示例: (["雷达", "信号"], "radar"),
]


def _get_cmd_handler(text: str) -> callable | None:
    for keywords, handler_key in _CMD_PATTERNS:
        if any(kw in text for kw in keywords):
            if handler_key == "cruise":
                return _handle_cruise
            # 未来: elif handler_key == "radar": return _handle_radar
    return None


def _run_command_async(chat_id: str, text: str, message_id: str = ""):
    """后台线程：发确认 → 执行指令 → 将结果推送到群。

    所有 IO 操作都在后台线程中完成，
    webhook 可以立即返回 200，彻底杜绝飞书超时重试。
    """
    import threading

    def _worker():
        try:
            # 1. 先发确认消息（在后台线程中，不阻塞 webhook 响应）
            if message_id:
                _reply_message(message_id, "收到，军师正在结合当前底仓与市场进行思考...")

            # 2. 执行指令或 LLM 问答
            handler = _get_cmd_handler(text)
            if handler:
                reply_text = handler()
            else:
                reply_text = _handle_qa(text)

            # 3. 推送回答到群
            _send_message_to_chat(chat_id, reply_text)
        except Exception as e:
            logger.error("后台指令执行失败: %s", e)
            _send_message_to_chat(chat_id, f"军师遇到了问题: {e}")

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


@app.get("/version")
async def version():
    """部署自检：报告本次运行的是哪个 commit。

    排查"Render 部署失败"时最缺的就是这个入口 —— 失败时 Render 会保留上一个成功
    版本继续服务，光看 /health 200 无法区分"新代码已上线"和"跑的是旧版本"。
    这些变量由 Render 自动注入（本地为空）。仅 Git 自动部署时才带 commit。
    """
    return {
        "service": os.getenv("RENDER_SERVICE_NAME", "local"),
        "commit": (os.getenv("RENDER_GIT_COMMIT") or "")[:7],
        "branch": os.getenv("RENDER_GIT_BRANCH", ""),
        "repo": os.getenv("RENDER_GIT_REPO_SLUG", ""),
        "production": os.getenv("RENDER") == "true",
        # #7 加固自检：false = 回调伪造校验未生效（生产应为 true）
        "verify_token_enabled": bool(FEISHU_VERIFY_TOKEN),
    }


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

    # ── Token 验证（#7 加固：抽出可测函数；未配置时生产打 ERROR 而非放行无痕）──
    allowed, reason = _verify_callback_token(header)
    if not allowed:
        logger.warning("Verification Token 校验失败（%s），拒绝请求", reason)
        return JSONResponse({"error": "invalid token"}, status_code=403)

    # ── event_id 去重（#7：set + 全清 → TTL dict）──
    event_id = header.get("event_id", "")
    if event_id and _seen_event(event_id):
        logger.info("重复事件已拦截: event_id=%s", event_id)
        return JSONResponse({"code": 0, "msg": "duplicate event intercepted"})

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

        if message_id and chat_type == "group" and chat_id:
            # 全部异步：确认 + 执行 + 推送都在后台线程中完成
            # webhook 立即返回 200，飞书不会因等待而触发超时重试
            _run_command_async(chat_id, text, message_id)

    # 快速返回 200 OK，所有耗时操作已放入后台线程
    return JSONResponse({"code": 0})


# ═══════════════════════════════════════════════════════════════
# 启动入口
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn

    from src.env import is_production

    port = int(os.getenv("PORT", "8000"))
    # ⚠️ 生产（Render）必须关掉 reload：
    # reload=True 会让 uvicorn fork 一个 reloader 子进程并递归监听整个仓库的文件，
    # 免费实例（512MB / 共享 CPU）上白吃内存与 CPU，而且给启动路径多添一个失败点
    # —— 表现就是 "Port scan timeout reached, no open ports detected"。
    # 2026-09-17 与 2026-09-05 各复发过一次该类超时（详见 TODO.md 运维段）。
    reload = not is_production()
    logger.info("启动 Bot 服务，监听 0.0.0.0:%d（reload=%s）", port, reload)
    uvicorn.run("bot_server:app", host="0.0.0.0", port=port, reload=reload)
