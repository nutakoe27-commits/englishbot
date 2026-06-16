/**
 * AccountSheet.tsx — «Аккаунт»: привязки входа (Telegram/Google), ссылка на
 * канал @kmo_ai, выход. Открывается из угла экрана выбора режима.
 *
 * Идея: привязав второй способ входа (Google), пользователь не потеряет
 * прогресс, если Telegram заблокируют.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import WebApp from "@twa-dev/sdk";
import {
  BOT_USERNAME,
  GOOGLE_CLIENT_ID,
  fetchMe,
  linkGoogle,
  linkTelegramWidget,
  logout,
  type MeInfo,
} from "./auth";

interface Props {
  onClose: () => void;
  onLoggedOut: () => void;
}

const PROVIDER_LABEL: Record<string, string> = {
  telegram: "Telegram",
  google: "Google",
  apple: "Apple",
};

export function AccountSheet({ onClose, onLoggedOut }: Props) {
  const [me, setMe] = useState<MeInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState<string>("");
  const googleBoxRef = useRef<HTMLDivElement | null>(null);
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
  const hasGoogle = linked.has("google");
  const hasTelegram = linked.has("telegram");

  // ── Кнопка привязки Google (GIS) ───────────────────────────────────────
  useEffect(() => {
    if (loading || hasGoogle || !GOOGLE_CLIENT_ID || !googleBoxRef.current) return;

    const onCredential = async (resp: { credential?: string }) => {
      if (!resp?.credential) return;
      setMsg("");
      const r = await linkGoogle(resp.credential);
      if (r.ok) { setMsg("Google привязан ✓"); void reload(); }
      else if (r.error === "taken") setMsg("Этот Google уже привязан к другому аккаунту.");
      else setMsg("Не удалось привязать Google.");
    };

    const render = () => {
      if (!window.google?.accounts?.id || !googleBoxRef.current) return;
      window.google.accounts.id.initialize({
        client_id: GOOGLE_CLIENT_ID,
        callback: onCredential,
      });
      window.google.accounts.id.renderButton(googleBoxRef.current, {
        theme: "filled_black", size: "large", shape: "pill",
        text: "continue_with", width: 260,
      });
    };

    if (window.google?.accounts?.id) render();
    else {
      const ex = document.getElementById("gsi-script");
      if (!ex) {
        const s = document.createElement("script");
        s.id = "gsi-script";
        s.src = "https://accounts.google.com/gsi/client";
        s.async = true; s.defer = true; s.onload = render;
        document.head.appendChild(s);
      } else ex.addEventListener("load", render);
    }
  }, [loading, hasGoogle, reload]);

  // ── Кнопка привязки Telegram (Login Widget, только на вебе) ────────────
  useEffect(() => {
    if (loading || hasTelegram || inTelegram || !BOT_USERNAME || !tgBoxRef.current) return;

    window.onTelegramAuth = async (user) => {
      setMsg("");
      const r = await linkTelegramWidget(user);
      if (r.ok) { setMsg("Telegram привязан ✓"); void reload(); }
      else if (r.error === "taken") setMsg("Этот Telegram уже привязан к другому аккаунту.");
      else setMsg("Не удалось привязать Telegram.");
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
                Привяжи второй способ входа — не потеряешь прогресс, если
                Telegram заблокируют.
              </p>

              <div className="acc-list">
                {(["telegram", "google", "apple"] as const).map((p) => {
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

              {!hasGoogle && GOOGLE_CLIENT_ID && (
                <div className="acc-link-block">
                  <div className="acc-link-title">Привязать Google</div>
                  <div ref={googleBoxRef} className="acc-gbtn" />
                </div>
              )}

              {!hasTelegram && !inTelegram && BOT_USERNAME && (
                <div className="acc-link-block">
                  <div className="acc-link-title">Привязать Telegram</div>
                  <div ref={tgBoxRef} className="acc-tgbtn" />
                </div>
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
