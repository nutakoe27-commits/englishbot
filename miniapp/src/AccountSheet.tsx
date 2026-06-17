/**
 * AccountSheet.tsx — «Аккаунт»: привязки входа (Telegram), ссылка на канал
 * @kmo_ai, выход. Открывается из угла экрана выбора режима.
 *
 * Google/Apple убраны (миграция 0021). Нативная регистрация (email+password)
 * добавится в PR-2 этой серии, VK ID — позже.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import WebApp from "@twa-dev/sdk";
import {
  BOT_USERNAME,
  fetchMe,
  linkTelegramWidget,
  logout,
  setPassword,
  type MeInfo,
} from "./auth";

interface Props {
  onClose: () => void;
  onLoggedOut: () => void;
}

const PROVIDER_LABEL: Record<string, string> = {
  telegram: "Telegram",
  native: "Email/пароль",
  vk: "VK ID",
};

export function AccountSheet({ onClose, onLoggedOut }: Props) {
  const [me, setMe] = useState<MeInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState<string>("");
  const tgBoxRef = useRef<HTMLDivElement | null>(null);

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

  // ── Кнопка привязки Telegram (Login Widget, только на вебе) ────────────
  useEffect(() => {
    if (loading || hasTelegram || inTelegram || !BOT_USERNAME || !tgBoxRef.current) return;

    window.onTelegramAuth = async (user) => {
      setMsg("");
      const r = await linkTelegramWidget(user);
      if (r.ok) {
        setMsg(
          r.merged
            ? "Аккаунты объединены ✓ — данные сохранены."
            : "Telegram привязан ✓",
        );
        void reload();
      } else {
        setMsg("Не удалось привязать Telegram.");
      }
    };

    const box = tgBoxRef.current;
    if (!box.querySelector("script")) {
      const s = document.createElement("script");
      s.src = "https://telegram.org/js/telegram-widget.js?22";
      s.async = true;
      s.setAttribute("data-telegram-login", BOT_USERNAME);
      s.setAttribute("data-size", "large");
      s.setAttribute("data-radius", "12");
      s.setAttribute("data-onauth", "onTelegramAuth(user)");
      box.appendChild(s);
    }
    return () => { window.onTelegramAuth = undefined; };
  }, [loading, hasTelegram, inTelegram, reload]);

  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <div className="sheet" onClick={(e) => e.stopPropagation()}>
        <header className="sheet__header">
          <h2 className="sheet__title">Аккаунт</h2>
          <button className="sheet__close" onClick={onClose} aria-label="Закрыть">✕</button>
        </header>

        <div className="sheet__content">
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

              <div className="acc-list">
                {(["telegram", "native", "vk"] as const).map((p) => {
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

              {!hasTelegram && !inTelegram && BOT_USERNAME && (
                <div className="acc-link-block">
                  <div className="acc-link-title">Привязать Telegram</div>
                  <div ref={tgBoxRef} className="acc-tgbtn" />
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

              {msg && <p className="acc-msg">{msg}</p>}

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

        <footer className="sheet__footer">
          {!inTelegram && (
            <button
              type="button"
              className="btn btn--ghost"
              onClick={() => { logout(); onLoggedOut(); }}
            >
              Выйти
            </button>
          )}
          <button type="button" className="btn btn--primary" onClick={onClose}>
            Готово
          </button>
        </footer>
      </div>
    </div>
  );
}
