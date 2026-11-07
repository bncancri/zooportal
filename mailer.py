"""Отправка писем через SMTP (только стандартная библиотека).

Настройки читаются из переменных окружения или из файла `.env` (если установлен
python-dotenv). Один модуль используют и приложение, и `check_smtp.py` — чтобы
диагностика проверяла ровно тот же транспорт, который шлёт подтверждения.
"""

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

try:
    from dotenv import load_dotenv

    load_dotenv()  # не перезаписывает уже заданные переменные окружения
except ImportError:
    pass  # без python-dotenv работают обычные export / $env:

logger = logging.getLogger(__name__)

SMTP_HOST = os.environ.get('SMTP_HOST', '').strip()
try:
    SMTP_PORT = int(os.environ.get('SMTP_PORT', '587'))
except ValueError:
    SMTP_PORT = 587
SMTP_USER = os.environ.get('SMTP_USER')
SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD')
SMTP_FROM = os.environ.get('SMTP_FROM') or SMTP_USER or 'no-reply@zooportal.ru'
SMTP_SECURITY = os.environ.get('SMTP_SECURITY', 'starttls').lower()  # starttls | ssl | none
try:
    SMTP_TIMEOUT = int(os.environ.get('SMTP_TIMEOUT', '10'))
except ValueError:
    SMTP_TIMEOUT = 10

# Без SMTP_HOST (или с EMAIL_DEV_MODE=1) письма не уходят: приложение показывает
# ссылку подтверждения на экране. Удобно для разработки, но не для продакшена.
EMAIL_DEV_MODE = (
    not SMTP_HOST
    or os.environ.get('EMAIL_DEV_MODE', '').lower() in ('1', 'true', 'yes')
)


def send_email(recipient, subject, text):
    """Отправляет одно письмо. Возвращает False при любой ошибке SMTP.

    Dev-режим здесь не обрабатывается: вызывающий код сам решает, показывать ли
    ссылку на экране. Так диагностический скрипт и приложение не расходятся.
    """
    message = EmailMessage()
    message['Subject'] = subject
    message['From'] = SMTP_FROM
    message['To'] = recipient
    message.set_content(text)

    try:
        if SMTP_SECURITY == 'ssl':
            smtp = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT)
        else:
            smtp = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT)
            if SMTP_SECURITY == 'starttls':
                smtp.starttls(context=ssl.create_default_context())
        with smtp:
            if SMTP_USER and SMTP_PASSWORD:
                smtp.login(SMTP_USER, SMTP_PASSWORD)
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        logger.exception('Не удалось отправить письмо на %s: %s', recipient, exc)
        return False
    return True
