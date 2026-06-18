/**
 * SubscribeScreen.tsx — страница подписки с тарифами и оплатой через ЮKassa.
 *
 * Два режима:
 *  - `mode='select'` (дефолт) — показывает 3 тарифа из /api/payments/plans,
 *    клик создаёт платёж и редиректит на confirmation_url ЮKassa.
 *  - `mode='return'` — юзер вернулся с ЮKassa (URL содержит ?payment_id=N).
 *    Опрашиваем /api/payments/status каждые 2с до status='succeeded' либо
 *    таймаута. На успехе зовём `onPaid()`.
 */

import { useCallback, useEffect, useState } from "react";
import {
  createPayment,
  fetchMe,
  fetchPaymentStatus,
  listPlans,
  type MeInfo,
  type Plan,
} from "./auth";
import { ymHit, ymReachGoal } from "./metrika";

// Публичная оферта — статичный файл, раздаётся nginx'ом miniapp из public/.
// Лежит в miniapp/public/oferta.html, Vite копирует его в dist при сборке.
const OFFER_URL = "/oferta.html";

interface Props {
  onClose: () => void;
  onPaid?: () => void;
  initialReturnPaymentId?: number;
}

export function SubscribeScreen({ onClose, onPaid, initialReturnPaymentId }: Props) {
  const [plans, setPlans] = useState<Plan[]>([]);
  const [me, setMe] = useState<MeInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null); // ключ тарифа в процессе
  const [error, setError] = useState<string>("");
  const [askEmailFor, setAskEmailFor] = useState<Plan["key"] | null>(null);
  const [emailInput, setEmailInput] = useState<string>("");
  const [returnPaymentId, setReturnPaymentId] = useState<number | null>(
    initialReturnPaymentId ?? null,
  );
  const [returnStatus, setReturnStatus] = useState<"pending" | "succeeded" | "canceled" | "expired" | null>(
    initialReturnPaymentId ? "pending" : null,
  );

  useEffect(() => {
    let alive = true;
    // Виртуальный pageview: фронт SPA, поэтому при «навигации» в Subscribe
    // отправляем hit отдельно. Цель subscribe_opened — точка входа в воронку.
    ymHit(window.location.origin + "/subscribe", "Подписка — тарифы");
    if (!initialReturnPaymentId) ymReachGoal("subscribe_opened");
    void (async () => {
      const [p, m] = await Promise.all([listPlans(), fetchMe()]);
      if (!alive) return;
      setPlans(p); setMe(m); setLoading(false);
      if (m?.email) setEmailInput(m.email);
    })();
    return () => { alive = false; };
  }, [initialReturnPaymentId]);

  // Полл статуса при возврате с ЮKassa.
  useEffect(() => {
    if (!returnPaymentId || returnStatus !== "pending") return;
    let cancelled = false;
    let attempts = 0;
    const tick = async () => {
      if (cancelled) return;
      attempts++;
      const s = await fetchPaymentStatus(returnPaymentId);
      if (cancelled) return;
      if (s?.status === "succeeded") {
        setReturnStatus("succeeded");
        ymHit(window.location.origin + "/subscribe/thanks", "Спасибо за оплату");
        ymReachGoal("subscription_paid", {
          plan: s.plan,
          amount_rub: s.amount_rub,
          days: s.days_granted,
        });
        await fetchMe();          // обновим subscription_until в кэше где-то
        onPaid?.();
        return;
      }
      if (s?.status === "canceled" || s?.status === "refunded") {
        setReturnStatus("canceled");
        return;
      }
      if (attempts > 60) {        // ~2 минуты на полл (webhook может прийти позже)
        setReturnStatus("expired");
        return;
      }
      setTimeout(tick, 2000);
    };
    void tick();
    return () => { cancelled = true; };
  }, [returnPaymentId, returnStatus, onPaid]);

  const launchPayment = useCallback(async (planKey: Plan["key"], emailOverride?: string) => {
    if (busy) return;
    setBusy(planKey); setError("");
    const planInfo = plans.find((p) => p.key === planKey);
    ymReachGoal("subscribe_plan_clicked", {
      plan: planKey,
      amount_rub: planInfo?.amount_rub,
    });
    try {
      const r = await createPayment(planKey, emailOverride);
      if (r.ok && r.confirmation_url) {
        window.location.href = r.confirmation_url;
        return;
      }
      if (r.error === "email_required") {
        setAskEmailFor(planKey);
        return;
      }
      if (r.error === "yookassa_not_configured") {
        setError("Оплата временно недоступна. Сообщи в @kmo_ai, разберёмся.");
      } else {
        setError("Не получилось создать платёж. Попробуй ещё раз.");
      }
    } finally { setBusy(null); }
  }, [busy]);

  const submitEmail = (e: React.FormEvent) => {
    e.preventDefault();
    if (!askEmailFor) return;
    const v = emailInput.trim();
    if (!/^.+@.+\..+$/.test(v)) {
      setError("Введи корректный email.");
      return;
    }
    setAskEmailFor(null);
    void launchPayment(askEmailFor, v);
  };

  // ── Render: режим возврата ────────────────────────────────────────────
  if (returnPaymentId) {
    return (
      <div className="sheet-backdrop" onClick={onClose}>
        <div className="sheet" onClick={(e) => e.stopPropagation()}>
          <header className="sheet__header">
            <h2 className="sheet__title">Оплата</h2>
            <button className="sheet__close" onClick={onClose} aria-label="Закрыть">✕</button>
          </header>
          <div className="sheet__content">
            {returnStatus === "pending" && (
              <>
                <p className="acc-lead">⏳ Ждём подтверждения от ЮKassa…</p>
                <p className="acc-hint">
                  Это может занять до минуты. Можешь не закрывать страницу —
                  как только платёж пройдёт, подписка активируется
                  автоматически.
                </p>
              </>
            )}
            {returnStatus === "succeeded" && (
              <>
                <p className="acc-lead">✅ Подписка активирована!</p>
                <p className="acc-hint">Открывай любой режим — лимиты сняты.</p>
              </>
            )}
            {returnStatus === "canceled" && (
              <>
                <p className="acc-lead">❌ Платёж отменён.</p>
                <p className="acc-hint">Можешь попробовать ещё раз.</p>
                <button
                  type="button"
                  className="btn btn--primary"
                  onClick={() => {
                    setReturnPaymentId(null);
                    setReturnStatus(null);
                  }}
                >
                  К тарифам
                </button>
              </>
            )}
            {returnStatus === "expired" && (
              <>
                <p className="acc-lead">⏰ Подтверждение задерживается.</p>
                <p className="acc-hint">
                  Если ты уже оплатил — подписка активируется в течение пары минут.
                  Открой эту страницу заново или напиши @kmo_ai, мы поможем.
                </p>
              </>
            )}
          </div>
          <footer className="sheet__footer">
            <button type="button" className="btn btn--primary" onClick={onClose}>
              Готово
            </button>
          </footer>
        </div>
      </div>
    );
  }

  // ── Render: режим выбора тарифа ───────────────────────────────────────
  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <div className="sheet" onClick={(e) => e.stopPropagation()}>
        <header className="sheet__header">
          <h2 className="sheet__title">Подписка</h2>
          <button className="sheet__close" onClick={onClose} aria-label="Закрыть">✕</button>
        </header>

        <div className="sheet__content">
          {loading ? (
            <p className="acc-hint">Загрузка…</p>
          ) : askEmailFor ? (
            <form className="acc-pwd-form" onSubmit={submitEmail}>
              <p className="acc-lead">
                Нужен email для электронного чека (требование 54-ФЗ).
              </p>
              <input
                className="login-input"
                type="email"
                placeholder="Email"
                value={emailInput}
                onChange={(e) => setEmailInput(e.target.value)}
                required
                autoFocus
                inputMode="email"
                maxLength={255}
              />
              <button type="submit" className="btn btn--primary" disabled={!!busy}>
                {busy ? "…" : "Продолжить к оплате"}
              </button>
              <button
                type="button"
                className="btn btn--ghost"
                onClick={() => setAskEmailFor(null)}
              >
                Назад
              </button>
              <p className="sub-offer">
                Продолжая, вы принимаете условия{" "}
                <a href={OFFER_URL} target="_blank" rel="noreferrer">
                  публичной оферты
                </a>
                .
              </p>
            </form>
          ) : (
            <>
              {me?.subscription_until && (
                <p className="acc-lead">
                  Подписка активна до <b>{_fmtDate(me.subscription_until)}</b>.
                  Можно продлить на любой срок — дни прибавляются.
                </p>
              )}

              <div className="sub-plans">
                {plans.map((p) => (
                  <button
                    key={p.key}
                    type="button"
                    className="sub-plan"
                    onClick={() => void launchPayment(p.key)}
                    disabled={!!busy}
                  >
                    <div className="sub-plan__title">{p.title}</div>
                    <div className="sub-plan__price">{p.amount_rub} ₽</div>
                    <div className="sub-plan__days">{p.days} {_daysWord(p.days)}</div>
                  </button>
                ))}
              </div>

              <p className="acc-hint">
                Оплата на сайте ЮKassa. После успешной оплаты подписка
                активируется автоматически. Эл. чек придёт на твой email.
              </p>
              <p className="sub-offer">
                Нажимая «Оплатить», вы принимаете условия{" "}
                <a href={OFFER_URL} target="_blank" rel="noreferrer">
                  публичной оферты
                </a>
                .
              </p>
            </>
          )}

          {error && <p className="login-error">{error}</p>}
        </div>

        <footer className="sheet__footer">
          <button type="button" className="btn btn--ghost" onClick={onClose}>
            Закрыть
          </button>
        </footer>
      </div>
    </div>
  );
}


function _fmtDate(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleDateString("ru-RU", { year: "numeric", month: "long", day: "numeric" });
  } catch { return iso; }
}

function _daysWord(n: number): string {
  const last2 = n % 100;
  const last1 = n % 10;
  if (last2 >= 11 && last2 <= 14) return "дней";
  if (last1 === 1) return "день";
  if (last1 >= 2 && last1 <= 4) return "дня";
  return "дней";
}
