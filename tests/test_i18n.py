import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from jinja2 import nodes

from app import app, login_manager, pet_age_filter, User
from i18n import TRANSLATIONS, translate


class LanguageTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def switch(self, language, target='/'):
        return self.client.post('/language', data={'language': language, 'next': target})

    def test_default_and_persistence(self):
        self.assertIn('<html lang="ru">', self.client.get('/').text)
        for language, label in [('en', 'Password'), ('zh', '密码'), ('ru', 'Пароль')]:
            response = self.switch(language, '/login?next=/dashboard')
            self.assertEqual(response.location, '/login?next=/dashboard')
            for path in ['/login', '/register']:
                html = self.client.get(path).text
                self.assertIn(f'<html lang="{language}">', html)
                self.assertIn(label, html)
            with self.client.session_transaction() as session:
                self.assertEqual(session['language'], language)
                self.assertTrue(session.permanent)

    def test_invalid_language_and_redirects(self):
        self.switch('en')
        self.assertEqual(self.switch('fr').status_code, 400)
        for target in ['https://example.com', '//example.com', '/\\example.com', '/\n/evil']:
            self.assertEqual(self.switch('en', target).location, '/')
        self.assertEqual(self.switch('zh', '/#catalog').location, '/#catalog')
        with self.client.session_transaction() as session:
            session['language'] = 'invalid'
        self.assertIn('<html lang="ru">', self.client.get('/login').text)

    def test_public_pages_and_validation(self):
        for language in ['ru', 'en', 'zh']:
            self.switch(language)
            for path in ['/', '/request/sitter', '/request/concierge',
                         '/request/care?sitter=anna-kovaleva']:
                self.assertEqual(self.client.get(path).status_code, 200)
        self.switch('en')
        html = self.client.post('/login', data={'email': 'missing@example.test', 'password': 'bad'}).text
        self.assertIn('Incorrect email or password.', html)
        html = self.client.post('/request', data={'kind': 'sitter', 'name': '<Alice>'}).text
        self.assertIn('Enter a valid email address', html)
        self.assertIn('&lt;Alice&gt;', html)
        self.assertNotIn('value="<Alice>"', html)

    def test_dashboard_keeps_stable_species_values(self):
        self.switch('zh')
        with self.client.session_transaction() as session:
            session['_user_id'] = '2147483647'
            session['_fresh'] = True
        with patch.object(login_manager, '_user_callback', return_value=User(id=2147483647, email='test@example.test')):
            response = self.client.get('/dashboard')
            self.assertEqual(response.status_code, 200)
            self.assertIn('value="Собака"', response.text)
            self.assertIn('>狗</option>', response.text)
            self.assertIn('我的宠物', response.text)

    def test_catalogue_covers_static_template_messages(self):
        for path in Path('templates').glob('*.html'):
            tree = app.jinja_env.parse(path.read_text())
            for call in tree.find_all(nodes.Call):
                if isinstance(call.node, nodes.Name) and call.node.name == '_':
                    if call.args and isinstance(call.args[0], nodes.Const):
                        self.assertIn(call.args[0].value, TRANSLATIONS, str(path))

    def test_age_and_placeholder_escaping(self):
        with app.test_request_context('/'):
            from flask import session
            with patch('app.date') as today:
                today.today.return_value = date(2026, 11, 7)
                session['language'] = 'en'
                self.assertEqual(pet_age_filter('2024-10-07'), '2 years 1 month')
                session['language'] = 'zh'
                self.assertEqual(pet_age_filter('2024-10-07'), '2岁1个月')
                self.assertIsNone(pet_age_filter('invalid'))
            result = translate('Карточка «{name}» удалена.', name='<script>')
            self.assertIn('<script>', result)  # Translation is plain text; Jinja escapes at output.
            self.assertNotIn('{name}', result)


if __name__ == '__main__':
    unittest.main()
