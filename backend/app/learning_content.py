"""Versioned lesson plans and deterministic, usable material for every level.

The curriculum is independent of the placement test bank. AI may supply fresh
practice variants; the authored material remains available during outages.
"""
from __future__ import annotations
import copy
import random
from .grammar_lessons import THEORY

LEVELS = ('A1','A2','B1','B2','C1')
GOALS = {'general':'Общий английский','travel':'Путешествия','work':'Работа','daily':'Повседневное общение'}
VERSION = 1
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

# Goal, a short listening story, a comprehension question, expected answer,
# distractors, and three turns with the tutor. These facts remain stable even
# when AI adds detail; a generated story is validated with its own questions.
SCENARIOS = [
 ('Знакомимся','представиться и узнать о собеседнике','My name is Alex. I am from Bristol. I work in a small shop. I like music. My new colleague is Anna. She is from Leeds. We talk during lunch.','Where is Alex from?','Bristol',['Leeds','London','Oxford'],['Hello! What is your name?','Where are you from?','What do you like doing?']),
 ('Рассказываем о семье','рассказать о близких','Anna has a brother called Tom. Tom is a teacher. He lives near their parents. On Sundays, the family cooks dinner together. Anna brings a cake. Tom makes tea.','What does Tom do?','He is a teacher.',['He is a doctor.','He works in a shop.','He is a student.'],['Tell me about someone in your family.','What does this person do?','What do you enjoy doing together?']),
 ('Описываем свой день','объяснить свой распорядок','Sam gets up at seven. He takes the bus to work at eight. He has lunch with a colleague at one. After work, he goes for a walk. In the evening, he reads.','How does Sam get to work?','By bus.',['By train.','By car.','On foot.'],['What time do you usually get up?','What do you do during the day?','How do you spend your evenings?']),
 ('Заказываем в кафе','заказать еду и уточнить детали','A customer orders vegetable soup and tea. The waiter asks about milk. The customer wants tea without milk. The soup costs six pounds. They sit by the window and ask for the bill after lunch.','What does the customer drink?','Tea without milk.',['Coffee with milk.','Water.','Tea with milk.'],['Welcome! What would you like to order?','Would you like anything to drink?','Would you like the bill now?']),
 ('Закрепляем знакомство','объединить изученные фразы в разговоре','Alex meets a new neighbour. Her name is Kate. She works at the library. They both enjoy cooking. Kate invites Alex for dinner on Friday. Alex agrees and offers to bring fruit.','When is the dinner?','On Friday.',['On Monday.','On Saturday.','On Sunday.'],['Introduce yourself to your new neighbour.','Ask me about my work and interests.','Suggest doing something together.']),
 ('Ориентируемся в городе','спросить дорогу и объяснить маршрут','The visitor needs the train station. A local person says to go straight, then turn left at the bank. The station is next to a supermarket. The walk takes ten minutes. The visitor thanks the local person.','What is next to the station?','A supermarket.',['A hospital.','A cafe.','A library.'],['Excuse me, where would you like to go?','The station is near the supermarket. Ask me how to get there.','Check that you understood the directions.']),
 ('Покупаем нужную вещь','уточнить цену и выбрать товар','Mia wants a blue jacket. The first jacket is too small. The assistant brings a larger one. It fits well and costs forty pounds. Mia pays by card and keeps the receipt.','How does Mia pay?','By card.',['With cash.','With a voucher.','She does not buy it.'],['What are you looking for today?','What size and colour would you like?','This one costs forty pounds. What would you like to ask?']),
 ('Выбираем жильё','уточнить условия проживания','Ben books a room for two nights. He wants a quiet room with breakfast. The receptionist offers a room on the third floor. Breakfast starts at seven. Ben arrives on Tuesday evening.','How long will Ben stay?','Two nights.',['One night.','Three nights.','A week.'],['How many nights would you like to stay?','Would you like breakfast?','Ask me about something important for your stay.']),
 ('Обсуждаем покупки','сравнить варианты и объяснить выбор','Two friends compare bicycles. The red one is cheaper, but the green one is lighter. They need a bike for long journeys. They choose the green one because it will be easier to carry.','Why do they choose the green bike?','It is lighter.',['It is cheaper.','It is red.','It is bigger.'],['Which of these options would you choose?','Explain why it is better for you.','What disadvantage does your choice have?']),
 ('Закрепляем бытовое общение','решить несколько бытовых задач','Ella moves to a new town. She asks where the supermarket is, buys a lamp and books a taxi. The taxi arrives at six. She needs to get to the station before seven.','When does the taxi arrive?','At six.',['At five.','At seven.','At eight.'],['You have just arrived in a new town. What do you need?','Ask for some practical information.','Explain your plan for the evening.']),
 ('Рассказываем о прошлом','рассказать о событии и его деталях','Last weekend, Noah visited a museum with his sister. They took the train. The museum was busy, but they enjoyed an exhibition about space. After the visit, they ate sandwiches in a park.','What was the exhibition about?','Space.',['Music.','Cooking.','Sport.'],['What did you do recently?','What happened next?','What did you like or dislike about it?']),
 ('Планируем встречу','согласовать время и место','Two colleagues arrange a meeting. Tuesday is not possible because one has a dentist appointment. They agree to meet on Wednesday at ten in the office. One colleague will send an agenda beforehand.','When is the meeting?','Wednesday at ten.',['Tuesday at ten.','Wednesday at two.','Friday at ten.'],['When would you like to meet?','That time does not work for me. Suggest another time.','Confirm where we will meet.']),
 ('Собираемся в поездку','объяснить планы и подготовку','Lina is going to visit Edinburgh. She has booked a train ticket and plans to stay with a friend. She wants to see the castle. She packs a raincoat because the weather forecast predicts rain.','Why does Lina pack a raincoat?','Rain is expected.',['She will go skiing.','It is very hot.','Her friend asked for it.'],['Where are you planning to go?','What have you already arranged?','What will you do if the weather changes?']),
 ('Просим о помощи','описать проблему и попросить решение','A passenger cannot find a bag after a train journey. A staff member asks for its colour and size. The bag is small and black. The staff member calls the next station and asks the passenger to wait.','What does the staff member do?','Calls the next station.',['Buys a new bag.','Calls a taxi.','Leaves the station.'],['Hello, how can I help you?','Could you describe the problem in more detail?','Tell me what solution you would prefer.']),
 ('Закрепляем рассказы и планы','связать прошлый опыт с будущими действиями','Owen missed a train last month because he left home late. For his next trip, he has bought an earlier ticket and set two alarms. He will prepare his bag the evening before the journey.','What will Owen do the evening before?','Prepare his bag.',['Buy a car.','Visit the station.','Cancel the ticket.'],['Tell me about a plan that went wrong.','What did you learn from it?','What will you do differently next time?']),
 ('Объясняем свою работу','рассказать о задачах и обязанностях','Sofia works in a team that designs websites. She talks to customers and writes project plans. Her colleague creates the visual design. They meet every morning to discuss progress and difficulties.','What does Sofia do?','Writes project plans.',['Delivers parcels.','Teaches children.','Repairs cars.'],['What do you do or study?','Describe one task you are responsible for.','What is challenging about it?']),
 ('Выражаем мнение','обосновать мнение и привести пример','Two friends discuss working from home. One likes the quiet and the time saved on travel. The other misses seeing colleagues. They agree that meeting in the office once a week could be a useful compromise.','What compromise do they suggest?','Meeting in the office once a week.',['Working every weekend.','Never using the office.','Moving to another city.'],['Do you prefer working alone or with others?','Give a reason and a concrete example.','What would someone with a different opinion say?']),
 ('Обсуждаем решение','предложить вариант и обсудить последствия','A club has a small budget for an event. Hiring a large hall would be expensive. A member suggests using the school garden. They could spend the saved money on food, but they would need a plan for bad weather.','What risk must they plan for?','Bad weather.',['A train delay.','A broken computer.','Missing tickets.'],['Our event has a small budget. Suggest a solution.','What are its advantages and risks?','How would you deal with a possible problem?']),
 ('Ведём сложный разговор','уточнить позицию и договориться','A customer is unhappy because a delivery arrived late. The manager apologises and explains the delay. The customer asks for a refund of the delivery fee. The manager agrees and confirms it by email.','What does the manager agree to refund?','The delivery fee.',['The whole order.','The hotel bill.','A train ticket.'],['You are unhappy with a service. Explain the problem politely.','Tell me what outcome you expect.','Respond to my proposed compromise.']),
 ('Итоговая практика цикла','применить изученные навыки в новой ситуации','A group plans a weekend activity. Some want a long walk, while others prefer a museum. They decide to visit the museum on Saturday and offer an optional walk on Sunday. Everyone can choose what suits them.','What is planned for Saturday?','A museum visit.',['A long walk.','A shopping trip.','A film.'],['Suggest a weekend activity for our group.','Explain your idea and respond to an objection.','Summarise what we have agreed.']),
]

EXTENSIONS = {
 'A1':'',
 'A2':'They check the details again before making a decision. Everyone knows what to do next.',
 'B1':'Although their initial ideas were different, they found a practical solution. They have agreed to review the arrangement if circumstances change.',
 'B2':'Having considered the alternatives, they acknowledge that the solution involves a trade-off. It would have been easier to postpone the decision, but doing so might have created further difficulties.',
 'C1':'The arrangement represents a tentative compromise rather than an unqualified endorsement. What matters most is that the parties have clarified their expectations; nevertheless, its effectiveness remains contingent on how consistently those expectations are met.',
}

SCENARIO_WORDS = [
 [('name','имя'),('colleague','коллега'),('music','музыка'),('lunch','обед')],
 [('family','семья'),('brother','брат'),('parents','родители'),('together','вместе')],
 [('morning','утро'),('usually','обычно'),('evening','вечер'),('walk','прогулка')],
 [('order','заказ'),('soup','суп'),('without','без'),('bill','счёт')],
 [('neighbour','сосед'),('invite','приглашать'),('dinner','ужин'),('fruit','фрукты')],
 [('station','станция'),('straight','прямо'),('left','налево'),('near','рядом')],
 [('jacket','куртка'),('size','размер'),('receipt','чек'),('price','цена')],
 [('room','комната'),('quiet','тихий'),('breakfast','завтрак'),('floor','этаж')],
 [('choose','выбирать'),('lighter','легче'),('cheaper','дешевле'),('journey','поездка')],
 [('lamp','лампа'),('taxi','такси'),('arrive','прибывать'),('before','до')],
 [('museum','музей'),('exhibition','выставка'),('weekend','выходные'),('visit','посещение')],
 [('meeting','встреча'),('agree','соглашаться'),('appointment','назначенная встреча'),('agenda','повестка')],
 [('ticket','билет'),('castle','замок'),('raincoat','дождевик'),('forecast','прогноз')],
 [('bag','сумка'),('describe','описывать'),('wait','ждать'),('help','помощь')],
 [('miss','пропустить'),('alarm','будильник'),('prepare','готовить'),('differently','иначе')],
 [('project','проект'),('customer','клиент'),('design','дизайн'),('difficulty','трудность')],
 [('opinion','мнение'),('quiet','тишина'),('compromise','компромисс'),('reason','причина')],
 [('budget','бюджет'),('solution','решение'),('risk','риск'),('alternative','альтернатива')],
 [('delivery','доставка'),('delay','задержка'),('refund','возврат денег'),('confirm','подтверждать')],
 [('activity','занятие'),('optional','необязательный'),('suggest','предлагать'),('suit','подходить')],
]
SCENARIO_ORDER = {
 'general':list(range(20)), 'daily':list(range(20)),
 'travel':[0,7,3,5,4,6,8,12,13,9,10,11,2,18,14,1,16,17,15,19],
 'work':[0,15,11,16,4,2,17,13,18,9,10,12,6,8,14,1,3,5,7,19],
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


def plan(level, goal, ordinal, weak_topics=()):
    position = (ordinal - 1) % 20
    keys = topic_keys(level)
    key = keys[(position // 2 + (ordinal - 1) // 20 * 3) % len(keys)]
    reinforcement = ordinal % 3 == 0 and bool(weak_topics)
    if reinforcement:
        key = next((k for k in weak_topics if k in keys), key)
    scenario_offset = {'general':0,'daily':0,'travel':5,'work':15}[goal]
    # The first cycle keeps the authored pedagogical order. Further cycles
    # revisit skills in different communicative situations.
    index = SCENARIO_ORDER[goal][position] if ordinal <= 20 else (position + scenario_offset + ((ordinal - 1)//20)*3) % 20
    scenario = SCENARIOS[index]
    return {'ordinal':ordinal,'goal':goal,'cycle':(ordinal-1)//20+1,'position':position+1,
            'level':level,'topic_key':key,'scenario_index':index,'title':scenario[0],
            'objective':scenario[1],'kind':'review' if ordinal%5==0 else 'reinforcement' if reinforcement else 'new',
            'estimated_minutes':18,'version':VERSION}


def material(spec, review_words=()):
    key, ordinal = spec['topic_key'], spec['ordinal']
    title, objective, script, question, answer, distractors, turns = SCENARIOS[spec['scenario_index']]
    new_words = [{'word':word,'translation':translation} for word,translation in SCENARIO_WORDS[spec['scenario_index']]]
    words = list(review_words)[:3] or new_words[:3]
    warmup = []
    for i, word in enumerate(words):
        options = list(dict.fromkeys([word['translation'], 'погода','книга','дорога','музыка']))[:4]
        random.Random(f'{ordinal}:w:{i}').shuffle(options)
        warmup.append({'id':f'w{i}','skill':'vocabulary','word':word['word'],'prompt':word['word'],
                       'choices':options,'correct':word['translation'],'explanation':f"{word['word']} — {word['translation']}"})
    questions = [quiz(key,f'p{i}',ordinal+i) for i in range(3)]
    keys = topic_keys(spec['level'])
    previous = keys[(keys.index(key)-1)%len(keys)]
    questions += [quiz(previous,f'p{i}',ordinal+i) for i in range(3,5)]
    if spec['kind']=='review':
        questions = [quiz(keys[(keys.index(key)-i//2)%len(keys)],f'p{i}',ordinal+i) for i in range(5)]
    options = [answer,*distractors]
    random.Random(ordinal).shuffle(options)
    return {'spec':copy.deepcopy(spec),'warmup':warmup,'theory':THEORY[key], 'practice':questions,
        'listening':{'script':script+' '+EXTENSIONS[spec['level']], 'questions':[
            {'id':'l0','skill':'listening','prompt':question,'choices':options,'correct':answer,
             'explanation':'Ответ следует из прослушанного текста.'},
        ]},
        'conversation':{'objective':objective,'prompts':turns,'opening':turns[0]},
        'words':new_words,'source':'authored'}


def validate_material(data):
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
