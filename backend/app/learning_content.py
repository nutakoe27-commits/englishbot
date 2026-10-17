"""Versioned lesson plans and deterministic, usable material for every level.

The curriculum is independent of the placement test bank. AI may supply fresh
practice variants; the authored material remains available during outages.
"""
from __future__ import annotations
import copy
import random
import re
from difflib import SequenceMatcher
from .grammar_lessons import THEORY
from .learning_curriculum import CURRICULUM, LEVEL_EXPECTATIONS, MIN_TURNS, CYCLE_FOCUS, TRANSFER_CHALLENGES

LEVELS = ('A1','A2','B1','B2','C1')
GOALS = {'general':'Общий английский','travel':'Путешествия','work':'Работа','daily':'Повседневное общение'}
VERSION = 2
STEPS = ('warmup','theory','practice','listening','conversation','summary')
STEP_LABELS = ('Вспоминаем','Разбираем правило','Практикуемся','Слушаем','Общаемся','Подводим итог')

# Each tuple is (gap sentence, correct answer, three distractors).
RULES = {
 'a1_to_be': [('I ___ a student.','am','is','are','be'),('My sister ___ at home.','is','am','are','be')],
 'a1_pronouns': [('Anna has a bag. This is ___ bag.','her','his','their','our'),('We have a car. This is ___ car.','our','his','her','my')],
 'a1_this_that': [('___ books here are mine.','These','This','That','Those'),('___ chair over there is free.','That','These','Those','This')],
 'a1_have_got': [('She ___ got two brothers.','has','have','is','are'),('Have you got a pen? Yes, I ___.','have','has','am','do')],
 'a1_there_is': [('There ___ a cafe near my house.','is','are','am','be'),('There ___ two shops on this street.','are','is','am','has')],
 'a1_present_simple_i': [('I ___ to work every day.','walk','walks','walking','am walk'),('They ___ English on Mondays.','study','studies','studying','is study')],
 'a1_present_simple_s': [('My friend ___ in a bank.','works','work','working','is work'),('She ___ tea every morning.','drinks','drink','drinking','are drink')],
 'a1_questions_negatives': [('___ you like coffee?','Do','Does','Are','Is'),('He ___ work on Sundays.','does not','do not','is not','are not')],
 'a1_articles_a_an': [('She is ___ engineer.','an','a','the','some'),('I have ___ bicycle.','a','an','some','any')],
 'a1_imperatives': [('___ the window, please.','Open','Opens','Opening','To open'),('___ run in the corridor.','Do not','Does not','Not to','Are not')],
 'a2_present_simple': [('He usually ___ home at six.','gets','get','getting','is get'),('___ your sister live here?','Does','Do','Is','Are')],
 'a2_present_continuous': [('Look! It ___ outside.','is raining','rains','rain','has rain'),('We ___ dinner right now.','are cooking','cook','cooks','have cook')],
 'a2_past_simple': [('Yesterday I ___ my friend.','visited','visit','visiting','have visit'),('Did she ___ the train?','catch','caught','catches','catching')],
 'a2_articles_basics': [('I bought a book. ___ book is interesting.','The','A','An','Any'),('He is ___ teacher at our school.','a','an','some','any')],
 'a2_plurals_countability': [('I need ___ information about the trip.','some','a','an','many'),('There are three ___ in the room.','children','child','childs','childes')],
 'a2_prepositions_place': [('The keys are ___ the table.','on','at','to','from'),('She is waiting ___ the bus stop.','at','on','to','from')],
 'a2_prepositions_time': [('The meeting starts ___ nine oclock.','at','in','on','to'),('We moved here ___ 2022.','in','on','at','to')],
 'a2_can_cant': [('She ___ swim very well.','can','cans','is can','can to'),('Can you ___ me?','help','helps','helped','to help')],
 'a2_going_to': [('We ___ visit our friends tomorrow.','are going to','is going to','going','go to'),('He is going to ___ a car.','buy','buys','bought','buying')],
 'a2_comparatives': [('This bag is ___ than that one.','cheaper','cheap','cheapest','more cheap'),('This is the ___ room in the hotel.','largest','larger','large','more large')],
 'b1_present_perfect_vs_past': [('I ___ that film last week.','saw','have seen','see','had see'),('She ___ never been to Rome.','has','did','was','had to')],
 'b1_past_continuous': [('I ___ dinner when she called.','was cooking','cooked','am cooking','have cooked'),('They ___ when the alarm rang.','were sleeping','slept','are sleeping','have slept')],
 'b1_will_vs_going_to': [('The phone is ringing. I ___ answer it.','will','am going','would to','have'),('Look at those clouds! It is ___ rain.','going to','will','would','go to')],
 'b1_first_conditional': [('If it rains, we ___ at home.','will stay','would stay','stayed','had stayed'),('I will call you when I ___.','arrive','will arrive','would arrive','arrived')],
 'b1_second_conditional': [('If I had more time, I ___ more.','would read','will read','read','had read'),('If she ___ here, she would help us.','were','will be','has been','would be')],
 'b1_modals_obligation': [('You ___ wear a seat belt. It is the law.','must','might','could','would'),('You ___ bring food. We have enough.','do not have to','must not','cannot','should not to')],
 'b1_passive_basics': [('This house ___ in 1990.','was built','built','is building','has build'),('English ___ in many countries.','is spoken','speaks','is speaking','has spoke')],
 'b1_relative_clauses': [('The woman ___ lives next door is a doctor.','who','where','when','whose'),('This is the cafe ___ we met.','where','who','whose','when')],
 'b1_used_to': [('I ___ play football when I was a child.','used to','use to','am used','was use'),('Did you ___ live in London?','use to','used to','using','were used')],
 'b1_gerund_vs_infinitive': [('I enjoy ___ books.','reading','to read','read','reads'),('She decided ___ a new job.','to find','finding','find','found')],
 'b1_quantifiers': [('There are only ___ seats left.','a few','a little','much','a bit'),('We have very ___ time.','little','few','many','a few')],
 'b1_phrasal_verbs_intro': [('Please ___ the light. It is dark here.','turn on','turn down','turn into','turn away'),('I need to ___ this word in a dictionary.','look up','look after','look into','look through')],
 'b2_present_perfect_cont': [('I ___ all morning, and I am still working.','have been working','worked','had worked','am work'),('She ___ here since June and still lives here.','has been living','was living','lives yesterday','had live')],
 'b2_third_conditional': [('If I had known, I ___ you.','would have helped','will help','would help','helped'),('If she ___ earlier, she would have caught the train.','had left','left','would leave','has left')],
 'b2_mixed_conditionals': [('If I had studied medicine, I ___ a doctor now.','would be','would have been','will be','had been'),('If I were more careful, I ___ that mistake yesterday.','would not have made','will not make','did not make','would not make')],
 'b2_passive_advanced': [('The building ___ repaired at the moment.','is being','has been','was been','is been'),('The report must ___ by Friday.','be finished','finish','be finishing','have finish')],
 'b2_reported_speech': [('She said she ___ tired that day.','was','is','will be','has been'),('He asked me where I ___.','lived','do live','am live','live did')],
 'b2_wish_if_only': [('I wish I ___ more free time.','had','have','will have','would have had'),('If only I ___ my keys yesterday!','had not lost','do not lose','will not lose','would not lose')],
 'b2_modals_deduction': [('He has been working all night. He ___ be exhausted.','must','must not','would to','has to to'),('She was abroad, so she ___ have seen the accident.','cannot','must','should','need')],
 'b2_causative': [('I had my car ___ yesterday.','repaired','repair','repairing','to repair'),('We are having the house ___ next week.','painted','painting','paint','to paint')],
 'b2_linking_words': [('___ it was raining, we went out.','Although','Despite','Because of','In spite of'),('We went out ___ the rain.','despite','although','even though','because')],
 'b2_phrasal_separable': [('Please turn ___ off before you leave.','it','off it','off them','off this'),('I need to pick ___ up from the station.','her','up her','up she','she')],
 'c1_inversion': [('Rarely ___ such a beautiful sunset.','have I seen','I have seen','I saw','I had see'),('Not until midnight ___ home.','did he get','he got','he did get','he gets')],
 'c1_cleft_sentences': [('It was Anna ___ solved the problem.','who','whose','where','when'),('What I need ___ a little time.','is','are','be','has')],
 'c1_subjunctive': [('It is essential that he ___ on time.','be','is','was','will be'),('They recommended that she ___ the offer.','accept','accepts','accepted','would accepts')],
 'c1_advanced_modality': [('You ___ have told me earlier; now it is too late.','should','must','can','will'),('We ___ have hurried; the train was delayed.','need not','must not','cannot','will not')],
 'c1_ellipsis': [('I like jazz, and so ___ my sister.','does','is','has','did'),('He has finished, but I ___ yet.','have not','am not','do not','did not')],
 'c1_discourse_markers': [('The plan is expensive. ___, it may be worth trying.','Nevertheless','Therefore','Consequently','As a result'),('She missed the bus. ___, she arrived late.','Consequently','Nevertheless','However','Despite this')],
 'c1_collocations': [('We need to ___ a decision soon.','make','do','have doing','take making'),('The new policy will ___ a significant impact.','have','make','do','take')],
 'c1_nominalisation': [('Their ___ of the proposal surprised us.','rejection','reject','rejecting it','rejected'),('The ___ of the project took two years.','completion','complete','completed','completely')],
}


def topic_keys(level):
    return [key for key in RULES if key.startswith(level.lower() + '_')]


def quiz(key, question_id, variant=0):
    frame = RULES[key][variant % len(RULES[key])]
    prompt, correct, *other = frame
    choices = [correct, *other]
    random.Random(question_id).shuffle(choices)
    return {'id':question_id,'skill':'grammar','topic_key':key,'prompt':prompt,
            'choices':choices,'correct':correct,'explanation':THEORY[key].split('\n\n')[0]}


def ordered_units(level, goal):
    units = CURRICULUM[level]
    if goal in {'general', 'daily'}:
        return units
    # Keep all targets; a goal changes priority, never lowers the CEFR level.
    # A1 retains its introductory unit before specialising in work or travel.
    prefix = units[:1] if level == 'A1' else ()
    rest = units[len(prefix):]
    return (*prefix, *(u for u in rest if goal in u.goals), *(u for u in rest if goal not in u.goals))


def plan(level, goal, ordinal, weak_topics=()):
    if level not in LEVELS or goal not in GOALS or not isinstance(ordinal, int) or ordinal < 1:
        raise ValueError('Invalid curriculum position')
    cycle, position = divmod(ordinal - 1, 20)
    unit = ordered_units(level, goal)[position]
    case = unit.cases[cycle % len(unit.cases)]
    challenge = TRANSFER_CHALLENGES[level][(cycle // len(unit.cases)) % len(TRANSFER_CHALLENGES[level])]
    focus = CYCLE_FOCUS[min(cycle, len(CYCLE_FOCUS) - 1)]
    review = [key for key in weak_topics if key in topic_keys(level)]
    return {'ordinal': ordinal, 'goal': goal, 'cycle': cycle + 1, 'position': position + 1,
            'level': level, 'topic_key': unit.grammar, 'unit_key': unit.key,
            'title': unit.title, 'objective': unit.objective, 'situation': case,
            'challenge': challenge if cycle else '', 'cycle_focus': focus,
            'outcomes': [unit.objective[0].upper() + unit.objective[1:] + '.', *LEVEL_EXPECTATIONS[level]],
            'required_turns': MIN_TURNS[level], 'review_topic_keys': review,
            'kind': 'review' if ordinal % 5 == 0 else 'reinforcement' if review and ordinal % 3 == 0 else 'new',
            'estimated_minutes': 18, 'version': VERSION}


def material(spec, review_words=()):
    unit = next(u for u in CURRICULUM[spec['level']] if u.key == spec['unit_key'])
    key, ordinal = spec['topic_key'], spec['ordinal']
    new_words = [{'word': word, 'translation': translation} for word, translation in unit.words]
    words = list(review_words)[:3] or new_words[:3]
    warmup = []
    for i, word in enumerate(words):
        options = list(dict.fromkeys([word['translation'], 'погода', 'книга', 'дорога', 'музыка']))[:4]
        random.Random(f'{ordinal}:w:{i}').shuffle(options)
        warmup.append({'id': f'w{i}', 'skill': 'vocabulary', 'word': word['word'], 'prompt': word['word'],
                       'choices': options, 'correct': word['translation'],
                       'explanation': f"{word['word']} — {word['translation']}"})
    # Compatibility material for stored older API consumers. The module engine
    # generates fresh exercises and podcasts in their existing endpoints.
    questions = [quiz(key, f'p{i}', ordinal + i) for i in range(5)]
    distractors = ['cancel the activity', 'buy a train ticket', 'order a meal']
    if unit.task in distractors:
        distractors = ['cancel the activity', 'return a library book', 'repair a bicycle']
    options = [unit.task, *distractors]
    random.Random(ordinal).shuffle(options)
    situation = spec['situation']
    return {'spec': copy.deepcopy(spec), 'warmup': warmup, 'theory': THEORY[key], 'practice': questions,
            'listening': {'script': situation + ' The aim is to ' + unit.task + '.', 'questions': [
                {'id': 'l0', 'skill': 'listening', 'prompt': 'What is the aim in this situation?',
                 'choices': options, 'correct': unit.task, 'explanation': 'Цель прямо указана в описании ситуации.'}]},
            'conversation': {'objective': spec['objective'], 'situation': situation,
                             'outcomes': spec['outcomes'], 'required_turns': spec['required_turns'],
                             'challenge': spec['challenge'], 'prompts': list(unit.prompts), 'opening': unit.prompts[0]},
            'words': new_words, 'source': 'authored'}


def scenario_variant(original, generated, recent=()):
    """Accept a fresh case; never let generation replace the authored syllabus."""
    if not isinstance(generated, dict): raise ValueError('Invalid scenario')
    for field, minimum, maximum in (('title', 8, 140), ('situation', 60, 1400)):
        value = generated.get(field)
        if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
            raise ValueError('Invalid scenario ' + field)
    prompts = generated.get('prompts')
    if (not isinstance(prompts, list) or len(prompts) != 3
            or any(not isinstance(p, str) or not 8 <= len(p.strip()) <= 350 for p in prompts)
            or len({p.strip().casefold() for p in prompts}) != 3):
        raise ValueError('Invalid scenario prompts')
    english = generated['situation'] + ' ' + ' '.join(prompts)
    if not re.search('[A-Za-z]', english) or re.search('[А-Яа-яЁё]', english):
        raise ValueError('Scenario and prompts must be English')
    vocabulary = [w['word'].casefold() for w in original['words']]
    if sum(word in english.casefold() for word in vocabulary) < 2:
        raise ValueError('Scenario must practise the lesson vocabulary')
    normalise = lambda value: ' '.join(re.findall(r'\w+', value.casefold()))
    case = normalise(generated['situation'])
    prior = [original['spec'].get('situation', ''), *(item.get('situation', '') for item in recent)]
    if any(SequenceMatcher(None, case, normalise(value)).ratio() > .88 for value in prior if value):
        raise ValueError('Repeated scenario')
    if any(normalise(generated['title']) == normalise(item.get('title', '')) for item in recent):
        raise ValueError('Repeated scenario title')
    candidate = copy.deepcopy(original)
    candidate['spec'].update(title=generated['title'].strip(), situation=generated['situation'].strip())
    candidate['conversation'].update(situation=generated['situation'].strip(), prompts=[p.strip() for p in prompts], opening=prompts[0].strip())
    unit = next(u for u in CURRICULUM[original['spec']['level']] if u.key == original['spec']['unit_key'])
    candidate['listening']['script'] = generated['situation'].strip() + ' The aim is to ' + unit.task + '.'
    candidate['source'] = 'generated_scenario'
    return validate_material(candidate)


def validate_material(data):
    spec = data['spec']
    if spec.get('version') == VERSION:
        units = {u.key: u for u in CURRICULUM[spec['level']]}
        unit = units[spec['unit_key']]
        if spec['topic_key'] != unit.grammar or data['theory'] != THEORY[unit.grammar]:
            raise ValueError('Curriculum grammar mismatch')
        if data['words'] != [{'word': w, 'translation': t} for w, t in unit.words]:
            raise ValueError('Curriculum vocabulary mismatch')
        if data['conversation'].get('required_turns') != MIN_TURNS[spec['level']]:
            raise ValueError('Curriculum participation mismatch')
        if data['conversation'].get('outcomes') != spec['outcomes']:
            raise ValueError('Curriculum outcomes mismatch')
        if data['conversation'].get('situation') != spec['situation']:
            raise ValueError('Curriculum situation mismatch')
    for group in ('warmup','practice'):
        if not isinstance(data.get(group),list) or not data[group]: raise ValueError('Missing question group')
        for question in data[group]:
            if len(question['choices']) != 4 or not all(isinstance(c,str) and c.strip() for c in question['choices']) \
                or len(set(question['choices'])) != 4 or question['correct'] not in question['choices']:
                raise ValueError('Invalid choices')
    if len(data['practice']) < 4: raise ValueError('Too few practice questions')
    for question in data['practice']:
        if '___' not in question['prompt']: raise ValueError('Missing gap')
    if len(data['listening']['script'].strip()) < 30: raise ValueError('Empty listening story')
    for question in data['listening']['questions']:
        if len(question['choices']) != 4 or not all(isinstance(c,str) and c.strip() for c in question['choices']) \
            or len(set(question['choices'])) != 4 or question['correct'] not in question['choices']: raise ValueError('Invalid listening choices')
    if not data['conversation']['prompts']: raise ValueError('Missing conversation')
    return data
