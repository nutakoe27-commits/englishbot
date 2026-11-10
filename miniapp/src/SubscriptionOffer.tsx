import WebApp from "@twa-dev/sdk";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { API_BASE, getToken } from "./auth";
import { useBackGuard } from "./backGuard";
import { Button } from "./ds-react/Button";
import "./SubscriptionOffer.css";

interface Offer {
  show: boolean;
  answered?: boolean;
  impression_id: string;
  audience: "trial_expired" | "free_learner";
  promo_code: string;
  discount_percent: number;
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

async function claimOffer(): Promise<Offer> {
  let id: string;
  if (typeof crypto.randomUUID === "function") id = crypto.randomUUID();
  else {
    // Older WebViews provide getRandomValues before randomUUID is available.
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
    const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, "0")).join("");
    id = `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }
  return request<Offer>("/claim", { request_id: id });
}

export function SubscriptionOffer({ onSubscribe, onAnswered }: { onSubscribe: (promo: string) => void; onAnswered?: (promo: string) => void }) {
  const [offer, setOffer] = useState<Offer | null>(null);
  const [step, setStep] = useState<"offer" | "survey" | "thanks" | "closed">("offer");
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [followup, setFollowup] = useState<{ promo_code: string; discount_percent: number } | null>(null);
  const dialog = useRef<HTMLDivElement>(null);
  const dismissed = useRef(false);
  const claim = useRef<Promise<Offer> | null>(null);
  const lastCheck = useRef(0);
  const answered = useRef(false);

  useEffect(() => {
    let alive = true;
    const receive = (data: Offer) => {
      if (data.answered) answered.current = true;
      if (alive && data.show) { dismissed.current = false; setOffer(data); setStep("offer"); }
    };
    // React StrictMode replays effects. Both listeners share one claim and
    // its response instead of letting the first request consume the cooldown.
    if (!claim.current) {
      lastCheck.current = Date.now();
      claim.current = claimOffer();
    }
    void claim.current.then(receive).catch(() => {});
    // A PWA/WebView may stay alive overnight. Check on a new foreground visit,
    // never on a timer during a lesson. The server owns the actual 24h limit.
    const resume = () => {
      if (answered.current || document.visibilityState !== "visible" || Date.now() - lastCheck.current < 60_000) return;
      lastCheck.current = Date.now();
      claim.current = claimOffer();
      void claim.current.then(receive).catch(() => {});
    };
    document.addEventListener("visibilitychange", resume);
    window.addEventListener("pageshow", resume);
    window.addEventListener("focus", resume);
    return () => {
      alive = false;
      document.removeEventListener("visibilitychange", resume);
      window.removeEventListener("pageshow", resume);
      window.removeEventListener("focus", resume);
    };
  }, []);

  const record = useCallback((action: "closed" | "declined" | "tariffs") => {
    if (offer) void request("/event", { impression_id: offer.impression_id, action,
      promo_code: step === "thanks" && followup ? followup.promo_code : offer.promo_code }, true).catch(() => {});
  }, [offer, step, followup]);
  const close = useCallback(() => {
    dismissed.current = true;
    if (step !== "thanks") record("closed");
    setStep("closed");
  }, [record, step]);
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
      const items = [...(dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled), [tabindex="0"]') || [])];
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
      const result = await request<{ promo_code: string; discount_percent: number }>("/answer", { impression_id: offer.impression_id, reason });
      answered.current = true;
      onAnswered?.(result.promo_code);
      if (!dismissed.current) { setFollowup(result); setStep("thanks"); }
    } catch (failure) {
      if (!dismissed.current) setError(failure instanceof Error ? failure.message : "Не удалось сохранить ответ. Попробуйте ещё раз.");
    } finally { setSaving(null); }
  }
  const tariffs = () => {
    if (!offer) return;
    record("tariffs"); dismissed.current = true; setStep("closed");
    onSubscribe(step === "thanks" && followup ? followup.promo_code : offer.promo_code);
  };

  if (!open || !offer) return null;
  const discount = step === "thanks" && followup ? followup : offer;
  const title = step === "survey" ? "Почему вы пока не хотите оформить подписку?"
    : step === "thanks" ? "Спасибо за ответ!" : "Продолжим заниматься?";

  return createPortal(
    <div className="retention-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) close(); }}>
      <div ref={dialog} className={`retention-dialog retention-dialog--${step}`} role="dialog"
        aria-modal="true" aria-labelledby="retention-title" aria-describedby="retention-description" tabIndex={-1}>
        <div className="retention-header">
          <div className="retention-eyebrow">{step === "survey" ? "Помогите нам стать лучше" : step === "thanks" ? "Ваше мнение важно" : "Небольшой подарок для вас"}</div>
          <button className="retention-close" type="button" aria-label="Закрыть предложение" onClick={close}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" /></svg>
          </button>
        </div>
        {step !== "survey" && <div className="retention-ticket" aria-label={`Скидка ${discount.discount_percent}%`}>
          <span className="retention-ticket__amount">−{discount.discount_percent}<span>%</span></span>
          <span className="retention-ticket__details">на любой тариф<strong>{discount.promo_code}</strong></span>
        </div>}
        <h2 id="retention-title">{title}</h2>
        <p id="retention-description" className="retention-description">
          {step === "survey" ? "Выберите основную причину. Один ответ поможет нам сделать занятия удобнее."
            : step === "thanks" ? `Мы учтём ваше мнение. Если передумаете, используйте ${discount.promo_code} — скидка ${discount.discount_percent}% уже будет применена при переходе к тарифам.`
            : `${offer.audience === "trial_expired" ? "Пробный период завершился. " : ""}Продолжайте учиться в своём темпе — дарим скидку ${offer.discount_percent}% на любой тариф.`}
        </p>
        {step === "survey" ? <div className="retention-reasons" aria-busy={!!saving}>
          {offer.reasons.map((reason, index) => <button type="button" className="retention-reason"
            key={reason.code} disabled={!!saving} onClick={() => { void answer(reason.code); }}>
            <span className="retention-reason__number" aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>
            <span>{reason.label}</span><span className="retention-reason__arrow" aria-hidden="true">{saving === reason.code ? "…" : "→"}</span>
          </button>)}
          {error && <p className="retention-error" role="alert">{error}</p>}
        </div> : <>
          <div className="retention-actions">
            <Button fullWidth size="lg" onClick={tariffs}>Перейти к тарифам <span aria-hidden="true">→</span></Button>
            <Button fullWidth variant="ghost" onClick={() => {
              if (step === "thanks") { close(); return; }
              record("declined"); setStep("survey");
            }}>{step === "thanks" ? "Продолжить заниматься" : "Спасибо, не надо"}</Button>
          </div>
          <p className="retention-fineprint">Промокод действует на первую оплату любого тарифа. Один раз на аккаунт.</p>
        </>}
      </div>
    </div>, document.body,
  );
}
