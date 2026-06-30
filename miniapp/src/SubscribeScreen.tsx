/**
 * SubscribeScreen.tsx — страница подписки с тарифами и оплатой через ЮKassa.
 *
 * Два режима:
 *  - `mode='select'` (дефолт) — показывает 3 тарифа из /api/payments/plans,
 *    клик создаёт платёж и редиректит на confirmation_url ЮKassa.
 *  - `mode='return'` — юзер вернулся с ЮKassa (URL содержит ?payment_id=N).
 *    Опрашиваем /api/payments/status каждые 2с до status='succeeded' либо
 *    таймаута. На успехе зовём `onPaid()`.
 *
 * UI v2: notebook-paper фон + warm cream surface, тарифы как sage-tinted
 * NoteCard'ы, цена крупным Source Serif, lucide x-close.
 */

import { useCallback, useEffect, useState } from "react";
import {
  createPayment,
  checkPromo,
  fetchMe,
  fetchPaymentStatus,
  listPlans,
  type MeInfo,
  type Plan,
} from "./auth";
import { ymHit, ymReachGoal } from "./metrika";
import { Button } from "./ds-react/Button";
import { IconButton } from "./ds-react/IconButton";
import { NoteCard } from "./ds-react/NoteCard";
import { SerifH } from "./ds-react/typography";
import { useLucide } from "./lucide";

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
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string>("");
  const [askEmailFor, setAskEmailFor] = useState<Plan["key"] | null>(null);
  const [emailInput, setEmailInput] = useState<string>("");
  // Промокод.
  const [promoInput, setPromoInput] = useState<string>("");
  const [promoApplied, setPromoApplied] = useState<{ code: string; pct: number } | null>(null);
  const [promoMsg, setPromoMsg] = useState<string>("");
  const [promoBusy, setPromoBusy] = useState<boolean>(false);
  const [returnPaymentId, setReturnPaymentId] = useState<number | null>(
    initialReturnPaymentId ?? null,
  );
  const [returnStatus, setReturnStatus] = useState<"pending" | "succeeded" | "canceled" | "expired" | null>(
    initialReturnPaymentId ? "pending" : null,
  );

  useLucide(`${loading}-${askEmailFor}-${returnStatus}-${plans.length}`);

  useEffect(() => {
    let alive = true;
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
        await fetchMe();
        onPaid?.();
        return;
      }
      if (s?.status === "canceled" || s?.status === "refunded") {
        setReturnStatus("canceled");
        return;
      }
      if (attempts > 60) {
        setReturnStatus("expired");
        return;
      }
      setTimeout(tick, 2000);
    };
    void tick();
    return () => { cancelled = true; };
  }, [returnPaymentId, returnStatus, onPaid]);

  const applyPromo = useCallback(async () => {
    const code = promoInput.trim().toUpperCase();
    if (!code || promoBusy) return;
    setPromoBusy(true); setPromoMsg("");
    try {
      const r = await checkPromo(code);
      if (!r) { setPromoMsg("Не удалось проверить промокод."); return; }
      if (r.already_used) { setPromoApplied(null); setPromoMsg("Этот промокод ты уже использовал."); return; }
      if (!r.valid) { setPromoApplied(null); setPromoMsg("Промокод недействителен."); return; }
      setPromoApplied({ code, pct: r.discount_percent });
      setPromoMsg(`Скидка ${r.discount_percent}% применена ко всем тарифам.`);
    } finally { setPromoBusy(false); }
  }, [promoInput, promoBusy]);

  const _discounted = useCallback((amount: number): number => {
    if (!promoApplied) return amount;
    return Math.max(1, Math.round(amount * (100 - promoApplied.pct) / 100));
  }, [promoApplied]);

  const launchPayment = useCallback(async (planKey: Plan["key"], emailOverride?: string) => {
    if (busy) return;
    setBusy(planKey); setError("");
    const planInfo = plans.find((p) => p.key === planKey);
    ymReachGoal("subscribe_plan_clicked", {
      plan: planKey,
      amount_rub: planInfo?.amount_rub,
    });
    try {
      const r = await createPayment(planKey, emailOverride, promoApplied?.code);
      if (r.ok && r.confirmation_url) {
        window.location.href = r.confirmation_url;
        return;
      }
      if (r.error === "email_required") {
        setAskEmailFor(planKey);
        return;
      }
      if (r.error === "promo_already_used") {
        setPromoApplied(null);
        setError("Этот промокод ты уже использовал.");
      } else if (r.error === "promo_invalid") {
        setPromoApplied(null);
        setError("Промокод недействителен.");
      } else if (r.error === "yookassa_not_configured") {
        setError("Оплата временно недоступна. Сообщи в @kmo_ai, разберёмся.");
      } else {
        setError("Не получилось создать платёж. Попробуй ещё раз.");
      }
    } finally { setBusy(null); }
  }, [busy, plans, promoApplied]);

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

  // Recommended plan — годовой (лучшее соотношение цена/срок).
  const recommendedKey: string | undefined =
    plans.find((p) => p.key === "yearly")?.key ?? plans[1]?.key ?? plans[0]?.key;

  return (
    <div className="sub-screen">
      <div className="sub-screen__inner">
        <header className="sub-screen__top">
          <SerifH as="h1" size={28}>Подписка</SerifH>
          <IconButton icon="x" variant="surface" size="md" label="Закрыть" onClick={onClose} />
        </header>

        <div className="sub-screen__body">
          {returnPaymentId ? (
            <NoteCard padding={20} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              {returnStatus === "pending" && (
                <>
                  <div className="sub-status sub-status--pending">⏳ Ждём подтверждения от ЮKassa…</div>
                  <p className="sub-hint">
                    Это может занять до минуты. Можешь не закрывать страницу —
                    как только платёж пройдёт, подписка активируется
                    автоматически.
                  </p>
                </>
              )}
              {returnStatus === "succeeded" && (
                <>
                  <div className="sub-status sub-status--ok">✅ Подписка активирована!</div>
                  <p className="sub-hint">Открывай любой режим — лимиты сняты.</p>
                </>
              )}
              {returnStatus === "canceled" && (
                <>
                  <div className="sub-status sub-status--err">❌ Платёж отменён.</div>
                  <p className="sub-hint">Можешь попробовать ещё раз.</p>
                  <Button variant="primary" fullWidth onClick={() => { setReturnPaymentId(null); setReturnStatus(null); }}>
                    К тарифам
                  </Button>
                </>
              )}
              {returnStatus === "expired" && (
                <>
                  <div className="sub-status sub-status--warn">⏰ Подтверждение задерживается.</div>
                  <p className="sub-hint">
                    Если ты уже оплатил — подписка активируется в течение пары минут.
                    Открой эту страницу заново или напиши @kmo_ai, мы поможем.
                  </p>
                </>
              )}
              <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 4 }}>
                <Button variant="secondary" onClick={onClose}>Готово</Button>
              </div>
            </NoteCard>
          ) : loading ? (
            <p className="sub-hint">Загрузка…</p>
          ) : askEmailFor ? (
            <NoteCard padding={20}>
              <form className="sub-email-form" onSubmit={submitEmail}>
                <p className="sub-lead">Нужен email для электронного чека (требование 54-ФЗ).</p>
                <input
                  className="sub-input"
                  type="email"
                  placeholder="Email"
                  value={emailInput}
                  onChange={(e) => setEmailInput(e.target.value)}
                  required
                  autoFocus
                  inputMode="email"
                  maxLength={255}
                />
                <Button type="submit" variant="primary" fullWidth disabled={!!busy}>
                  {busy ? "…" : "Продолжить к оплате"}
                </Button>
                <Button type="button" variant="ghost" fullWidth onClick={() => setAskEmailFor(null)}>
                  Назад
                </Button>
                <p className="sub-offer">
                  Продолжая, вы принимаете условия{" "}
                  <a href={OFFER_URL} target="_blank" rel="noreferrer">публичной оферты</a>.
                </p>
              </form>
            </NoteCard>
          ) : (
            <>
              {me?.subscription_until && (
                <p className="sub-lead">
                  Подписка активна до <b>{_fmtDate(me.subscription_until)}</b>.
                  Можно продлить на любой срок — дни прибавляются.
                </p>
              )}

              {/* Промокод */}
              <div className="sub-promo">
                <input
                  className="sub-input"
                  type="text"
                  placeholder="Промокод (если есть)"
                  value={promoInput}
                  onChange={(e) => setPromoInput(e.target.value.toUpperCase())}
                  maxLength={32}
                  style={{ textTransform: "uppercase", margin: 0 }}
                  disabled={promoBusy}
                />
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() => void applyPromo()}
                  disabled={promoBusy || !promoInput.trim()}
                >
                  {promoBusy ? "…" : "Применить"}
                </Button>
              </div>
              {promoMsg && (
                <p className={promoApplied ? "sub-promo-ok" : "sub-error"} style={{ margin: 0 }}>
                  {promoMsg}
                </p>
              )}

              <div className="sub-plans-v2">
                {plans.map((p) => {
                  const isRecommended = p.key === recommendedKey;
                  const finalPrice = _discounted(p.amount_rub);
                  const hasDiscount = !!promoApplied && finalPrice !== p.amount_rub;
                  return (
                    <button
                      key={p.key}
                      type="button"
                      className={`sub-plan-v2 ${isRecommended ? "is-recommended" : ""}`}
                      onClick={() => void launchPayment(p.key)}
                      disabled={!!busy}
                    >
                      <div className="sub-plan-v2__head">
                        <span className="sub-plan-v2__title">{p.title}</span>
                        {isRecommended && <span className="sub-plan-v2__badge">Рекомендуем</span>}
                      </div>
                      <div className="sub-plan-v2__price">
                        {hasDiscount && <s className="sub-plan-v2__old">{p.amount_rub} ₽</s>}
                        {finalPrice} ₽
                      </div>
                      <div className="sub-plan-v2__days">{p.days} {_daysWord(p.days)}</div>
                    </button>
                  );
                })}
              </div>

              <p className="sub-hint">
                Оплата на сайте ЮKassa. После успешной оплаты подписка
                активируется автоматически. Эл. чек придёт на твой email.
              </p>
              <p className="sub-offer">
                Нажимая «Оплатить», вы принимаете условия{" "}
                <a href={OFFER_URL} target="_blank" rel="noreferrer">публичной оферты</a>.
              </p>
            </>
          )}

          {error && <p className="sub-error">{error}</p>}
        </div>
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
