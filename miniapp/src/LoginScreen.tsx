/**
 * LoginScreen.tsx — экран входа для веб-версии (вне Telegram).
 *
 * Telegram-вход через deep-link в бота (`t.me/<bot>?start=login_<token>`):
 * сайт → открывает приложение Telegram → юзер тапает Start → бот выполняет
 * upsert → сайт через poll забирает JWT. Login Widget oauth.telegram.org НЕ
 * используется (юзеры путались с «телеграм-вебом»).
 *
 * Нативный вход (email+password) — без изменений.
 */

import { useCallback, useEffect, useState } from "react";
import {
  loginNative,
  pollAuth,
  registerNative,
  startTelegramFlow,
} from "./auth";

interface Props {
  onAuthed: () => void;
}

type Tab = "login" | "register";

// Ключ в sessionStorage — токен pending TG-флоу. На обновлении страницы
// возобновляем poll.
const PENDING_TG_KEY = "englishbot_tg_pending";

export function LoginScreen({ onAuthed }: Props) {
  const [error, setError] = useState<string>("");
  const [busy, setBusy] = useState<boolean>(false);
  const [tab, setTab] = useState<Tab>("login");
  const [email, setEmail] = useState<string>("");
  const [password, setPassword] = useState<string>("");
  const [firstName, setFirstName] = useState<string>("");
  const [pending, setPending] = useState<{ token: string; url: string } | null>(null);

  // ── Восстановить pending-токен (если юзер вернулся из Telegram) ────────
  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(PENDING_TG_KEY);
      if (raw) {
        const parsed = JSON.parse(raw) as { token: string; url: string };
        if (parsed?.token) setPending(parsed);
      }
    } catch { /* ignore */ }
  }, []);

  // ── Poll-цикл, пока есть pending ───────────────────────────────────────
  useEffect(() => {
    if (!pending) return;
    let cancelled = false;
    let attempts = 0;
    const tick = async () => {
      if (cancelled) return;
      attempts++;
      const r = await pollAuth(pending.token);
      if (cancelled) return;
      if (r.status === "done" && r.token) {
        sessionStorage.removeItem(PENDING_TG_KEY);
        onAuthed();
        return;
      }
      if (r.status === "cancelled" || r.status === "failed" || r.status === "expired") {
        sessionStorage.removeItem(PENDING_TG_KEY);
        setPending(null);
        setError("Не удалось войти через Telegram. Попробуй ещё раз.");
        return;
      }
      // ~10 минут максимум, шаг 3с — 200 попыток
      if (attempts > 200) {
        sessionStorage.removeItem(PENDING_TG_KEY);
        setPending(null);
        setError("Время вышло. Открой Telegram ещё раз.");
        return;
      }
      setTimeout(tick, 3000);
    };
    void tick();
    return () => { cancelled = true; };
  }, [pending, onAuthed]);

  const startTelegram = useCallback(async () => {
    setError(""); setBusy(true);
    try {
      const r = await startTelegramFlow("login");
      if (!r) { setError("Не удалось запустить вход. Попробуй ещё раз."); return; }
      try {
        sessionStorage.setItem(PENDING_TG_KEY, JSON.stringify(r));
      } catch { /* ignore */ }
      setPending(r);
      // Открыть Telegram. Если deep-link не сработает — юзер увидит fallback
      // ссылку и сможет открыть вручную.
      window.location.href = r.url;
    } finally { setBusy(false); }
  }, []);

  const cancelTelegram = () => {
    sessionStorage.removeItem(PENDING_TG_KEY);
    setPending(null);
    setError("");
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setError(""); setBusy(true);
    try {
      const r = tab === "login"
        ? await loginNative(email, password)
        : await registerNative(email, password, firstName.trim() || undefined);
      if (r.ok) { onAuthed(); return; }
      if (r.error === "bad_credentials") setError("Неверный email или пароль.");
      else if (r.error === "email_taken") setError("Этот email уже зарегистрирован. Войди по нему.");
      else if (r.error === "bad_email") setError("Введи корректный email.");
      else if (r.error === "weak_password") setError("Пароль слишком короткий — минимум 8 символов.");
      else setError("Что-то пошло не так. Попробуй ещё раз.");
    } catch { setError("Ошибка сети. Попробуй ещё раз."); }
    finally { setBusy(false); }
  };

  return (
    <div className="login-screen">
      <div className="login-card">
        <div className="login-brand">
          <span className="tutor-brand__dot" aria-hidden />
          <span className="login-brand__name">English Tutor</span>
        </div>
        <h1 className="login-title">Вход в English Tutor</h1>
        <p className="login-subtitle">
          Говори, слушай, учи грамматику и слова с AI-репетитором. Войди, чтобы
          сохранять прогресс на любом устройстве.
        </p>

        {pending ? (
          <div className="login-pending">
            <p className="login-pending__title">⏳ Ждём подтверждения в Telegram</p>
            <p className="login-pending__hint">
              Открой Telegram и нажми «Start» в боте. После этого вернись
              сюда — страница обновится автоматически.
            </p>
            <a className="btn btn--primary login-submit" href={pending.url}>
              Открыть Telegram ещё раз
            </a>
            <button
              type="button"
              className="btn btn--ghost"
              onClick={cancelTelegram}
            >
              Отмена
            </button>
          </div>
        ) : (
          <>
            <div className="login-tabs" role="tablist">
              <button
                type="button"
                role="tab"
                aria-selected={tab === "login"}
                className={`login-tab ${tab === "login" ? "is-active" : ""}`}
                onClick={() => { setTab("login"); setError(""); }}
              >Вход</button>
              <button
                type="button"
                role="tab"
                aria-selected={tab === "register"}
                className={`login-tab ${tab === "register" ? "is-active" : ""}`}
                onClick={() => { setTab("register"); setError(""); }}
              >Регистрация</button>
            </div>

            <form className="login-form" onSubmit={submit}>
              {tab === "register" && (
                <input
                  className="login-input"
                  type="text"
                  placeholder="Имя (необязательно)"
                  value={firstName}
                  onChange={(e) => setFirstName(e.target.value)}
                  maxLength={64}
                  autoComplete="given-name"
                />
              )}
              <input
                className="login-input"
                type="email"
                placeholder="Email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                autoComplete="email"
                inputMode="email"
                maxLength={255}
              />
              <input
                className="login-input"
                type="password"
                placeholder={tab === "register" ? "Пароль (от 8 символов)" : "Пароль"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                autoComplete={tab === "register" ? "new-password" : "current-password"}
                minLength={8}
                maxLength={128}
              />
              <button
                type="submit"
                className="btn btn--primary login-submit"
                disabled={busy}
              >
                {busy ? "…" : tab === "login" ? "Войти" : "Зарегистрироваться"}
              </button>
            </form>

            <div className="login-divider"><span>или</span></div>

            <button
              type="button"
              className="btn btn--ghost login-tg-btn"
              onClick={startTelegram}
              disabled={busy}
            >
              ✈️ Войти через Telegram
            </button>
            <p className="login-hint">Откроется приложение Telegram, нажми «Start».</p>
          </>
        )}

        {error && <p className="login-error">{error}</p>}

        <a
          className="login-channel"
          href="https://t.me/kmo_ai"
          target="_blank"
          rel="noreferrer"
        >
          📣 Новости проекта — @kmo_ai
        </a>
      </div>
    </div>
  );
}
