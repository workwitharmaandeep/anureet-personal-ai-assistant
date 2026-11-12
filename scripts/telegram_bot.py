#!/usr/bin/env python3
"""
Telegram Bot Listener for Local AI Agent Gateway
Allows controlling your laptop's AI Agent remotely via Telegram on Android/iOS.
"""

import html
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from typing import List

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("TelegramBot")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GATEWAY_URL = os.getenv("GATEWAY_URL", "http://localhost:8000/api/chat")
TELEGRAM_MAX_MESSAGE_LEN = 4000


def chunk_message(text: str, limit: int = TELEGRAM_MAX_MESSAGE_LEN) -> List[str]:
    """Split a long message into chunks within Telegram's character limit."""
    if len(text) <= limit:
        return [text]
    chunks: List[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n", 0, limit)
        if split_at <= 0:
            split_at = limit
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")
    return chunks


def format_telegram_reply(res: dict) -> str:
    """Safely format agent response and executed tools as Telegram-compatible HTML."""
    response_text = html.escape(str(res.get("response_text", "No response.")))
    executed_tools = res.get("executed_tools") or []

    reply_html = f"🤖 <b>AI Agent Response:</b>\n\n{response_text}"
    if executed_tools:
        reply_html += "\n\n🛠️ <b>Executed Tools:</b>"
        for t in executed_tools:
            tool_name = html.escape(str(t.get("tool") or t.get("name") or "tool"))
            details = html.escape(str(t.get("details") or ""))
            exec_ms = html.escape(str(t.get("execution_time_ms", 0)))
            reply_html += f"\n• <b>{tool_name}</b>: {details} (⚡ {exec_ms}ms)"
    return reply_html


def send_telegram_request(method: str, params: dict = None) -> dict:
    """Helper to communicate with Telegram Bot API."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    try:
        data = json.dumps(params).encode("utf-8") if params else None
        headers = {"Content-Type": "application/json"} if params else {}
        req = urllib.request.Request(url, data=data, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as e:
        logger.error(f"Telegram API request failed for '{method}': {e}")
        return {"ok": False, "error": str(e)}


def query_local_agent(user_prompt: str) -> dict:
    """Send user query to the local FastAPI agent server."""
    try:
        payload = json.dumps({"query": user_prompt}).encode("utf-8")
        req = urllib.request.Request(
            GATEWAY_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=65) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as e:
        logger.error(f"Local AI Agent gateway error: {e}")
        return {
            "response_text": f"⚠️ Local Gateway Error: Could not reach agent at {GATEWAY_URL}. ({e})",
            "executed_tools": [],
        }


def process_update(update: dict):
    """Process an incoming Telegram update."""
    message = update.get("message")
    if not message:
        return

    chat_id = message.get("chat", {}).get("id")
    text = (message.get("text") or "").strip()
    if not chat_id:
        return

    if text == "/start":
        welcome_msg = (
            "⚡ <b>Local AI Agent Telegram Control</b>\n\n"
            "Welcome! Send any command or query to control your laptop AI Agent remotely.\n\n"
            "<b>Quick Examples:</b>\n"
            "• <i>Open Notepad and write a note</i>\n"
            "• <i>What is today's weather?</i>\n"
            "• <i>Create a white layer and import video in Premiere</i>\n"
            "• <i>USD to INR exchange rate</i>"
        )
        send_telegram_request("sendMessage", {
            "chat_id": chat_id,
            "text": welcome_msg,
            "parse_mode": "HTML",
        })
        return

    if not text:
        send_telegram_request("sendMessage", {
            "chat_id": chat_id,
            "text": "Received media/voice note. Please send a text command.",
        })
        return

    logger.info(f"Received query from Chat ID {chat_id}: '{text}'")
    send_telegram_request("sendChatAction", {"chat_id": chat_id, "action": "typing"})

    res = query_local_agent(text)
    reply_html = format_telegram_reply(res)
    for part in chunk_message(reply_html):
        send_telegram_request("sendMessage", {
            "chat_id": chat_id,
            "text": part,
            "parse_mode": "HTML",
        })


def main():
    logger.info("Starting Telegram Bot listener for Local AI Agent...")
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        logger.error("TELEGRAM_BOT_TOKEN environment variable is not set.")
        print(
            "\n[!] Setup Required: Set TELEGRAM_BOT_TOKEN before starting the bot:\n"
            "    set TELEGRAM_BOT_TOKEN=your_token_from_botfather\n"
            "    python scripts/telegram_bot.py\n"
        )
        sys.exit(1)

    offset = 0
    while True:
        try:
            res = send_telegram_request("getUpdates", {"offset": offset, "timeout": 30})
            if res.get("ok"):
                for update in res.get("result", []):
                    offset = update["update_id"] + 1
                    process_update(update)
            else:
                time.sleep(5)
        except KeyboardInterrupt:
            logger.info("Bot listener stopped by user.")
            sys.exit(0)
        except Exception as e:
            logger.error(f"Unexpected error in polling loop: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
