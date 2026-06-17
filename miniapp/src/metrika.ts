/**
 * metrika.ts — обёртки над Yandex.Metrika (counter 109924904).
 *
 * Сам счётчик инициализируется в index.html (тег <script>). Здесь — типизированные
 * хелперы для целей и виртуальных hit'ов. Если ym ещё не подгрузился (adblock,
 * сетевая проблема), вызовы no-op'нут.
 */

const COUNTER_ID = 109924904;

type YmFn = (counterId: number, action: string, ...args: unknown[]) => void;

function ym(): YmFn | null {
  // @ts-expect-error — ym инжектится глобально из index.html
  const fn = typeof window !== "undefined" ? window.ym : undefined;
  return typeof fn === "function" ? fn : null;
}

/** Виртуальный pageview — для SPA-навигации (открытие модалок и экранов). */
export function ymHit(url: string, title?: string): void {
  const fn = ym();
  if (!fn) return;
  fn(COUNTER_ID, "hit", url, {
    title: title || (typeof document !== "undefined" ? document.title : ""),
    referer: typeof document !== "undefined" ? document.referrer : "",
  });
}

/** Достижение цели. Параметры опциональны и попадут в отчёт «Параметры визитов». */
export function ymReachGoal(goalName: string, params?: Record<string, unknown>): void {
  const fn = ym();
  if (!fn) return;
  if (params) fn(COUNTER_ID, "reachGoal", goalName, params);
  else fn(COUNTER_ID, "reachGoal", goalName);
}
