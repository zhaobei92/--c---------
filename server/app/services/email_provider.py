"""邮件发送 Provider。

prod:SmtpEmailProvider(启动拦截保证 smtp_host/smtp_from 已配置);
dev/staging:ConsoleEmailProvider(打日志,验证码经 dev_code 回显联调)。
多实例部署时验证码状态必须迁移到共享存储(Redis),见 auth.py 注释。
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import Protocol

from ..core.config import Settings

logger = logging.getLogger(__name__)


class EmailProvider(Protocol):
    def send(self, to: str, subject: str, body: str) -> None:
        """发送失败抛异常(调用方决定重试策略)。"""
        ...


class ConsoleEmailProvider:
    def send(self, to: str, subject: str, body: str) -> None:
        logger.info("[dev email] to=%s subject=%s body=%s", to, subject, body)


class SmtpEmailProvider:
    def __init__(self, settings: Settings):
        self.settings = settings

    def send(self, to: str, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"] = self.settings.smtp_from
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=10) as smtp:
            smtp.starttls()
            if self.settings.smtp_user:
                smtp.login(self.settings.smtp_user, self.settings.smtp_password)
            smtp.send_message(msg)

    def healthcheck(self) -> None:
        """部署探活:能建立 SMTP 连接即通过(不真正发信)。"""
        with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=5):
            pass


def get_email_provider(settings: Settings) -> EmailProvider:
    if settings.env == "prod":
        return SmtpEmailProvider(settings)
    return ConsoleEmailProvider()
