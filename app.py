import logging
import os
import re
from datetime import date, datetime, timedelta

from flask import (
    Flask, render_template, request, redirect, url_for, flash, abort, session
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user,
    login_required, logout_user, current_user
)
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy import inspect, text
from werkzeug.security import generate_password_hash, check_password_hash

from mailer import EMAIL_DEV_MODE, send_email
from sitters import SITTERS, SERVICE_LABELS, get_sitter

app = Flask(__name__)

# Секретный ключ нужен для сессий Flask-Login и подписи ссылок подтверждения.
# В продакшене обязательно задайте SECRET_KEY через переменную окружения.
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY') or 'maison-patte-dev-secret-key'
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL') or 'sqlite:///maison_patte.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# --- Подтверждение email ---
# SMTP и dev-режим настраиваются в mailer.py (переменные окружения / .env).
# Публичный адрес сайта за обратным прокси: если задан, письмо содержит ссылку на нём,
# а не на внутреннем хосте, который видит Flask.
APP_BASE_URL = os.environ.get('APP_BASE_URL', '').rstrip('/')

CONFIRM_TOKEN_MAX_AGE = 24 * 60 * 60  # ссылка живёт сутки
CONFIRM_RESEND_COOLDOWN = timedelta(seconds=60)  # не даём спамить повторными письмами

if EMAIL_DEV_MODE:
    app.logger.setLevel(logging.INFO)

confirm_serializer = URLSafeTimedSerializer(
    app.config['SECRET_KEY'], salt='zooportal-email-confirm-v1'
)

db = SQLAlchemy(app)

# Настройка менеджера авторизации
login_manager = LoginManager(app)
login_manager.login_view = 'login'  # Куда перенаправлять неавторизованных пользователей
login_manager.login_message = 'Войдите в аккаунт, чтобы открыть личный кабинет.'
login_manager.login_message_category = 'error'

# Типы обращений: один экран заявки, три сценария
REQUEST_KINDS = {
    'care': {
        'eyebrow': 'Запрос ухода',
        'title': 'Заявка на уход',
        'lead': 'Вы выбрали специалиста. Оставьте контакты и опишите питомца — консьерж подтвердит даты. Если вы вошли в аккаунт, приложите ветпаспорт: ситтер увидит его до первого визита.',
        'submit': 'Отправить запрос',
    },
    'sitter': {
        'eyebrow': 'Специалистам',
        'title': 'Стать специалистом',
        'lead': 'Расскажите о себе — куратор свяжется, проверит рекомендации и пригласит на собеседование.',
        'submit': 'Отправить заявку',
    },
    'concierge': {
        'eyebrow': 'Служба консьержа',
        'title': 'Индивидуальный подбор',
        'lead': 'Для сложных задач: проживание в загородном доме, послеоперационный уход, знакомство с питомцем заранее. Подберём специалиста вручную.',
        'submit': 'Отправить заявку',
    },
}

REQUEST_STATUS_LABELS = {
    'new': 'Новая',
    'in_progress': 'В работе',
    'done': 'Закрыта',
}


# --- Модели базы данных ---

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    # Пока email не подтверждён, вход в кабинет закрыт (жёсткая политика).
    email_confirmed = db.Column(
        db.Boolean, nullable=False, default=False, server_default='0'
    )
    email_confirmed_at = db.Column(db.DateTime)
    email_confirm_sent_at = db.Column(db.DateTime)
    pets = db.relationship('PetPassport', backref='owner', lazy=True)


class PetPassport(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    pet_name = db.Column(db.String(100), nullable=False)
    species = db.Column(db.String(50), nullable=False)
    breed = db.Column(db.String(100))
    birth_date = db.Column(db.String(20))
    vaccinations = db.Column(db.Text)


class Request(db.Model):
    """Обращение: запрос ухода у конкретного ситтера, заявка специалиста или подбор консьержа."""
    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(20), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default='new')
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    # Связь с аккаунтом и ветпаспортом (у гостя может быть пусто)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True, index=True)
    pet_id = db.Column(db.Integer, db.ForeignKey('pet_passport.id'), nullable=True)  # ветпаспорт: care / concierge

    # Если запрос адресован конкретному специалисту из каталога
    sitter_slug = db.Column(db.String(80), nullable=True)
    sitter_name = db.Column(db.String(100), nullable=True)

    # Контактные данные на момент обращения
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), nullable=False, index=True)
    phone = db.Column(db.String(40))
    district = db.Column(db.String(120))
    pet_info = db.Column(db.String(200))  # описание гостя или снимок ветпаспорта на момент заявки
    message = db.Column(db.Text)

    author = db.relationship('User', backref='requests')
    pet = db.relationship('PetPassport')


@login_manager.user_loader
def load_user(user_id):
    # Единая точка жёсткой проверки: неподтверждённый аккаунт не считается
    # авторизованным, даже если в сессии остался старый cookie.
    user = db.session.get(User, int(user_id))
    if user is None or not user.email_confirmed:
        return None
    return user


def _ensure_email_confirmation_columns():
    """Лёгкая миграция: db.create_all() не добавляет колонки в существующую таблицу.

    Аккаунты, созданные до появления подтверждения, считаем уже подтверждёнными,
    иначе после обновления все существующие пользователи потеряли бы доступ.
    """
    if 'user' not in inspect(db.engine).get_table_names():
        return

    columns = {column['name'] for column in inspect(db.engine).get_columns('user')}
    with db.engine.begin() as connection:
        if 'email_confirmed' not in columns:
            connection.execute(text(
                'ALTER TABLE "user" ADD COLUMN email_confirmed BOOLEAN NOT NULL DEFAULT 0'
            ))
            connection.execute(text('UPDATE "user" SET email_confirmed = 1'))
        if 'email_confirmed_at' not in columns:
            connection.execute(text(
                'ALTER TABLE "user" ADD COLUMN email_confirmed_at DATETIME'
            ))
        if 'email_confirm_sent_at' not in columns:
            connection.execute(text(
                'ALTER TABLE "user" ADD COLUMN email_confirm_sent_at DATETIME'
            ))


with app.app_context():
    db.create_all()
    _ensure_email_confirmation_columns()


# --- Вспомогательные функции ---

def _plural(count, one, few, many):
    """Русское склонение: 1 год / 2 года / 5 лет."""
    mod10 = count % 10
    mod100 = count % 100
    if mod10 == 1 and mod100 != 11:
        return one
    if 2 <= mod10 <= 4 and not 12 <= mod100 <= 14:
        return few
    return many


_EMAIL_LOCAL_RE = re.compile(
    r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
)
_EMAIL_LABEL_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")


def _is_valid_email(value):
    """Проверка формата адреса: теперь по нему реально отправляется письмо.

    ASCII-адреса (включая punycode-домены вроде xn--...): пробелы, несколько @,
    двойные точки, пустые метки домена и односимвольные TLD не пропускаем.
    """
    if not isinstance(value, str) or len(value) > 254 or value != value.strip():
        return False

    local, separator, domain = value.rpartition('@')
    if not separator or not local or len(local) > 64:
        return False
    if not _EMAIL_LOCAL_RE.fullmatch(local):
        return False

    labels = domain.split('.')
    if len(labels) < 2 or len(labels[-1]) < 2:
        return False
    return all(_EMAIL_LABEL_RE.fullmatch(label) for label in labels)


def _resend_wait(user):
    """Сколько секунд ещё нельзя отправлять письмо повторно (0 — можно)."""
    if not user.email_confirm_sent_at:
        return 0
    remaining = CONFIRM_RESEND_COOLDOWN - (datetime.utcnow() - user.email_confirm_sent_at)
    return max(0, int(remaining.total_seconds()) + 1)


def _confirmation_links(user):
    """Подписанная ссылка с ограниченным сроком жизни; токен в БД не хранится.

    Возвращает пару: абсолютную (для письма и лога) и относительную (для dev-экрана).
    Относительная не зависит от Host/X-Forwarded-Proto, поэтому работает и в preview.
    """
    token = confirm_serializer.dumps({'uid': user.id, 'email': user.email})
    path = url_for('confirm_email_token', token=token)
    absolute = f'{APP_BASE_URL}{path}' if APP_BASE_URL else url_for(
        'confirm_email_token', token=token, _external=True
    )
    return absolute, path


def _send_confirmation_email(user, link):
    """Отправляет письмо через SMTP. В dev-режиме только пишет ссылку в лог."""
    if EMAIL_DEV_MODE:
        app.logger.info('[DEV] Подтверждение email для %s: %s', user.email, link)
        return True

    return send_email(
        user.email,
        'Подтвердите email — Зоопортал',
        'Здравствуйте!\n\n'
        'Вы зарегистрировались на Зоопортале. Чтобы подтвердить адрес и получить '
        'доступ к личному кабинету, перейдите по ссылке:\n\n'
        f'{link}\n\n'
        'Ссылка действует 24 часа. Если вы не регистрировались, просто '
        'проигнорируйте это письмо — аккаунт останется неподтверждённым.\n\n'
        'Зоопортал · hello@zooportal.ru',
    )


def _issue_confirmation(user):
    """Готовит письмо, запоминает адрес в сессии; возвращает ссылку или None при ошибке."""
    session['pending_confirm_email'] = user.email
    session.pop('confirm_dev_link', None)

    absolute_link, dev_path = _confirmation_links(user)
    if not _send_confirmation_email(user, absolute_link):
        return None

    user.email_confirm_sent_at = datetime.utcnow()
    db.session.commit()
    if EMAIL_DEV_MODE:
        # Только в dev-режиме: показать ссылку на экране /confirm-email.
        # Относительный путь переживает любой прокси и не уводит с https на http.
        session['confirm_dev_link'] = dev_path
    return absolute_link


def _user_pets():
    """Питомцы текущего пользователя (или пустой список для гостя)."""
    if not current_user.is_authenticated:
        return []
    return PetPassport.query.filter_by(owner_id=current_user.id).order_by(PetPassport.id.desc()).all()


def _request_defaults(kind):
    """Подсказки для формы заявки из аккаунта и последнего обращения того же типа.

    Возвращает обычные значения для полей: пользователь может исправить любое из них руками.
    Ищем именно тот же kind, иначе район работы из анкеты sitter подставился бы клиенту.
    """
    if not current_user.is_authenticated:
        return {}

    defaults = {'email': current_user.email}
    last = (
        Request.query
        .filter_by(user_id=current_user.id, kind=kind)
        .order_by(Request.created_at.desc(), Request.id.desc())
        .first()
    )
    if last:
        for field in ('name', 'phone', 'district'):
            value = getattr(last, field)
            if value:
                defaults[field] = value
        # Подставляем питомца, только если карточка ещё принадлежит этому пользователю
        if last.pet_id and PetPassport.query.filter_by(id=last.pet_id, owner_id=current_user.id).first():
            defaults['pet_id'] = str(last.pet_id)
    return defaults


@app.template_filter('pet_age')
def pet_age_filter(birth_date):
    """Возвращает возраст питомца строкой: '2 года 4 мес.'"""
    if not birth_date:
        return None
    try:
        born = datetime.strptime(str(birth_date), '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None

    today = date.today()
    months = (today.year - born.year) * 12 + (today.month - born.month)
    if today.day < born.day:
        months -= 1
    if months < 0:
        return None

    years, rest_months = divmod(months, 12)
    parts = []
    if years:
        parts.append(f'{years} {_plural(years, "год", "года", "лет")}')
    if rest_months:
        parts.append(f'{rest_months} мес.')
    return ' '.join(parts) if parts else 'меньше месяца'


@app.context_processor
def inject_globals():
    return {
        'request_kinds': REQUEST_KINDS,
        'request_status_labels': REQUEST_STATUS_LABELS,
        'service_labels': SERVICE_LABELS,
    }


# --- Маршруты ---

@app.route('/')
def index():
    pets = _user_pets()
    return render_template(
        'index.html',
        sitters=SITTERS,
        hero_pet=pets[0] if pets else None,
        pets_count=len(pets),
        pets=pets,
        form=_request_defaults('concierge'),
    )


@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    error = None
    confirmation_pending = False
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        password = request.form.get('password') or ''
        password_confirm = request.form.get('password_confirm') or ''

        if not email or not password:
            error = 'Заполните email и пароль.'
        elif not _is_valid_email(email):
            error = 'Проверьте адрес электронной почты.'
        elif password != password_confirm:
            error = 'Пароли не совпадают.'
        elif len(password) < 8:
            error = 'Пароль должен содержать не менее 8 символов.'
        else:
            user = User.query.filter_by(email=email).first()
            if user and user.email_confirmed:
                error = 'Пользователь с таким email уже существует.'
            else:
                # Повторная регистрация с ещё неподтверждённым адресом: обновляем
                # пароль и шлём новое письмо, чтобы чужой адрес не «залип» за аккаунтом.
                wait = _resend_wait(user) if user else 0
                if wait:
                    error = f'Письмо с подтверждением уже отправлено. Повторите через {wait} сек.'
                    confirmation_pending = True
                else:
                    password_hash = generate_password_hash(password)
                    if user:
                        user.password_hash = password_hash
                    else:
                        user = User(email=email, password_hash=password_hash)
                        db.session.add(user)
                        db.session.flush()  # нужен id для подписанной ссылки

                    # Заявки, отправленные гостем с этим email, привязываем к аккаунту
                    Request.query.filter_by(email=email, user_id=None).update({'user_id': user.id})
                    db.session.commit()

                    if _issue_confirmation(user) is None:
                        confirmation_pending = True
                        error = 'Не удалось отправить письмо. Попробуйте запросить его повторно.'
                    else:
                        flash(
                            'Аккаунт создан. Подтвердите email по ссылке из письма — '
                            'до этого вход в кабинет закрыт.',
                            'success',
                        )
                        return redirect(url_for('confirm_email'))

    return render_template(
        'register.html', error=error, confirmation_pending=confirmation_pending
    )


@app.route('/confirm-email', methods=['GET'])
def confirm_email():
    """Экран «подтвердите email»: повторная отправка письма и dev-ссылка."""
    return render_template(
        'confirm_email.html',
        email=session.get('pending_confirm_email'),
        dev_link=session.get('confirm_dev_link'),
    )


@app.route('/confirm-email/resend', methods=['POST'])
def resend_confirmation():
    email = (request.form.get('email') or session.get('pending_confirm_email') or '').strip().lower()
    user = User.query.filter_by(email=email).first() if email else None

    if not user:
        flash('Аккаунт с таким email не найден. Зарегистрируйтесь.', 'error')
        return redirect(url_for('register'))
    if user.email_confirmed:
        flash('Email уже подтверждён — можно войти в кабинет.', 'success')
        return redirect(url_for('login'))

    wait = _resend_wait(user)
    if wait:
        flash(f'Письмо уже отправлено. Повторите через {wait} сек.', 'error')
        return redirect(url_for('confirm_email'))
    if _issue_confirmation(user) is None:
        flash('Не удалось отправить письмо. Попробуйте позже.', 'error')
    else:
        flash('Письмо отправлено повторно. Проверьте входящие и папку «Спам».', 'success')
    return redirect(url_for('confirm_email'))


@app.route('/confirm-email/<token>')
def confirm_email_token(token):
    """Подтверждает адрес по подписанной ссылке из письма."""
    try:
        data = confirm_serializer.loads(token, max_age=CONFIRM_TOKEN_MAX_AGE)
        uid = int(data.get('uid'))
    except SignatureExpired:
        flash('Срок действия ссылки истёк. Запросите новое письмо.', 'error')
        return redirect(url_for('confirm_email'))
    except (BadSignature, TypeError, ValueError, AttributeError):
        flash('Ссылка подтверждения недействительна. Запросите новое письмо.', 'error')
        return redirect(url_for('confirm_email'))

    user = db.session.get(User, uid)
    if not user or user.email != data.get('email'):
        flash('Ссылка подтверждения недействительна. Запросите новое письмо.', 'error')
        return redirect(url_for('confirm_email'))

    if not user.email_confirmed:
        user.email_confirmed = True
        user.email_confirmed_at = datetime.utcnow()
        db.session.commit()

    session.pop('pending_confirm_email', None)
    session.pop('confirm_dev_link', None)
    flash('Email подтверждён. Теперь можно войти в личный кабинет.', 'success')
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    # Куда вернуть пользователя после входа (только внутренние адреса)
    next_page = request.values.get('next') or ''
    if not next_page.startswith('/'):
        next_page = ''

    error = None
    needs_confirmation = False
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        password = request.form.get('password') or ''

        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password_hash, password):
            if not user.email_confirmed:
                session['pending_confirm_email'] = user.email
                needs_confirmation = True
                error = 'Email ещё не подтверждён. Откройте письмо и перейдите по ссылке.'
            else:
                login_user(user)
                flash('Вы вошли в личный кабинет.', 'success')
                return redirect(next_page or url_for('dashboard'))
        else:
            error = 'Неверный email или пароль.'

    return render_template(
        'login.html',
        error=error,
        next_page=next_page,
        needs_confirmation=needs_confirmation,
    )


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Вы вышли из аккаунта.', 'success')
    return redirect(url_for('index'))


@app.route('/dashboard', methods=['GET', 'POST'])
@login_required
def dashboard():
    edit_id = request.args.get('edit_id', type=int)
    edit_pet = None
    if edit_id:
        edit_pet = PetPassport.query.filter_by(id=edit_id, owner_id=current_user.id).first()

    if request.method == 'POST':
        pet_id = request.form.get('pet_id', type=int)
        pet_name = (request.form.get('pet_name') or '').strip()
        species = request.form.get('species')
        breed = (request.form.get('breed') or '').strip() or None
        birth_date = request.form.get('birth_date') or None
        # Если галочка стоит, сохраняем статус в текстовое поле vaccinations
        vaccinations = 'Вакцинирован' if request.form.get('vaccinations') else None

        if pet_name and species:
            if pet_id:
                # Редактирование существующего питомца текущего пользователя
                pet = PetPassport.query.filter_by(id=pet_id, owner_id=current_user.id).first()
                if pet:
                    pet.pet_name = pet_name
                    pet.species = species
                    pet.breed = breed
                    pet.birth_date = birth_date
                    pet.vaccinations = vaccinations
                    db.session.commit()
                    flash(f'Карточка «{pet.pet_name}» обновлена.', 'success')
            else:
                # Добавление нового питомца
                new_pet = PetPassport(
                    owner_id=current_user.id,
                    pet_name=pet_name,
                    species=species,
                    breed=breed,
                    birth_date=birth_date,
                    vaccinations=vaccinations
                )
                db.session.add(new_pet)
                db.session.commit()
                flash(f'«{new_pet.pet_name}» добавлен в ветпаспорт.', 'success')
        else:
            flash('Укажите имя питомца и вид животного.', 'error')

        return redirect(url_for('dashboard'))

    my_requests = (
        Request.query
        .filter_by(user_id=current_user.id)
        .order_by(Request.created_at.desc())
        .all()
    )

    return render_template(
        'dashboard.html',
        pets=_user_pets(),
        edit_pet=edit_pet,
        my_requests=my_requests,
    )


@app.route('/delete_pet/<int:pet_id>', methods=['POST'])
@login_required
def delete_pet(pet_id):
    # Находим питомца, строго проверяя привязку к текущему владельцу
    pet = PetPassport.query.filter_by(id=pet_id, owner_id=current_user.id).first()
    if pet:
        db.session.delete(pet)
        db.session.commit()
        flash(f'Карточка «{pet.pet_name}» удалена.', 'success')
    return redirect(url_for('dashboard'))


@app.route('/request/<kind>', methods=['GET'])
def request_form(kind):
    """Экран заявки: уход у выбранного ситтера / заявка специалиста / подбор консьержа."""
    if kind not in REQUEST_KINDS:
        abort(404)

    sitter = get_sitter(request.args.get('sitter'))

    if kind == 'care' and sitter is None:
        flash('Сначала выберите специалиста в каталоге — заявка привязывается к нему.', 'error')
        return redirect(url_for('index', _anchor='catalog'))

    return render_template(
        'request.html',
        kind=kind,
        sitter=sitter,
        pets=_user_pets(),
        error=None,
        form=_request_defaults(kind),
    )


@app.route('/request', methods=['POST'])
def request_submit():
    kind = request.form.get('kind', '')
    if kind not in REQUEST_KINDS:
        abort(400)

    sitter = get_sitter(request.form.get('sitter')) if kind == 'care' else None

    name = (request.form.get('name') or '').strip()
    email = (request.form.get('email') or '').strip().lower()
    phone = (request.form.get('phone') or '').strip() or None
    district = (request.form.get('district') or '').strip() or None
    # Ограничиваем строку под размер колонки pet_info
    pet_info = (request.form.get('pet_info') or '').strip()[:200] or None
    message = (request.form.get('message') or '').strip() or None

    # Ветпаспорт прикладываем только к уходу и консьержу; чужие id игнорируем
    pet = None
    pet_id = request.form.get('pet_id', type=int)
    if pet_id and current_user.is_authenticated and kind in ('care', 'concierge'):
        pet = PetPassport.query.filter_by(id=pet_id, owner_id=current_user.id).first()

    if pet:
        # Снимок ветпаспорта на момент заявки: карточку потом могут изменить или удалить,
        # а заявка должна оставаться самодостаточной для консьержа и ситтера.
        parts = [pet.pet_name, pet.species, pet.breed, pet_age_filter(pet.birth_date)]
        if pet.vaccinations:
            parts.append(pet.vaccinations)
        snapshot = ', '.join(str(part) for part in parts if part)
        pet_info = f'{snapshot} · {pet_info}' if pet_info else snapshot
        pet_info = pet_info[:200]

    error = None
    if not name:
        error = 'Укажите, как к вам обращаться.'
    elif not _is_valid_email(email):
        error = 'Укажите корректный email — на него придёт ответ.'
    elif kind == 'care' and sitter is None:
        error = 'Специалист не найден. Выберите его в каталоге заново.'
    elif kind == 'care' and not pet and not pet_info:
        error = 'Опишите питомца — консьерж не сможет подтвердить уход без этих данных.'
    elif kind == 'sitter' and not district:
        error = 'Укажите район, в котором вам удобно работать.'

    if error:
        return render_template(
            'request.html',
            kind=kind,
            sitter=sitter,
            pets=_user_pets(),
            error=error,
            form=request.form,
        )

    new_request = Request(
        kind=kind,
        user_id=current_user.id if current_user.is_authenticated else None,
        pet_id=pet.id if pet else None,
        sitter_slug=sitter['id'] if sitter else None,
        sitter_name=sitter['name'] if sitter else None,
        name=name,
        email=email,
        phone=phone,
        district=district,
        pet_info=pet_info,
        message=message,
    )
    db.session.add(new_request)
    db.session.commit()

    # Редирект без якоря: подтверждение (flash) должно быть на виду сразу
    if kind == 'care':
        if current_user.is_authenticated:
            flash(
                f'Заявка отправлена: {new_request.sitter_name}. Консьерж подтвердит даты и свяжется с вами на {email}.',
                'success',
            )
            return redirect(url_for('dashboard'))
        flash(
            f'Заявка отправлена: {new_request.sitter_name}. Консьерж уточнит данные питомца и свяжется с вами на {email}.',
            'success',
        )
        return redirect(url_for('index'))

    if kind == 'sitter':
        flash(f'Спасибо, {name}! Заявка специалиста принята — куратор ответит на {email}.', 'success')
    else:
        flash(f'{name}, заявка принята. Консьерж ответит на {email} в течение рабочего дня.', 'success')

    return redirect(url_for('index'))


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
