/**
 * LevelProgressBar — шкала уровня сверху главного экрана.
 *
 * Очки начисляются за активность (1/мин разговора, 1/мин подкаста,
 * 5 за урок грамматики). Уровень растёт от lifetime-очков, не сбрасывается.
 * Данные: GET /api/me/level.
 */

import { useEffect, useState } from "react";
import WebApp from "@twa-dev/sdk";
import { ProgressBar } from "./ds-react/ProgressBar";
import { Icon } from "./ds-react/Icon";
import { useLucide } from "./lucide";

const API_BASE =
  (import.meta.env.VITE_API_BASE as string | undefined) ||
  "https://api-english.krichigindocs.ru";

interface LevelData {
  level: number;
  lifetime_points: number;
  into_level: number;
  level_span: number;
  progress_pct: number;
}

export function LevelProgressBar() {
  const [data, setData] = useState<LevelData | null>(null);

  useLucide(data ? `lvl-${data.level}` : "lvl-loading");

  useEffect(() => {
    const initData = WebApp.initData || "";
    const url = initData
      ? `${API_BASE}/api/me/level?init_data=${encodeURIComponent(initData)}`
      : `${API_BASE}/api/me/level`;
    fetch(url)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (d) setData(d as LevelData); })
      .catch(() => { /* не критично */ });
  }, []);

  if (!data) return null;

  return (
    <div className="lvl-bar">
      <div className="lvl-bar__top">
        <span className="lvl-bar__badge">
          <Icon name="star" size={13} /> Уровень {data.level}
        </span>
        <span className="lvl-bar__counter">
          {data.into_level} / {data.level_span} очков
        </span>
      </div>
      <ProgressBar value={data.into_level} max={data.level_span} tone="accent" height={8} />
      <div className="lvl-bar__hint">
        До уровня {data.level + 1} — ещё {Math.max(0, data.level_span - data.into_level)} очков
      </div>
    </div>
  );
}
