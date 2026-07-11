/**
 * LeaderboardScreen — вкладка «Лидерборд».
 *
 * Топ-5 участников по очкам за текущий месяц + строка самого юзера с его
 * местом и очками. Обновляется каждый календарный месяц (1 числа).
 * Данные: GET /api/leaderboard.
 */

import { useEffect, useState } from "react";
import WebApp from "@twa-dev/sdk";
import { SerifH } from "./ds-react/typography";
import { Icon } from "./ds-react/Icon";
import { useLucide } from "./lucide";

const API_BASE =
  (import.meta.env.VITE_API_BASE as string | undefined) ||
  "https://api-english.krichigindocs.ru";

interface Row {
  rank: number;
  name: string;
  points: number;
  is_me: boolean;
}
interface LbData {
  month: string;
  top: Row[];
  me: { rank: number; points: number; name: string };
  total: number;
  // B2B: имя школы — ученик видит лидерборд только своей школы.
  org_name?: string | null;
}

const MEDALS = ["🥇", "🥈", "🥉"];

function _monthLabel(ym: string): string {
  try {
    const d = new Date(ym + "-01");
    return d.toLocaleDateString("ru-RU", { month: "long", year: "numeric" });
  } catch { return ym; }
}

export function LeaderboardScreen() {
  const [data, setData] = useState<LbData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useLucide(data ? `lb-${data.top.length}` : "lb-loading");

  useEffect(() => {
    const initData = WebApp.initData || "";
    const url = initData
      ? `${API_BASE}/api/leaderboard?init_data=${encodeURIComponent(initData)}`
      : `${API_BASE}/api/leaderboard`;
    fetch(url)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d) => setData(d as LbData))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  const meInTop = !!data?.top.some((r) => r.is_me);

  return (
    <div className="lb">
      <header className="lb__top">
        <div className="lb__title-wrap">
          <SerifH as="h1" size={28}>
            {data?.org_name ? "Лидерборд школы" : "Лидерборд"}
          </SerifH>
          {data && (
            <span className="lb__sub">
              {data.org_name ? `${data.org_name} · ` : ""}
              {_monthLabel(data.month)} · обновляется 1 числа
            </span>
          )}
        </div>
        <Icon name="trophy" size={26} />
      </header>

      {error && <p className="lb__empty">Не удалось загрузить: {error}</p>}
      {!error && !data && <p className="lb__loading">Загружаем…</p>}

      {data && (
        <>
          {data.top.length === 0 ? (
            <p className="lb__empty">
              В этом месяце ещё нет активности. Позанимайся — и попадёшь в топ! 🚀
            </p>
          ) : (
            <div className="lb__list">
              {data.top.map((r) => (
                <div
                  key={r.rank}
                  className={`lb-row ${r.rank === 1 ? "lb-row--top1" : ""} ${r.is_me ? "is-me" : ""}`}
                >
                  <span className={`lb-row__rank ${r.rank <= 3 ? "lb-row__rank--medal" : ""}`}>
                    {r.rank <= 3 ? MEDALS[r.rank - 1] : r.rank}
                  </span>
                  <span className="lb-row__name">
                    {r.name}{r.is_me ? " (ты)" : ""}
                  </span>
                  <span className="lb-row__pts">
                    {r.points} <span>очк.</span>
                  </span>
                </div>
              ))}
            </div>
          )}

          {/* Строка самого юзера — если не в топ-3 */}
          {!meInTop && (
            <>
              <div className="lb__divider" />
              <div className="lb__me-label">Твоё место</div>
              <div className="lb-row is-me">
                <span className="lb-row__rank">
                  {data.me.rank > 0 ? data.me.rank : "—"}
                </span>
                <span className="lb-row__name">{data.me.name} (ты)</span>
                <span className="lb-row__pts">
                  {data.me.points} <span>очк.</span>
                </span>
              </div>
              {data.me.rank === 0 && (
                <p className="lb__sub" style={{ textAlign: "center" }}>
                  Позанимайся в этом месяце, чтобы попасть в рейтинг.
                </p>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}
