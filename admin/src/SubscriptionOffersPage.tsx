import { useEffect, useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, type SubscriptionOfferAnalytics } from "./api";
import { fmtDate, fmtRub } from "./ui";
import "./SubscriptionOffersPage.css";

const number = (value: number) => value.toLocaleString("ru-RU");

export function SubscriptionOffersPage({ onUser }: { onUser: (id: number) => void }) {
  const [days, setDays] = useState(30);
  const [reload, setReload] = useState(0);
  const [data, setData] = useState<SubscriptionOfferAnalytics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    setLoading(true); setError("");
    void api.subscriptionOffers(days).then(result => { if (alive) setData(result); })
      .catch(failure => { if (alive) setError(failure instanceof Error ? failure.message : "Не удалось загрузить аналитику"); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [days, reload]);

  return <div className="so-analytics" aria-busy={loading}>
    <div className="so-header">
      <div><div className="so-eyebrow">Обратная связь и предложения</div><h2>Почему не оформляют подписку</h2></div>
      <label className="so-period">Период<select value={days} onChange={event => setDays(Number(event.target.value))}>
        <option value={7}>7 дней</option><option value={30}>30 дней</option><option value={90}>90 дней</option><option value={365}>Год</option>
      </select></label>
    </div>
    <p className="so-description">Первый урок бесплатно, затем — 7 дней за 1 ₽ с ежемесячным продлением. После отказа — опрос без дополнительных скидок.
      Повторный показ без ответа возможен через 24 часа; после ответа опрос больше не появляется.</p>
    {loading ? <div className="so-panel" role="status">Загружаем аналитику…</div>
      : error ? <div className="so-panel so-error" role="alert">{error}<button onClick={() => setReload(value => value + 1)}>Повторить</button></div>
      : data && <>
        <div className="so-stats">
          {[
            ["Показы", number(data.impressions), `${number(data.users_shown)} уникальных пользователей`],
            ["Открыли оплату", number(data.users_clicked), `${data.click_rate}% от увидевших предложение`],
            ["Ответили на вопрос", number(data.answers), `${data.answer_rate}% от увидевших предложение`],
            ["Закрытия окна", number(data.dismissals), `${number(data.declines)} переходов к вопросу`],
            ["Оформили подписку", number(data.paid_users), "Неделя за 1 ₽ после показа предложения"],
            ["Выручка", fmtRub(data.revenue_rub), "По успешным подключениям недели за 1 ₽"],
          ].map(([title, value, note]) => <div className="so-stat" key={title}><span>{title}</span><strong>{value}</strong><small>{note}</small></div>)}
        </div>
        <div className="so-two-columns">
          <section className="so-panel"><h3>Что мешает подписаться</h3><p>Один сохранённый ответ на аккаунт. Всего: {number(data.answers)}.</p>
            {!data.answers && <div className="so-empty">Ответов пока нет. Здесь появится распределение причин.</div>}
            <div className="so-reason-list">{data.reasons.map(reason => <div className="so-reason" key={reason.code}>
              <div><span>{reason.label}</span><strong>{number(reason.count)} <small>· {reason.percent}%</small></strong></div>
              <div className="so-bar" role="meter" aria-label={reason.label} aria-valuenow={reason.percent} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${reason.percent}%` }} /></div>
            </div>)}</div>
          </section>
          <section className="so-panel"><h3>Как работают предложения</h3><p>Оплаты учитываются, если созданы после показа окна.</p>
            <div className="so-promo"><div className="so-promo-title"><strong>7 дней за 1 ₽</strong><span>Первое подключение</span></div>
              <p>После недели — автоматическое продление по месячному тарифу.</p>
              <dl><div><dt>Открыли оплату</dt><dd>{number(data.users_clicked)}</dd></div>
                <div><dt>Оплатили</dt><dd>{number(data.paid_users)}</dd></div><div><dt>Выручка</dt><dd>{fmtRub(data.revenue_rub)}</dd></div></dl>
            </div>
          </section>
        </div>
        <section className="so-panel"><h3>Динамика по дням</h3><p>Показы и действия по окнам за выбранный период. Дни — по московскому времени.</p>
          <div className="so-chart"><ResponsiveContainer width="100%" height={230}>
            <LineChart data={data.daily} margin={{ top: 14, right: 12, left: -20, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e5e7eb" />
              <XAxis dataKey="date" tickFormatter={value => String(value).slice(5).split("-").reverse().join(".")} tick={{ fontSize: 11 }} minTickGap={30} />
              <YAxis allowDecimals={false} tick={{ fontSize: 11 }} /><Tooltip />
              <Line type="monotone" dataKey="shown" name="Показы" stroke="#2563eb" strokeWidth={2} dot={false} isAnimationActive={false} />
              <Line type="monotone" dataKey="tariffs" name="Открытия оплаты" stroke="#a16207" strokeWidth={2} dot={false} isAnimationActive={false} />
              <Line type="monotone" dataKey="answers" name="Ответы" stroke="#15803d" strokeWidth={2} dot={false} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer></div>
          <div className="so-legend"><span style={{ color: "#2563eb" }}>● Показы</span><span style={{ color: "#a16207" }}>● Оплата</span><span style={{ color: "#15803d" }}>● Ответы</span></div>
        </section>
        <section className="so-panel"><h3>Последние ответы</h3><p>До 100 последних ответов по окнам выбранного периода.</p>
          {!data.recent_answers.length ? <div className="so-empty">Пока никто не ответил на вопрос.</div> : <div className="so-table-scroll"><table>
            <thead><tr><th>Когда</th><th>Пользователь</th><th>Причина</th><th>После ответа</th></tr></thead>
            <tbody>{data.recent_answers.map(answer => <tr key={answer.user_id}>
              <td>{fmtDate(answer.answered_at)}</td><td><button className="so-user-link" onClick={() => onUser(answer.user_id)}>{answer.name}</button><small>#{answer.user_id}</small></td>
              <td>{answer.reason_label}</td><td><span className={`so-status ${answer.paid ? "so-status--paid" : ""}`}>{answer.paid ? "Оплатил(а)" : answer.clicked_tariffs ? "Открыл(а) оплату" : "Ответ сохранён"}</span></td>
            </tr>)}</tbody>
          </table></div>}
        </section>
        <p className="so-footnote">Показы считаются при выдаче окна сервером. Учитываются первые оплаты недели за 1 ₽ после показа нового предложения. Старые скидочные кампании и последующие ежемесячные списания сюда не входят.</p>
      </>}
  </div>;
}
