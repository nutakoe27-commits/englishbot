"""
grammar_lessons.py — рукописная теория уроков Grammar Learn.

Источник истины для ТЕОРИИ — этот файл (не LLM, не БД). LLM генерирует
только упражнения к уроку — каждому юзеру свежие (см. grammar.py /lesson).

Ключи совпадают с grammar_topics.key (миграции 0011 + 0012).
Формат theory: plain text, абзацы через пустую строку; примеры в формате
"EN — RU" каждый на своей строке. Фронт рендерит split("\\n").

Правка контента: обычный git-PR в этот файл, без SQL.
"""

# fmt: off

THEORY: dict[str, str] = {

    # ═══ A1 — совсем новички ═══════════════════════════════════════════

    "a1_to_be": """Глагол to be — «быть, являться, находиться». В русском мы говорим «я студент», в английском связка обязательна: «я ЕСТЬ студент» — I am a student.

Три формы в настоящем: **I am; he / she / it is; you / we / they are**. Сокращения: I'm, he's, she's, it's, you're, we're, they're.

Отрицание — добавляем not: I'm not, he isn't, they aren't. Вопрос — be выходит вперёд: Are you tired? Is she at home?

Главная ловушка русскоговорящих — пропускать to be: ❌ He doctor → ✅ He IS a doctor.

I am from Russia. — Я из России.
She is my sister. — Она моя сестра.
They are at work now. — Они сейчас на работе.
Is he your friend? — Он твой друг?
We aren't ready. — Мы не готовы.""",

    "a1_pronouns": """Личные местоимения — кто делает действие: I (я), you (ты/вы), he (он), she (она), it (оно — для предметов и животных), we (мы), they (они).

Притяжательные — чей предмет: my, your, his, her, its, our, their. Они всегда стоят ПЕРЕД существительным: my car, her phone.

Ловушка: his (его) и her (её) путают. Правило простое: his — про мужчину, her — про женщину, независимо от рода предмета: his bag (его сумка), her book (её книга).

It используется для всего неживого и для животных: The car is new. It is red.

I love my job. — Я люблю свою работу.
She lost her keys. — Она потеряла свои ключи.
This is his brother. — Это его брат.
Our house is small. — Наш дом маленький.
They sold their car. — Они продали свою машину.""",

    "a1_this_that": """Указательные местоимения: this (этот — близко), that (тот — далеко), these (эти — близко, мн. число), those (те — далеко, мн. число).

Запоминается парами: this/these — рядом, можно дотронуться; that/those — там, дальше.

Часто this/that используется при знакомстве и по телефону: This is my friend Anna. (Это моя подруга Анна.)

Ловушка: these и this звучат похоже — these [ðiːz] длинный звук, this [ðɪs] короткий.

This is my desk. — Это мой стол (рядом).
That building is a museum. — То здание — музей (далеко).
These shoes are new. — Эти туфли новые.
Those people are tourists. — Те люди — туристы.
Is that your car? — Это (вон та) твоя машина?""",

    "a1_have_got": """Have got — «иметь, обладать»: I have got = I've got (у меня есть). Для he/she/it: has got = he's got.

Отрицание: haven't got / hasn't got. Вопрос: Have you got…? Has she got…?

В разговорном британском have got — самый частый способ сказать «у меня есть». Американцы чаще говорят просто have: I have a car.

Ловушка: «у меня есть» НЕ переводится через is: ❌ At me is car → ✅ I've got a car / I have a car.

I've got two brothers. — У меня два брата.
She's got a new phone. — У неё новый телефон.
Have you got a pen? — У тебя есть ручка?
We haven't got time. — У нас нет времени.
He hasn't got a bike. — У него нет велосипеда.""",

    "a1_there_is": """There is / there are — конструкция «есть, находится, имеется» — когда говорим, ЧТО где-то существует: There is a cat in the room (В комнате кошка).

There is + единственное число; there are + множественное. Сокращение: there's.

Отрицание: there isn't / there aren't. Вопрос: Is there…? Are there…?

Ловушка: в русском «в комнате кошка» начинается с места. В английском конструкция начинается с there: ❌ In room is cat → ✅ There is a cat in the room.

There is a park near my house. — Рядом с моим домом есть парк.
There are five people in the office. — В офисе пять человек.
Is there a bank here? — Здесь есть банк?
There isn't any milk. — Молока нет.
Are there any questions? — Есть вопросы?""",

    "a1_present_simple_i": """Present Simple — регулярные действия, привычки, факты. Для I / you / we / they глагол идёт в начальной форме: I work, they live.

Используем со словами-маркерами: every day (каждый день), usually (обычно), often (часто), never (никогда), sometimes (иногда).

Маркер ставится перед глаголом: I usually get up at 7. Но after to be: She is always late.

I work in a bank. — Я работаю в банке.
We live in Moscow. — Мы живём в Москве.
They play football every Sunday. — Они играют в футбол каждое воскресенье.
You speak English well. — Ты хорошо говоришь по-английски.
I often read before bed. — Я часто читаю перед сном.""",

    "a1_present_simple_s": """В Present Simple для he / she / it к глаголу добавляется -s: he works, she lives, it costs.

Особые случаи: go → goes, do → does, watch → watches, study → studies, have → has.

Это окончание — ловушка №1 для русскоговорящих на любом уровне. Проговаривай про себя: «он/она/оно — плюс s».

He works in IT. — Он работает в IT.
She speaks three languages. — Она говорит на трёх языках.
My brother watches TV a lot. — Мой брат много смотрит телевизор.
The shop opens at 9. — Магазин открывается в 9.
It costs ten dollars. — Это стоит десять долларов.""",

    "a1_questions_negatives": """Вопросы и отрицания в Present Simple строятся через do / does. Does — для he/she/it, do — для остальных.

Отрицание: don't / doesn't + глагол в начальной форме: I don't smoke. He doesn't smoke (НЕ doesn't smokes — s уходит в does!).

Вопрос: Do you like coffee? Does she work here? Краткий ответ: Yes, I do. / No, she doesn't.

Ловушка: после does глагол БЕЗ -s: ❌ Does he works? → ✅ Does he work?

Do you like pizza? — Ты любишь пиццу?
Does she live here? — Она здесь живёт?
I don't understand. — Я не понимаю.
He doesn't eat meat. — Он не ест мясо.
Where do you work? — Где ты работаешь?""",

    "a1_articles_a_an": """Неопределённый артикль a / an значит «один, какой-то, любой». Используется с исчисляемыми существительными в единственном числе: a book, a car.

An — перед гласным ЗВУКОМ: an apple, an hour (h не читается!). A — перед согласным: a banana, a university (звук [ju] — согласный!).

Когда употреблять: впервые упоминаем предмет (I see a dog), профессии (She is a doctor), «один из многих» (Give me an apple — любое).

Ловушка: в русском артиклей нет, поэтому их забывают: ❌ She is doctor → ✅ She is A doctor.

I need a pen. — Мне нужна ручка (любая).
He is an engineer. — Он инженер.
It takes an hour. — Это занимает час.
She bought a new dress. — Она купила новое платье.
There is a problem. — Есть (одна) проблема.""",

    "a1_imperatives": """Повелительное наклонение — команды, просьбы, инструкции. Просто глагол без подлежащего: Sit down. Open the door.

Отрицание через don't: Don't worry. Don't be late.

Чтобы звучать вежливо, добавляем please: Please wait. / Wait, please.

Let's (= let us) — предложение сделать что-то вместе: Let's go! (Пойдём!) Отрицание: Let's not argue.

Open your books. — Откройте книги.
Don't touch it! — Не трогай это!
Please call me later. — Пожалуйста, позвони мне позже.
Let's have lunch. — Давай пообедаем.
Don't forget your keys. — Не забудь ключи.""",

    # ═══ A2 ════════════════════════════════════════════════════════════

    "a2_present_simple": """Present Simple описывает привычки, регулярные действия, расписания и факты: I drink coffee every morning. The train leaves at 8.

**Форма:** I/you/we/they + глагол; he/she/it + глагол с -s. Вопросы и отрицания через do/does: Do you…? She doesn't…

Используется также для расписаний (даже про будущее): The film starts at 7 tonight.

Маркеры: always, usually, often, sometimes, rarely, never, every day/week.

I check email every morning. — Я проверяю почту каждое утро.
She doesn't eat after six. — Она не ест после шести.
The bus leaves at 7:30. — Автобус отходит в 7:30.
Water boils at 100 degrees. — Вода кипит при 100 градусах.
How often do you exercise? — Как часто ты тренируешься?""",

    "a2_present_continuous": """Present Continuous — действие происходит ПРЯМО СЕЙЧАС или в текущий период: I am reading (я читаю в данный момент).

**Форма:** am/is/are + глагол с -ing: She is working. They are watching TV.

Также — про запланированное будущее: I'm meeting Anna tomorrow (уже договорились).

Ловушка: глаголы состояния (know, like, want, need, understand) в Continuous НЕ ставятся: ❌ I am knowing → ✅ I know.

Маркеры: now, right now, at the moment, today, this week.

I'm cooking dinner now. — Я сейчас готовлю ужин.
She is talking on the phone. — Она разговаривает по телефону.
We're learning English this year. — Мы учим английский в этом году.
He isn't sleeping. — Он не спит.
What are you doing? — Что ты делаешь?""",

    "a2_past_simple": """Past Simple — завершённое действие в прошлом, часто с указанием когда: I visited Paris last year.

Правильные глаголы: + -ed (worked, played). Неправильные — третья колонка наизусть: go → went, see → saw, have → had, do → did.

Отрицание и вопрос через did: I didn't go. Did you see it? После did глагол в начальной форме!

Маркеры: yesterday, last week/month/year, ago, in 2010.

I worked late yesterday. — Вчера я работал допоздна.
She went to Italy in May. — Она ездила в Италию в мае.
We didn't watch the film. — Мы не смотрели фильм.
Did you call him? — Ты ему звонил?
They moved here two years ago. — Они переехали сюда два года назад.""",

    "a2_articles_basics": """Три варианта: a/an (неопределённый), the (определённый), нулевой артикль (без артикля).

A/an — впервые упомянутый, «один из многих»: I saw a film. The — конкретный, уже известный обоим: The film was great (тот самый).

The также: единственное в своём роде (the sun, the internet), музыкальные инструменты (play the guitar), некоторые географические названия (the USA, the Alps).

Без артикля: имена (Anna), города и большинство стран (Moscow, Russia), еда/языки/виды спорта в общем смысле (I like pizza, speak English, play football).

I bought a jacket. The jacket is warm. — Я купил куртку. (Эта) куртка тёплая.
The sun is bright today. — Солнце сегодня яркое.
She plays the piano. — Она играет на пианино.
We visited France. — Мы посетили Францию.
I love music. — Я люблю музыку.""",

    "a2_plurals_countability": """Множественное число: обычно + -s (cars), после -ch/-sh/-s/-x — -es (watches), согласная + y → -ies (cities). Неправильные: man → men, woman → women, child → children, person → people, foot → feet, tooth → teeth.

Исчисляемые можно посчитать: an apple, two apples. Неисчисляемые — нет: water, money, information, advice, news. Они всегда в единственном числе: The news IS good.

Ловушка: money, advice, information в английском НЕисчисляемые: ❌ informations, ❌ advices → ✅ some information, a piece of advice.

Two children are playing. — Двое детей играют.
People are friendly here. — Люди здесь дружелюбные.
I need some information. — Мне нужна информация.
The money is on the table. — Деньги на столе.
She gave me good advice. — Она дала мне хороший совет.""",

    "a2_prepositions_place": """Предлоги места: in (внутри) — in the box, in the room, in Moscow; on (на поверхности) — on the table, on the wall; at (в точке, у) — at the door, at the bus stop, at work.

Запомнить связки: at home, at work, at school, BUT in the office, in bed.

Другие: under (под), next to (рядом с), between (между), behind (за), in front of (перед), opposite (напротив).

The keys are in my bag. — Ключи у меня в сумке.
The picture is on the wall. — Картина на стене.
I'm at the airport. — Я в аэропорту.
The cat is under the sofa. — Кошка под диваном.
The bank is next to the cafe. — Банк рядом с кафе.""",

    "a2_prepositions_time": """Предлоги времени — правило «матрёшки»: at — точное время (at 5 o'clock, at noon, at night); on — день/дата (on Monday, on 5 May, on my birthday); in — месяц/год/сезон/части дня (in May, in 2024, in winter, in the morning).

Исключения-ловушки: at night (но in the morning/afternoon/evening), at the weekend (брит.) / on the weekend (амер.).

Без предлога: this/last/next/every — ❌ in next week → ✅ next week.

The meeting is at 3 pm. — Встреча в 15:00.
I was born on 12 June. — Я родился 12 июня.
We met in 2020. — Мы познакомились в 2020-м.
He works at night. — Он работает по ночам.
See you next Friday. — Увидимся в следующую пятницу.""",

    "a2_can_cant": """Can — умения, возможности, разрешения: I can swim (умею), You can go (можно), Can you help? (просьба).

Форма не меняется: he can (без -s!), после can глагол в начальной форме: She can drive (❌ can drives, ❌ can to drive).

Отрицание: can't / cannot. Вопрос: Can you…?

Для вежливых просьб также could: Could you open the window? (Не могли бы вы…)

I can speak a little English. — Я немного говорю по-английски.
She can't come today. — Она не может прийти сегодня.
Can I ask a question? — Можно задать вопрос?
He can fix anything. — Он может починить что угодно.
Could you repeat that, please? — Не могли бы вы повторить?""",

    "a2_going_to": """Be going to — планы и намерения (уже решил): I'm going to buy a car (собираюсь купить).

**Форма:** am/is/are + going to + глагол: She's going to study abroad.

Также — предсказания по очевидным признакам: Look at the clouds! It's going to rain (вот-вот пойдёт — видно по тучам).

Разговорное сокращение: gonna (I'm gonna call you) — только в речи, не на письме.

I'm going to start a new job. — Я собираюсь выйти на новую работу.
We're going to visit my parents. — Мы собираемся навестить моих родителей.
She isn't going to agree. — Она не согласится (не собирается).
Are you going to tell him? — Ты собираешься ему сказать?
It's going to be a long day. — Это будет долгий день.""",

    "a2_comparatives": """Сравнительная степень: короткие прилагательные + -er (older, bigger — удвоение согласной после краткого гласного), длинные — more + прилагательное (more expensive). Сравниваем через than: He is taller than me.

Превосходная: the + -est / the most: the oldest, the most interesting.

Неправильные: good → better → the best; bad → worse → the worst; far → further → the furthest.

Ловушка: не смешивать формы: ❌ more better → ✅ better; ❌ more easier → ✅ easier.

This phone is cheaper than that one. — Этот телефон дешевле того.
Today is hotter than yesterday. — Сегодня жарче, чем вчера.
It's the best film of the year. — Это лучший фильм года.
The test was more difficult than I expected. — Тест был сложнее, чем я ожидал.
She is the most talented person here. — Она самый талантливый человек здесь.""",

    # ═══ B1 ════════════════════════════════════════════════════════════

    "b1_present_perfect_vs_past": """Present Perfect (**have/has + 3-я форма глагола**: done, seen, lost) — результат к настоящему моменту, время не указано: I have lost my keys (ключей нет СЕЙЧАС).

Past Simple — факт в прошлом, время известно: I lost my keys yesterday.

Ключевое правило: есть слова yesterday, last week, ago, in 2020 → ТОЛЬКО Past Simple. Слова just, already, yet, ever, never, since, for → обычно Present Perfect.

Ловушка русскоговорящих: в русском одно прошедшее время, поэтому Present Perfect кажется лишним. Думай о СВЯЗИ с настоящим: важен результат сейчас — Perfect; важно когда — Past Simple.

I've already eaten. — Я уже поел (сейчас не голоден).
She has never been to Asia. — Она никогда не была в Азии.
We saw that film last week. — Мы посмотрели этот фильм на прошлой неделе.
Have you finished the report? — Ты закончил отчёт?
He worked there in 2019. — Он работал там в 2019-м.""",

    "b1_past_continuous": """Past Continuous (**was/were + глагол с -ing**) — процесс в конкретный момент прошлого: At 8 pm I was having dinner (в 8 я ужинал — процесс).

Классическая связка с Past Simple — «длинное действие прервано коротким»: I was taking a shower when the phone rang (принимал душ, когда зазвонил телефон).

When + Past Simple, while + Past Continuous: While I was cooking, he was watching TV.

Маркеры: at 5 o'clock yesterday, all evening, while, when.

I was working at midnight. — В полночь я (ещё) работал.
They were playing football when it started to rain. — Они играли в футбол, когда начался дождь.
She was reading while he was driving. — Она читала, пока он вёл машину.
What were you doing at 9? — Что ты делал в 9?
I wasn't sleeping. — Я не спал.""",

    "b1_will_vs_going_to": """Will — спонтанное решение в момент речи, предсказание-мнение, обещание: I'll help you (решил прямо сейчас).

Going to — план, принятый ЗАРАНЕЕ: I'm going to help him tomorrow (уже решено).

Предсказания: will — мнение (I think it will rain), going to — по очевидным признакам (Look at the sky — it's going to rain).

Ловушка: после when/if в придаточном будущего НЕТ: ❌ when I will come → ✅ when I come.

I'll call you back. — Я перезвоню (решил сейчас).
We're going to move next month. — Мы переезжаем в следующем месяце (план).
She'll probably agree. — Она, вероятно, согласится.
I'll have the steak, please. — Мне, пожалуйста, стейк (решение у официанта).
When he arrives, I'll tell him. — Когда он приедет, я ему скажу.""",

    "b1_first_conditional": """First Conditional — реальное условие в будущем: If it rains, we will stay home (вполне возможно).

**Формула:** If + Present Simple, will + глагол. В if-части БУДУЩЕГО НЕТ: ❌ If it will rain → ✅ If it rains.

Вместо if: unless (= if not): Unless you hurry, you'll be late (Если не поторопишься…). When, as soon as, before, after — то же правило: As soon as I finish, I'll call.

Вместо will возможны can/may/might/императив: If you're tired, take a break.

If you study, you'll pass the exam. — Если будешь заниматься, сдашь экзамен.
If she calls, I'll let you know. — Если она позвонит, я дам тебе знать.
Unless we leave now, we'll miss the train. — Если не выйдем сейчас, опоздаем на поезд.
What will you do if he says no? — Что будешь делать, если он откажет?
If I see him, I'll pass on your message. — Если увижу его, передам твоё сообщение.""",

    "b1_second_conditional": """Second Conditional — нереальная/маловероятная ситуация сейчас или в будущем: If I had a million, I would travel (но миллиона нет).

**Формула:** If + Past Simple, would + глагол. Past здесь НЕ про прошлое — это «нереальность».

С to be часто were для всех лиц: If I were you, I would apologize (на твоём месте я бы…).

Сравни: If I win (реально — First) vs If I won (мечтаю — Second).

If I had more time, I would learn Spanish. — Будь у меня больше времени, я бы выучил испанский.
If she lived closer, we would meet more often. — Если бы она жила ближе, мы бы виделись чаще.
What would you do if you lost your job? — Что бы ты делал, если бы потерял работу?
If I were you, I wouldn't worry. — На твоём месте я бы не переживал.
He would help if he could. — Он бы помог, если бы мог.""",

    "b1_modals_obligation": """Must — внутренняя убеждённость или строгое правило: I must finish this today (сам считаю важным).

Have to — внешняя необходимость: I have to wear a uniform (требование работодателя). В вопросах и отрицаниях почти всегда have to: Do I have to…?

Ловушка-перевёртыш: mustn't = ЗАПРЕЩЕНО (You mustn't smoke here), don't have to = НЕ ОБЯЗАН (You don't have to come — можешь не приходить).

Should — совет, рекомендация: You should see a doctor (стоит, советую).

I must remember her birthday. — Я обязательно должен вспомнить про её день рождения.
We have to check in at 2 pm. — Мы должны зарегистрироваться в 14:00.
You mustn't tell anyone. — Никому нельзя говорить.
You don't have to pay — it's free. — Платить не нужно — это бесплатно.
You should get more sleep. — Тебе стоит больше спать.""",

    "b1_passive_basics": """Пассивный залог — когда важно действие, а не кто его сделал: The house was built in 1990 (дом построили — кем, неважно).

**Формула:** be (в нужном времени) + 3-я форма глагола: is made, was sent, will be done.

Деятель добавляется через by: The book was written by Orwell.

Используем когда: деятель неизвестен/неважен/очевиден (English is spoken here), формальный стиль (The decision has been made).

This cheese is made in Italy. — Этот сыр делают в Италии.
The email was sent yesterday. — Письмо отправили вчера.
The road is being repaired. — Дорогу (сейчас) ремонтируют.
You will be informed. — Вас проинформируют.
The window was broken by the kids. — Окно разбили дети.""",

    "b1_relative_clauses": """Относительные придаточные уточняют существительное: The man who called you is my boss (который позвонил).

Who — для людей, which — для предметов, that — универсальный (разговорный): The book that/which I read…

Whose — чей: The girl whose phone rang… Where — где: The cafe where we met…

Если местоимение — дополнение, его можно опустить: The film (that) I watched was great.

The woman who lives next door is a vet. — Женщина, которая живёт по соседству, — ветеринар.
This is the laptop which/that I bought. — Это ноутбук, который я купил.
He's the guy whose brother plays in a band. — Это парень, чей брат играет в группе.
That's the place where we first met. — Это место, где мы впервые встретились.
The pizza (that) you ordered is here. — Пицца, которую ты заказал, прибыла.""",

    "b1_used_to": """Used to + глагол — прошлые привычки и состояния, которых больше НЕТ: I used to smoke (раньше курил, теперь нет).

Отрицание: didn't use to (без -d!): I didn't use to like coffee. Вопрос: Did you use to…?

Не путать: be used to + -ing = «привыкший к» (I'm used to getting up early — мне привычно), get used to = «привыкать».

Для повторявшихся действий (не состояний) также would: We would play outside all day. Но для состояний только used to: ❌ I would have a dog → ✅ I used to have a dog.

I used to live in the countryside. — Раньше я жил за городом.
She used to be very shy. — Раньше она была очень стеснительной.
Did you use to play any sports? — Ты раньше занимался каким-нибудь спортом?
We didn't use to have a car. — Раньше у нас не было машины.
I'm used to working late. — Я привык работать допоздна.""",

    "b1_gerund_vs_infinitive": """После одних глаголов идёт герундий (-ing), после других — инфинитив (to + глагол). Это нужно запоминать списками.

Герундий после: enjoy, finish, mind, suggest, avoid, keep, practise: I enjoy reading. Также после предлогов: good at swimming, interested in learning.

Инфинитив после: want, decide, hope, plan, need, agree, promise, learn: I want to go.

Смена смысла: stop smoking (бросить курить) vs stop to smoke (остановиться, чтобы покурить); remember to lock (не забудь запереть) vs remember locking (помню, как запирал).

I enjoy cooking on weekends. — Я люблю готовить по выходным.
She decided to change jobs. — Она решила сменить работу.
He avoided answering the question. — Он уклонился от ответа.
We hope to see you soon. — Надеемся скоро тебя увидеть.
Stop making that noise! — Перестань шуметь!""",

    "b1_quantifiers": """Much — с неисчисляемыми (much time), many — с исчисляемыми (many friends). В утверждениях разговорнее a lot of (подходит для всего).

Some — в утверждениях и предложениях/просьбах (Would you like some tea?), any — в вопросах и отрицаниях (Is there any sugar? There isn't any).

A few (немного, достаточно) + исчисляемые; a little + неисчисляемые. Без a — оттенок нехватки: few friends (мало друзей, грустно).

How much money…? How many people…?

I don't have much time. — У меня мало времени.
There are many options. — Есть много вариантов.
Can I have some water? — Можно мне воды?
We have a few minutes left. — У нас осталось несколько минут.
There's a little milk in the fridge. — В холодильнике есть немного молока.""",

    "b1_phrasal_verbs_intro": """Фразовый глагол = глагол + частица, смысл часто не складывается из частей: give up — сдаваться, look after — присматривать.

Базовый набор: get up (вставать), turn on/off (включать/выключать), find out (узнавать), look for (искать), pick up (забирать/поднимать), put on (надевать), take off (снимать/взлетать), go on (продолжать).

Один глагол — много значений: take off — снимать одежду И взлетать (самолёт).

Учить лучше в контексте предложения, а не списком переводов.

I get up at seven. — Я встаю в семь.
Can you turn off the light? — Выключишь свет?
I need to find out the truth. — Мне нужно узнать правду.
She's looking for her glasses. — Она ищет свои очки.
The plane took off on time. — Самолёт взлетел вовремя.""",

    # ═══ B2 ════════════════════════════════════════════════════════════

    "b2_present_perfect_cont": """Present Perfect Continuous (**have/has been + глагол с -ing**) — действие началось в прошлом и ДЛИТСЯ до сих пор, акцент на процессе и длительности: I have been waiting for an hour (жду уже час).

For + период (for two hours), since + точка отсчёта (since Monday).

Сравни с Present Perfect: I've been reading the book (процесс, ещё читаю) vs I've read the book (результат, дочитал).

Глаголы состояния (know, love, have-владеть) — только Perfect: I've known him for years (❌ have been knowing).

I've been learning English for three years. — Я учу английский три года.
She's been working here since 2021. — Она работает здесь с 2021-го.
It has been raining all day. — Дождь идёт весь день.
How long have you been waiting? — Сколько ты уже ждёшь?
He's tired because he's been training. — Он устал, потому что тренировался.""",

    "b2_third_conditional": """Third Conditional — нереальное прошлое, сожаления и «а если бы»: If I had known, I would have come (но я не знал и не пришёл).

**Формула:** If + Past Perfect (had + 3-я форма глагола), would have + 3-я форма.

Это время «упущенных возможностей» — обе части про прошлое, изменить уже нельзя.

Сокращения в речи: would have → would've, had → 'd: If I'd known, I would've helped.

If you had told me, I would have helped. — Если бы ты сказал мне, я бы помог.
She would have passed if she had studied more. — Она бы сдала, если бы больше занималась.
If we hadn't missed the train, we would have arrived on time. — Если бы мы не опоздали на поезд, приехали бы вовремя.
I wouldn't have said that if I had known the truth. — Я бы этого не сказал, если бы знал правду.
What would you have done? — А что бы сделал ты?""",

    "b2_mixed_conditionals": """Mixed Conditionals смешивают времена: условие в прошлом → результат в настоящем, или наоборот.

Тип 1 (прошлое → настоящее): If + Past Perfect, would + глагол: If I had taken that job, I would be rich now (не взял тогда — не богат сейчас).

Тип 2 (настоящее → прошлое): **If + Past Simple, would have + 3-я форма**: If I were braver, I would have asked her out (я в принципе не смелый — поэтому тогда не позвал).

Ключ — определить, КОГДА условие и КОГДА результат.

If I had studied medicine, I would be a doctor now. — Если бы я (тогда) выучился на врача, сейчас был бы доктором.
If she had caught the flight, she would be here already. — Если бы она успела на рейс, уже была бы здесь.
If he weren't so lazy, he would have finished by now. — Не будь он таким ленивым, уже бы закончил.
If I didn't have kids, I would have moved abroad. — Если бы у меня не было детей, я бы (тогда) переехал за границу.""",

    "b2_passive_advanced": """Продвинутый пассив. Have something done — «мне сделали»: I had my hair cut (мне подстригли волосы — сам не стриг).

Пассив с двумя дополнениями: I was given a present (мне подарили) — естественнее, чем A present was given to me.

Пассив с say/believe/know: He is said to be rich (говорят, что он богат). It is believed that… (считается, что…).

Get вместо be в разговорном: He got promoted (его повысили).

I'm having my car repaired. — Мне ремонтируют машину.
She had her phone stolen. — У неё украли телефон.
We were told to wait. — Нам велели подождать.
The company is known to pay well. — Известно, что компания хорошо платит.
He got invited to the party. — Его пригласили на вечеринку.""",

    "b2_reported_speech": """Косвенная речь — пересказ чужих слов со сдвигом времени назад: "I am tired" → She said (that) she was tired.

Сдвиги: Present Simple → Past Simple; Present Continuous → Past Continuous; Past Simple → Past Perfect; will → would; can → could.

Указатели тоже меняются: today → that day, tomorrow → the next day, here → there.

Вопросы: порядок слов прямой! He asked where I lived (❌ where did I live). Yes/No-вопросы через if/whether: She asked if I was ready.

He said he was busy. — Он сказал, что занят.
She told me she had seen the film. — Она сказала мне, что (уже) видела фильм.
They said they would come later. — Они сказали, что придут позже.
He asked me where I worked. — Он спросил, где я работаю.
She asked if I could help. — Она спросила, могу ли я помочь.""",

    "b2_wish_if_only": """Wish/If only — сожаления. О настоящем: wish + Past Simple: I wish I knew (жаль, что не знаю — сейчас).

О прошлом: wish + Past Perfect: I wish I had gone (жаль, что не пошёл — тогда).

О раздражающем поведении: wish + would: I wish you would stop shouting (да прекрати же).

If only — то же, но эмоциональнее: If only I had more time!

I wish I spoke French. — Жаль, что я не говорю по-французски.
She wishes she had accepted the offer. — Она жалеет, что не приняла предложение.
I wish it would stop raining. — Хоть бы дождь прекратился.
If only we had left earlier! — Если бы мы только выехали раньше!
I wish I were taller. — Жаль, что я не выше ростом.""",

    "b2_modals_deduction": """Модальные глаголы дедукции о ПРОШЛОМ: **модальный глагол + have + 3-я форма глагола** (done, been, gone).

Must have — уверен, что было: He must have forgotten (наверняка забыл). Can't have — уверен, что НЕ было: She can't have said that (не может быть, чтобы она это сказала). Might/may/could have — возможно: They might have left already.

Отдельная ловушка: **needn't have + 3-я форма** = сделал зря (You needn't have cooked — we ate out. Зря готовил).

Should have — упрёк/сожаление: You should have called me (надо было позвонить).

He must have missed the bus. — Он, должно быть, опоздал на автобус.
She can't have finished so quickly. — Не может быть, что она так быстро закончила.
They might have got lost. — Возможно, они заблудились.
I should have listened to you. — Надо было тебя послушать.
You needn't have worried. — Зря ты волновался.""",

    "b2_causative": """Каузатив have/get something done — действие выполняет кто-то другой для нас.

**Have + объект + 3-я форма глагола**: I had the house painted (мне покрасили дом). Get — разговорнее: I got my phone fixed.

Сравни: I cut my hair (сам) vs I had my hair cut (в парикмахерской).

Также get someone to do (уговорить/заставить): I got him to help me. Have someone do (поручить): I had the assistant book the tickets.

We're having the kitchen renovated. — Нам делают ремонт кухни.
She got her dress cleaned. — Она отдала платье в чистку.
I need to have my eyes tested. — Мне нужно проверить зрение.
He had his wallet stolen. — У него украли кошелёк.
I'll get my brother to drive us. — Я попрошу брата нас отвезти.""",

    "b2_linking_words": """Связки делают речь логичной. Противопоставление: however (однако — после точки/точки с запятой), although/though (хотя + придаточное), despite/in spite of (несмотря на + существительное/-ing).

Ловушка: despite БЕЗ of: ❌ despite of the rain → ✅ despite the rain / in spite of the rain.

Причина: because + придаточное, because of + существительное. Следствие: therefore, as a result, that's why. Добавление: moreover, in addition, besides.

Although it was late, we kept working. — Хотя было поздно, мы продолжали работать.
The flight was delayed. However, we arrived on time. — Рейс задержали. Однако мы прибыли вовремя.
Despite the traffic, she wasn't late. — Несмотря на пробки, она не опоздала.
He stayed home because of the storm. — Он остался дома из-за шторма.
The product is cheap. Moreover, it's reliable. — Продукт дешёвый. Более того, надёжный.""",

    "b2_phrasal_separable": """Разделяемые фразовые глаголы: дополнение может стоять между глаголом и частицей: turn on the light = turn the light on.

ЖЁСТКОЕ правило: местоимение — ТОЛЬКО внутри: turn it on (❌ turn on it).

Неразделяемые — частица приклеена: look after the kids → look after them (❌ look the kids after). Типичные неразделяемые: look for, look after, get on/off, run into, deal with.

Трёхчастные всегда неразделяемые: look forward to, get on with, put up with: I put up with it.

Turn the music down, please. — Сделай музыку потише.
I'll pick you up at eight. — Я заберу тебя в восемь.
She takes after her mother. — Она пошла в мать.
We need to deal with this problem. — Нам нужно разобраться с этой проблемой.
I'm looking forward to it. — Я этого с нетерпением жду.""",

    # ═══ C1 ════════════════════════════════════════════════════════════

    "c1_inversion": """Инверсия — вынос отрицательного/ограничительного наречия вперёд с порядком слов вопроса. Делает речь выразительной и формальной.

Never have I seen such beauty (= I have never seen…). После: never, rarely, seldom, little, no sooner… than, hardly… when, not only… but also, under no circumstances.

Механика: наречие + вспомогательный глагол + подлежащее: Not only DID he win, but he also broke the record.

Условные без if: Had I known… (= If I had known), Were she here… (= If she were), Should you need help… (= If you should need).

Never have I been so tired. — Никогда я не был так измотан.
Rarely does he make mistakes. — Он редко ошибается.
No sooner had we left than it started to rain. — Едва мы вышли, начался дождь.
Under no circumstances should you open this door. — Ни при каких обстоятельствах не открывай эту дверь.
Had I known about the meeting, I would have come. — Знай я о встрече, я бы пришёл.""",

    "c1_cleft_sentences": """Cleft sentences (расщеплённые предложения) выделяют нужный элемент.

It-cleft: It was John who broke the cup (именно Джон). It was yesterday that we met (именно вчера).

What-cleft: What I need is a holiday (что мне нужно, так это отпуск). What he did was (to) call the police.

The thing/reason/place: The reason why I left was the noise. All — «единственное»: All I want is peace.

Эти конструкции — признак естественной беглой речи на высоком уровне.

It was the manager who made the decision. — Именно менеджер принял решение.
It's the small details that matter. — Важны именно мелкие детали.
What surprised me was his honesty. — Что меня удивило, так это его честность.
What we did was start over. — Что мы сделали — начали заново.
All she asked for was respect. — Всё, чего она просила, — уважение.""",

    "c1_subjunctive": """Subjunctive — базовая форма глагола (без -s, без to) после глаголов требования/предложения: suggest, demand, insist, recommend, propose, request + that.

I suggest that he BE on time (❌ is). They demanded that she RESIGN (❌ resigns).

Отрицание — not перед глаголом: I recommend that you not sign it.

Та же форма после it is important/essential/vital that: It is essential that everyone be informed.

В британском разговорном часто заменяется на should: I suggest that he should be on time — тоже верно.

I suggest that he see a doctor. — Я предлагаю, чтобы он показался врачу.
They insisted that the meeting be postponed. — Они настояли на переносе встречи.
It's vital that she know the truth. — Жизненно важно, чтобы она знала правду.
We recommend that he not travel alone. — Мы рекомендуем ему не ездить одному.
The judge ordered that the documents be released. — Судья распорядился обнародовать документы.""",

    "c1_advanced_modality": """Тонкая модальность уровня C1.

Be bound to — неизбежность: It's bound to rain (точно пойдёт). Be likely/unlikely to: She's likely to agree (вероятно согласится).

Dare — сметь: How dare you! Don't you dare touch it! Need как модальный: Need I say more? You needn't worry.

May/might as well — «можно и… (всё равно нечего терять)»: We might as well walk (давай уж пешком).

Will для типичного поведения: He WILL leave his socks everywhere (вечно он разбрасывает носки — раздражение).

You're bound to succeed. — Ты обязательно добьёшься успеха.
He's unlikely to change his mind. — Вряд ли он передумает.
How dare she speak to you like that! — Да как она смеет так с тобой разговаривать!
We may as well start without him. — Можно начинать и без него.
She will keep interrupting people. — Вечно она всех перебивает.""",

    "c1_ellipsis": """Эллипсис и замещение — пропуск повторяющихся частей, признак естественной речи.

So/neither + вспомогательный: I love jazz. — So do I (я тоже). I can't swim. — Neither can I (я тоже не).

Замещение: one/ones вместо существительного (I'll take the blue one), do вместо глагольной группы (He runs faster than I do), so/not после think/hope/afraid: I think so. I hope not. I'm afraid so.

Инфинитив-обрубок: You can stay if you want to (без повторения stay).

She's been to Japan, and so have I. — Она была в Японии, и я тоже.
I don't like horror films. — Neither does he. — Я не люблю ужасы. — Он тоже.
Which cake do you want? The chocolate one. — Какой торт хочешь? Шоколадный.
Will it work? — I hope so. — Сработает? — Надеюсь.
You don't have to come, but you're welcome to. — Можешь не приходить, но мы будем рады.""",

    "c1_discourse_markers": """Дискурсивные маркеры структурируют живую речь.

Mind you — «учти, правда»: It's expensive. Mind you, the quality is superb. As it were — «так сказать»: He's the boss, as it were. That said — «при этом»: The plan is risky. That said, it could work.

Having said that = that said. Incidentally / by the way — «кстати». As far as X is concerned — «что касается X». At the end of the day — «в конечном счёте».

Эти маркеры — то, что отличает учебниковый английский от живого.

The hotel was pricey. Mind you, breakfast was included. — Отель дорогой. Правда, завтрак входил.
Incidentally, have you heard from Mark? — Кстати, Марк не объявлялся?
As far as money is concerned, we're fine. — Что касается денег, у нас всё в порядке.
That said, I still have doubts. — При этом сомнения у меня остаются.
At the end of the day, it's your call. — В конечном счёте, решать тебе.""",

    "c1_collocations": """Коллокации — устойчивые сочетания, которые носители используют автоматически. Ошибка в коллокации выдаёт неносителя мгновенно.

Классика: make a decision (❌ take/do), do research, pay attention, heavily dependent (❌ strongly), bitterly disappointed, fully aware, highly unlikely.

Глагол+существительное: draw a conclusion, raise a question, meet a deadline, run a business, strike a balance.

Усилители подбираются к слову: deeply concerned, vastly different, utterly ridiculous, perfectly normal.

Учи слово сразу с его «партнёрами», а не в одиночку.

We need to make a decision today. — Нам нужно принять решение сегодня.
She's doing research on climate change. — Она занимается исследованием изменения климата.
He failed to meet the deadline. — Он не уложился в дедлайн.
I'm fully aware of the risks. — Я полностью осознаю риски.
It's highly unlikely to happen. — Это крайне маловероятно.""",

    "c1_nominalisation": """Номинализация — превращение глаголов и прилагательных в существительные. Основа формального и академического стиля.

We decided to expand → The decision to expand… They failed because they didn't plan → Their failure resulted from poor planning.

Типичные пары: grow → growth, improve → improvement, fail → failure, refuse → refusal, aware → awareness, able → ability.

Эффект: текст становится плотнее и официальнее. В живой речи злоупотреблять не стоит — звучит канцелярски.

The growth of the company surprised everyone. — Рост компании всех удивил.
His refusal to cooperate caused problems. — Его отказ сотрудничать создал проблемы.
There's been a significant improvement in sales. — Произошло значительное улучшение продаж.
The analysis revealed several weaknesses. — Анализ выявил несколько слабых мест.
Awareness of the issue is growing. — Осведомлённость о проблеме растёт.""",
}

# fmt: on
