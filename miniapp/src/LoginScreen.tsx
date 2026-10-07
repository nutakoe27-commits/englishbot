/**
 * LoginScreen.tsx — экран входа для веб-версии (вне Telegram).
 *
 * Основной вход: Яндекс ID (OAuth 2.0, миграция 0023) и под ним три иконки
 * VK ID — ВКонтакте, Одноклассники, Mail.ru (миграция 0040). Email/пароль —
 * за отдельной кнопкой: вход через сервисы быстрее и чаще доходит до
 * регистрации. Telegram как способ входа на сайте УБРАН (юр.
 * ограничения РФ); привязка Telegram-аккаунта остаётся в Аккаунте.
 *
 * UI v2: warm cream surface, Source Serif heading, sage CTA для Яндекс,
 * lucide-иконки.
 */

import { useEffect, useState } from "react";
import {
  extractYandexCallback,
  loginNative,
  registerNative,
  startVkFlow,
  startYandexFlow,
  type VkVia,
} from "./auth";
import { VK_VIA_LABEL, VkViaRow } from "./ProviderIcons";
import { Button } from "./ds-react/Button";
import { LogoBox } from "./ds-react/LogoBox";
import { SerifH } from "./ds-react/typography";
import { Icon } from "./ds-react/Icon";
import { useLucide } from "./lucide";
import { isAppMode } from "./appMode";

interface Props {
  onAuthed: () => void;
}

type Tab = "login" | "register";

export function LoginScreen({ onAuthed }: Props) {
  const [error, setError] = useState<string>("");
  const [busy, setBusy] = useState<boolean>(false);
  const [tab, setTab] = useState<Tab>("login");
  const [email, setEmail] = useState<string>("");
  const [password, setPassword] = useState<string>("");
  const [firstName, setFirstName] = useState<string>("");
  // Форма email/пароля свёрнута за кнопкой — основной путь через сервисы.
  const [emailOpen, setEmailOpen] = useState<boolean>(false);

  useLucide(`${tab}-${busy}-${!!error}-${emailOpen}`);

  useEffect(() => {
    const r = extractYandexCallback();
    if (!r) return;
    if (r.jwt && r.mode === "login") {
      onAuthed();
      return;
    }
    if (r.error) {
      setError(_oauthErrorMessage(r.error, r.provider === "vk" ? (r.via ? VK_VIA_LABEL[r.via] : "VK ID") : "Яндекс"));
    }
  }, [onAuthed]);

  const startVk = async (via: VkVia) => {
    if (busy) return;
    setError(""); setBusy(true);
    try {
      const r = await startVkFlow("login", via);
      if (!r) {
        setError(`Не удалось запустить вход через ${VK_VIA_LABEL[via]}. Попробуй ещё раз.`);
        return;
      }
      window.location.href = r.url;
    } finally { setBusy(false); }
  };

  const startYandex = async () => {
    if (busy) return;
    setError(""); setBusy(true);
    try {
      const r = await startYandexFlow("login");
      if (!r) {
        setError("Не удалось запустить вход через Яндекс. Попробуй ещё раз.");
        return;
      }
      window.location.href = r.url;
    } finally { setBusy(false); }
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
    <div className="login-v2">
      <div className="login-v2__card">
        <div className="login-v2__brand">
          <LogoBox size={44} />
          <div className="login-v2__brand-text">
            <span className="login-v2__brand-name">English Tutor</span>
            <span className="login-v2__brand-tag">AI-репетитор</span>
          </div>
        </div>

        <SerifH as="h1" size={28} className="login-v2__title">
          Вход в English Tutor
        </SerifH>
        <p className="login-v2__subtitle">
          Говори, слушай, учи грамматику и слова с AI-репетитором. Войди, чтобы
          сохранять прогресс на любом устройстве.
        </p>

        <button
          type="button"
          className="login-v2__yandex"
          onClick={startYandex}
          disabled={busy}
        >
          <span className="login-v2__yandex-mark" aria-hidden>Я</span>
          <span>Войти через Яндекс ID</span>
        </button>
        <p className="login-v2__alt-caption">или через</p>
        <VkViaRow onPick={(via) => void startVk(via)} disabled={busy} />

        {!emailOpen ? (
          <Button
            variant="ghost"
            fullWidth
            className="login-v2__email-toggle"
            onClick={() => { setEmailOpen(true); setError(""); }}
          >
            Войти по email и паролю
          </Button>
        ) : (
        <>
        <div className="login-v2__divider"><span>по email и паролю</span></div>

        <div className="login-v2__tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "login"}
            className={`login-v2__tab ${tab === "login" ? "is-active" : ""}`}
            onClick={() => { setTab("login"); setError(""); }}
          >Вход</button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "register"}
            className={`login-v2__tab ${tab === "register" ? "is-active" : ""}`}
            onClick={() => { setTab("register"); setError(""); }}
          >Регистрация</button>
        </div>

        <form className="login-v2__form" onSubmit={submit}>
          {tab === "register" && (
            <input
              className="login-v2__input"
              type="text"
              placeholder="Имя (необязательно)"
              value={firstName}
              onChange={(e) => setFirstName(e.target.value)}
              maxLength={64}
              autoComplete="given-name"
            />
          )}
          <input
            className="login-v2__input"
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
            className="login-v2__input"
            type="password"
            placeholder={tab === "register" ? "Пароль (от 8 символов)" : "Пароль"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            autoComplete={tab === "register" ? "new-password" : "current-password"}
            minLength={8}
            maxLength={128}
          />
          <Button type="submit" variant="secondary" fullWidth disabled={busy}>
            {busy ? "…" : tab === "login" ? "Войти" : "Зарегистрироваться"}
          </Button>
        </form>
        </>
        )}

        {error && <p className="login-v2__error">{error}</p>}

        {/* Внешняя ссылка на сайте уместна, а в установленном приложении
            открывает вкладку браузера с адресной строкой — и приложение
            перестаёт выглядеть законченным. Внутри её не показываем. */}
        {!isAppMode() && (
          <a
            className="login-v2__channel"
            href="https://t.me/kmo_ai"
            target="_blank"
            rel="noreferrer"
          >
            <Icon name="megaphone" size={14} /> Новости проекта — @kmo_ai
          </a>
        )}
      </div>
    </div>
  );
}

export function _oauthErrorMessage(code: string, service = "Яндекс"): string {
  switch (code) {
    case "state_invalid":
      return "Ссылка устарела. Попробуй войти ещё раз.";
    case "exchange_failed":
    case "userinfo_failed":
      return `Не получилось проверить аккаунт (${service}). Попробуй ещё раз.`;
    case "access_denied":
      return `Доступ к аккаунту (${service}) не разрешён.`;
    case "identity_conflict":
      return `Этот аккаунт (${service}) уже привязан к другому профилю, и у обоих есть свои способы входа. Сначала отвяжи лишний способ в одном из профилей.`;
    default:
      return `Не удалось войти через ${service}. Попробуй ещё раз.`;
  }
}
