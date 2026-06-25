/**
 * AccountSheet.tsx — «Аккаунт»: привязки входа (Telegram), ссылка на канал
 * @kmo_ai, выход. Открывается из угла экрана выбора режима.
 *
 * Поддерживаются провайдеры: telegram (deep-link), native (email+password),
 * yandex (OAuth, PR-7). Google/Apple убраны миграцией 0021.
 */

import { useCallback, useEffect, useState } from "react";
import WebApp from "@twa-dev/sdk";
import {
  extractYandexCallback,
  fetchMe,
  logout,
  pollAuth,
  requestUnlinkNative,
  setPassword,
  setToken,
  startTelegramFlow,
  startYandexFlow,
  type MeInfo,
} from "./auth";
import { ThemeToggle } from "./ThemeToggle";

interface Props {
  onLoggedOut: () => void;
  /** Обязателен в sheet-режиме (default), не нужен в embedded (tab). */
  onClose?: () => void;
  onOpenSubscribe?: () => void;
  onOpenTutorial?: () => void;
  /** embedded=true → рендерится как fullscreen-tab без backdrop'a и без
   *  sheet-обёртки. Используется в BottomNav-табе «Профиль». */
  embedded?: boolean;
}

const PROVIDER_LABEL: Record<string, string> = {
  telegram: "Telegram",
  native: "Email/пароль",
  yandex: "Яндекс ID",
};

// Ключ pending TG link-токена в sessionStorage. На монтировании компонента
// возобновляем poll, если юзер вернулся из Telegram.
const PENDING_TG_KEY = "englishbot_tg_link_pending";

export function AccountSheet({ onClose, onLoggedOut, onOpenSubscribe, onOpenTutorial, embedded = false }: Props) {
  const [me, setMe] = useState<MeInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState<string>("");
  const [tgPending, setTgPending] = useState<{ token: string; url: string } | null>(null);
  const [tgBusy, setTgBusy] = useState<boolean>(false);

  const inTelegram = (() => {
    try { return !!WebApp.initData; } catch { return false; }
  })();

  const reload = useCallback(async () => {
    setLoading(true);
    setMe(await fetchMe());
    setLoading(false);
  }, []);

  useEffect(() => { void reload(); }, [reload]);

  const linked = new Set((me?.identities || []).map((i) => i.provider));
  const hasTelegram = linked.has("telegram");
  const hasNative = linked.has("native");
  const hasYandex = linked.has("yandex");

  // ── Привязка через Яндекс OAuth: после возврата (#yandex_jwt=…) ────────
  const [yandexBusy, setYandexBusy] = useState<boolean>(false);
  useEffect(() => {
    const r = extractYandexCallback();
    if (!r) return;
    if (r.error) {
      setMsg(r.error === "identity_conflict"
        ? "Этот Яндекс уже привязан к другому аккаунту, и у обоих есть свои способы входа. Сначала отвяжи лишний способ в одном из аккаунтов."
        : "Не удалось привязать Яндекс. Попробуй ещё раз.");
      return;
    }
    if (r.jwt && r.mode === "link") {
      setToken(r.jwt);                  // мог обновиться после merge
      setMsg(r.merged
        ? "Аккаунты объединены ✓ — данные сохранены."
        : "Яндекс привязан ✓");
      void reload();
    }
  }, [reload]);

  const startYandexLink = async () => {
    if (yandexBusy) return;
    setYandexBusy(true); setMsg("");
    try {
      const r = await startYandexFlow("link");
      if (!r) { setMsg("Не удалось запустить привязку Яндекса. Попробуй позже."); return; }
      window.location.href = r.url;
    } finally { setYandexBusy(false); }
  };

  const [pwdEmail, setPwdEmail] = useState<string>("");
  const [pwd1, setPwd1] = useState<string>("");
  const [pwdBusy, setPwdBusy] = useState<boolean>(false);
  // Если у юзера уже есть email — поле в форме скроем; используем существующий.
  const accountEmail = me?.email ?? "";

  const handleSetPassword = async (e: React.FormEvent) => {
    e.preventDefault();
    if (pwdBusy) return;
    if (pwd1.length < 8) { setMsg("Пароль слишком короткий — минимум 8 символов."); return; }
    setPwdBusy(true); setMsg("");
    try {
      const r = await setPassword(pwd1, accountEmail ? undefined : pwdEmail);
      if (r.ok) {
        setMsg("Пароль задан ✓ — теперь можно войти по email на сайте.");
        setPwd1(""); setPwdEmail("");
        void reload();
      } else if (r.error === "email_taken") {
        setMsg("Этот email уже занят другим аккаунтом.");
      } else if (r.error === "weak_password") {
        setMsg("Пароль слишком короткий — минимум 8 символов.");
      } else if (r.error === "bad_email") {
        setMsg("Введи корректный email.");
      } else if (r.error === "email_required") {
        setMsg("Сначала укажи email.");
      } else {
        setMsg("Не удалось сохранить пароль.");
      }
    } finally { setPwdBusy(false); }
  };

  // ── Отвязка email/пароля через подтверждение в боте ────────────────────
  const [unlinkBusy, setUnlinkBusy] = useState(false);
  const handleRequestUnlinkNative = async () => {
    if (unlinkBusy) return;
    setUnlinkBusy(true); setMsg("");
    try {
      const r = await requestUnlinkNative();
      if (r.ok) {
        setMsg(
          "Отправили подтверждение в Telegram-бот. Открой чат, нажми " +
          "«✅ Подтвердить» — и обнови этот экран.",
        );
      } else if (r.error === "no_telegram") {
        setMsg("Сначала привяжи Telegram — это запасной способ входа.");
      } else if (r.error === "telegram_send_failed") {
        setMsg("Не получилось отправить сообщение в Telegram. Проверь, что не блокировал бота.");
      } else {
        setMsg("Не удалось запросить отвязку. Попробуй позже.");
      }
    } finally { setUnlinkBusy(false); }
  };

  // ── Привязка Telegram через deep-link в бот + poll ─────────────────────
  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(PENDING_TG_KEY);
      if (raw) {
        const p = JSON.parse(raw) as { token: string; url: string };
        if (p?.token) setTgPending(p);
      }
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    if (!tgPending) return;
    let cancelled = false;
    let attempts = 0;
    const tick = async () => {
      if (cancelled) return;
      attempts++;
      const r = await pollAuth(tgPending.token);
      if (cancelled) return;
      if (r.status === "done") {
        sessionStorage.removeItem(PENDING_TG_KEY);
        setTgPending(null);
        setMsg("Telegram привязан ✓ — данные объединены.");
        void reload();
        return;
      }
      if (r.status === "cancelled" || r.status === "failed" || r.status === "expired") {
        sessionStorage.removeItem(PENDING_TG_KEY);
        setTgPending(null);
        setMsg("Не удалось привязать Telegram. Попробуй ещё раз.");
        return;
      }
      if (attempts > 200) {
        sessionStorage.removeItem(PENDING_TG_KEY);
        setTgPending(null);
        setMsg("Время вышло. Открой Telegram ещё раз.");
        return;
      }
      setTimeout(tick, 3000);
    };
    void tick();
    return () => { cancelled = true; };
  }, [tgPending, reload]);

  const startTelegramLink = async () => {
    if (tgBusy) return;
    setTgBusy(true); setMsg("");
    try {
      const r = await startTelegramFlow("link");
      if (!r) { setMsg("Не удалось запустить привязку. Попробуй позже."); return; }
      try { sessionStorage.setItem(PENDING_TG_KEY, JSON.stringify(r)); } catch { /* ignore */ }
      setTgPending(r);
      window.location.href = r.url;
    } finally { setTgBusy(false); }
  };

  // Обёртка зависит от embedded: или fullscreen-tab (.profile-screen)
  // или модалка (.sheet-backdrop + .sheet).
  const wrapperClass = embedded ? "profile-screen" : "sheet-backdrop";
  const innerClass = embedded ? "ps-inner" : "sheet";
  const headerClass = embedded ? "ps-header" : "sheet__header";
  const titleClass = embedded ? "ps-title" : "sheet__title";
  const contentClass = embedded ? "ps-content" : "sheet__content";
  const footerClass = embedded ? "ps-footer" : "sheet__footer";

  return (
    <div className={wrapperClass} onClick={embedded ? undefined : onClose}>
      <div className={innerClass} onClick={embedded ? undefined : (e) => e.stopPropagation()}>
        <header className={headerClass}>
          <h2 className={titleClass}>{embedded ? "Профиль" : "Аккаунт"}</h2>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <ThemeToggle />
            {!embedded && onClose && (
              <button className="sheet__close" onClick={onClose} aria-label="Закрыть">✕</button>
            )}
          </div>
        </header>

        <div className={contentClass}>
          {loading ? (
            <p className="acc-hint">Загрузка…</p>
          ) : (
            <>
              <p className="acc-lead">
                Привяжи второй способ входа (email с паролем или Telegram) —
                не потеряешь прогресс при блокировке. Если у привязываемого
                способа уже есть отдельный аккаунт — аккаунты объединятся:
                данные сохранятся у того, который был создан раньше.
              </p>

              {onOpenSubscribe && (
                <div className="acc-link-block">
                  <div className="acc-link-title">
                    {me?.subscription_until
                      ? `Подписка до ${_fmtSubUntil(me.subscription_until)}`
                      : "Подписка не активна"}
                  </div>
                  <button
                    type="button"
                    className="btn btn--primary acc-link-btn"
                    onClick={onOpenSubscribe}
                  >
                    {me?.subscription_until ? "Продлить подписку" : "Оформить подписку"}
                  </button>
                </div>
              )}

              <div className="acc-list">
                {(["telegram", "native", "yandex"] as const).map((p) => {
                  const id = me?.identities.find((i) => i.provider === p);
                  return (
                    <div key={p} className="acc-row">
                      <span className="acc-row__name">{PROVIDER_LABEL[p]}</span>
                      {id ? (
                        <span className="acc-row__on">
                          ✓ {id.email || "привязан"}
                        </span>
                      ) : (
                        <span className="acc-row__off">не привязан</span>
                      )}
                    </div>
                  );
                })}
              </div>

              {!hasTelegram && !inTelegram && (
                tgPending ? (
                  <div className="acc-link-block">
                    <div className="acc-link-title">⏳ Ждём подтверждения в Telegram</div>
                    <p className="acc-hint">
                      Открой Telegram и нажми «Start» в боте. Этот экран
                      обновится автоматически.
                    </p>
                    <a className="btn btn--primary acc-link-btn" href={tgPending.url}>
                      Открыть Telegram ещё раз
                    </a>
                  </div>
                ) : (
                  <div className="acc-link-block">
                    <button
                      type="button"
                      className="btn btn--primary acc-link-btn"
                      onClick={startTelegramLink}
                      disabled={tgBusy}
                    >
                      ✈️ Привязать Telegram
                    </button>
                    <p className="acc-hint">Откроется приложение Telegram, нажми «Start».</p>
                  </div>
                )
              )}

              {!hasYandex && !inTelegram && (
                <div className="acc-link-block">
                  <button
                    type="button"
                    className="btn btn--primary acc-link-btn"
                    onClick={startYandexLink}
                    disabled={yandexBusy}
                  >
                    🟡 Привязать Яндекс ID
                  </button>
                  <p className="acc-hint">
                    Перейдёшь на oauth.yandex.ru, вернёшься на сайт уже привязанным.
                  </p>
                </div>
              )}

              {!hasNative && (
                <form className="acc-link-block acc-pwd-form" onSubmit={handleSetPassword}>
                  <div className="acc-link-title">
                    Задать пароль (для входа по email)
                  </div>
                  {!accountEmail && (
                    <input
                      className="login-input"
                      type="email"
                      placeholder="Email"
                      value={pwdEmail}
                      onChange={(e) => setPwdEmail(e.target.value)}
                      required
                      inputMode="email"
                      maxLength={255}
                    />
                  )}
                  <input
                    className="login-input"
                    type="password"
                    placeholder="Пароль (от 8 символов)"
                    value={pwd1}
                    onChange={(e) => setPwd1(e.target.value)}
                    required
                    minLength={8}
                    maxLength={128}
                  />
                  <button
                    type="submit"
                    className="btn btn--primary"
                    disabled={pwdBusy}
                  >
                    {pwdBusy ? "…" : "Задать пароль"}
                  </button>
                </form>
              )}

              {hasTelegram && hasNative && (
                <div className="acc-link-block">
                  <div className="acc-link-title">Снять email/пароль</div>
                  <p className="acc-hint">
                    Останется только вход через Telegram. Подтвердить нужно
                    будет в Telegram-боте.
                  </p>
                  <button
                    type="button"
                    className="btn btn--ghost acc-link-btn"
                    onClick={handleRequestUnlinkNative}
                    disabled={unlinkBusy}
                  >
                    {unlinkBusy ? "…" : "Отвязать email/пароль"}
                  </button>
                </div>
              )}

              {msg && <p className="acc-msg">{msg}</p>}

              {onOpenTutorial && (
                <button
                  type="button"
                  className="acc-channel"
                  onClick={onOpenTutorial}
                >
                  📖 Открыть гид заново
                </button>
              )}

              <a
                className="acc-channel"
                href="https://t.me/kmo_ai"
                target="_blank"
                rel="noreferrer"
              >
                📣 Новости проекта — @kmo_ai
              </a>
            </>
          )}
        </div>

        <footer className={footerClass}>
          {!inTelegram && (
            <button
              type="button"
              className="btn btn--ghost"
              onClick={() => { logout(); onLoggedOut(); }}
            >
              Выйти
            </button>
          )}
          {!embedded && onClose && (
            <button type="button" className="btn btn--primary" onClick={onClose}>
              Готово
            </button>
          )}
        </footer>
      </div>
    </div>
  );
}

function _fmtSubUntil(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleDateString("ru-RU", { year: "numeric", month: "long", day: "numeric" });
  } catch { return iso; }
}
