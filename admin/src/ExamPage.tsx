// ExamPage.tsx — админка подготовки к ЕГЭ: банк заданий, генерация, модерация.
//
// Поток модератора: «Сгенерировать» → задания попадают в очередь review с
// оценкой quality (доля совпадений ключа со «слепым» решением второй
// моделью) → открыть задание → увидеть его в формате КИМ, ключ, разбор и
// расхождения проверки → «Опубликовать» / «В брак» / поправить JSON.

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  type ExamJob,
  type ExamStats,
  type ExamTaskBrief,
  type ExamTaskFull,
} from "./api";
import { S, colors } from "./ui";
import { useIsMobile } from "./useIsMobile";

const TYPE_LABEL: Record<string, string> = {
  gram_form: "Грамматика (форма слова)",
  word_form: "Словообразование",
  lex_mcq: "Лексика (выбор из 4)",
};

const STATUS_LABEL: Record<string, string> = {
  draft: "черновик",
  review: "на модерации",
  published: "опубликовано",
  retired: "снято",
};

const STATUS_BG: Record<string, string> = {
  draft: "#f3f4f6",
  review: colors.warningBg,
  published: colors.successBg,
  retired: colors.dangerBg,
};

function qualityBadge(q: number | null | undefined) {
  if (q === null || q === undefined) return <span style={{ ...S.badge, backgroundColor: "#f3f4f6" }}>—</span>;
  const bg = q >= 100 ? colors.successBg : q >= 70 ? colors.warningBg : colors.dangerBg;
  return <span style={{ ...S.badge, backgroundColor: bg }}>{q}%</span>;
}

function statusBadge(st: string) {
  return <span style={{ ...S.badge, backgroundColor: STATUS_BG[st] || "#f3f4f6" }}>{STATUS_LABEL[st] || st}</span>;
}

/** Текст задания как в КИМ: пропуск → «___ (19)». */
function renderKimText(text: string) {
  const parts = text.split(/\{\{\s*(\d+)\s*\}\}/g);
  return parts.map((p, i) =>
    i % 2 === 1 ? (
      <span key={i} style={{ fontWeight: 600, color: colors.primary }}>
        ______ ({p})
      </span>
    ) : (
      <span key={i}>{p}</span>
    ),
  );
}

export function ExamPage() {
  const isMobile = useIsMobile();
  const [stats, setStats] = useState<ExamStats | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const [genGroup, setGenGroup] = useState("19-24");
  const [genCount, setGenCount] = useState(3);
  const [genTopic, setGenTopic] = useState("");
  const [job, setJob] = useState<ExamJob | null>(null);
  const [genBusy, setGenBusy] = useState(false);

  const [fStatus, setFStatus] = useState<string>("review");
  const [fGroup, setFGroup] = useState<string>("");
  const [list, setList] = useState<{ items: ExamTaskBrief[]; total: number } | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);

  const loadStats = useCallback(() => {
    api.examStats("ege").then(setStats).catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, []);

  const loadList = useCallback(() => {
    api.examTasks({ exam: "ege", status: fStatus || undefined, task_no: fGroup || undefined, limit: 100 })
      .then(setList)
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, [fStatus, fGroup]);

  useEffect(() => { loadStats(); }, [loadStats]);
  useEffect(() => { loadList(); }, [loadList]);

  // Опрос job'а, пока он идёт.
  useEffect(() => {
    if (!job || job.status !== "running") return;
    const t = window.setInterval(() => {
      api.examJob(job.id)
        .then((j) => {
          setJob(j);
          if (j.status === "done") { loadStats(); loadList(); }
        })
        .catch(() => { /* backend перезапустился — job потерян, не шумим */ });
    }, 3000);
    return () => window.clearInterval(t);
  }, [job, loadStats, loadList]);

  const startGen = async () => {
    setGenBusy(true);
    setErr(null);
    try {
      const j = await api.examGenerate({ exam: "ege", task_no: genGroup, count: genCount, topic: genTopic.trim() || undefined });
      setJob(j);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setGenBusy(false);
    }
  };

  const totalPublished = useMemo(() => (stats?.groups || []).reduce((a, g) => a + g.published, 0), [stats]);
  const totalReview = useMemo(() => (stats?.groups || []).reduce((a, g) => a + g.review, 0), [stats]);

  if (err && !stats) return <div style={S.error}>{err}</div>;
  if (!stats) return <div style={{ color: colors.textMuted }}>Загрузка…</div>;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <h2 style={S.h2}>🎓 ЕГЭ — банк заданий</h2>

      {err && <div style={S.error}>{err}</div>}

      <div style={S.card}>
        <div style={{ fontSize: 13, color: colors.textMuted, lineHeight: 1.6 }}>
          Задания генерирует наша модель по спецификации ФИПИ{" "}
          {stats.spec_year ? <b>{stats.spec_year}</b> : null} и сразу решает их «вслепую» второй раз:
          процент совпадений с ключом показан как качество. В приложение попадают только{" "}
          <b>опубликованные</b> задания. Пока поддерживается раздел «Грамматика и лексика»:
          группы {stats.generatable.join(", ")}.
          {!stats.spec_loaded && (
            <div style={{ ...S.error, marginTop: 10 }}>Спецификация не загружена — накатите миграцию 0038.</div>
          )}
          {!stats.llm_configured && (
            <div style={{ ...S.error, marginTop: 10 }}>LLM не настроен (VLLM_BASE_URL) — генерация недоступна.</div>
          )}
        </div>
      </div>

      <div style={{ ...S.metricsGrid, gridTemplateColumns: isMobile ? "repeat(auto-fit, minmax(130px, 1fr))" : "repeat(auto-fit, minmax(170px, 1fr))" }}>
        <div style={S.metricCard}>
          <div style={S.metricValue}>{totalPublished}</div>
          <div style={S.metricLabel}>Опубликовано</div>
        </div>
        <div style={S.metricCard}>
          <div style={S.metricValue}>{totalReview}</div>
          <div style={S.metricLabel}>Ждут модерации</div>
        </div>
        <div style={S.metricCard}>
          <div style={S.metricValue}>{stats.max_primary ?? "—"}</div>
          <div style={S.metricLabel}>Макс. первичный балл</div>
        </div>
      </div>

      <div style={S.card}>
        <h3 style={S.h3}>📚 Банк по группам заданий</h3>
        <div style={{ overflowX: "auto" }}>
          <table style={S.table}>
            <thead>
              <tr>
                <th style={S.th}>Группа</th>
                <th style={S.th}>Тип</th>
                <th style={S.th}>Опубликовано</th>
                <th style={S.th}>На модерации</th>
                <th style={S.th}>Черновики</th>
                <th style={S.th}>Снято</th>
                <th style={S.th}>Ср. качество</th>
              </tr>
            </thead>
            <tbody>
              {stats.groups.map((g) => (
                <tr key={g.task_no} style={S.rowClickable} onClick={() => { setFGroup(g.task_no); setFStatus("review"); }}>
                  <td style={S.td}><b>{g.task_no}</b></td>
                  <td style={S.td}>{TYPE_LABEL[g.task_type] || g.task_type}</td>
                  <td style={S.td}>{g.published}</td>
                  <td style={S.td}>{g.review}</td>
                  <td style={S.td}>{g.draft}</td>
                  <td style={S.td}>{g.retired}</td>
                  <td style={S.td}>{g.avg_quality === null ? "—" : `${g.avg_quality}%`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div style={{ marginTop: 8, fontSize: 12, color: colors.textMuted }}>
          Цель на старт — по 30 опубликованных заданий в каждой группе.
        </div>
      </div>

      <div style={S.card}>
        <h3 style={S.h3}>⚙️ Сгенерировать задания</h3>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-end" }}>
          <div>
            <div style={S.label}>Группа</div>
            <select style={{ ...S.input, width: 220 }} value={genGroup} onChange={(e) => setGenGroup(e.target.value)}>
              {stats.generatable.map((g) => {
                const grp = stats.groups.find((x) => x.task_no === g);
                return <option key={g} value={g}>{g} — {grp ? TYPE_LABEL[grp.task_type] || grp.task_type : ""}</option>;
              })}
            </select>
          </div>
          <div>
            <div style={S.label}>Сколько (1–20)</div>
            <input style={{ ...S.input, width: 90 }} type="number" min={1} max={20} value={genCount}
              onChange={(e) => setGenCount(Math.max(1, Math.min(20, parseInt(e.target.value || "1", 10))))} />
          </div>
          <div style={{ flex: 1, minWidth: 220 }}>
            <div style={S.label}>Тема (по-английски, необязательно)</div>
            <input style={S.input} value={genTopic} placeholder="a famous museum or gallery"
              onChange={(e) => setGenTopic(e.target.value)} />
          </div>
          <button style={S.btn} disabled={genBusy || !stats.llm_configured || (job?.status === "running")} onClick={() => void startGen()}>
            {job?.status === "running" ? "Идёт генерация…" : "Сгенерировать"}
          </button>
        </div>
        {job && (
          <div style={{ marginTop: 12, fontSize: 13 }}>
            <div>
              Job <code>{job.id}</code>, группа {job.task_no}: готово <b>{job.done}</b> из {job.requested},
              ошибок {job.failed}{job.status === "running" ? " · идёт…" : " · завершено"}
            </div>
            {job.qualities.length > 0 && (
              <div style={{ marginTop: 4, display: "flex", gap: 6, flexWrap: "wrap" }}>
                {job.qualities.map((q, i) => <span key={i}>{qualityBadge(q)}</span>)}
              </div>
            )}
            {job.errors.length > 0 && (
              <div style={{ marginTop: 6, color: colors.danger }}>
                {job.errors.map((e, i) => <div key={i}>• {e}</div>)}
              </div>
            )}
          </div>
        )}
        <div style={{ marginTop: 8, fontSize: 12, color: colors.textMuted }}>
          Одна группа — это два обращения к модели (генерация и слепая проверка), 30–90 секунд.
          Генерация идёт в фоне, страницу можно закрыть; перезапуск backend прерывает job.
        </div>
      </div>

      <div style={S.card}>
        <h3 style={S.h3}>🧐 Очередь</h3>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
          {["review", "published", "draft", "retired", ""].map((st) => (
            <button key={st || "all"} style={fStatus === st ? S.tabActive : S.tab} onClick={() => setFStatus(st)}>
              {st ? STATUS_LABEL[st] : "все"}
            </button>
          ))}
          <span style={{ width: 12 }} />
          {["", ...stats.generatable].map((g) => (
            <button key={g || "allg"} style={fGroup === g ? S.tabActive : S.tab} onClick={() => setFGroup(g)}>
              {g || "все группы"}
            </button>
          ))}
        </div>
        {!list ? (
          <div style={{ color: colors.textMuted }}>Загрузка…</div>
        ) : list.items.length === 0 ? (
          <div style={{ color: colors.textMuted }}>Пусто.</div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={S.table}>
              <thead>
                <tr>
                  <th style={S.th}>ID</th>
                  <th style={S.th}>Группа</th>
                  <th style={S.th}>Заголовок / тема</th>
                  <th style={S.th}>Качество</th>
                  <th style={S.th}>Статус</th>
                  <th style={S.th}>Замечания</th>
                  <th style={S.th}>Создано</th>
                </tr>
              </thead>
              <tbody>
                {list.items.map((t) => (
                  <tr key={t.id} style={S.rowClickable} onClick={() => setOpenId(t.id)}>
                    <td style={S.td}>{t.id}</td>
                    <td style={S.td}><b>{t.task_no}</b></td>
                    <td style={S.td}>{t.title || "—"}<div style={{ fontSize: 12, color: colors.textMuted }}>{t.topic}</div></td>
                    <td style={S.td}>{qualityBadge(t.quality)}</td>
                    <td style={S.td}>{statusBadge(t.status)}</td>
                    <td style={S.td}>{t.notes.length ? <span style={{ color: colors.warning }}>{t.notes.length}</span> : "—"}</td>
                    <td style={S.td}>{t.created_at ? t.created_at.slice(0, 16).replace("T", " ") : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div style={{ marginTop: 6, fontSize: 12, color: colors.textMuted }}>Показано {list.items.length} из {list.total}.</div>
          </div>
        )}
      </div>

      {openId !== null && (
        <ExamTaskModal
          id={openId}
          onClose={() => setOpenId(null)}
          onChanged={() => { loadStats(); loadList(); }}
        />
      )}
    </div>
  );
}

// ─── Карточка задания ────────────────────────────────────────────────────────

function ExamTaskModal({ id, onClose, onChanged }: { id: number; onClose: () => void; onChanged: () => void }) {
  const [task, setTask] = useState<ExamTaskFull | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [contentJson, setContentJson] = useState("");
  const [keyJson, setKeyJson] = useState("");
  const [explJson, setExplJson] = useState("");

  const load = useCallback(() => {
    api.examTask(id).then((t) => {
      setTask(t);
      setContentJson(JSON.stringify(t.content, null, 2));
      setKeyJson(JSON.stringify(t.answer_key, null, 2));
      setExplJson(JSON.stringify(t.explanation || {}, null, 2));
    }).catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, [id]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const setStatus = async (status: "published" | "retired" | "review" | "draft") => {
    setBusy(true); setErr(null);
    try {
      const t = await api.examTaskPatch(id, { status });
      setTask(t); onChanged();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };

  const saveEdits = async () => {
    setBusy(true); setErr(null);
    try {
      const body = {
        content: JSON.parse(contentJson),
        answer_key: JSON.parse(keyJson),
        explanation: JSON.parse(explJson),
      };
      const t = await api.examTaskPatch(id, body);
      setTask(t); setEditing(false); onChanged();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };

  const check = task?.gen_meta?.check;

  return (
    <div
      onClick={onClose}
      style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.45)", zIndex: 100, display: "flex", justifyContent: "center", alignItems: "flex-start", overflowY: "auto", padding: 16 }}
    >
      <div onClick={(e) => e.stopPropagation()} style={{ ...S.card, width: "100%", maxWidth: 860, marginTop: 24 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, marginBottom: 12 }}>
          <h3 style={{ ...S.h3, margin: 0 }}>
            Задание #{id} {task ? <>· группа <b>{task.task_no}</b> · {TYPE_LABEL[task.task_type] || task.task_type}</> : null}
          </h3>
          <button style={S.btnSecondary} onClick={onClose}>Закрыть</button>
        </div>

        {err && <div style={S.error}>{err}</div>}
        {!task ? (
          <div style={{ color: colors.textMuted }}>Загрузка…</div>
        ) : (
          <>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", marginBottom: 12, fontSize: 13 }}>
              {statusBadge(task.status)} качество {qualityBadge(task.quality)}
              <span style={{ color: colors.textMuted }}>· тема: {task.topic || "—"} · {task.gen_meta?.model || "manual"} · {task.gen_meta?.prompt_version || ""}</span>
            </div>

            {task.notes.length > 0 && (
              <div style={{ ...S.error, backgroundColor: colors.warningBg, color: colors.text, borderColor: "#f5d78e" }}>
                <b>Замечания автопроверки:</b>
                {task.notes.map((n, i) => <div key={i}>• {n}</div>)}
              </div>
            )}

            {!editing ? (
              <>
                {task.content.texts.map((t, i) => (
                  <div key={i} style={{ marginBottom: 12 }}>
                    {t.title && <div style={{ fontWeight: 600, marginBottom: 4 }}>{t.title}</div>}
                    <div style={{ lineHeight: 1.7, fontSize: 15, whiteSpace: "pre-wrap" }}>{renderKimText(t.text)}</div>
                  </div>
                ))}

                <table style={{ ...S.table, marginTop: 8 }}>
                  <thead>
                    <tr>
                      <th style={S.th}>№</th>
                      <th style={S.th}>{task.task_type === "lex_mcq" ? "Варианты" : "Базовое слово"}</th>
                      <th style={S.th}>Ключ</th>
                      <th style={S.th}>Слепая проверка</th>
                      <th style={S.th}>Разбор</th>
                    </tr>
                  </thead>
                  <tbody>
                    {task.content.items.map((it) => {
                      const k = String(it.n);
                      const key = task.answer_key[k];
                      const chk = check?.items?.[k];
                      return (
                        <tr key={k}>
                          <td style={S.td}><b>{k}</b></td>
                          <td style={S.td}>
                            {it.options
                              ? it.options.map((o, i) => (
                                  <div key={i} style={{ fontWeight: String(i + 1) === key ? 700 : 400 }}>{i + 1}) {o}</div>
                                ))
                              : <b>{it.base}</b>}
                          </td>
                          <td style={S.td}>{Array.isArray(key) ? key.join(" / ") : key}</td>
                          <td style={S.td}>
                            {chk ? (
                              <span style={{ color: chk.ok ? colors.success : colors.danger }}>
                                {chk.ok ? "✓" : "✗"} {chk.model || "—"}
                              </span>
                            ) : (check?.error ? <span style={{ color: colors.textMuted }}>ошибка: {check.error}</span> : "—")}
                          </td>
                          <td style={{ ...S.td, fontSize: 13, color: colors.textMuted }}>{task.explanation?.[k] || "—"}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                {check && check.quality !== null && check.quality < 100 && (
                  <div style={{ marginTop: 8, fontSize: 12, color: colors.textMuted }}>
                    Расхождение не всегда ошибка ключа: у формы может быть второй допустимый вариант.
                    Тогда добавьте его в ключ через «Править» (для 19–29 ключ — список форм).
                  </div>
                )}
              </>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                <div>
                  <div style={S.label}>content (texts[], items[]; пропуски как {"{{19}}"})</div>
                  <textarea style={{ ...S.input, minHeight: 220, fontFamily: "monospace", fontSize: 12 }} value={contentJson} onChange={(e) => setContentJson(e.target.value)} />
                </div>
                <div>
                  <div style={S.label}>answer_key (19–29: списки форм; 30–36: номер варианта строкой)</div>
                  <textarea style={{ ...S.input, minHeight: 120, fontFamily: "monospace", fontSize: 12 }} value={keyJson} onChange={(e) => setKeyJson(e.target.value)} />
                </div>
                <div>
                  <div style={S.label}>explanation (разбор по номерам, по-русски)</div>
                  <textarea style={{ ...S.input, minHeight: 120, fontFamily: "monospace", fontSize: 12 }} value={explJson} onChange={(e) => setExplJson(e.target.value)} />
                </div>
              </div>
            )}

            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 16 }}>
              {!editing ? (
                <>
                  {task.status !== "published" && (
                    <button style={S.btn} disabled={busy} onClick={() => void setStatus("published")}>Опубликовать</button>
                  )}
                  {task.status === "published" && (
                    <button style={S.btnSecondary} disabled={busy} onClick={() => void setStatus("retired")}>Снять с публикации</button>
                  )}
                  {task.status !== "retired" && task.status !== "published" && (
                    <button style={S.btnDanger} disabled={busy} onClick={() => void setStatus("retired")}>В брак</button>
                  )}
                  {task.status === "retired" && (
                    <button style={S.btnSecondary} disabled={busy} onClick={() => void setStatus("review")}>Вернуть на модерацию</button>
                  )}
                  <button style={S.btnSecondary} disabled={busy} onClick={() => setEditing(true)}>Править</button>
                </>
              ) : (
                <>
                  <button style={S.btn} disabled={busy} onClick={() => void saveEdits()}>Сохранить</button>
                  <button style={S.btnSecondary} disabled={busy} onClick={() => { setEditing(false); load(); }}>Отмена</button>
                </>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
