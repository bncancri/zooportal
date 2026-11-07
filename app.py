from datetime import date, datetime

from flask import (
    Flask, render_template, request, redirect, url_for, flash, abort
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user,
    login_required, logout_user, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash

from sitters import SITTERS, SERVICE_LABELS, get_sitter

app = Flask(__name__)

# Секретный ключ необходим для работы сессий Flask-Login
app.config['SECRET_KEY'] = 'maison-patte-dev-secret-key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///maison_patte.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

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
    pet_id = db.Column(db.Integer, db.ForeignKey('pet_passport.id'), nullable=True)

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
    return db.session.get(User, int(user_id))


with app.app_context():
    db.create_all()


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


def _is_valid_email(value):
    if not value or '@' not in value:
        return False
    local, _, domain = value.rpartition('@')
    return bool(local) and '.' in domain and not domain.startswith('.') and not domain.endswith('.')


def _user_pets():
    """Питомцы текущего пользователя (или пустой список для гостя)."""
    if not current_user.is_authenticated:
        return []
    return PetPassport.query.filter_by(owner_id=current_user.id).order_by(PetPassport.id.desc()).all()


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
    )


@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    error = None
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
        elif User.query.filter_by(email=email).first():
            error = 'Пользователь с таким email уже существует.'
        else:
            new_user = User(email=email, password_hash=generate_password_hash(password))
            db.session.add(new_user)
            db.session.commit()

            # Заявки, отправленные гостем с этим email, привязываем к новому аккаунту
            Request.query.filter_by(email=email, user_id=None).update({'user_id': new_user.id})
            db.session.commit()

            # Автоматический вход после регистрации
            login_user(new_user)
            flash('Аккаунт создан. Добавьте первого питомца в ветпаспорт.', 'success')
            return redirect(url_for('dashboard'))

    return render_template('register.html', error=error)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    # Куда вернуть пользователя после входа (только внутренние адреса)
    next_page = request.values.get('next') or ''
    if not next_page.startswith('/'):
        next_page = ''

    error = None
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        password = request.form.get('password') or ''

        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            flash('Вы вошли в личный кабинет.', 'success')
            return redirect(next_page or url_for('dashboard'))

        error = 'Неверный email или пароль.'

    return render_template('login.html', error=error, next_page=next_page)


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
        form={},
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

    # Питомца берём только из своего аккаунта — чужие id игнорируем
    pet = None
    pet_id = request.form.get('pet_id', type=int)
    if pet_id and current_user.is_authenticated:
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
