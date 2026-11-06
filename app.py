from flask import Flask, render_template, request, redirect, url_for
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
login_manager.login_view = 'login' # Куда перенаправлять неавторизованных пользователей

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
        email = request.form.get('email')
        password = request.form.get('password')

        if email and password:
            existing_user = User.query.filter_by(email=email).first()
            if existing_user:
                error = 'Пользователь с таким email уже существует.'
            else:
                hashed_pw = generate_password_hash(password)
                new_user = User(email=email, password_hash=hashed_pw)
                db.session.add(new_user)
                db.session.commit()

                # Автоматический вход после регистрации
                login_user(new_user)
                return redirect(url_for('dashboard'))

    return render_template('register.html', error=error)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    error = None
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')

        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for('dashboard'))
        else:
            error = 'Неверный email или пароль.'

    return render_template('login.html', error=error)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/dashboard', methods=['GET', 'POST'])
@login_required
def dashboard():
    if request.method == 'POST':
        pet_name = request.form.get('pet_name')
        species = request.form.get('species')
        breed = request.form.get('breed')
        birth_date = request.form.get('birth_date')

        if pet_name and species:
            new_pet = PetPassport(
                owner_id=current_user.id, # Привязываем к текущему пользователю
                pet_name=pet_name,
                species=species,
                breed=breed,
                birth_date=birth_date
            )
            db.session.add(new_pet)
            db.session.commit()

        return redirect(url_for('dashboard'))

    # Передаем только питомцев авторизованного владельца через связь relationship
    return render_template('dashboard.html', pets=current_user.pets)

if __name__ == '__main__':
    app.run(debug=True)