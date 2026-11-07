"""Тесты подтверждения email: формат, жёсткая блокировка, ссылки, повторная отправка."""
import os
import tempfile
from datetime import datetime, timedelta

# Настройки задаём до импорта приложения: тесты не трогают рабочую БД из instance/.
os.environ['DATABASE_URL'] = 'sqlite:///' + os.path.join(
    tempfile.mkdtemp(prefix='zooportal-tests-'), 'test.db'
)
os.environ['EMAIL_DEV_MODE'] = '1'

import pytest
from sqlalchemy import inspect, text

import app as app_module
from app import (
    app, db, User, Request, CONFIRM_RESEND_COOLDOWN, _is_valid_email
)

EMAIL = 'owner@example.com'
PASSWORD = 'password123'


@pytest.fixture(autouse=True)
def clean_db():
    app.config['TESTING'] = True
    with app.app_context():
        db.drop_all()
        db.create_all()
    yield
    with app.app_context():
        db.session.remove()


@pytest.fixture
def client(clean_db):
    with app.test_client() as test_client:
        yield test_client


def register(client, email=EMAIL, password=PASSWORD):
    return client.post('/register', data={
        'email': email,
        'password': password,
        'password_confirm': password,
    })


def dev_link(client):
    with client.session_transaction() as session:
        return session.get('confirm_dev_link')


def login(client, email=EMAIL, password=PASSWORD):
    return client.post('/login', data={'email': email, 'password': password})


def shift_sent_at(minutes=5):
    """«Отматываем» время последней отправки, чтобы обойти защитный интервал."""
    with app.app_context():
        user = User.query.filter_by(email=EMAIL).first()
        user.email_confirm_sent_at = datetime.utcnow() - timedelta(minutes=minutes)
        db.session.commit()


@pytest.mark.parametrize('value, valid', [
    ('you@example.com', True),
    ('First.Last+tag@sub.example.co.uk', True),
    ('user@xn--80ak6aa92e.com', True),
    ('a@@b.c', False),
    ('a@b..c', False),
    ('a b@c.com', False),
    ('a@.com', False),
    ('a@b.com ', False),
    ('@example.com', False),
    ('a@', False),
    ('a@b.c', False),
    ('..a@example.com', False),
    ('a..b@example.com', False),
])
def test_email_format(value, valid):
    assert _is_valid_email(value) is valid


def test_registration_is_blocked_until_confirmation(client):
    response = register(client)
    assert response.status_code == 302
    assert '/confirm-email' in response.headers['Location']

    page = client.get('/confirm-email').get_data(as_text=True)
    assert EMAIL in page
    assert 'Подтвердите email' in page
    assert dev_link(client)  # в dev-режиме ссылка доступна на экране

    # Неподтверждённый аккаунт: ни входа, ни кабинета
    assert 'Email ещё не подтверждён' in login(client).get_data(as_text=True)
    dashboard = client.get('/dashboard')
    assert dashboard.status_code == 302
    assert 'login' in dashboard.headers['Location']

    # Переход по ссылке из «письма»
    confirmed = client.get(dev_link(client))
    assert confirmed.status_code == 302
    assert 'login' in confirmed.headers['Location']
    with app.app_context():
        user = User.query.filter_by(email=EMAIL).first()
        assert user.email_confirmed is True
        assert user.email_confirmed_at is not None

    assert 'dashboard' in login(client).headers['Location']
    assert client.get('/dashboard').status_code == 200


def test_invalid_and_expired_links(client, monkeypatch):
    register(client)
    link = dev_link(client)

    bad = client.get('/confirm-email/not-a-real-token', follow_redirects=True)
    assert 'недействительна' in bad.get_data(as_text=True)

    monkeypatch.setattr(app_module, 'CONFIRM_TOKEN_MAX_AGE', -1)
    expired = client.get(link, follow_redirects=True)
    assert 'истёк' in expired.get_data(as_text=True)

    monkeypatch.setattr(app_module, 'CONFIRM_TOKEN_MAX_AGE', 24 * 60 * 60)
    assert client.get(link).status_code == 302


def test_confirmation_wrong_user_or_email(client):
    register(client)
    token = app_module.confirm_serializer.dumps({'uid': 9999, 'email': EMAIL})
    response = client.get(f'/confirm-email/{token}', follow_redirects=True)
    assert 'недействительна' in response.get_data(as_text=True)


def test_resend_cooldown_and_repeat(client):
    register(client)

    too_soon = client.post(
        '/confirm-email/resend', data={'email': EMAIL}, follow_redirects=True
    )
    assert 'Повторите через' in too_soon.get_data(as_text=True)

    shift_sent_at()
    again = client.post(
        '/confirm-email/resend', data={'email': EMAIL}, follow_redirects=True
    )
    assert 'отправлено повторно' in again.get_data(as_text=True)


def test_unconfirmed_email_can_be_registered_again(client):
    register(client, password='firstpass1')

    # В рамках защитного интервала повторная регистрация не шлёт второе письмо,
    # но оставляет переход на экран подтверждения
    too_soon = register(client, password='secondpass2').get_data(as_text=True)
    assert 'Повторите через' in too_soon
    assert 'Открыть экран подтверждения' in too_soon

    shift_sent_at()
    assert register(client, password='secondpass2').status_code == 302

    client.get(dev_link(client))
    assert 'Неверный email или пароль' in login(
        client, password='firstpass1'
    ).get_data(as_text=True)
    assert 'dashboard' in login(client, password='secondpass2').headers['Location']


def test_send_failure_keeps_resend_path(client, monkeypatch):
    monkeypatch.setattr(
        app_module, '_send_confirmation_email', lambda user, link: False
    )
    page = register(client).get_data(as_text=True)
    assert 'Не удалось отправить письмо' in page
    assert 'Открыть экран подтверждения' in page
    assert client.get('/confirm-email').status_code == 200


def test_guest_request_is_still_linked_after_confirmation(client):
    client.post('/request', data={
        'kind': 'concierge',
        'name': 'Артур',
        'email': EMAIL,
        'message': 'Нужен ситтер на выходные',
    })
    with app.app_context():
        assert Request.query.filter_by(email=EMAIL, user_id=None).count() == 1

    register(client)
    client.get(dev_link(client))
    login(client)
    page = client.get('/dashboard').get_data(as_text=True)
    assert 'Индивидуальный подбор' in page
    assert 'Нужен ситтер на выходные' in page


def test_legacy_users_are_grandfathered_by_migration():
    """Старые аккаунты не должны терять доступ после появления email_confirmed."""
    with app.app_context():
        db.drop_all()
        db.session.execute(text(
            'CREATE TABLE "user" ('
            'id INTEGER PRIMARY KEY, '
            'email VARCHAR(120) NOT NULL UNIQUE, '
            'password_hash VARCHAR(256) NOT NULL)'
        ))
        db.session.execute(text(
            "INSERT INTO \"user\" (email, password_hash) VALUES ('legacy@example.com', 'hash')"
        ))
        db.session.commit()

        app_module._ensure_email_confirmation_columns()

        columns = {column['name'] for column in inspect(db.engine).get_columns('user')}
        assert {'email_confirmed', 'email_confirmed_at', 'email_confirm_sent_at'} <= columns

        legacy = db.session.get(User, 1)
        assert legacy.email_confirmed is True

        newcomer = User(email='new@example.com', password_hash='hash')
        db.session.add(newcomer)
        db.session.commit()
        assert newcomer.email_confirmed is False


def test_dev_link_is_relative_and_base_url_is_used_in_mail(client, monkeypatch):
    sent_links = []
    monkeypatch.setattr(app_module, 'APP_BASE_URL', 'https://zooportal.example')
    monkeypatch.setattr(
        app_module, '_send_confirmation_email',
        lambda user, link: sent_links.append(link) or True,
    )

    assert register(client).status_code == 302
    path = dev_link(client)
    assert path.startswith('/confirm-email/')  # работает за любым прокси
    assert client.get(path).status_code == 302
    assert sent_links[0].startswith('https://zooportal.example/confirm-email/')
