/**
 * SchoolsLanding.tsx — публичный лендинг B2B: подключение школы английского.
 *
 * Живёт по пути /schools (SPA-fallback nginx отдаёт index.html на любой путь,
 * роутинг по window.location.pathname в main.tsx).
 *
 * Флоу самоподключения:
 *   калькулятор → «Подключить школу» → вход (Яндекс ID / email) →
 *   форма заказа → ЮKassa → возврат на /schools?payment_id=N&org=1 →
 *   опрос статуса → выдача ссылок-приглашений.
 *
 * Цены считает backend (org_pricing.py). Формула здесь продублирована только
 * для мгновенного отклика ползунка; сумма платежа всегда серверная.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  fetchOrgPricing,
  fetchOrgOrderStatus,
  getToken,
  orgCheckout,
  type OrgOrderStatus,
  type OrgPricing,
} from "./auth";
import { ymHit, ymReachGoal } from "./metrika";
import "./Landing.css";

const OFFER_URL = "/oferta-school.html";
const SCHOOLS_INTENT_KEY = "et_schools_intent";

/** Пришли ли мы сюда сразу после входа «чтобы подключить школу». */
function consumeConnectIntent(): boolean {
  if (typeof window === "undefined") return false;
  let intent = false;
  try {
    if (new URLSearchParams(window.location.search).get("connect") === "1") {
      intent = true;
      // Чистим URL, чтобы флаг не срабатывал при перезагрузке страницы.
      const url = new URL(window.location.href);
      url.searchParams.delete("connect");
      window.history.replaceState(null, "", url.pathname + (url.search || "") + url.hash);
    }
    if (localStorage.getItem(SCHOOLS_INTENT_KEY) === "1") {
      intent = true;
      localStorage.removeItem(SCHOOLS_INTENT_KEY);
    }
  } catch { /* private mode */ }
  return intent;
}

/** Дефолты на случай, если backend недоступен — калькулятор всё равно считает. */
const FALLBACK: OrgPricing = {
  base_price_per_seat_month: 590,
  min_seats: 10,
  max_seats: 5000,
  allowed_months: [3, 6, 12],
  volume_tiers: [{ seats: 100, pct: 20 }, { seats: 30, pct: 10 }],
  period_tiers: [{ months: 12, pct: 15 }, { months: 6, pct: 5 }],
};

function calcQuote(p: OrgPricing, seats: number, months: number) {
  const vol = p.volume_tiers.find((t) => seats >= t.seats)?.pct ?? 0;
  const per = p.period_tiers.find((t) => months >= t.months)?.pct ?? 0;
  const discount = vol + per;
  const baseTotal = seats * months * p.base_price_per_seat_month;
  const total = Math.round((baseTotal * (100 - discount)) / 100);
  return {
    vol, per, discount, baseTotal, total,
    perSeat: Math.round(total / (seats * months)),
    saving: baseTotal - total,
  };
}

const fmt = (n: number) => n.toLocaleString("ru-RU");

export function SchoolsLanding({ onLogin }: { onLogin: () => void }) {
  const [pricing, setPricing] = useState<OrgPricing>(FALLBACK);
  const [seats, setSeats] = useState<number>(30);
  const [months, setMonths] = useState<number>(6);
  // Вернулись после входа с намерением подключить школу — форма сразу открыта.
  const [formOpen, setFormOpen] = useState<boolean>(
    () => consumeConnectIntent() && !!getToken(),
  );
  const [schoolName, setSchoolName] = useState("");
  const [person, setPerson] = useState("");
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Возврат с оплаты: /schools?payment_id=N&org=1
  const [returnPaymentId] = useState<number | null>(() => {
    try {
      const p = new URLSearchParams(window.location.search);
      const id = parseInt(p.get("payment_id") || "0", 10);
      return id > 0 ? id : null;
    } catch { return null; }
  });
  const [order, setOrder] = useState<OrgOrderStatus | null>(null);
  const [orderWait, setOrderWait] = useState<boolean>(!!returnPaymentId);

  const authed = !!getToken();
  const q = useMemo(() => calcQuote(pricing, seats, months), [pricing, seats, months]);

  useEffect(() => {
    ymHit(window.location.origin + "/schools", "Лендинг для школ");
    ymReachGoal("schools_landing_view");
    void (async () => {
      const p = await fetchOrgPricing();
      if (p) setPricing(p);
    })();
  }, []);

  // Опрос статуса заказа после возврата с оплаты.
  useEffect(() => {
    if (!returnPaymentId) return;
    let alive = true, attempts = 0;
    const tick = async () => {
      if (!alive) return;
      attempts++;
      const st = await fetchOrgOrderStatus(returnPaymentId);
      if (!alive) return;
      if (st) {
        setOrder(st);
        if (st.order_status === "done") {
          setOrderWait(false);
          ymReachGoal("school_connected", { seats: st.seats, months: st.months });
          return;
        }
      }
      if (attempts > 60) { setOrderWait(false); return; }
      setTimeout(tick, 2000);
    };
    void tick();
    return () => { alive = false; };
  }, [returnPaymentId]);

  // Форма открылась автоматически после входа — подкрутим к ней экран.
  useEffect(() => {
    if (!formOpen) return;
    const t = setTimeout(() => {
      document.getElementById("school-order")?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 120);
    return () => clearTimeout(t);
  }, [formOpen]);

  const startCheckout = useCallback(async () => {
    if (busy) return;
    const name = schoolName.trim();
    if (!name) { setError("Укажите название школы."); return; }
    if (!/^.+@.+\..+$/.test(email.trim())) {
      setError("Укажите корректный email — на него придёт чек.");
      return;
    }
    setBusy(true); setError("");
    ymReachGoal("school_checkout_started", { seats, months, amount: q.total });
    try {
      const r = await orgCheckout({
        school_name: name, seats, months,
        contact_person: person.trim() || undefined,
        email: email.trim(),
      });
      if (r.ok && r.confirmation_url) { window.location.href = r.confirmation_url; return; }
      if (r.error === "email_required") setError("Нужен email для электронного чека.");
      else if (r.error === "yookassa_not_configured") setError("Оплата временно недоступна. Напишите @NuTak0e.");
      else if (r.error === "bad_seats") setError(`Минимальный пакет — ${pricing.min_seats} мест.`);
      else setError("Не получилось создать счёт. Попробуйте ещё раз.");
    } finally { setBusy(false); }
  }, [busy, schoolName, email, person, seats, months, q.total, pricing.min_seats]);

  const onConnect = () => {
    ymReachGoal("school_cta_click");
    if (!authed) {
      // Запоминаем намерение: вход через Яндекс перезагрузит страницу и
      // вернёт на корень сайта — оттуда нас отправят обратно на /schools.
      try { localStorage.setItem(SCHOOLS_INTENT_KEY, "1"); } catch { /* private mode */ }
      onLogin();
      return;
    }
    setFormOpen(true);
    setTimeout(() => {
      document.getElementById("school-order")?.scrollIntoView({ behavior: "smooth" });
    }, 50);
  };

  // ─── Экран результата после оплаты ────────────────────────────────
  if (returnPaymentId) {
    return (
      <div className="lp">
        <SchoolsHeader onLogin={onLogin} />
        <section className="lp-section">
          <div className="lp-container" style={{ maxWidth: 720 }}>
            {orderWait && (
              <>
                <h1 className="lp-h2">Подтверждаем оплату…</h1>
                <p className="lp-section__sub">
                  Это занимает до минуты. Не закрывайте страницу — как только
                  ЮKassa подтвердит платёж, школа будет создана автоматически.
                </p>
              </>
            )}
            {!orderWait && order?.order_status === "done" && (
              <>
                <h1 className="lp-h2">Школа «{order.school_name}» подключена</h1>
                <p className="lp-section__sub">
                  Пакет: {order.seats} мест на {order.months} мес.
                  {order.valid_until && ` Доступ до ${new Date(order.valid_until).toLocaleDateString("ru-RU")}.`}
                </p>
                <div className="sch-links">
                  <div className="sch-link-row">
                    <span className="sch-link-label">Ссылка для учеников — Telegram</span>
                    <code className="sch-link-val">{order.invite_link}</code>
                  </div>
                  <div className="sch-link-row">
                    <span className="sch-link-label">Ссылка для учеников — сайт</span>
                    <code className="sch-link-val">{order.invite_link_web}</code>
                  </div>
                  <p className="lp-hero__note">
                    Разошлите любую из ссылок ученикам — они подключатся сами и займут места.
                    Вы уже администратор школы: откройте приложение → Профиль → «Кабинет школы»,
                    там статистика учеников, отчёты и эти же ссылки. Чтобы дать доступ
                    преподавателю, попросите его перейти по ссылке и напишите нам —
                    переключим его роль на «учитель» (место при этом освобождается).
                  </p>
                </div>
                <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginTop: 20 }}>
                  <a className="lp-btn lp-btn--primary lp-btn--md" href="/">Открыть кабинет школы</a>
                  <a className="lp-btn lp-btn--ghost lp-btn--md" href="https://t.me/NuTak0e" target="_blank" rel="noreferrer">Написать нам</a>
                </div>
              </>
            )}
            {!orderWait && order?.order_status !== "done" && (
              <>
                <h1 className="lp-h2">Платёж ещё не подтверждён</h1>
                <p className="lp-section__sub">
                  Если деньги списались — школа появится в течение нескольких минут,
                  просто обновите страницу. Если нет — напишите нам, разберёмся: @NuTak0e
                </p>
                <a className="lp-btn lp-btn--ghost lp-btn--md" href="/schools">Вернуться к тарифам</a>
              </>
            )}
          </div>
        </section>
      </div>
    );
  }

  // ─── Основной лендинг ─────────────────────────────────────────────
  return (
    <div className="lp">
      <SchoolsHeader onLogin={onLogin} />

      <section className="lp-hero">
        <div className="lp-container lp-hero__inner">
          <div className="lp-hero__text">
            <h1 className="lp-h1">
              AI-репетитор для вашей школы английского
            </h1>
            <p className="lp-hero__sub">
              Ученики говорят по-английски дома каждый день — с искусственным
              интеллектом, без стеснения и без вашего участия. Вы видите, кто
              занимался, сколько и какие ошибки повторяет.
            </p>
            <button type="button" className="lp-btn lp-btn--primary lp-btn--lg" onClick={onConnect}>
              Подключить школу
            </button>
            <p className="lp-hero__note">
              Подключение за 5 минут · оплата картой · чек по 54-ФЗ · от {pricing.min_seats} мест
            </p>
          </div>
        </div>
      </section>

      <section className="lp-section">
        <div className="lp-container">
          <h2 className="lp-h2">Проблема, которую это решает</h2>
          <div className="sch-grid3">
            <SchCard
              title="Домашку по говорению никто не делает"
              body="Говорить дома не с кем, а на уроке в группе на каждого ученика приходится пара минут речи. Язык не разговаривается."
            />
            <SchCard
              title="Прогресс между уроками не виден"
              body="«Занимался?» — «Да». Проверить нечем. Преподаватель узнаёт о пробелах только на занятии."
            />
            <SchCard
              title="Ученики уходят, не увидев результата"
              body="Без ощущения прогресса мотивация падает, и через пару месяцев человек бросает — вместе с абонементом."
            />
          </div>
        </div>
      </section>

      <section className="lp-section">
        <div className="lp-container">
          <h2 className="lp-h2">Как это работает</h2>
          <div className="sch-steps">
            {[
              { n: "1", t: "Вы оплачиваете пакет мест", d: "Выбираете количество учеников и срок прямо здесь, платите картой. Чек приходит на почту." },
              { n: "2", t: "Получаете ссылку-приглашение", d: "Сразу после оплаты. Отправляете её ученикам в чат группы — двумя способами: в Telegram или на сайт." },
              { n: "3", t: "Ученики занимаются", d: "Каждый переходит по ссылке и получает полный доступ без лимитов: разговор с ИИ, подкасты, грамматика, словарь." },
              { n: "4", t: "Вы видите результат", d: "В кабинете школы: минуты практики, очки, стрики и частые ошибки по каждому ученику. Отчёт выгружается в таблицу." },
            ].map((s) => (
              <div key={s.n} className="sch-step">
                <span className="sch-step__num">{s.n}</span>
                <div>
                  <div className="sch-step__title">{s.t}</div>
                  <div className="sch-step__body">{s.d}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ─── Калькулятор ─────────────────────────────────────────── */}
      <section className="lp-section" id="calc">
        <div className="lp-container">
          <h2 className="lp-h2">Сколько это стоит</h2>
          <p className="lp-section__sub">
            Цена за место — ниже розничной подписки. Чем больше учеников и срок,
            тем дешевле каждое место.
          </p>

          <div className="sch-calc">
            <div className="sch-calc__controls">
              <label className="sch-field">
                <span className="sch-field__label">Учеников: <b>{seats}</b></span>
                <input
                  type="range"
                  min={pricing.min_seats}
                  max={300}
                  step={5}
                  value={seats}
                  onChange={(e) => setSeats(parseInt(e.target.value, 10))}
                  className="sch-range"
                />
                <span className="sch-field__hint">
                  от {pricing.min_seats} до 300 — если нужно больше, напишите нам
                </span>
              </label>

              <div className="sch-field">
                <span className="sch-field__label">Срок доступа</span>
                <div className="sch-months">
                  {pricing.allowed_months.map((m) => (
                    <button
                      key={m}
                      type="button"
                      className={`sch-month ${m === months ? "is-active" : ""}`}
                      onClick={() => setMonths(m)}
                    >
                      {m} мес
                      {(pricing.period_tiers.find((t) => m >= t.months)?.pct ?? 0) > 0 && (
                        <span className="sch-month__badge">
                          −{pricing.period_tiers.find((t) => m >= t.months)?.pct}%
                        </span>
                      )}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            <div className="sch-calc__total">
              <div className="sch-total__row">
                <span>Базовая цена</span>
                <span>{fmt(pricing.base_price_per_seat_month)} ₽ / место / мес</span>
              </div>
              {q.vol > 0 && (
                <div className="sch-total__row sch-total__row--disc">
                  <span>Скидка за объём ({seats} мест)</span><span>−{q.vol}%</span>
                </div>
              )}
              {q.per > 0 && (
                <div className="sch-total__row sch-total__row--disc">
                  <span>Скидка за срок ({months} мес)</span><span>−{q.per}%</span>
                </div>
              )}
              <div className="sch-total__sep" />
              <div className="sch-total__price">
                {fmt(q.total)} ₽
                {q.saving > 0 && <s className="sch-total__old">{fmt(q.baseTotal)} ₽</s>}
              </div>
              <div className="sch-total__note">
                {fmt(q.perSeat)} ₽ за место в месяц · {seats} мест × {months} мес
                {q.saving > 0 && ` · экономия ${fmt(q.saving)} ₽`}
              </div>
              <button
                type="button"
                className="lp-btn lp-btn--primary lp-btn--lg"
                style={{ width: "100%", marginTop: 16 }}
                onClick={onConnect}
              >
                {authed ? "Подключить школу" : "Войти и подключить"}
              </button>
              {!authed && (
                <p className="sch-total__note" style={{ marginTop: 10 }}>
                  Вход через Яндекс ID или email — нужен, чтобы кабинет школы был
                  привязан к вам.
                </p>
              )}
            </div>
          </div>

          {/* Форма заказа */}
          {formOpen && authed && (
            <div className="sch-order" id="school-order">
              <h3 className="sch-order__title">Данные школы</h3>
              <div className="sch-order__grid">
                <label className="sch-field">
                  <span className="sch-field__label">Название школы *</span>
                  <input
                    className="sch-input" value={schoolName} maxLength={128}
                    onChange={(e) => setSchoolName(e.target.value)}
                    placeholder="Skyrocket English"
                  />
                </label>
                <label className="sch-field">
                  <span className="sch-field__label">Контактное лицо</span>
                  <input
                    className="sch-input" value={person} maxLength={128}
                    onChange={(e) => setPerson(e.target.value)}
                    placeholder="Иван Петров"
                  />
                </label>
                <label className="sch-field">
                  <span className="sch-field__label">Email для чека *</span>
                  <input
                    className="sch-input" type="email" value={email} maxLength={255}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="director@school.ru"
                  />
                </label>
              </div>
              <div className="sch-order__summary">
                К оплате: <b>{fmt(q.total)} ₽</b> — {seats} мест на {months} мес.
              </div>
              {error && <div className="sch-error">{error}</div>}
              <button
                type="button"
                className="lp-btn lp-btn--primary lp-btn--lg"
                onClick={() => void startCheckout()}
                disabled={busy}
              >
                {busy ? "Готовим счёт…" : "Перейти к оплате"}
              </button>
              <p className="lp-hero__note">
                Нажимая «Перейти к оплате», вы принимаете условия{" "}
                <a href={OFFER_URL} target="_blank" rel="noreferrer">оферты для образовательных организаций</a>.
              </p>
            </div>
          )}
        </div>
      </section>

      <section className="lp-section">
        <div className="lp-container">
          <h2 className="lp-h2">Что получает школа</h2>
          <div className="sch-grid3">
            <SchCard title="Кабинет преподавателя" body="Список учеников с минутами практики, очками, стриками и датой последнего занятия. Видно, кто перестал заниматься." />
            <SchCard title="Разбор ошибок учеников" body="По каждому ученику — частые ошибки за 30 дней в формате «было → стало». Готовый материал для урока." />
            <SchCard title="Отчёт в таблице" body="Выгрузка по всем ученикам за месяц одной кнопкой — открывается в Excel, подходит для отчётности." />
            <SchCard title="Лидерборд школы" body="Ученики соревнуются между собой внутри вашей школы, а не со всей базой сервиса. Это заметно поднимает вовлечённость." />
            <SchCard title="Управление составом" body="Ученик ушёл — отключаете его, место освобождается и достаётся новому. Пакет не пропадает." />
            <SchCard title="Без рекламы вашим ученикам" body="Ученикам школы сервис не предлагает купить личную подписку и не шлёт продающих рассылок." />
          </div>
        </div>
      </section>

      <section className="lp-section">
        <div className="lp-container">
          <h2 className="lp-h2">Вопросы</h2>
          <div className="sch-faq">
            <SchFaq q="Что нужно от школы, чтобы начать?" a="Только оплатить пакет и разослать ссылку ученикам. Списки учеников заранее собирать не нужно — они подключаются сами, вы видите их в кабинете по мере подключения." />
            <SchFaq q="Ученику нужен Telegram?" a="Нет. Есть вторая ссылка — на сайт. Ученик заходит через Яндекс ID или email с паролем и занимается в браузере." />
            <SchFaq q="Что если учеников станет больше?" a="Докупаете места — напишите нам, расширим пакет. Мы сами предупредим, если мест перестанет хватать." />
            <SchFaq q="Как получить доступ преподавателю?" a="Преподаватель переходит по той же ссылке, после чего мы переключаем его роль на «учитель»: он получает кабинет со статистикой и не занимает ученическое место." />
            <SchFaq q="Нужны закрывающие документы?" a="Да, работаем по оферте, чек по 54-ФЗ приходит на указанный email. Если нужен договор и счёт на юрлицо — напишите @NuTak0e, оформим." />
            <SchFaq q="Что будет, когда срок закончится?" a="Доступ учеников вернётся к бесплатным лимитам, данные и прогресс сохранятся. Мы напомним заранее, чтобы вы успели продлить." />
          </div>
        </div>
      </section>

      <section className="lp-section">
        <div className="lp-container" style={{ textAlign: "center" }}>
          <h2 className="lp-h2 lp-h2--center">Попробуйте на одной группе</h2>
          <p className="lp-section__sub">
            Возьмите минимальный пакет на 3 месяца и посмотрите на цифрах, как
            изменится домашняя практика. Не подойдёт — не продлевайте.
          </p>
          <button type="button" className="lp-btn lp-btn--primary lp-btn--lg" onClick={onConnect}>
            Подключить школу
          </button>
        </div>
      </section>

      <footer className="lp-footer">
        <div className="lp-container">
          <a href={OFFER_URL} target="_blank" rel="noreferrer">Оферта для образовательных организаций</a>
          {" · "}
          <a href="/oferta.html" target="_blank" rel="noreferrer">Оферта для физических лиц</a>
          {" · "}
          <a href="https://t.me/NuTak0e" target="_blank" rel="noreferrer">@NuTak0e</a>
          {" · "}
          <a href="/">Личная версия сервиса</a>
        </div>
      </footer>
    </div>
  );
}

function SchoolsHeader({ onLogin }: { onLogin: () => void }) {
  return (
    <header className="lp-header">
      <div className="lp-container lp-header__inner">
        <a className="lp-brand" href="/" style={{ textDecoration: "none" }}>
          <span className="lp-brand__dot" aria-hidden />
          <span className="lp-brand__name">English Tutor <span className="sch-badge">для школ</span></span>
        </a>
        <nav className="lp-nav">
          <button type="button" className="lp-nav__link" onClick={onLogin}>Войти</button>
          <a className="lp-btn lp-btn--primary lp-btn--sm" href="#calc">Рассчитать стоимость</a>
        </nav>
      </div>
    </header>
  );
}

function SchCard({ title, body }: { title: string; body: string }) {
  return (
    <div className="sch-card">
      <div className="sch-card__title">{title}</div>
      <div className="sch-card__body">{body}</div>
    </div>
  );
}

function SchFaq({ q, a }: { q: string; a: string }) {
  return (
    <details className="sch-faq__item">
      <summary className="sch-faq__q">{q}</summary>
      <div className="sch-faq__a">{a}</div>
    </details>
  );
}
