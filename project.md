# Контекст проекта: Агрегатор сиделок для животных "Maison Patte"

## Стек технологий
* Бэкенд: Python, Flask
* База данных: SQLite + Flask-SQLAlchemy
* Авторизация: Flask-Login, Werkzeug (хеширование)
* Фронтенд: Чистый HTML/CSS (без фреймворков), шаблонизатор Jinja2 для динамического вывода данных

## Дизайн и стилистика
* Основной фон: #F5EFE7, с добавлением легкого, ненавязчивого паттерна из кошачьих/собачьих лапок на заднем плане.
* Дополнительные элементы (подложки, карточки): #DDD1C2
* Акценты (текст, кнопки, обводки): #BFB6A8
* Стиль: аккуратный, минималистичный, скругленные углы, легкие тени.

## Структура классов HTML/CSS
* При генерации или обновлении верстки строго опираться на существующие CSS-классы проекта (например: контейнеры `.wrapper` / `.container`, карточки `.card` / `.pet-card`, кнопки `.btn` и т.д.).
* Запрещено придумывать новые классы с нуля и использовать инлайн-стили (style="..."), если в этом нет крайней необходимости.

## Модели базы данных (SQLAlchemy)

```python
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin

db = SQLAlchemy()

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    pets = db.relationship('PetPassport', backref='owner', lazy=True)

class PetPassport(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    pet_name = db.Column(db.String(100), nullable=False)
    species = db.Column(db.String(50), nullable=False) # вид (собака, кот и т.д.)
    breed = db.Column(db.String(100)) # порода
    birth_date = db.Column(db.String(20)) # дата рождения
    vaccinations = db.Column(db.Text) # прививки
```