import { useEffect, useState } from 'react';
import type { LearningAccess } from './learningApi';

export function resetCountdown(resetsAt: string, now = Date.now()) {
  const minutes = Math.max(0, Math.ceil((Date.parse(resetsAt) - now) / 60000));
  return minutes >= 60 ? `${Math.floor(minutes / 60)} ч ${minutes % 60} мин` : `${minutes} мин`;
}

export function LearningAccessCard({access,onSubscribe}: {access: LearningAccess; onSubscribe?: () => void}) {
  const [now,setNow] = useState(Date.now);
  useEffect(() => {const timer=window.setInterval(() => setNow(Date.now()),30000);return () => clearInterval(timer);},[]);
  const full=access.full_access, unlimited=access.remaining_today===null;
  const title=full ? access.trial_active ? 'Пробный полный доступ' : 'Полный доступ'
    : unlimited ? 'Уроки без суточного лимита' : access.remaining_today===0 ? 'Новые уроки на сегодня закончились' : access.daily_limit===1 ? 'Твой бесплатный урок на сегодня' : 'Бесплатные уроки на сегодня';
  return <section className="learn-access" aria-label="Доступ к программе">
    <div className="learn-access__top"><strong>{title}</strong><span>{unlimited ? 'Без лимита уроков' : `${access.remaining_today} / ${access.daily_limit}`}</span></div>
    <p>{full ? 'Занимайся в своём темпе: уроки и разговоры без дневных ограничений.'
      : `В урок входит до ${Math.ceil((access.conversation_seconds || 300)/60)} мин разговора в сутки. Самостоятельная практика считается отдельно.`}</p>
    {access.trial_active && access.trial_until && <p className="learn-muted">Пробный доступ закончится через {resetCountdown(access.trial_until,now)}.</p>}
    {!unlimited && <p className="learn-muted">{access.remaining_today===0 ? 'Следующий новый урок' : 'Лимит обновится'} в 00:00 МСК · через {resetCountdown(access.resets_at,now)}. Начатый урок можно продолжить.</p>}
    {!full && onSubscribe && <button className="learn-secondary" onClick={onSubscribe}>Выбрать подписку</button>}
  </section>;
}
