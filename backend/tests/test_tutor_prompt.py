"""Conversation entry uses the same topic for the UI, text and voice prompts."""
import unittest

from app.tutor_prompt import SessionSettings, build_greeting, build_system_prompt


class TutorPromptTests(unittest.TestCase):
    def test_client_query_cannot_set_lesson_context(self):
        settings = SessionSettings.from_query({
            'level': 'a1', 'mode': 'chat', 'lesson_context': {'opening': 'Untrusted'},
        })
        self.assertEqual(settings.level, 'A1')
        self.assertIsNone(settings.lesson_context)
        self.assertEqual(build_greeting(settings), 'Hi! How is your day?')

    def test_standalone_role_still_sets_the_situation(self):
        settings = SessionSettings.from_query({'role': 'barista', 'level': 'A1'})
        self.assertEqual(build_greeting(settings), 'Hey, what can I get you?')
        self.assertNotIn('Guided lesson', build_system_prompt(settings))
        self.assertIn('barista', build_system_prompt(settings))

    def test_course_greeting_and_resume_are_shared_by_voice_and_chat(self):
        for mode in ('chat', 'voice'):
            settings = SessionSettings(mode=mode, level='A1', lesson_context={
                'title': 'Знакомимся', 'objective': 'представиться',
                'opening': 'Hello! What is your name?', 'prompts': ['Where are you from?'],
                'words': ['name', 'city'],
            })
            self.assertEqual(build_greeting(settings), 'Hello! What is your name?')
            history = [
                {'role': 'assistant', 'text': build_greeting(settings)},
                {'role': 'user', 'text': 'I am Sam.'},
                {'role': 'assistant', 'text': 'Where are you from?'},
            ]
            self.assertEqual(build_greeting(settings, history), 'Where are you from?')
            prompt = build_system_prompt(settings, learning_goal='work')
            self.assertIn('Знакомимся', prompt)
            self.assertIn('представиться', prompt)
            self.assertIn('Where are you from?', prompt)
            self.assertIn('"words": ["name", "city"]', prompt)
            self.assertIn('ONE short question', prompt)
            self.assertIn('A1', prompt)
            self.assertIn('Always speak English', prompt)
