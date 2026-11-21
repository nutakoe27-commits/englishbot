import WebApp from "@twa-dev/sdk";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { API_BASE, getToken, createPayment } from "./auth";
import { useBackGuard } from "./backGuard";
import { Button } from "./ds-react/Button";
import "./SubscriptionOffer.css";

interface Offer {
  show: boolean;
  answered?: boolean;
  impression_id: string;
  audience: string;
  plan: "trial7" | "monthly";
  amount_rub: number; days: number; renew_amount_rub: number; available: boolean;
  reasons: { code: string; label: string }[];
}

async function request<T>(path: string, body: Record<string, unknown>, keepalive = false): Promise<T> {
  const token = getToken();
  const response = await fetch(`${API_BASE}/api/subscription-offers${path}`, {
    method: "POST", keepalive,
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify({ ...body, init_data: WebApp.initData || "" }),
  });
  if (!response.ok) throw new Error("Не удалось сохранить ответ. Проверьте соединение и попробуйте ещё раз.");
  return response.json() as Promise<T>;
}

async function claimOffer(trigger: OfferTrigger = "entry"): Promise<Offer> {
  let id: string;
  if (typeof crypto.randomUUID === "function") id = crypto.randomUUID();
  else {
    // Older WebViews provide getRandomValues before randomUUID is available.
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
    const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, "0")).join("");
    id = `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }
  return request<Offer>("/claim", { request_id: id, trigger });
}

type OfferTrigger = "entry" | "access" | "lesson_completed";
export function requestSubscription(trigger: OfferTrigger = "access") {
  window.dispatchEvent(new CustomEvent("subscription:open", { detail: trigger }));
}

export function SubscriptionOffer({ enabled = true }: { enabled?: boolean }) {
  const [offer, setOffer] = useState<Offer | null>(null);
  const [step, setStep] = useState<"offer" | "survey" | "closed">("offer");
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [email, setEmail] = useState("");
  const [askEmail, setAskEmail] = useState(false);
  const [paying, setPaying] = useState(false);
  const dialog = useRef<HTMLDivElement>(null);
  const dismissed = useRef(false);
  const claim = useRef<Promise<Offer> | null>(null);
  const handledClaim = useRef<Promise<Offer> | null>(null);
  const claimTrigger = useRef<OfferTrigger>("entry");
  const lastCheck = useRef(0);
  const lastRequested = useRef(0);
  const answered = useRef(false);

  useEffect(() => {
    let alive = true;
    const receive = (pending: Promise<Offer>, trigger: OfferTrigger) => pending.then(data => {
      if (!alive || pending !== claim.current || pending === handledClaim.current || (trigger === "entry" && !enabled)) return;
      handledClaim.current = pending;
      if (data.answered) answered.current = true;
      if (data.show) { dismissed.current = false; setOffer(data); setStep("offer"); setError(""); setAskEmail(false); }
    });
    const watchClaim = () => {
      if (claim.current) void receive(claim.current, claimTrigger.current).catch(() => {});
    };
    // React StrictMode replays effects. Both listeners share one claim and
    // its response instead of letting the first request consume the cooldown.
    if (enabled && !claim.current) {
      lastCheck.current = Date.now();
      claim.current = claimOffer();
    }
    watchClaim();
    // A PWA/WebView may stay alive overnight. Check on a new foreground visit,
    // never on a timer during a lesson. The server owns the actual 24h limit.
    const resume = () => {
      if (!enabled || answered.current || document.visibilityState !== "visible" || Date.now() - lastCheck.current < 60_000) return;
      lastCheck.current = Date.now();
      claim.current = claimOffer();
      claimTrigger.current = "entry";
      watchClaim();
    };
    const requested = (event: Event) => {
      if (Date.now() - lastRequested.current < 1000) return;
      lastRequested.current = Date.now();
      const trigger = (event as CustomEvent<OfferTrigger>).detail;
      claim.current = claimOffer(trigger);
      claimTrigger.current = trigger;
      void receive(claim.current, trigger).catch(() => {
        window.dispatchEvent(new CustomEvent("subscription:error"));
      });
    };
    window.addEventListener("subscription:open", requested);
    document.addEventListener("visibilitychange", resume);
    window.addEventListener("pageshow", resume);
    window.addEventListener("focus", resume);
    return () => {
      alive = false;
      window.removeEventListener("subscription:open", requested);
      document.removeEventListener("visibilitychange", resume);
      window.removeEventListener("pageshow", resume);
      window.removeEventListener("focus", resume);
    };
  }, [enabled]);

  const record = useCallback((action: "closed" | "declined" | "checkout") => {
    if (offer) void request("/event", { impression_id: offer.impression_id, action }, true).catch(() => {});
  }, [offer]);
  const close = useCallback(() => {
    dismissed.current = true; record("closed"); setStep("closed");
  }, [record]);
  const open = !!offer && step !== "closed";
  useBackGuard(open, close);

  useLayoutEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    if (dialog.current) { dialog.current.scrollTop = 0; dialog.current.focus({ preventScroll: true }); }
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); close(); }
      if (event.key !== "Tab") return;
      const items = [...(dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), a[href], [tabindex="0"]') || [])];
      const first = items[0], last = items[items.length - 1];
      if (!first) { event.preventDefault(); return; }
      if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog.current)) {
        event.preventDefault(); last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || document.activeElement === dialog.current)) {
        event.preventDefault(); first.focus();
      }
    };
    document.addEventListener("keydown", keydown);
    return () => {
      document.body.style.overflow = overflow;
      document.removeEventListener("keydown", keydown);
      if (previous?.isConnected) previous.focus();
    };
  }, [close, open, step]);

  async function answer(reason: string) {
    if (!offer || saving) return;
    setSaving(reason); setError("");
    try {
      await request<{ ok: boolean }>("/answer", { impression_id: offer.impression_id, reason });
      answered.current = true;
      if (!dismissed.current) setStep("closed");
    } catch (failure) {
      if (!dismissed.current) setError(failure instanceof Error ? failure.message : "Не удалось сохранить ответ. Попробуйте ещё раз.");
    } finally { setSaving(null); }
  }
  const pay = async () => {
    if (!offer || paying) return;
    setPaying(true); setError(""); record("checkout");
    try {
      const result = await createPayment(offer.plan, email || undefined, undefined,
        { recurring_consent: true, renew_amount_rub: offer.renew_amount_rub });
      if (dismissed.current) return;
      if (result.ok && result.confirmation_url) { window.location.assign(result.confirmation_url); return; }
      if (result.error === "email_required") { setAskEmail(true); return; }
      const messages: Record<string,string> = {
        email_invalid: "Проверьте email для чека.",
        recurring_unavailable: "Подключение подписки временно недоступно. Попробуйте позже.",
        plan_already_used: "Предложение уже использовано. Закройте окно и откройте подписку снова.",
        renewal_price_changed: "Условия продления изменились. Закройте окно и откройте предложение заново.",
        payment_pending: "Предыдущий платёж ещё проверяется. Дождитесь его результата, чтобы избежать повторной оплаты.",
      };
      setError(messages[result.error || ""] || "Не удалось открыть оплату. Попробуйте ещё раз.");
    } finally { setPaying(false); }
  };

  if (!open || !offer) return null;
  const intro = offer.plan === "trial7";
  const title = step === "survey" ? "Почему вы пока не хотите оформить подписку?"
    : intro ? "7 дней за 1 ₽" : "Продолжим заниматься?";

  return createPortal(
    <div className="retention-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) close(); }}>
      <div ref={dialog} className={`retention-dialog retention-dialog--${step}`} role="dialog"
        aria-modal="true" aria-labelledby="retention-title" aria-describedby="retention-description" tabIndex={-1}>
        <div className="retention-header">
          <div className="retention-eyebrow">{step === "survey" ? "Помогите нам стать лучше" : "Ваш следующий шаг"}</div>
          <button className="retention-close" type="button" aria-label="Закрыть предложение" onClick={close}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" /></svg>
          </button>
        </div>
        {step !== "survey" && <div className="retention-ticket">
          <span className="retention-ticket__amount">{offer.amount_rub}<span> ₽</span></span>
          <span className="retention-ticket__details"><strong>{intro ? "7 дней" : "30 дней"}</strong>полного доступа</span>
        </div>}
        <h2 id="retention-title">{title}</h2>
        <p id="retention-description" className="retention-description">
          {step === "survey" ? "Выберите основную причину. Ваш ответ поможет нам сделать занятия удобнее."
            : "Этот раздел доступен по подписке. Откройте все уроки, разговоры, слова и подкасты."}
        </p>
        {step === "survey" ? <div className="retention-reasons" aria-busy={!!saving}>
          {offer.reasons.map((reason, index) => <button type="button" className="retention-reason"
            key={reason.code} disabled={!!saving} onClick={() => { void answer(reason.code); }}>
            <span className="retention-reason__number" aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>
            <span>{reason.label}</span><span className="retention-reason__arrow" aria-hidden="true">{saving === reason.code ? "…" : "→"}</span>
          </button>)}
          {error && <p className="retention-error" role="alert">{error}</p>}
        </div> : <>
          <form onSubmit={event => { event.preventDefault(); void pay(); }} className="retention-actions">
            {askEmail && <label className="retention-email">Email для чека
              <input type="email" required autoComplete="email" value={email} onChange={event => setEmail(event.target.value)} placeholder="you@example.com" />
            </label>}
            <Button type="submit" fullWidth size="lg" disabled={paying || !offer.available}>
              {paying ? "Открываем оплату…" : intro ? "Подключить 7 дней за 1 ₽" : `Подключить за ${offer.amount_rub} ₽`}
            </Button>
            <p className="retention-fineprint">* {intro ? `Сейчас — 1 ₽ за 7 дней. Затем — ${offer.renew_amount_rub} ₽ каждые 30 дней автоматически.` : `${offer.renew_amount_rub} ₽ каждые 30 дней автоматически.`} Отменить автопродление можно в профиле до следующего списания. {intro && "Предложение доступно один раз при первом подключении."}</p>
            <a className="retention-offer-link" href="/oferta.html" target="_blank" rel="noreferrer">Ознакомиться с офертой</a>
            {!offer.available && <p className="retention-error" role="status">Подключение подписки временно недоступно. Попробуйте позже.</p>}
            {error && <p className="retention-error" role="alert">{error}</p>}
            <Button type="button" fullWidth variant="ghost" onClick={() => {
              if (offer.answered || answered.current) { close(); return; }
              record("declined"); setStep("survey"); setError("");
            }}>Спасибо, не надо</Button>
          </form>
        </>}
      </div>
    </div>, document.body,
  );
}
