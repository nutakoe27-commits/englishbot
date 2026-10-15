import { useEffect, useRef, useState } from 'react';
import { LearningMap } from './LearningProgress';
import { learningGet, learningPost, learningSetup, learningUrl, type LearningOverview, type LearningLesson,
  type LearningQuestion, type Checkpoint } from './learningApi';
import { GrammarScreen } from './GrammarScreen';
import { SrsScreen } from './SrsScreen';
import { ListeningScreen } from './ListeningScreen';
import Tutor from './App';
import { LevelPicker } from './LevelPicker';
import type { Level } from './tutorSettings';
import './Learning.css';

const steps = ['Грамматика','Работа над ошибками','Слова','Аудирование','Разговор','Итог'];
const moduleKeys = ['grammar','mistakes','words','listening','conversation'];
const message = (error: unknown) => error instanceof Error ? error.message : 'Не удалось сохранить. Попробуй ещё раз.';

function AudioStory({path,script,textMode,onText}: {path: string; script: string; textMode: boolean; onText: () => void}) {
  const [src,setSrc]=useState(''), [error,setError]=useState(''), [loading,setLoading]=useState(false);
  const urlRef=useRef('');
  const mounted=useRef(true);
  useEffect(() => { mounted.current=true; return () => {mounted.current=false; if(urlRef.current) URL.revokeObjectURL(urlRef.current);}; },[]);
  const load=async () => {
    setLoading(true); setError('');
    try {
      const response=await fetch(learningUrl(path));
      if(!response.ok) throw new Error('Озвучка пока недоступна. Попробуй ещё раз или продолжи с текстом.');
      const blob=await response.blob();
      if(!mounted.current) return;
      if(urlRef.current) URL.revokeObjectURL(urlRef.current);
      urlRef.current=URL.createObjectURL(blob); setSrc(urlRef.current);
    } catch(e) {if(mounted.current) setError(message(e));}
    finally {if(mounted.current) setLoading(false);}
  };
  return <section className="learn-audio">
    <p>Сначала прослушай историю, затем ответь на вопросы.</p>
    {src ? <audio controls src={src} aria-label="Аудиотекст урока" /> : <button className="learn-primary" disabled={loading} onClick={() => void load()}>{loading ? 'Готовим озвучку…' : 'Загрузить аудио'}</button>}
    {error && <p role="alert">{error}</p>}
    {!textMode && <button className="learn-link" onClick={onText}>Открыть текст вместо аудио</button>}
    {textMode && <><p className="learn-muted">Этот этап учитывается как чтение. Понимание на слух проверим в другой раз.</p><blockquote>{script}</blockquote></>}
  </section>;
}

function VoiceInput({path,onResult,disabled}: {path: string; onResult: (text: string,token: string) => void; disabled: boolean}) {
  const [recording,setRecording]=useState(false), [busy,setBusy]=useState(false), [error,setError]=useState('');
  const recorder=useRef<MediaRecorder | null>(null), stream=useRef<MediaStream | null>(null), timer=useRef<number>();
  const alive=useRef(true);
  useEffect(() => { alive.current=true; return () => { alive.current=false; clearTimeout(timer.current); if(recorder.current?.state==='recording') recorder.current.stop(); stream.current?.getTracks().forEach(track => track.stop()); }; },[]);
  const start=async () => {
    setError('');
    try {
      if(!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) throw new Error('Запись голоса недоступна в этом браузере. Можно ответить текстом.');
      const media=await navigator.mediaDevices.getUserMedia({audio:true});
      if(!alive.current) {media.getTracks().forEach(track => track.stop()); return;}
      stream.current=media;
      const chunks: BlobPart[]=[];
      const rec=new MediaRecorder(media); recorder.current=rec;
      rec.ondataavailable=e => {if(e.data.size) chunks.push(e.data);};
      rec.onstop=() => {
        clearTimeout(timer.current); media.getTracks().forEach(track => track.stop());
        if(!alive.current) return;
        setRecording(false); setBusy(true);
        const body=new FormData(); body.append('file',new Blob(chunks,{type:rec.mimeType}),'voice.webm');
        void fetch(learningUrl(path),{method:'POST',body}).then(async response => {
          if(!response.ok) throw new Error('Не удалось распознать голос. Попробуй ещё раз или напиши ответ.');
          const data=await response.json() as {text: string; voice_token: string};
          if(alive.current) onResult(data.text,data.voice_token);
        }).catch(e => {if(alive.current) setError(message(e));}).finally(() => {if(alive.current) setBusy(false);});
      };
      rec.start(); setRecording(true);
      timer.current=window.setTimeout(() => {if(rec.state==='recording') rec.stop();},60000);
    } catch(e) {setError(message(e)); stream.current?.getTracks().forEach(track => track.stop());}
  };
  return <div className="learn-voice">
    <button className="learn-secondary" disabled={disabled || busy} onClick={() => recording ? recorder.current?.stop() : void start()}>
      {recording ? 'Остановить запись' : busy ? 'Распознаём…' : 'Ответить голосом'}</button>
    {recording && <small role="status">Запись идёт · до одной минуты</small>}
    {error && <p role="alert">{error}</p>}
  </div>;
}

function Choices({question,busy,onAnswer}: {question: LearningQuestion; busy: boolean; onAnswer: (value: string) => void}) {
  return <div className="learn-question"><h3>{question.prompt}</h3><div className="learn-choices">
    {question.choices.map(choice => <button key={choice} disabled={busy || !!question.answer} className="learn-choice"
      data-result={question.answer ? choice === question.answer.correct_answer ? 'correct' : choice === question.answer.value ? 'wrong' : '' : ''}
      onClick={() => onAnswer(choice)}>{choice}</button>)}
    </div>{question.answer && <div className="learn-feedback" role="status">
      <strong>{question.answer.correct ? 'Верно!' : `Правильный ответ: ${question.answer.correct_answer}`}</strong>
      <p>{question.answer.explanation}</p></div>}
  </div>;
}

function LessonRunner({initial,onExit,onDone}: {initial: LearningLesson; onExit: () => void; onDone: () => void}) {
  const [lesson,setLesson]=useState(initial), [busy,setBusy]=useState(false), [error,setError]=useState('');
  const clock=useRef({base:initial.used_seconds,started:Date.now(),running:initial.status==='active'});
  const current=useRef(lesson); current.current=lesson;
  const alive=useRef(true), pending=useRef(false), finishBody=useRef<Record<string,unknown> | null>(null);
  const pauseTimer=useRef<number>();
  const elapsed=() => clock.current.base+(clock.current.running ? Math.max(0,Math.round((Date.now()-clock.current.started)/1000)) : 0);
  const path=`/lessons/${lesson.id}`;
  const pause=() => {
    if(!clock.current.running || current.current.status==='completed') return;
    const seconds=elapsed(); clock.current={base:seconds,started:Date.now(),running:false};
    void learningPost(path+'/pause',{elapsed_sec:seconds,activity_token:current.current.activity_token},true).catch(() => {});
  };
  useEffect(() => {
    alive.current=true;
    clearTimeout(pauseTimer.current);
    const tick=() => {
      if(!clock.current.running || document.hidden || current.current.status==='completed') return;
      const token=current.current.activity_token;
      void learningPost<{active: boolean}>(path+'/heartbeat',{elapsed_sec:elapsed(),activity_token:token}).then(data => {
        if(alive.current && !data.active && current.current.status!=='completed' && token===current.current.activity_token) {
          clock.current.running=false; setLesson(previous => previous.status==='completed' ? previous : {...previous,status:'paused'});
        }
      }).catch(() => {});
    };
    const visibility=() => {if(document.hidden) {pause(); setLesson(previous => previous.status==='completed' ? previous : {...previous,status:'paused'});}};
    const hide=() => pause();
    const interval=window.setInterval(tick,20000);
    document.addEventListener('visibilitychange',visibility); window.addEventListener('pagehide',hide);
    return () => {alive.current=false; clearInterval(interval); document.removeEventListener('visibilitychange',visibility); window.removeEventListener('pagehide',hide); pauseTimer.current=window.setTimeout(pause,0);};
  },[path]);
  const act=async (operation: () => Promise<void>) => {
    if(pending.current) return;
    pending.current=true; setBusy(true); setError('');
    try {await operation();} catch(e) {if(alive.current) setError(message(e));}
    finally {pending.current=false; if(alive.current) setBusy(false);}
  };
  const update=(data: LearningLesson) => {if(alive.current) setLesson(data);};
  const advance=() => act(async () => {
    if (lesson.step_index===3) await learningPost(path+'/listened');
    // The tutor saves its session on socket close. Acknowledge its durable result.
    if (lesson.step_index===4) {
      for (let i=0;i<8;i++) {
        const saved=await learningGet<LearningLesson>(path);
        if (saved.modules?.conversation?.completed) break;
        if (i===7) throw new Error('Для завершения разговора нужны три ответа. Продолжи разговор и заверши сессию.');
        await new Promise(resolve => setTimeout(resolve,200));
      }
    }
    update(await learningPost<LearningLesson>(path+'/advance',{step_index:lesson.step_index}));
    window.scrollTo({top:0});
  });
  const finish=() => act(async () => {
    finishBody.current ||= {elapsed_sec:elapsed()};
    const data=await learningPost<LearningLesson>(path+'/finish',finishBody.current,true);
    clock.current.running=false; update(data);
  });
  const step=lesson.step_index;
  const course={goal:lesson.content.spec.goal,lessonId:lesson.id,level:lesson.content.spec.level as Level,topicKey:lesson.content.spec.topic_key,
    conversation:{title:lesson.content.spec.title,objective:lesson.content.conversation.objective || lesson.content.spec.objective,prompts:lesson.content.conversation.prompts || []},
    onComplete:() => void advance()};
  const finished=lesson.status==='completed';
  const stageResult=lesson.modules?.[moduleKeys[step]];
  return <div className="learn-course">
    <div className="learn-course-bar">
      <button className="learn-link" onClick={onExit}>← Сохранить и выйти</button>
      <span>{lesson.content.spec.level} · урок {lesson.content.spec.ordinal}</span>
      <progress value={finished ? 6 : step} max={6} aria-label="Прогресс урока" />
      <p><strong>{steps[step]}</strong><span>{step+1} / 6</span></p>
    </div>
    {error && <div className="learn-course-notice learn-error" role="alert">{error}</div>}
    {lesson.status==='paused' ? <section className="learn-panel learn-course-notice"><h2>Урок сохранён</h2><p>Продолжим с этого этапа.</p>
      <button className="learn-primary" disabled={busy} onClick={() => void act(async () => {
        const data=await learningPost<LearningLesson>(path+'/start',{request_id:crypto.randomUUID()});
        clock.current={base:data.used_seconds,started:Date.now(),running:true}; update(data);
      })}>Продолжить</button></section>
    : <div className="learn-module" aria-busy={busy}>
      {step<5 && stageResult?.completed ? <section className="learn-panel learn-course-notice"><h2>{steps[step]} — готово</h2>
        <p>Результат сохранён. Можно перейти к следующей части урока.</p>
        <button className="learn-primary" disabled={busy} onClick={() => void advance()}>Продолжить урок →</button></section> : <>
        {step===0 && <GrammarScreen onExit={onExit} course={{...course,track:'learn'}} />}
        {step===1 && <GrammarScreen onExit={onExit} course={{...course,track:'test'}} />}
        {step===2 && <SrsScreen onExit={onExit} course={course} />}
        {step===3 && <ListeningScreen onExit={onExit} course={{...course,podcast:stageResult?.podcast,completed:stageResult?.completed}} />}
        {step===4 && <Tutor onExit={onExit} course={course} />}
      </>}
      {step===5 && <section className="learn-panel learn-course-notice">
        <span className="learn-eyebrow">Урок {lesson.content.spec.ordinal} · {lesson.content.spec.level}</span>
        <h1>{finished ? 'Урок завершён' : 'Твоя практика сегодня'}</h1>
        <p>{lesson.content.spec.title}: {lesson.content.spec.objective}.</p>
        {lesson.content.engine==='modules' ? <ul className="learn-completed-stages">{steps.slice(0,5).map((label,i) => <li key={label}>
          <span>✓ {label}</span><span>{lesson.modules?.[moduleKeys[i]]?.skipped ? 'Нет ошибок для повторения' : 'Готово'}</span>
        </li>)}</ul> : <p>Занятие из предыдущей версии программы. Результат сохранён.</p>}
        {lesson.result && <p className="learn-success" role="status">Прогресс сохранён. {lesson.result.checkpoint_due ? 'Открылась проверка уровня.' : 'Следующий урок уже ждёт.'}</p>}
        <button className="learn-primary" disabled={busy} onClick={() => finished ? onDone() : void finish()}>
          {busy ? 'Сохраняем…' : finished ? 'К программе →' : error ? 'Сохранить ещё раз' : 'Завершить урок'}</button>
      </section>}
    </div>}
  </div>;
}

function CheckpointRunner({initial,onExit,onDone}: {initial: Checkpoint; onExit: () => void; onDone: () => void}) {
  const [data,setData]=useState(initial), [busy,setBusy]=useState(false), [error,setError]=useState('');
  const [draft,setDraft]=useState(''), [token,setToken]=useState(''), [textMode,setTextMode]=useState(false);
  const alive=useRef(true), pending=useRef(false);
  useEffect(() => {alive.current=true; return () => {alive.current=false;};},[]);
  const send=async (answer: string) => {
    if(pending.current || !data.question) return;
    pending.current=true; setBusy(true); setError('');
    try {const updated=await learningPost<Checkpoint>(`/checkpoint/${data.id}/answer`,{question_id:data.question.id,answer,text_mode:textMode,voice_token:token || null});
      if(alive.current) {setData(updated); setDraft(''); setToken('');}}
    catch(e) {if(alive.current) setError(message(e));}
    finally {pending.current=false; if(alive.current) setBusy(false);}
  };
  const q=data.question;
  return <div className="learn-screen"><header className="learn-header"><button className="learn-link" onClick={onExit}>← Сохранить и выйти</button><span>После {data.after_count} уроков</span></header>
    <h1>Проверка развития</h1><p>Сравним навыки с прошлым результатом и настроим следующий цикл.</p>
    <progress max={data.total} value={data.position} aria-label="Прогресс диагностики" />
    {error && <p role="alert" className="learn-error">{error}</p>}
    {data.result ? <section className="learn-panel"><span className="learn-level">{data.result.cefr}</span><h2>Результат сохранён</h2>
      <p>До проверки: {data.result.level_before}. Сейчас по тесту: {data.result.cefr}.</p>
      <p>Завершённые уроки и освоенные навыки сохранены. Следующий цикл учитывает результат проверки.</p>
      {Object.entries(data.result.skills).map(([key,value]) => <p key={key}>{({grammar:'Грамматика',vocabulary:'Лексика',listening:'Понимание речи',speaking:'Устная речь'} as Record<string,string>)[key]}: {value.score !== undefined ? `${value.score}% · оценка AI по расшифровке` : `${value.correct}/${value.total}`}</p>)}
      {!data.result.skills.speaking && <p>Устная речь пока не оценена.</p>}
      <button className="learn-primary" onClick={onDone}>К следующему циклу →</button></section> : q && <section className="learn-panel">
      <p className="learn-muted">Задание {data.position+1} из {data.total}</p>
      {q.kind==='listening' && <AudioStory path={`/checkpoint/${data.id}/audio`} script={q.script || ''} textMode={textMode} onText={() => setTextMode(true)} />}
      {q.kind==='speaking' ? <><h2>{q.prompt}</h2><VoiceInput path={`/checkpoint/${data.id}/transcribe`} disabled={busy} onResult={(text,voiceToken) => {setDraft(text); setToken(voiceToken);}} />
        <label className="learn-input-label">Ответ<textarea value={draft} maxLength={1200} disabled={busy} onChange={e => {setDraft(e.target.value); setToken('');}} /></label>
        <p className="learn-muted">При текстовом ответе устная речь останется без оценки.</p><button className="learn-primary" disabled={busy || draft.trim().length<2} onClick={() => void send(draft)}>Завершить проверку</button></>
        : <Choices question={q} busy={busy} onAnswer={value => void send(value)} />}
    </section>}
  </div>;
}

export function LearningScreen({onExit,onPlacement}: {onExit: () => void; onPlacement: () => void}) {
  const [data,setData]=useState<LearningOverview | null>(null), [lesson,setLesson]=useState<LearningLesson | null>(null), [checkpoint,setCheckpoint]=useState<Checkpoint | null>(null);
  const [goal,setGoal]=useState('general'), [busy,setBusy]=useState(false), [error,setError]=useState('');
  const alive=useRef(true), pending=useRef(false), starting=useRef<{key: string; requestId: string} | null>(null), loadVersion=useRef(0);
  const load=async () => {
    const version=++loadVersion.current;
    const result=await learningGet<LearningOverview>('/overview');
    if(alive.current && version===loadVersion.current) {setData(result); setGoal(result.profile?.goal || 'general');}
  };
  useEffect(() => {
    alive.current=true; void load().catch(e => {if(alive.current) setError(message(e));});
    return () => {alive.current=false; if(starting.current) void learningPost(`/lessons/${starting.current.key}/pause`,{elapsed_sec:0,request_id:starting.current.requestId},true).catch(() => {});};
  },[]);
  const act=async (operation: () => Promise<void>) => {
    if(pending.current) return;
    pending.current=true; setBusy(true); setError('');
    try {await operation();} catch(e) {if(alive.current) setError(message(e));}
    finally {pending.current=false; if(alive.current) setBusy(false);}
  };
  const returnToProgram=() => {setLesson(null); setCheckpoint(null); void act(load);};
  const begin=() => act(async () => {
    if(data?.profile?.checkpoint_due) {
      const value=await learningPost<Checkpoint>('/checkpoint/start'); if(alive.current) setCheckpoint(value);
    } else if(data?.next_lesson) {
      starting.current={key:data.next_lesson.id,requestId:crypto.randomUUID()};
      const value=await learningPost<LearningLesson>(`/lessons/${data.next_lesson.id}/start`,{request_id:starting.current.requestId});
      if(alive.current) {setLesson(value); starting.current=null;}
    }
  });
  if(lesson) return <LessonRunner key={lesson.id} initial={lesson} onExit={returnToProgram} onDone={returnToProgram} />;
  if(checkpoint) return <CheckpointRunner key={checkpoint.id} initial={checkpoint} onExit={returnToProgram} onDone={returnToProgram} />;
  return <div className="learn-screen"><header className="learn-header"><button className="learn-link" onClick={onExit}>← Главная</button><span>Персональная программа</span></header>
    <h1>Твой английский, шаг за шагом</h1>
    {error && <p className="learn-error" role="alert">{error}<button className="learn-link" disabled={busy} onClick={() => void act(load)}>Повторить загрузку</button></p>}
    {!data && !error && <p role="status">Собираем твой маршрут…</p>}
    {data && !data.enabled && <p>Программа пока недоступна в этом сервисе.</p>}
    {data?.enabled && !data.profile && <section className="learn-panel">
      <span className="learn-eyebrow">Первый шаг</span><h2>{data.placement_level ? `Начинаем с ${data.placement_level}` : 'Определим точку старта'}</h2>
      <p>В каждом уроке — правило с практикой, работа над ошибками, слова с озвучкой, подкаст и разговор. После 20 занятий проверим изменения.</p>
      <LevelPicker value={data.placement_level || ''} onSaved={load} />
      {!data.placement_level ? <button className="learn-primary" onClick={onPlacement}>Пройти тест уровня →</button> : <>
        <label className="learn-input-label">Для чего тебе английский?<select value={goal} onChange={e => setGoal(e.target.value)}>{Object.entries(data.goals || {}).map(([key,label]) => <option key={key} value={key}>{label}</option>)}</select></label>
        <button className="learn-primary" disabled={busy} onClick={() => void act(async () => {await learningSetup(goal); await load();})}>{busy ? 'Сохраняем…' : 'Создать мою программу'}</button></>}
    </section>}
    {data?.profile && <><section className="learn-home-card"><span className="learn-eyebrow">{data.profile.checkpoint_due ? 'Контрольная точка' : `Урок ${data.next_lesson?.content.spec.ordinal || data.profile.completed_count+1}`}</span>
      <h2>{data.profile.checkpoint_due ? 'Пора проверить развитие' : data.next_lesson?.content.spec.title}</h2>
      <p>{data.profile.checkpoint_due ? 'Диагностика сохранит новый результат и откроет следующий цикл.' : `Научишься ${data.next_lesson?.content.spec.objective}. 15–20 минут, в своём темпе.`}</p>
      <button className="learn-primary" disabled={busy} onClick={() => void begin()}>{busy ? 'Открываем…' : data.profile.checkpoint_due ? 'Начать проверку' : data.next_lesson?.status==='active' || data.next_lesson?.status==='paused' ? 'Продолжить урок' : 'Начать урок'} →</button>
    </section><LearningMap data={data} onLesson={id => void act(async () => {
      const value=await learningPost<LearningLesson>(`/lessons/${id}/start`,{request_id:crypto.randomUUID()}); if(alive.current) setLesson(value);
    })} /><details className="learn-settings"><summary>Настройки программы</summary><LevelPicker value={data.profile.level} onSaved={load} /><label className="learn-input-label">Цель обучения<select value={goal} onChange={e => setGoal(e.target.value)}>{Object.entries(data.goals || {}).map(([key,label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      <button className="learn-secondary" disabled={busy} onClick={() => void act(async () => {await learningSetup(goal); await load();})}>Сохранить цель</button></details></>}
  </div>;
}
