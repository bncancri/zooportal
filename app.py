from datetime import date, datetime

from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user,
    login_required, logout_user, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash

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


# --- Маршруты ---

@app.route('/')
def index():
    return render_template('index.html')


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

            # Автоматический вход после регистрации
            login_user(new_user)
            flash('Аккаунт создан. Добавьте первого питомца в ветпаспорт.', 'success')
            return redirect(url_for('dashboard'))

    return render_template('register.html', error=error)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    error = None
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        password = request.form.get('password') or ''

        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            flash('Вы вошли в личный кабинет.', 'success')
            return redirect(url_for('dashboard'))

        error = 'Неверный email или пароль.'

    return render_template('login.html', error=error)


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

    return render_template('dashboard.html', pets=current_user.pets, edit_pet=edit_pet)


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


@app.route('/contact', methods=['POST'])
def contact():
    """Заявка консьержу с главной страницы (демо-обработчик без отправки писем)."""
    name = (request.form.get('name') or '').strip()
    email = (request.form.get('email') or '').strip()

    if not name or not email:
        flash('Укажите имя и email — иначе мы не сможем ответить.', 'error')
    else:
        flash(f'{name}, заявка отправлена. Консьерж ответит на {email} в течение рабочего дня.', 'success')

    return redirect(url_for('index', _anchor='contact'))


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
