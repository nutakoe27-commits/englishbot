/**
 * SchoolCabinetScreen.tsx — кабинет школы (B2B, фаза 2).
 *
 * Доступен учителю/админу школы (role teacher/admin в org_members) — кнопка
 * в Профиле показывается по /api/auth/me → org.role. Backend дополнительно
 * гейтит каждый запрос (403 для остальных).
 *
 * Показывает: сводку школы (места/срок), список учеников со статистикой
 * за текущий месяц, детали ученика (уровень + частые ошибки) по тапу,
 * кнопку выгрузки CSV-отчёта.
 *
 * Обёртка — .sub-screen/.sub-screen__inner (тот же fullscreen-popover
 * паттерн, что SubscribeScreen).
 */

import { useEffect, useState } from "react";
import {
  downloadOrgReport,
  fetchOrgCabinet,
  fetchOrgStudent,
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

function _fmtDate(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString("ru-RU", {
      day: "numeric", month: "short",
    });
  } catch { return iso; }
}

function _name(s: OrgStudentRow): string {
  const n = (s.first_name || "").trim();
  if (n) return n;
  if (s.username) return `@${s.username}`;
  return `Ученик #${s.user_id}`;
}

export function SchoolCabinetScreen({ onClose }: Props) {
  const [data, setData] = useState<OrgCabinet | null>(null);
  const [error, setError] = useState<string>("");
  const [expanded, setExpanded] = useState<number | null>(null);
  const [details, setDetails] = useState<Record<number, OrgStudentDetail | null>>({});
  const [csvBusy, setCsvBusy] = useState(false);

  useLucide(`cab-${data ? data.students.length : "load"}-${expanded}`);

  useEffect(() => {
    let alive = true;
    void (async () => {
      const r = await fetchOrgCabinet();
      if (!alive) return;
      if (!r) { setError("Не удалось загрузить кабинет. Попробуй позже."); return; }
      setData(r);
    })();
    return () => { alive = false; };
  }, []);

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
              <NoteCard padding={16} tone="sage">
                <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  <strong style={{ fontSize: 17 }}>{data.org.name}</strong>
                  <span style={{ fontSize: 13, opacity: 0.75 }}>
                    Мест занято: {data.org.seats_used} / {data.org.seats_total}
                    {" · "}Доступ до {_fmtDate(data.org.valid_until)}
                  </span>
                </div>
              </NoteCard>

              <Button
                variant="secondary"
                fullWidth
                onClick={() => void exportCsv()}
                disabled={csvBusy}
              >
                {csvBusy ? "…" : "⬇ Скачать отчёт за месяц (CSV)"}
              </Button>

              {data.students.length === 0 && (
                <p className="sub-hint">
                  Учеников пока нет — отправь им ссылку-приглашение школы.
                </p>
              )}

              {data.students.length > 0 && (
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  <p className="sub-hint" style={{ margin: 0 }}>
                    Статистика за текущий месяц. Тап по ученику — уровень и
                    частые ошибки.
                  </p>
                  {data.students.map((s) => {
                    const det = details[s.user_id];
                    const isOpen = expanded === s.user_id;
                    return (
                      <NoteCard key={s.user_id} padding={12}>
                        <button
                          type="button"
                          onClick={() => void toggleStudent(s.user_id)}
                          style={{
                            display: "block", width: "100%", textAlign: "left",
                            background: "transparent", border: 0, padding: 0,
                            color: "inherit", font: "inherit", cursor: "pointer",
                            opacity: s.active ? 1 : 0.5,
                          }}
                        >
                          <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                            <strong style={{ fontSize: 15 }}>
                              {_name(s)}{!s.active && " · отключён"}
                            </strong>
                            <span style={{ fontSize: 14, fontWeight: 700 }}>
                              {s.points_month} очк.
                            </span>
                          </div>
                          <div style={{ fontSize: 13, opacity: 0.75, marginTop: 2 }}>
                            🎙 {s.speaking_min} мин · 🎧 {s.listening_min} мин ·
                            {" "}📝 {s.grammar_lessons} · 🔥 {s.streak_days} дн ·
                            {" "}был: {_fmtDate(s.last_practice_date)}
                          </div>
                        </button>

                        {isOpen && (
                          <div style={{ marginTop: 10, paddingTop: 10, borderTop: "1px solid var(--border, rgba(0,0,0,0.1))" }}>
                            {det === undefined && (
                              <span className="sub-hint">Загрузка…</span>
                            )}
                            {det === null && (
                              <span className="sub-hint">Не удалось загрузить детали.</span>
                            )}
                            {det && (
                              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                                <div style={{ fontSize: 13 }}>
                                  Уровень <b>{det.level.level}</b> · всего очков:{" "}
                                  <b>{det.level.lifetime_points}</b>
                                </div>
                                {det.mistakes.length === 0 ? (
                                  <span style={{ fontSize: 13, opacity: 0.7 }}>
                                    Свежих ошибок нет — либо мало занимался, либо молодец. 🙂
                                  </span>
                                ) : (
                                  <>
                                    <div style={{ fontSize: 13, fontWeight: 600 }}>
                                      Частые ошибки (30 дней):
                                    </div>
                                    {det.mistakes.map((m, i) => (
                                      <div key={i} style={{ fontSize: 13, lineHeight: 1.45 }}>
                                        {m.category && (
                                          <span style={{ opacity: 0.6 }}>[{m.category}] </span>
                                        )}
                                        <s style={{ opacity: 0.7 }}>{m.bad}</s>
                                        {" → "}
                                        <b>{m.good}</b>
                                      </div>
                                    ))}
                                  </>
                                )}
                              </div>
                            )}
                          </div>
                        )}
                      </NoteCard>
                    );
                  })}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
