import type { LearningAccess } from './learningApi';

export function resetCountdown(resetsAt: string, now = Date.now()) {
  const minutes = Math.max(0, Math.ceil((Date.parse(resetsAt) - now) / 60000));
  return minutes >= 60 ? `${Math.floor(minutes / 60)} ч ${minutes % 60} мин` : `${minutes} мин`;
}

export function LearningAccessCard({access,onSubscribe}: {access: LearningAccess; onSubscribe?: () => void}) {
  const full=access.full_access;
  return <section className="learn-access" aria-label="Доступ к программе">
    <div className="learn-access__top"><strong>{full ? 'Полный доступ' : 'Нужна подписка'}</strong><span>{full ? 'Все уроки и тренажёры' : 'Доступ к занятиям'}</span></div>
    <p>{full ? 'Занимайся в своём темпе: уроки и разговоры без дневных ограничений.'
      : 'Продолжай программу и открывай самостоятельные тренажёры с подпиской.'}</p>
    {!full && onSubscribe && <button className="learn-secondary" onClick={onSubscribe}>Открыть полный доступ</button>}
  </section>;
}
