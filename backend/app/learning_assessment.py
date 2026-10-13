"""Separate alternate forms for course checkpoints; never used in practice."""
import copy
import random
from .level_test_bank import BANK, VOCAB, LEVELS

EXTRA_GRAMMAR = {
 'A1':[
  ('My father ___ a nurse.','is','am','are','be'),('We ___ near the park.','live','lives','living','is live'),
  ('This is ___ orange.','an','a','some','any'),('___ you have a bicycle?','Do','Does','Is','Are'),
  ('My cousin ___ not like milk.','does','do','is','are'),('There ___ two doors.','are','is','am','has'),
  ('Those are my ___.','shoes','shoe','a shoe','an shoe'),('He has a sister. ___ name is Julia.','Her','His','Their','Its'),
  ('___ the door, please.','Close','Closes','Closing','To close'),('I go to school ___ Monday.','on','in','at','to')],
 'A2':[
  ('They ___ a new flat last year.','bought','buy','buying','have buy'),('He ___ his homework at the moment.','is doing','does','do','did'),
  ('We are going to ___ tennis tomorrow.','play','played','playing','plays'),('There is not ___ juice left.','much','many','a few','an'),
  ('This street is ___ than our street.','wider','wide','widest','most wide'),('Did you ___ to the party?','go','went','going','gone'),
  ('My birthday is ___ October.','in','on','at','to'),('She can ___ three languages.','speak','speaks','speaking','to speak'),
  ('The cat is hiding ___ the bed.','under','from','to','onto'),('Yesterday was the ___ day of the week.','hottest','hotter','hot','more hot')],
 'B1':[
  ('I ___ my wallet, so I cannot pay.','have lost','lose','was losing','had lose'),('We ___ TV when the doorbell rang.','were watching','watch','are watching','have watch'),
  ('If you ask politely, she ___ help you.','will','would','had','was'),('If I lived closer, I ___ walk to work.','would','will','had','have'),
  ('The bridge ___ opened in 2005.','was','is being','has','did'),('That is the man ___ repaired our roof.','who','where','when','whose'),
  ('He avoided ___ about the problem.','talking','to talk','talk','talked'),('We used to ___ near the sea.','live','lived','living','lives'),
  ('You ___ smoke in this building. It is forbidden.','must not','do not have to','need not','might'),('There are ___ people here than yesterday.','fewer','less','little','much')],
 'B2':[
  ('If they had listened, they ___ the mistake.','would have avoided','will avoid','would avoid','avoid'),('She asked whether I ___ the letter.','had received','have receive','do receive','will receiving'),
  ('The new road is ___ built now.','being','been','be','having'),('I wish you ___ making that noise.','would stop','will stop','have stopped','had stop'),
  ('His car is outside. He ___ be at home.','must','must not','cannot','would to'),('___ being tired, he continued working.','Despite','Although','Even though','Because'),
  ('She has been ___ for two hours.','waiting','wait','waited','to wait'),('I got my computer ___ by a specialist.','repaired','repairing','repair','to repairing'),
  ('If I had taken that job, I ___ in Paris now.','would be','will be','would have been','had been'),('He denied ___ the document.','signing','to sign','sign','signed')],
 'C1':[
  ('Under no circumstances ___ this information.','should you disclose','you should disclose','you disclose should','should disclose you'),('No sooner had we arrived ___ the lights went out.','than','when','then','that'),
  ('It is vital that every applicant ___ the form.','complete','completes','completed','will completes'),('What surprised me most ___ her confidence.','was','were','has','did'),
  ('Had I known the risks, I ___ differently.','would have acted','will act','would act','had act'),('The evidence is weak; ___, the claim remains unproven.','consequently','nevertheless','however','despite this'),
  ('She objected to the ___ of the agreement.','termination','terminate','terminated','terminally'),('You ___ have booked; there were plenty of seats.','need not','must not','cannot','will not'),
  ('The report ___ light on the issue.','sheds','does','takes','makes'),('Only after the vote ___ the result.','did they announce','they announced','they did announce','announced they')],
}
EXTRA_VOCAB = {
 'A1':[('chair','стул'),('window','окно'),('kitchen','кухня'),('sister','сестра'),('street','улица')],
 'A2':[('receipt','чек'),('appointment','назначенная встреча'),('luggage','багаж'),('neighbour','сосед'),('journey','поездка')],
 'B1':[('reliable','надёжный'),('opportunity','возможность'),('requirement','требование'),('achievement','достижение'),('disadvantage','недостаток')],
 'B2':[('reluctant','неохотный'),('drawback','недостаток'),('feasible','осуществимый'),('consequence','последствие'),('evidence','доказательство')],
 'C1':[('ambiguous','неоднозначный'),('scrutiny','тщательная проверка'),('contingent','зависящий от условий'),('discrepancy','расхождение'),('mitigate','смягчать')],
}


def bank():
    result = [dict(q, domain='vocabulary' if q in VOCAB else 'grammar') for q in BANK + VOCAB]
    for level, frames in EXTRA_GRAMMAR.items():
        for i, (prompt,correct,*other) in enumerate(frames):
            result.append({'id':f'cp_g_{level}_{i}','level':level,'domain':'grammar','skill':'grammar',
                'prompt':prompt,'choices':[correct,*other],'correct':correct,'note':'Повтори соответствующее правило в следующих занятиях.'})
    for level, words in EXTRA_VOCAB.items():
        all_translations = [translation for _,translation in words]
        for i, (word,correct) in enumerate(words):
            other = [t for t in all_translations if t != correct][:3]
            result.append({'id':f'cp_v_{level}_{i}','level':level,'domain':'vocabulary','skill':'vocab',
                'prompt':word,'choices':[correct,*other],'correct':correct,'note':f'{word} — {correct}'})
    return result


LISTENING = {
 'A1':('My name is Emma. I live in a small house. My brother lives in a flat. On Saturday we have lunch at my house. He brings bread and I make soup.', 'Where do they have lunch?','At Emma’s house.',['At a cafe.','At her brother’s flat.','At school.']),
 'A2':('Yesterday, Lucy went to the library to return a book. It was closed, so she went to a cafe next door. She met an old friend there. They agreed to come back to the library together on Thursday.', 'Why did Lucy go to the cafe?','The library was closed.',['She wanted to buy a book.','Her friend worked there.','She lost her keys.']),
 'B1':('Daniel has been planning a cycling trip for several weeks. His original route went through the mountains. However, his friend injured a knee, so they have chosen a shorter route along the river. They still plan to leave on Friday.', 'Why did they change the route?','His friend injured a knee.',['The river was flooded.','Their bicycles broke.','They had less time.']),
 'B2':('A community group had intended to renovate a neglected building. Although the council supported the idea, the initial funding application was rejected. Rather than abandon the project, the group reduced its scope and submitted a revised proposal focusing on essential repairs.', 'How did the group respond to the rejection?','Reduced the scope and applied again.',['Abandoned the project.','Bought another building.','Started without permission.']),
 'C1':('The committee’s recommendation was initially interpreted as an endorsement of immediate expansion. Its chair subsequently clarified that the proposal was contingent on an independent review of existing capacity. This qualification, overlooked in the preliminary press coverage, substantially altered the implications of the recommendation.', 'What was overlooked in the initial coverage?','Expansion depended on an independent review.',['The committee opposed any expansion.','The review had already been completed.','The chair had resigned.']),
}


def question_set(level, seed, recent_ids=()):
    rng = random.Random(seed)
    excluded = set(recent_ids)
    all_items = bank()
    result = []
    for lv in LEVELS:
        for domain in ('grammar','vocabulary'):
            pool = [q for q in all_items if q['level']==lv and q['domain']==domain]
            fresh = [q for q in pool if q['id'] not in excluded]
            selected = rng.sample(fresh if len(fresh)>=2 else pool,2)
            for q in selected:
                item = copy.deepcopy(q); rng.shuffle(item['choices']); item['kind']='choice'; result.append(item)
    rng.shuffle(result)
    script,prompt,correct,other = LISTENING[level]
    choices = [correct,*other]; rng.shuffle(choices)
    result.append({'id':'cp_listening','level':level,'domain':'listening','kind':'listening','script':script,
                   'prompt':prompt,'choices':choices,'correct':correct,'note':'Ответ следует из аудиотекста.'})
    result.append({'id':'cp_speaking','level':level,'domain':'speaking','kind':'speaking',
        'prompt':'Расскажи по-английски о решении, которое ты недавно принял: что произошло, почему ты так решил и что будешь делать дальше. На A1 достаточно нескольких простых фраз.',
        'choices':[]})
    return result


def grade(questions, answers):
    levels = {lv:{'correct':0,'total':0} for lv in LEVELS}
    skills = {}
    for question in questions:
        response = answers.get(question['id'],{})
        domain = question['domain']
        if domain=='speaking':
            if response.get('verified_voice') and response.get('score') is not None:
                skills['speaking']={'score':response['score'],'attempts':1,'assessment':'AI estimate from transcribed speech'}
            continue
        if domain=='listening' and response.get('text_mode'): continue
        metric = skills.setdefault(domain,{'correct':0,'total':0})
        metric['total'] += 1; metric['correct'] += int(response.get('correct',False))
        if domain in ('grammar','vocabulary'):
            levels[question['level']]['total'] += 1
            levels[question['level']]['correct'] += int(response.get('correct',False))
    cefr = LEVELS[0]
    for i, lv in enumerate(LEVELS):
        if levels[lv]['total'] and levels[lv]['correct']/levels[lv]['total'] >= .75 and all(
            levels[lower]['correct']/max(1,levels[lower]['total']) >= .5 for lower in LEVELS[:i]):
            cefr=lv
    return {'cefr':cefr,'by_level':levels,'skills':skills,
        'correct_cnt':sum(v.get('correct',0) for v in skills.values()),
        'total_cnt':sum(v.get('total',0) for v in skills.values())}
