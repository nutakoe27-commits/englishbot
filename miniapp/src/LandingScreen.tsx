/**
 * LandingScreen.tsx — публичная landing-страница для холодного трафика.
 *
 * Показывается до LoginScreen для не-залогиненных юзеров (см. main.tsx).
 * Цель — конверсия в регистрацию ~20% (по исследованию ниши).
 *
 * Структура (по итогам исследования рынка AI-English): hero с killer-feature
 * (голосовая практика с разбором ошибок на русском), боль→решение, 4 режима,
 * скриншоты, social proof (реальные отзывы), цена, FAQ, финальный CTA.
 *
 * Технически — обычный React-компонент в SPA. Если конверсия подтвердится,
 * Этап 2 — вынести в отдельный Vite entry point с pre-rendered HTML, чтобы
 * не тянуть бандл приложения и улучшить SEO/LCP.
 */

import { useEffect } from "react";
import { ymHit, ymReachGoal } from "./metrika";
import "./Landing.css";

interface Props {
  /** Юзер кликнул CTA «Попробовать» — открыть LoginScreen. */
  onStartTrial: () => void;
  /** Возвращающийся юзер «У меня есть аккаунт» — тоже LoginScreen. */
  onLogin: () => void;
}

export function LandingScreen({ onStartTrial, onLogin }: Props) {
  useEffect(() => {
    // Глобально html/body/#root имеют overflow:hidden (для SPA-приложения с
    // фиксированным viewport). На лендинге нужен обычный скролл документа —
    // добавляем body-класс на время монтирования, убираем при размонтировании.
    document.body.classList.add("lp-active");

    // Виртуальный pageview для отдельной страницы в Метрике.
    ymHit(window.location.origin + "/landing", "Лендинг — English Tutor");
    ymReachGoal("landing_view");

    // Цель «доскролл до прайсинга» — индикатор реального интереса.
    let firedScroll75 = false;
    const onScroll = () => {
      if (firedScroll75) return;
      const scrolled = window.scrollY + window.innerHeight;
      const total = document.body.scrollHeight;
      if (total > 0 && scrolled / total > 0.75) {
        firedScroll75 = true;
        ymReachGoal("landing_scroll_75");
      }
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      document.body.classList.remove("lp-active");
    };
  }, []);

  const handleCta = (location: string) => {
    ymReachGoal("landing_cta_click", { location });
    onStartTrial();
  };

  return (
    <div className="lp">
      <Header onLogin={onLogin} onCta={() => handleCta("header")} />
      <Hero onCta={() => handleCta("hero")} />
      <PainSolution />
      <KillerFeature />
      <Modes />
      <Screenshots />
      <SocialProof />
      <Pricing onCta={() => handleCta("pricing")} />
      <Charity />
      <Faq />
      <FinalCta onCta={() => handleCta("final")} />
      <Footer />
    </div>
  );
}

/* ─── Header ─────────────────────────────────────────────────────────── */
function Header({ onLogin, onCta }: { onLogin: () => void; onCta: () => void }) {
  return (
    <header className="lp-header">
      <div className="lp-container lp-header__inner">
        <div className="lp-brand">
          <span className="lp-brand__dot" aria-hidden />
          <span className="lp-brand__name">English Tutor</span>
        </div>
        <nav className="lp-nav">
          <button type="button" className="lp-nav__link" onClick={onLogin}>
            Войти
          </button>
          <button type="button" className="lp-btn lp-btn--primary lp-btn--sm" onClick={onCta}>
            Попробовать
          </button>
        </nav>
      </div>
    </header>
  );
}

/* ─── Hero ───────────────────────────────────────────────────────────── */
function Hero({ onCta }: { onCta: () => void }) {
  return (
    <section className="lp-hero">
      <div className="lp-container lp-hero__inner">
        <div className="lp-hero__text">
          <h1 className="lp-h1">
            Заговори на английском,<br />
            а не молчи
          </h1>
          <p className="lp-hero__sub">
            Голосовая практика с ИИ — каждый день по 15 минут. Знаешь слова и
            грамматику, но язык немеет в разговоре? Тренируйся вслух с
            ИИ-собеседником, который объясняет ошибки <b>по-русски</b>, а не
            просто исправляет.
          </p>
          <button type="button" className="lp-btn lp-btn--primary lp-btn--lg" onClick={onCta}>
            Попробовать бесплатно
          </button>
          <p className="lp-hero__note">
            Без подписки. Отмена в любой момент. Регистрация в один тап
            через Яндекс ID или email.
          </p>
          <div className="lp-hero__proof">
            <span className="lp-hero__proof-dot" aria-hidden />
            Уже <b>2 000+ учеников</b> тренируют речь с English Tutor
          </div>
        </div>
        <div className="lp-hero__visual">
          <ScreenshotFrame src="/screenshots/main.png" alt="Главный экран English Tutor — выбор режима" />
        </div>
      </div>
    </section>
  );
}

/* ─── Pain/Solution ──────────────────────────────────────────────────── */
function PainSolution() {
  return (
    <section className="lp-section lp-pain">
      <div className="lp-container">
        <h2 className="lp-h2">Знакомо?</h2>
        <p className="lp-pain__lead">
          Учил в школе, в универе, в Duolingo — а на собеседовании или в
          путешествии всё равно <i>«эээ… how to say…»</i>. Проблема не в
          знаниях. Проблема в практике речи — её просто негде взять.
          Репетитор за 1 500 ₽ за урок, занят 4 раза в неделю по расписанию,
          а говорить хочется здесь и сейчас.
        </p>
        <p className="lp-pain__solution">
          <b>English Tutor — это собеседник, который всегда онлайн.</b>{" "}
          Не оценивает кривую фразу, не закатывает глаза, не торопит.
          Говоришь голосом или текстом — ИИ отвечает голосом и разбирает
          твои ошибки понятно.
        </p>
      </div>
    </section>
  );
}

/* ─── Killer feature ─────────────────────────────────────────────────── */
function KillerFeature() {
  return (
    <section className="lp-section lp-killer">
      <div className="lp-container lp-killer__inner">
        <div className="lp-killer__visual">
          <ScreenshotFrame src="/screenshots/speaking-fix.png" alt="Разбор ошибки прямо в диалоге" />
        </div>
        <div className="lp-killer__text">
          <span className="lp-eyebrow">Главное отличие</span>
          <h2 className="lp-h2">Не просто исправляет — объясняет почему</h2>
          <p>
            В Duolingo тебе скажут «ошибка», в ChatGPT — перепишут фразу. В
            English Tutor — покажут что не так <b>и объяснят на русском</b>,
            почему правильно именно так. Через неделю ты сам начинаешь
            ловить эти конструкции в речи.
          </p>
          <ul className="lp-bullets">
            <li>Выделение ошибки прямо в ответе ИИ.</li>
            <li>Объяснение правила на русском — понятно с первого раза.</li>
            <li>Опционально: жёсткий режим «исправляй всё» или мягкий
              «только серьёзные».</li>
          </ul>
        </div>
      </div>
    </section>
  );
}

/* ─── Modes ──────────────────────────────────────────────────────────── */
function Modes() {
  return (
    <section className="lp-section lp-modes">
      <div className="lp-container">
        <h2 className="lp-h2 lp-h2--center">Четыре режима — общий прогресс</h2>
        <p className="lp-section__sub">
          Прогресс, словарь и стрик едины между режимами. Учишь слово в
          подкасте — оно появится в карточках на повтор. Делаешь ошибку в
          разговоре — попадает в практику грамматики.
        </p>
        <div className="lp-modes__grid">
          <ModeCard
            emoji="🎙"
            title="Разговор"
            desc="Голосом или текстом, любой партнёр — от друга и бариста до интервьюера и Python-разработчика. Уровень A1–C1."
          />
          <ModeCard
            emoji="🎧"
            title="Слушание"
            desc="Подкасты по темам: News, Tech, Business, Travel, Psychology. ИИ вплетает в подкаст слова из твоего словаря."
          />
          <ModeCard
            emoji="📘"
            title="Грамматика"
            desc="50 уроков от A1 до C1 с объяснениями на русском и упражнениями на закрепление. Без воды."
          />
          <ModeCard
            emoji="🔁"
            title="Слова"
            desc="Карточки с интервальным повтором: 1 → 3 → 7 → 14 → 30 дней. Лексика остаётся в голове, а не теряется."
          />
        </div>
      </div>
    </section>
  );
}

function ModeCard({ emoji, title, desc }: { emoji: string; title: string; desc: string }) {
  return (
    <div className="lp-mode">
      <div className="lp-mode__emoji" aria-hidden>{emoji}</div>
      <h3 className="lp-mode__title">{title}</h3>
      <p className="lp-mode__desc">{desc}</p>
    </div>
  );
}

/* ─── Screenshots gallery ────────────────────────────────────────────── */
function Screenshots() {
  return (
    <section className="lp-section lp-shots">
      <div className="lp-container">
        <h2 className="lp-h2 lp-h2--center">Как это выглядит</h2>
        <div className="lp-shots__grid">
          <ScreenshotFrame src="/screenshots/grammar.png" alt="Грамматика — объяснение на русском + примеры" caption="Грамматика" />
          <ScreenshotFrame src="/screenshots/listening.png" alt="Подкаст с твоей темой и словарём" caption="Слушание" />
          <ScreenshotFrame src="/screenshots/progress.png" alt="Прогресс, стрик, медали" caption="Прогресс" />
        </div>
      </div>
    </section>
  );
}

/* ─── Social proof ───────────────────────────────────────────────────── */
function SocialProof() {
  return (
    <section className="lp-section lp-social">
      <div className="lp-container">
        <h2 className="lp-h2 lp-h2--center">Что говорят 2 000+ учеников</h2>
        <div className="lp-social__grid">
          <Review name="Вячеслав К." level="">
            Классная история. Спасибо за <b>20 минут — это уже больше, чем в
            бесплатных приложениях аналогичного характера</b>. Купил бы год.
          </Review>
          <Review name="Анастасия И." level="B1">
            Протестировала — реально <b>интересно общаться</b>, спасибо,
            крутая штука 🔥
          </Review>
          <Review name="Alexander" level="">
            Кто ты, воин? Ты сделал <b>шикарный продукт</b>, молодец!
          </Review>
          <Review name="Михаил Д." level="B1">
            Уау, очень <b>классный бот</b>, буду заниматься тут.
          </Review>
        </div>
        <p className="lp-social__note">
          Реальные сообщения из канала <a href="https://t.me/kmo_ai" target="_blank" rel="noreferrer">@kmo_ai</a>.
          Имена сокращены для приватности.
        </p>
      </div>
    </section>
  );
}

function Review({ name, level, children }: { name: string; level: string; children: React.ReactNode }) {
  return (
    <div className="lp-review">
      <p className="lp-review__text">{children}</p>
      <div className="lp-review__author">
        <span className="lp-review__name">— {name}</span>
        {level && <span className="lp-review__level">уровень {level}</span>}
      </div>
    </div>
  );
}

/* ─── Pricing ────────────────────────────────────────────────────────── */
function Pricing({ onCta }: { onCta: () => void }) {
  return (
    <section className="lp-section lp-pricing" id="pricing">
      <div className="lp-container">
        <h2 className="lp-h2 lp-h2--center">Стоимость</h2>
        <p className="lp-section__sub">
          Дешевле одного занятия с репетитором (1 000–1 500 ₽). Оплата
          картой через ЮKassa. Отмена в любой момент, никаких скрытых
          списаний.
        </p>
        <div className="lp-plans">
          <PlanCard
            title="Пробный"
            price="99 ₽"
            period="3 дня"
            hint="Попробовать без риска"
            onCta={onCta}
            ctaLabel="Начать бесплатно"
            highlighted
          />
          <PlanCard
            title="Месячный"
            price="499 ₽"
            period="30 дней"
            hint="Самый популярный"
            onCta={onCta}
            ctaLabel="Начать бесплатно"
          />
          <PlanCard
            title="Годовой"
            price="2 999 ₽"
            oldPrice="5 988 ₽"
            period="365 дней"
            hint="Экономия 2 989 ₽ — это 50%"
            onCta={onCta}
            ctaLabel="Начать бесплатно"
          />
        </div>
        <p className="lp-offer-link">
          Оплачивая, вы принимаете условия{" "}
          <a href="/oferta.html" target="_blank" rel="noreferrer">публичной оферты</a>.
        </p>
      </div>
    </section>
  );
}

function PlanCard({
  title, price, oldPrice, period, hint, onCta, ctaLabel, highlighted = false,
}: {
  title: string; price: string; oldPrice?: string; period: string; hint: string;
  onCta: () => void; ctaLabel: string; highlighted?: boolean;
}) {
  return (
    <div className={`lp-plan ${highlighted ? "lp-plan--hl" : ""}`}>
      <div className="lp-plan__title">{title}</div>
      {oldPrice && <div className="lp-plan__old-price">{oldPrice}</div>}
      <div className="lp-plan__price">{price}</div>
      <div className="lp-plan__period">{period}</div>
      <div className="lp-plan__hint">{hint}</div>
      <button
        type="button"
        className={`lp-btn ${highlighted ? "lp-btn--primary" : "lp-btn--ghost"} lp-btn--md`}
        onClick={onCta}
      >
        {ctaLabel}
      </button>
    </div>
  );
}

/* ─── Charity ────────────────────────────────────────────────────────── */
function Charity() {
  return (
    <section className="lp-section lp-charity">
      <div className="lp-container">
        <div className="lp-charity__card">
          <div className="lp-charity__emoji" aria-hidden>💛</div>
          <h3 className="lp-charity__title">Нет возможности оплатить?</h3>
          <p>
            Это не повод бросать английский. Напиши <b>«прошу доступ»</b> в
            комментариях под любым постом в{" "}
            <a href="https://t.me/kmo_ai" target="_blank" rel="noreferrer">@kmo_ai</a>{" "}
            — выдам подписку бесплатно. Без бюрократии и сложных условий.
          </p>
        </div>
      </div>
    </section>
  );
}

/* ─── FAQ ────────────────────────────────────────────────────────────── */
function Faq() {
  const items: { q: string; a: React.ReactNode }[] = [
    {
      q: "Подойдёт ли, если мой уровень совсем низкий (A1)?",
      a: <>Да. В настройках разговора выбираешь уровень A1, ИИ
        подстраивает темп и сложность фраз. Грамматика тоже начинается
        с A1 — местоимения, артикли, простое настоящее.</>,
    },
    {
      q: "Чем отличается от Duolingo, Skyeng или просто ChatGPT?",
      a: <>От Duolingo — здесь живая голосовая практика и объяснение
        ошибок по-русски, а не просто «правильно/неправильно». От
        Skyeng — нет привязки к расписанию и репетитору; занимаешься
        когда удобно, цена в 30 раз ниже. От ChatGPT — есть структура
        от A1 до C1, прогресс, словарь с интервальным повтором.</>,
    },
    {
      q: "Сколько времени занимает в день?",
      a: <>15–20 минут хватает, чтобы держать привычку. На бесплатном
        тарифе — 20 минут разговора в день. С подпиской — без лимитов.</>,
    },
    {
      q: "Как происходит оплата? Это безопасно?",
      a: <>Оплата картой через <b>ЮKassa</b> (тот же сервис, что у
        Avito, Яндекс.Маркет, OZON). Электронный чек по 54-ФЗ
        автоматически придёт на твой email. Отмена подписки — в один
        тап в Аккаунте, никаких списаний без твоего ведома.</>,
    },
    {
      q: "Я могу заниматься в Telegram?",
      a: <>Да. У нас есть Mini App внутри Telegram-бота{" "}
        @kmo_ai_english_bot — все функции те же, прогресс
        синхронизируется с веб-версией.</>,
    },
    {
      q: "А если не понравится?",
      a: <>Пробный тариф 3 дня за 99 ₽ — фактически без риска. Не зашло —
        просто не продлеваешь, никто не списывает дальше.</>,
    },
  ];
  return (
    <section className="lp-section lp-faq">
      <div className="lp-container">
        <h2 className="lp-h2 lp-h2--center">Частые вопросы</h2>
        <div className="lp-faq__list">
          {items.map((it, i) => (
            <details key={i} className="lp-faq__item">
              <summary>{it.q}</summary>
              <div className="lp-faq__answer">{it.a}</div>
            </details>
          ))}
        </div>
      </div>
    </section>
  );
}

/* ─── Final CTA ──────────────────────────────────────────────────────── */
function FinalCta({ onCta }: { onCta: () => void }) {
  return (
    <section className="lp-section lp-final">
      <div className="lp-container lp-final__inner">
        <h2 className="lp-h2">Хватит молчать.<br />Скажи первую фразу сегодня.</h2>
        <button type="button" className="lp-btn lp-btn--primary lp-btn--lg" onClick={onCta}>
          Попробовать бесплатно
        </button>
        <p className="lp-final__note">15 минут в день. Без подписки. Отмена в любой момент.</p>
      </div>
    </section>
  );
}

/* ─── Footer ─────────────────────────────────────────────────────────── */
function Footer() {
  return (
    <footer className="lp-footer">
      <div className="lp-container lp-footer__inner">
        <div className="lp-footer__brand">
          <span className="lp-brand__dot" aria-hidden />
          <span>English Tutor</span>
        </div>
        <div className="lp-footer__links">
          <a href="/oferta.html" target="_blank" rel="noreferrer">Публичная оферта</a>
          <a href="https://t.me/kmo_ai" target="_blank" rel="noreferrer">Канал @kmo_ai</a>
          <a href="https://t.me/kmo_ai_english_bot" target="_blank" rel="noreferrer">Telegram-бот</a>
        </div>
        <div className="lp-footer__copyright">
          © {new Date().getFullYear()} English Tutor
        </div>
      </div>
    </footer>
  );
}

/* ─── Скриншот в стилизованной рамке ─────────────────────────────────── */
function ScreenshotFrame({ src, alt, caption }: { src: string; alt: string; caption?: string }) {
  return (
    <figure className="lp-shot">
      <div className="lp-shot__frame">
        <img src={src} alt={alt} loading="lazy" />
      </div>
      {caption && <figcaption className="lp-shot__caption">{caption}</figcaption>}
    </figure>
  );
}
