"""Диагностика SMTP: отправляет тестовое письмо без регистрации и без БД.

Использует ровно тот же транспорт и те же переменные окружения, что и письма
подтверждения в app.py / mailer.py.

    python check_smtp.py you@example.com

Если письмо не пришло — сначала проверьте «Спам», затем логи/порт/логин.
"""

import sys

from mailer import (
    EMAIL_DEV_MODE, SMTP_FROM, SMTP_HOST, SMTP_PORT, SMTP_SECURITY, send_email
)


def main():
    if len(sys.argv) != 2:
        raise SystemExit('Использование: python check_smtp.py you@example.com')

    if EMAIL_DEV_MODE:
        raise SystemExit(
            'Включён dev-режим: SMTP_HOST не задан или EMAIL_DEV_MODE=1. '
            'Заполните .env / переменные окружения — в этом режиме письма не уходят.'
        )

    recipient = sys.argv[1].strip()
    ok = send_email(
        recipient,
        'Проверка SMTP — Зоопортал',
        'Если это письмо дошло, SMTP настроен верно: письма с подтверждением '
        'регистрации будут приходить на почту.\n\nЗоопортал',
    )
    if not ok:
        raise SystemExit(
            'Отправка не удалась. Проверьте '
            f'SMTP_HOST={SMTP_HOST}, SMTP_PORT={SMTP_PORT}, '
            f'SMTP_SECURITY={SMTP_SECURITY}, SMTP_FROM={SMTP_FROM}, '
            'логин/пароль и доступность порта (см. traceback выше).'
        )

    print(f'OK: тестовое письмо отправлено на {recipient}. Проверьте входящие и «Спам».')
    print('Если письмо не пришло — проверьте SPF/DKIM/DMARC домена отправителя.')


if __name__ == '__main__':
    main()
