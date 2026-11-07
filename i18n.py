"""Small server-side translation catalogue. Russian messages are stable keys."""
import csv
from pathlib import Path

from flask import session

LANGUAGES = {'ru': 'Русский', 'en': 'English', 'zh': '中文'}
with Path(__file__).with_name('translations.tsv').open(encoding='utf-8') as source:
    TRANSLATIONS = {ru: {'en': en, 'zh': zh} for ru, en, zh in csv.reader(source, delimiter='\t')}


def get_locale():
    locale = session.get('language', 'ru')
    return locale if locale in LANGUAGES else 'ru'


def translate(message, **values):
    translated = TRANSLATIONS.get(message, {}).get(get_locale(), message)
    return translated.format(**values) if values else translated
