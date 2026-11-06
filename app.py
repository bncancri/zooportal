from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)

# Конфигурация базы данных SQLite
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///maison_patte.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# 1. Модель БД PetPassport
class PetPassport(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    pet_name = db.Column(db.String(100), nullable=False)
    species = db.Column(db.String(50), nullable=False)
    breed = db.Column(db.String(100))
    birth_date = db.Column(db.String(20))

# Автоматическое создание таблиц при запуске
with app.app_context():
    db.create_all()

# 2. Главная страница
@app.route('/')
def index():
    return render_template('index.html')

# 3. Личный кабинет (добавление и вывод ветпаспортов)
@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    if request.method == 'POST':
        # Получаем данные из HTML-формы по атрибутам name
        pet_name = request.form.get('pet_name')
        species = request.form.get('species')
        breed = request.form.get('breed')
        birth_date = request.form.get('birth_date')

        # Проверяем обязательные поля и сохраняем запись в БД
        if pet_name and species:
            new_pet = PetPassport(
                pet_name=pet_name,
                species=species,
                breed=breed,
                birth_date=birth_date
            )
            db.session.add(new_pet)
            db.session.commit()
            
        # Перенаправляем обратно на /dashboard (паттерн Post/Redirect/Get),
        # чтобы избежать повторной отправки формы при обновлении страницы
        return redirect(url_for('dashboard'))

    # Для GET-запроса: достаем все паспорта из базы и передаем в шаблон
    pets = PetPassport.query.all()
    return render_template('dashboard.html', pets=pets)

if __name__ == '__main__':
    app.run(debug=True)