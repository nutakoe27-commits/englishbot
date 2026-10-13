import { useEffect, useState } from 'react';
import { learningGet, type LearningOverview } from './learningApi';
import './Learning.css';

export function LearningMap({data,onLesson}: {data: LearningOverview; onLesson?: (id: string) => void}) {
  const p = data.profile;
  if (!p) return null;
  return <section className="learn-map" aria-label="Развитие английского">
    <div className="learn-section-head"><h2>Твой маршрут</h2><span className="learn-level">{p.level}</span></div>
    <p>Цикл {p.cycle} · {p.cycle_completed} из 20 уроков · {p.goal_label}</p>
    <div className="learn-road" aria-label="Уровни английского">
      {['A1','A2','B1','B2','C1'].map(level => <span key={level} data-current={level === p.level}>{level}</span>)}
    </div>
    <div className="learn-nodes" aria-label="Уроки текущего цикла">
      {(data.route || []).map(node => <button type="button" key={node.ordinal} className="learn-node" data-status={node.status}
        disabled={!node.id || !onLesson} onClick={() => {if(node.id) onLesson?.(node.id);}}
        title={`${node.ordinal}. ${node.title}`} aria-label={`Урок ${node.ordinal}: ${node.title}, ${node.status === 'completed' ? 'завершён' : node.status === 'active' || node.status === 'paused' ? 'начат' : node.status === 'prepared' ? 'следующий' : 'впереди'}`}>
        {node.status === 'completed' ? '✓' : node.position}</button>)}
      <div className="learn-checkpoint-node" data-due={p.checkpoint_due}>Проверка уровня</div>
    </div>
    <p className="learn-muted">{p.checkpoint_due ? 'Пора проверить изменения и выбрать следующий цикл.' : `До проверки — ${p.until_checkpoint} уроков. Каждый пятый урок помогает закрепить пройденное.`}</p>
    <h3>Карта навыков</h3>
    <div className="learn-skills">{(data.skills || []).map(skill => <article key={skill.key}>
      <div><strong>{skill.label}</strong><span>{skill.accuracy === null ? 'Пока не оценено' : `${skill.accuracy}%`}</span></div>
      {skill.accuracy !== null && <progress max={100} value={skill.accuracy} aria-label={skill.label} />}
      <small>{skill.attempts ? `${skill.attempts} наблюдений в программе` : 'Данные появятся после практики'}</small>
    </article>)}</div>
    <p className="learn-muted">Навыки показывают результаты практики. Уровень английского уточняется на диагностике. Текстовые ответы не оценивают устную речь.</p>
    {!!data.competencies?.filter(item => item.key.includes('_')).length && <><h3>Над чем работаем</h3><div className="learn-competencies">
      {data.competencies.filter(item => item.key.includes('_')).map(item => <article key={item.key}>
        <span>{item.label}</span><strong>{item.status === 'mastered' ? 'Освоено' : item.status === 'review' ? 'Повторим' : 'Практикуем'}</strong>
      </article>)}</div></>}
    {!!data.history?.length && <><h3>История проверок</h3><div className="learn-history">
      {data.history.map(item => <article key={item.id}><strong>{item.result.level_before} → {item.result.cefr}</strong>
        <span>После {item.after_count} уроков · {new Date(item.date+'Z').toLocaleDateString('ru-RU')}</span></article>)}
    </div></>}
  </section>;
}

export function LearningProgress() {
  const [data,setData] = useState<LearningOverview | null>(null);
  useEffect(() => { let alive = true; void learningGet<LearningOverview>('/overview').then(result => {if (alive) setData(result);}).catch(() => {}); return () => {alive=false;}; }, []);
  return data?.enabled && data.profile ? <LearningMap data={data} /> : null;
}

export function CourseHomeCard({onOpen,onAvailability}: {onOpen: () => void; onAvailability?: (enabled: boolean) => void}) {
  const [data,setData] = useState<LearningOverview | null>(null);
  useEffect(() => { let alive = true; void learningGet<LearningOverview>('/overview').then(result => {
    if (alive) {setData(result); onAvailability?.(result.enabled);}
  }).catch(() => {}); return () => {alive=false;}; }, []);
  if (!data?.enabled) return null;
  const p=data.profile, next=data.next_lesson;
  return <section className="learn-home-card">
    <span className="learn-eyebrow">Твоя программа английского</span>
    <h2>{p?.checkpoint_due ? 'Посмотрим, чему ты научился' : next ? next.content.spec.title : 'Начнём с твоего маршрута'}</h2>
    <p>{next ? `Научишься ${next.content.spec.objective}.` : 'Связанные занятия: слова, грамматика, понимание речи и разговор.'}</p>
    {p && <><div className="learn-home-meta"><span>{p.level} · цикл {p.cycle}</span><span>{p.cycle_completed}/20 уроков</span></div>
      <progress value={p.cycle_completed} max={20} aria-label="Прогресс цикла" /></>}
    <button className="learn-primary" onClick={onOpen}>{p?.checkpoint_due ? 'Проверить уровень' : next?.status === 'active' || next?.status === 'paused' ? 'Продолжить урок' : p ? 'Начать урок' : 'Составить программу'} →</button>
  </section>;
}
