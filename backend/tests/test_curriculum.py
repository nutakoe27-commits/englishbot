"""Curriculum invariants and case validation without network or a database."""
import copy
import unittest

from app import learning_content as content
from app.learning_curriculum import CURRICULUM, MIN_TURNS


class CurriculumContentTests(unittest.TestCase):
    def test_each_level_has_a_complete_distinct_syllabus(self):
        all_titles, all_openings = set(), set()
        for level, units in CURRICULUM.items():
            self.assertEqual(len(units), 20)
            self.assertEqual(len({u.key for u in units}), 20)
            self.assertEqual({u.grammar for u in units}, set(content.topic_keys(level)))
            for unit in units:
                self.assertNotIn(unit.title, all_titles)
                self.assertNotIn(unit.prompts, all_openings)
                all_titles.add(unit.title); all_openings.add(unit.prompts)
                self.assertEqual(len(unit.words), 4)
                self.assertEqual(len({w for w, _ in unit.words}), 4)
                self.assertTrue(all(w.strip() and t.strip() for w, t in unit.words))
                self.assertEqual(len(set(unit.prompts)), 3)
                self.assertEqual(len(set(unit.cases)), 3)
                self.assertTrue(unit.grammar.startswith(level.lower() + '_'))

    def test_goals_preserve_coverage_and_prioritise_relevant_units(self):
        for level, units in CURRICULUM.items():
            for goal in content.GOALS:
                plans = [content.plan(level, goal, i) for i in range(1, 21)]
                self.assertEqual({p['unit_key'] for p in plans}, {u.key for u in units})
                if goal in ('travel', 'work'):
                    ordered = content.ordered_units(level, goal)
                    relevant = [goal in u.goals for u in ordered[1 if level == 'A1' else 0:]]
                    self.assertEqual(relevant, sorted(relevant, reverse=True))

    def test_three_cycles_offer_different_cases_without_skipping_targets(self):
        for level in content.LEVELS:
            for goal in content.GOALS:
                for position in range(1, 21):
                    plans = [content.plan(level, goal, position + cycle * 20) for cycle in range(3)]
                    self.assertEqual(len({p['situation'] for p in plans}), 3)
                    self.assertEqual(len({p['unit_key'] for p in plans}), 1)
                    self.assertEqual(len({p['topic_key'] for p in plans}), 1)
                for ordinal in (1, 20, 21, 60, 61, 1000, 10001):
                    material = content.validate_material(content.material(content.plan(level, goal, ordinal)))
                    self.assertEqual(material['conversation']['required_turns'], MIN_TURNS[level])
                    self.assertEqual(material['conversation']['outcomes'], material['spec']['outcomes'])

    def test_c1_does_not_reuse_a1_vocabulary_or_introductory_questions(self):
        beginners = {word for u in CURRICULUM['A1'] for word, _ in u.words}
        advanced = {word for u in CURRICULUM['C1'] for word, _ in u.words}
        self.assertFalse(beginners & advanced)
        first = content.material(content.plan('C1', 'general', 1))
        self.assertIn('позицию', first['spec']['title'])
        self.assertIn('counterargument', str(first['words']))
        self.assertNotIn('What is your name?', str(first['conversation']))
        self.assertGreater(first['conversation']['required_turns'], 3)

    def test_weak_points_do_not_replace_the_planned_grammar_with_an_unrelated_rule(self):
        original = content.plan('B1', 'general', 3)
        reinforced = content.plan('B1', 'general', 3, ['b1_second_conditional', 'a1_to_be'])
        self.assertEqual(reinforced['topic_key'], original['topic_key'])
        self.assertEqual(reinforced['kind'], 'reinforcement')
        self.assertEqual(reinforced['review_topic_keys'], ['b1_second_conditional'])

    def scenario(self):
        return {
            'title': 'Обсуждаем доступ к профессиональному обучению',
            'situation': 'A training board considers a qualified claim about access to professional learning. Its underlying assumption is disputed by participants with different needs.',
            'prompts': ['What qualified claim would you defend in this case?', 'Which underlying assumption is most open to challenge?', 'How would you answer the counterargument while qualifying your conclusion?'],
        }

    def test_generated_case_cannot_replace_grammar_words_or_outcomes(self):
        original = content.material(content.plan('C1', 'general', 21))
        supplied = self.scenario()
        supplied.update(words=[{'word': 'name', 'translation': 'имя'}], topic_key='a1_to_be', required_turns=1)
        generated = content.scenario_variant(original, supplied)
        for field in ('topic_key', 'unit_key', 'required_turns', 'outcomes', 'version'):
            self.assertEqual(generated['spec'][field], original['spec'][field])
        self.assertEqual(generated['words'], original['words'])
        self.assertEqual(generated['theory'], original['theory'])
        self.assertEqual(generated['conversation']['situation'], generated['spec']['situation'])
        self.assertIn(supplied['situation'], generated['listening']['script'])
        self.assertNotEqual(generated['spec']['title'], original['spec']['title'])
        self.assertNotIn('generated_scenario', str(original))

    def test_repeated_unusable_or_wrong_language_cases_are_rejected(self):
        original = content.material(content.plan('C1', 'general', 21))
        scenario = self.scenario()
        invalid = [None, {}, {**scenario, 'situation': original['spec']['situation']},
                   {**scenario, 'prompts': ['What do you think?'] * 3},
                   {**scenario, 'prompts': ['Как вас зовут?', *scenario['prompts'][1:]]},
                   {**scenario, 'situation': 'x' * 1401},
                   {**scenario, 'situation': 'A group discusses the weather and a simple meal for the next day.',
                    'prompts': ['What food do you like?', 'Where do you live?', 'What do you do on Sundays?']}]
        for candidate in invalid:
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                content.scenario_variant(original, candidate)
        for recent in ([scenario], [{'title': scenario['title'], 'situation': 'A different case.'}]):
            with self.assertRaises(ValueError): content.scenario_variant(original, scenario, recent)

    def test_material_validation_rejects_mismatched_curriculum_fields(self):
        original = content.material(content.plan('C1', 'general', 1))
        for field, value in [('words', [{'word': 'name', 'translation': 'имя'}]), ('theory', 'A1 rule')]:
            candidate = copy.deepcopy(original); candidate[field] = value
            with self.assertRaises(ValueError): content.validate_material(candidate)
        candidate = copy.deepcopy(original); candidate['conversation']['required_turns'] = 3
        with self.assertRaises(ValueError): content.validate_material(candidate)


if __name__ == '__main__': unittest.main()
