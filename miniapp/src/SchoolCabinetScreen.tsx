/**
 * SchoolCabinetScreen.tsx — кабинет школы (B2B).
 *
 * Доступен учителю/админу школы (role teacher/admin в org_members) — кнопка
 * в Профиле показывается по /api/auth/me → org.role. Backend гейтит каждый
 * запрос (403 для остальных).
 *
 * Приоритет экрана: сначала ответ на вопрос «как дела у группы и кто
 * отстаёт», потом сами ученики, и только в конце — служебное (ссылки,
 * оплата, отчёт). Ссылки нужны один раз при запуске, список учеников —
 * каждый день, поэтому редкое убрано под раскрывающийся блок.
 *
 * Период по умолчанию — неделя: календарный месяц в первых числах
 * показывает нули у всех и выглядит как «сервисом не пользуются».
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  downloadOrgReport,
  fetchOrgAddonQuote,
  fetchOrgCabinet,
  fetchOrgStudent,
  orgCheckout,
  setOrgStudentActive,
  type OrgAddonQuote,
  type OrgCabinet,
  type OrgStudentDetail,
  type OrgStudentRow,
} from "./auth";
import { Button } from "./ds-react/Button";
import { IconButton } from "./ds-react/IconButton";
import { NoteCard } from "./ds-react/NoteCard";
import { SerifH } from "./ds-react/typography";
import { useLucide } from "./lucide";

interface Props {
  onClose: () => void;
}

type SortKey = "practice" | "points" | "name";

function _fmtDate(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
  } catch { return iso; }
}

/** «сегодня» читается мгновенно, «9 авг» требует вспомнить число. */
function _lastSeen(s: OrgStudentRow): string {
  const d = s.days_since_practice;
  if (d === null || d === undefined) return "не начинал";
  if (d <= 0) return "сегодня";
  if (d === 1) return "вчера";
  if (d < 7) return `${d} дн. назад`;
  if (d < 14) return "неделю назад";
  if (d < 31) return `${Math.floor(d / 7)} нед. назад`;
  return "больше месяца";
}

function _name(s: OrgStudentRow): string {
  const n = (s.first_name || "").trim();
  if (n) return n;
  if (s.username) return `@${s.username}`;
  return `Ученик #${s.user_id}`;
}

function _plural(n: number, one: string, few: string, many: string): string {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few;
  return many;
}

/** Часы и минуты: «6 ч 20 мин» вместо «380 мин». */
function _dur(min: number): string {
  if (min < 60) return `${min} мин`;
  const h = Math.floor(min / 60);
  const m = min % 60;
  return m ? `${h} ч ${m} мин` : `${h} ч`;
}

/** Компактная длительность для плитки: «14 ч» вместо «14 ч 2 мин»,
 *  иначе значение переносится на две строки и плитки разъезжаются. */
function _durShort(min: number): string {
  if (min < 60) return `${min} мин`;
  return `${Math.round(min / 60)} ч`;
}

/** Изменение к прошлому периоду: то, ради чего сводка вообще нужна.
 *  Единицу показываем всегда — «↑ 152» без неё ничего не значит. */
function Delta({ now, prev, kind }: { now: number; prev: number; kind: "count" | "minutes" }) {
  if (prev === 0 && now === 0) return null;
  const diff = now - prev;
  if (diff === 0) return <span className="cab-delta cab-delta--flat">без изменений</span>;
  const abs = Math.abs(diff);
  const text = kind === "minutes"
    ? (abs >= 60 ? `${Math.round(abs / 60)} ч` : `${abs} мин`)
    : `${abs}`;
  const up = diff > 0;
  return (
    <span className={`cab-delta ${up ? "cab-delta--up" : "cab-delta--down"}`}>
      {up ? "↑" : "↓"} {text}
    </span>
  );
}

export function SchoolCabinetScreen({ onClose }: Props) {
  const [data, setData] = useState<OrgCabinet | null>(null);
  const [error, setError] = useState<string>("");
  const [period, setPeriod] = useState<string>("7");
  const [loading, setLoading] = useState(true);
  const [sort, setSort] = useState<SortKey>("practice");
  const [expanded, setExpanded] = useState<number | null>(null);
  const [details, setDetails] = useState<Record<number, OrgStudentDetail | null>>({});
  const [csvBusy, setCsvBusy] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const [memberBusy, setMemberBusy] = useState(false);
  // Служебный блок свёрнут: ссылки нужны один раз, ученики — каждый день.
  const [manageOpen, setManageOpen] = useState(false);
  const [billOpen, setBillOpen] = useState<null | "renew" | "seats">(null);
  const [renewMonths, setRenewMonths] = useState<number>(6);
  const [renewSeats, setRenewSeats] = useState<number>(0);
  const [addSeats, setAddSeats] = useState<number>(5);
  const [addQuote, setAddQuote] = useState<OrgAddonQuote | null>(null);
  const [billBusy, setBillBusy] = useState(false);

  useLucide(`cab-${data ? data.students.length : "load"}-${expanded}-${manageOpen}`);

  const load = useCallback(async (p: string) => {
    setLoading(true);
    const r = await fetchOrgCabinet(p);
    setLoading(false);
    if (!r) { setError("Не удалось загрузить кабинет. Попробуйте позже."); return; }
    setError("");
    setData(r);
  }, []);

  useEffect(() => { void load(period); }, [load, period]);

  const students = data?.students ?? [];

  // Требующие внимания — то, ради чего кабинет открывают.
  const attention = useMemo(
    () => students.filter((s) => s.active && s.status !== "ok"),
    [students],
  );

  const sorted = useMemo(() => {
    const arr = [...students];
    arr.sort((a, b) => {
      // Отключённые всегда в конце — они не часть текущей картины.
      if (a.active !== b.active) return a.active ? -1 : 1;
      if (sort === "name") return _name(a).localeCompare(_name(b), "ru");
      if (sort === "points") return b.points_month - a.points_month;
      return b.practice_min - a.practice_min;
    });
    return arr;
  }, [students, sort]);

  // Масштаб для полосок: сравниваем учеников между собой внутри группы.
  const maxPractice = useMemo(
    () => Math.max(1, ...students.map((s) => s.practice_min)),
    [students],
  );

  const toggleStudent = async (userId: number) => {
    if (expanded === userId) { setExpanded(null); return; }
    setExpanded(userId);
    if (details[userId] === undefined) {
      const d = await fetchOrgStudent(userId);
      setDetails((m) => ({ ...m, [userId]: d }));
    }
  };

  const exportCsv = async () => {
    if (csvBusy) return;
    setCsvBusy(true);
    try {
      const ok = await downloadOrgReport();
      if (!ok) setError("Не удалось скачать отчёт.");
    } finally { setCsvBusy(false); }
  };

  const copyLink = async (key: string, value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(key);
      setTimeout(() => setCopied(null), 1800);
    } catch { /* clipboard недоступен (старый WebView) */ }
  };

  // Пересчёт докупки мест — сумма считается на сервере (остаток срока).
  useEffect(() => {
    if (billOpen !== "seats" || addSeats < 1) { setAddQuote(null); return; }
    let alive = true;
    const t = setTimeout(() => {
      void (async () => {
        const q = await fetchOrgAddonQuote(addSeats);
        if (alive) setAddQuote(q);
      })();
    }, 350);
    return () => { alive = false; clearTimeout(t); };
  }, [billOpen, addSeats]);

  const startBilling = async (kind: "renew" | "seats") => {
    if (!data || billBusy) return;
    setBillBusy(true);
    try {
      const r = await orgCheckout({
        kind,
        org_id: data.org.id,
        school_name: data.org.name,
        seats: kind === "renew" ? (renewSeats || data.org.seats_total) : addSeats,
        months: kind === "renew" ? renewMonths : 0,
      });
      if (r.ok && r.confirmation_url) { window.location.href = r.confirmation_url; return; }
      setError(
        r.error === "email_required"
          ? "Нужен email для чека — добавьте его в Профиле."
          : "Не получилось создать счёт. Попробуйте ещё раз.",
      );
    } finally { setBillBusy(false); }
  };

  const toggleStudentActive = async (s: OrgStudentRow) => {
    if (memberBusy) return;
    const sure = s.active
      ? window.confirm(
          `Исключить «${_name(s)}» из школы? Доступ у ученика пропадёт, ` +
          "место освободится. Вернуть можно в любой момент.",
        )
      : true;
    if (!sure) return;
    setMemberBusy(true);
    try {
      const ok = await setOrgStudentActive(s.user_id, !s.active);
      if (!ok) { setError("Не получилось изменить статус ученика."); return; }
      await load(period);
    } finally { setMemberBusy(false); }
  };

  const renderStudent = (s: OrgStudentRow, compact = false) => {
    const det = details[s.user_id];
    const isOpen = expanded === s.user_id;
    const share = Math.round((s.practice_min / maxPractice) * 100);
    return (
      <div
        key={`${compact ? "a" : "s"}-${s.user_id}`}
        className={`cab-row ${s.active ? "" : "is-off"} ${isOpen ? "is-open" : ""}`}
      >
        <button type="button" className="cab-row__main" onClick={() => void toggleStudent(s.user_id)}>
          <span className={`cab-dot cab-dot--${s.status}`} aria-hidden />
          <span className="cab-row__name">
            {_name(s)}
            {!s.active && <span className="cab-row__off">отключён</span>}
          </span>
          <span className="cab-row__metric">{_dur(s.practice_min)}</span>
        </button>
        <div className="cab-row__bar" aria-hidden>
          <span style={{ width: `${s.practice_min > 0 ? Math.max(4, share) : 0}%` }} />
        </div>
        <div className="cab-row__meta">
          <span>{s.points_month} очк.</span>
          {s.streak_days > 0 && <span>🔥 {s.streak_days}</span>}
          <span className={s.status === "ok" ? "" : "cab-row__meta--warn"}>
            {_lastSeen(s)}
          </span>
        </div>

        {isOpen && (
          <div className="cab-row__details">
            {det === undefined && <span className="sub-hint">Загрузка…</span>}
            {det === null && <span className="sub-hint">Не удалось загрузить детали.</span>}
            {det && (
              <>
                <div className="cab-detail__modes">
                  <span>Разговор <b>{s.speaking_min} мин</b></span>
                  <span>Подкасты <b>{s.listening_min} мин</b></span>
                  <span>Уроки <b>{s.grammar_lessons}</b></span>
                </div>
                <div className="cab-detail__level">
                  Уровень <b>{det.level.level}</b> · всего очков <b>{det.level.lifetime_points}</b>
                </div>
                {det.mistakes.length === 0 ? (
                  <span className="sub-hint">
                    Свежих ошибок нет — либо мало занимался, либо молодец 🙂
                  </span>
                ) : (
                  <div className="cab-detail__mistakes">
                    <div className="cab-detail__mistakes-title">Частые ошибки за 30 дней</div>
                    {det.mistakes.map((m, i) => (
                      <div key={i} className="cab-mistake">
                        <s>{m.bad}</s> → <b>{m.good}</b>
                        {m.category && <span className="cab-mistake__cat">{m.category}</span>}
                      </div>
                    ))}
                  </div>
                )}
                <Button variant="ghost" onClick={() => void toggleStudentActive(s)} disabled={memberBusy}>
                  {memberBusy ? "…" : s.active ? "Исключить из школы" : "Вернуть в школу"}
                </Button>
              </>
            )}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="sub-screen">
      <div className="sub-screen__inner">
        <header className="sub-screen__top">
          <SerifH as="h1" size={28}>Кабинет школы</SerifH>
          <IconButton icon="x" variant="surface" size="md" label="Закрыть" onClick={onClose} />
        </header>

        <div className="sub-screen__body">
          {error && <p className="sub-error">{error}</p>}
          {!data && !error && <p className="sub-hint">Загрузка…</p>}

          {data && (
            <>
              {/* ─── Сводка: главное за 3 секунды ─────────────────── */}
              <NoteCard padding={16} tone="sage">
                <div className="cab-head">
                  <strong className="cab-head__name">
                    {data.org.name}
                    {data.org.is_trial && <span className="cab-trial">пробный период</span>}
                  </strong>
                  <span className="cab-head__sub">
                    {data.org.seats_used} / {data.org.seats_total} мест ·
                    {" "}доступ до {_fmtDate(data.org.valid_until)}
                    {data.org.days_left <= 14 && (
                      <b> · осталось {data.org.days_left} {_plural(data.org.days_left, "день", "дня", "дней")}</b>
                    )}
                  </span>
                </div>
              </NoteCard>

              <div className="cab-periods" role="tablist" aria-label="Период">
                {data.period.options.map((o) => (
                  <button
                    key={o.key}
                    type="button"
                    role="tab"
                    aria-selected={o.key === period}
                    className={`cab-period ${o.key === period ? "is-active" : ""}`}
                    onClick={() => setPeriod(o.key)}
                  >
                    {o.label}
                  </button>
                ))}
              </div>

              <div className={`cab-kpis ${loading ? "is-loading" : ""}`}>
                <div className="cab-kpi">
                  <span className="cab-kpi__value">
                    {data.summary.active_students}
                    <i>/ {data.summary.students_total}</i>
                  </span>
                  <span className="cab-kpi__label">занимались</span>
                  <Delta now={data.summary.active_students} prev={data.summary.active_students_prev} kind="count" />
                </div>
                <div className="cab-kpi">
                  <span className="cab-kpi__value" title={_dur(data.summary.practice_min)}>
                    {_durShort(data.summary.practice_min)}
                  </span>
                  <span className="cab-kpi__label">практики</span>
                  <Delta now={data.summary.practice_min} prev={data.summary.practice_min_prev} kind="minutes" />
                </div>
                <div className={`cab-kpi ${attention.length ? "cab-kpi--warn" : ""}`}>
                  <span className="cab-kpi__value">{attention.length}</span>
                  <span className="cab-kpi__label">требуют внимания</span>
                </div>
              </div>

              {/* ─── Кто отстаёт ──────────────────────────────────── */}
              {attention.length > 0 && (
                <div className="cab-attention">
                  <div className="cab-attention__head">
                    Не занимались неделю и дольше — {attention.length}
                  </div>
                  <div className="cab-attention__list">
                    {attention.map((s) => renderStudent(s, true))}
                  </div>
                  <p className="sub-hint" style={{ margin: 0 }}>
                    Напомните им на занятии — обычно достаточно одного разговора.
                  </p>
                </div>
              )}

              {/* ─── Список учеников ──────────────────────────────── */}
              {students.length === 0 ? (
                <NoteCard padding={16}>
                  <div className="cab-empty">
                    <b>Учеников пока нет</b>
                    <p>
                      Отправьте ссылку-приглашение в чат группы — ученики
                      подключатся сами и появятся здесь.
                    </p>
                    <Button variant="primary" onClick={() => setManageOpen(true)}>
                      Взять ссылку
                    </Button>
                  </div>
                </NoteCard>
              ) : (
                <>
                  <div className="cab-listhead">
                    <span className="cab-listhead__title">
                      Ученики · {students.length}
                    </span>
                    <div className="cab-sort">
                      {([
                        ["practice", "по практике"],
                        ["points", "по очкам"],
                        ["name", "по имени"],
                      ] as [SortKey, string][]).map(([k, label]) => (
                        <button
                          key={k}
                          type="button"
                          className={`cab-sort__btn ${sort === k ? "is-active" : ""}`}
                          onClick={() => setSort(k)}
                        >
                          {label}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="cab-list">{sorted.map((s) => renderStudent(s))}</div>
                </>
              )}

              {/* ─── Служебное: ссылки, оплата, отчёт ─────────────── */}
              <div className="cab-manage">
                <button
                  type="button"
                  className="cab-manage__toggle"
                  aria-expanded={manageOpen}
                  onClick={() => setManageOpen((v) => !v)}
                >
                  Управление школой
                  <span className={`cab-manage__chev ${manageOpen ? "is-open" : ""}`}>▾</span>
                </button>

                {manageOpen && (
                  <div className="cab-manage__body">
                    <div className="cab-links">
                      <div className="cab-links__row">
                        <span className="cab-links__label">
                          Ссылка для учеников — каждый занимает место
                        </span>
                        <div className="cab-links__btns">
                          <Button variant="primary" fullWidth
                            onClick={() => void copyLink("s-tg", data.org.invite_link)}>
                            {copied === "s-tg" ? "Скопировано ✓" : "Telegram"}
                          </Button>
                          <Button variant="secondary" fullWidth
                            onClick={() => void copyLink("s-web", data.org.invite_link_web)}>
                            {copied === "s-web" ? "Скопировано ✓" : "Сайт"}
                          </Button>
                        </div>
                      </div>
                      <div className="cab-links__row">
                        <span className="cab-links__label">
                          Ссылка для преподавателей — даёт этот кабинет, место не занимает
                        </span>
                        <div className="cab-links__btns">
                          <Button variant="secondary" fullWidth
                            onClick={() => void copyLink("t-tg", data.org.teacher_link)}>
                            {copied === "t-tg" ? "Скопировано ✓" : "Telegram"}
                          </Button>
                          <Button variant="secondary" fullWidth
                            onClick={() => void copyLink("t-web", data.org.teacher_link_web)}>
                            {copied === "t-web" ? "Скопировано ✓" : "Сайт"}
                          </Button>
                        </div>
                      </div>
                    </div>

                    <div className="cab-bill">
                      <div className="cab-bill__tabs">
                        <button type="button"
                          className={`cab-bill__tab ${billOpen === "renew" ? "is-active" : ""}`}
                          onClick={() => setBillOpen(billOpen === "renew" ? null : "renew")}>
                          Продлить доступ
                        </button>
                        <button type="button"
                          className={`cab-bill__tab ${billOpen === "seats" ? "is-active" : ""}`}
                          onClick={() => setBillOpen(billOpen === "seats" ? null : "seats")}>
                          Добавить мест
                        </button>
                      </div>

                      {billOpen === "renew" && (
                        <div className="cab-bill__body">
                          <div className="cab-bill__field">
                            <span className="cab-bill__label">Срок</span>
                            <div className="cab-bill__months">
                              {[3, 6, 12].map((m) => (
                                <button key={m} type="button"
                                  className={`sch-month ${m === renewMonths ? "is-active" : ""}`}
                                  onClick={() => setRenewMonths(m)}>
                                  {m} мес
                                </button>
                              ))}
                            </div>
                          </div>
                          <div className="cab-bill__field">
                            <span className="cab-bill__label">
                              Мест после продления (сейчас {data.org.seats_total})
                            </span>
                            <input type="number" className="sch-input" min={2} max={5000}
                              value={renewSeats || data.org.seats_total}
                              onChange={(e) => setRenewSeats(parseInt(e.target.value, 10) || 0)} />
                          </div>
                          <p className="sub-hint" style={{ margin: 0 }}>
                            Оставшиеся дни не сгорают — новый срок прибавляется к текущему.
                          </p>
                          <Button variant="primary" fullWidth disabled={billBusy}
                            onClick={() => void startBilling("renew")}>
                            {billBusy ? "Готовим счёт…" : "Перейти к оплате"}
                          </Button>
                        </div>
                      )}

                      {billOpen === "seats" && (
                        <div className="cab-bill__body">
                          <div className="cab-bill__field">
                            <span className="cab-bill__label">Сколько мест добавить</span>
                            <input type="number" className="sch-input" min={1} max={1000}
                              value={addSeats}
                              onChange={(e) => setAddSeats(Math.max(1, parseInt(e.target.value, 10) || 1))} />
                          </div>
                          {addQuote && !addQuote.expired && (
                            <div className="cab-bill__total">
                              <b>{addQuote.total_rub.toLocaleString("ru-RU")} ₽</b>
                              <span>
                                за {addSeats} мест до конца срока ({addQuote.remaining_days} дн.)
                                {addQuote.volume_discount_pct > 0 && ` · скидка ${addQuote.volume_discount_pct}%`}
                              </span>
                            </div>
                          )}
                          {addQuote?.expired && (
                            <p className="sub-hint" style={{ margin: 0 }}>
                              Срок доступа истёк — сначала продлите его.
                            </p>
                          )}
                          <p className="sub-hint" style={{ margin: 0 }}>
                            Платите только за оставшиеся дни оплаченного периода.
                          </p>
                          <Button variant="primary" fullWidth
                            disabled={billBusy || !addQuote || !!addQuote?.expired}
                            onClick={() => void startBilling("seats")}>
                            {billBusy ? "Готовим счёт…" : "Перейти к оплате"}
                          </Button>
                        </div>
                      )}
                    </div>

                    <Button variant="secondary" fullWidth onClick={() => void exportCsv()} disabled={csvBusy}>
                      {csvBusy ? "…" : "Скачать отчёт за месяц (CSV)"}
                    </Button>
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
