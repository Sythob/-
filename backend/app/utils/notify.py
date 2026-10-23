"""通知推送：邮件(SMTP) / Server酱(微信) / Telegram，配置驱动、全部可选。

无任何配置时静默跳过（只记日志）。默认异步发送（后台线程），不阻塞交易链路。
"""
import smtplib
import threading
from email.mime.text import MIMEText

import requests

from app.config import get_settings
from app.utils.logger import get_logger

logger = get_logger("utils.notify")


def _send_email(title: str, content: str) -> str:
    s = get_settings()
    if not (s.smtp_host and s.smtp_user and s.smtp_password and s.notify_email_to):
        return "skip"
    msg = MIMEText(content, "plain", "utf-8")
    msg["Subject"] = title
    msg["From"] = s.smtp_user
    msg["To"] = s.notify_email_to
    with smtplib.SMTP_SSL(s.smtp_host, s.smtp_port) as server:
        server.login(s.smtp_user, s.smtp_password)
        server.send_message(msg)
    return "ok"


def _send_serverchan(title: str, content: str) -> str:
    s = get_settings()
    if not s.serverchan_key:
        return "skip"
    resp = requests.post(
        f"https://sctapi.ftqq.com/{s.serverchan_key}.send",
        data={"title": title[:32], "desp": content[:4000]}, timeout=10,
    )
    return "ok" if resp.status_code == 200 else f"fail:{resp.status_code}"


def _send_telegram(title: str, content: str) -> str:
    s = get_settings()
    if not (s.telegram_bot_token and s.telegram_chat_id):
        return "skip"
    url = f"https://api.telegram.org/bot{s.telegram_bot_token}/sendMessage"
    resp = requests.post(
        url, json={"chat_id": s.telegram_chat_id, "text": f"{title}\n{content}"}, timeout=10,
    )
    return "ok" if resp.status_code == 200 else f"fail:{resp.status_code}"


_CHANNELS = {
    "email": _send_email,
    "serverchan": _send_serverchan,
    "telegram": _send_telegram,
}


def send_notification(title: str, content: str, async_send: bool = True) -> dict:
    """多通道推送。async_send=True 时后台线程发送，立即返回。"""
    if async_send:
        def _run():
            try:
                results = {name: fn(title, content) for name, fn in _CHANNELS.items()}
                sent = {k: v for k, v in results.items() if v != "skip"}
                logger.info("通知已发送: %s", sent or "无可用通道")
            except Exception:  # noqa: BLE001 通知失败不影响主流程
                logger.exception("通知发送异常")
        threading.Thread(target=_run, daemon=True).start()
        return {"status": "async"}

    results = {}
    for name, fn in _CHANNELS.items():
        try:
            results[name] = fn(title, content)
        except Exception as exc:  # noqa: BLE001
            results[name] = f"error:{exc}"
    return results
