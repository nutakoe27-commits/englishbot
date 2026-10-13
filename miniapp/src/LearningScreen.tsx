import { useEffect, useRef, useState } from 'react';
import { LearningMap } from './LearningProgress';
import { learningGet, learningPost, learningSetup, learningUrl, type LearningOverview, type LearningLesson,
  type LearningQuestion, type Checkpoint, type Turn } from './learningApi';
import './Learning.css';

const steps = ['Вспоминаем','Разбираем правило','Практикуемся','Слушаем','Общаемся','Подводим итог'];
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
  const [feedback,setFeedback]=useState<LearningQuestion | null>(null), [draft,setDraft]=useState(''), [voiceToken,setVoiceToken]=useState('');
  const clock=useRef({base:initial.used_seconds,started:Date.now(),running:initial.status==='active'});
  const current=useRef(lesson); current.current=lesson;
  const alive=useRef(true), pending=useRef(false), finishBody=useRef<Record<string,unknown> | null>(null), turnId=useRef('');
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
    const data=await learningPost<LearningLesson>(path+'/advance',{step_index:lesson.step_index}); update(data); setFeedback(null);
  });
  const answer=(value: string,q: LearningQuestion) => act(async () => {
    const data=await learningPost<{lesson: LearningLesson}>(path+'/answer',{question_id:q.id,answer:value}); update(data.lesson);
    if(alive.current) {
      const all=[...data.lesson.content.warmup,...data.lesson.content.practice,...data.lesson.content.listening.questions];
      setFeedback(all.find(question => question.id===q.id) || null);
    }
  });
  const send=() => act(async () => {
    if(!turnId.current) turnId.current=crypto.randomUUID();
    const data=await learningPost<{lesson: LearningLesson; turn: Turn}>(path+'/talk',{turn_id:turnId.current,text:draft,voice_token:voiceToken || null});
    update(data.lesson); if(alive.current) {setDraft(''); setVoiceToken(''); turnId.current='';}
  });
  const finish=() => act(async () => {
    finishBody.current ||= {elapsed_sec:elapsed()};
    const data=await learningPost<LearningLesson>(path+'/finish',finishBody.current,true);
    clock.current.running=false; update(data);
  });
  const step=lesson.step_index;
  const questions=step===0 ? lesson.content.warmup : step===2 ? lesson.content.practice : step===3 ? lesson.content.listening.questions : [];
  const next=questions.find(q => !q.answer), shown=feedback || next;
  const allQuestions=[...lesson.content.warmup,...lesson.content.practice,...lesson.content.listening.questions];
  const correct=allQuestions.filter(q => q.answer?.correct).length;
  return <div className="learn-screen learn-runner">
    <header className="learn-header"><button className="learn-link" onClick={onExit}>← Сохранить и выйти</button><span>{lesson.content.spec.level} · урок {lesson.content.spec.ordinal}</span></header>
    <div className="learn-stepper" aria-label="Этапы урока">{steps.map((label,i) => <span key={label} data-active={i===step} data-done={i<step}>{i<step ? '✓' : i+1}<small>{label}</small></span>)}</div>
    <h1>{lesson.content.spec.title}</h1><p className="learn-objective">Научишься {lesson.content.spec.objective}.</p>
    {error && <p className="learn-error" role="alert">{error}</p>}
    {lesson.status==='paused' ? <section className="learn-panel"><h2>Урок сохранён</h2><p>Продолжим с этого этапа.</p>
      <button className="learn-primary" disabled={busy} onClick={() => void act(async () => {
        const data=await learningPost<LearningLesson>(path+'/start',{request_id:crypto.randomUUID()}); clock.current={base:data.used_seconds,started:Date.now(),running:true}; update(data);
      })}>Продолжить</button></section> : <section className="learn-panel">
      <span className="learn-eyebrow">{steps[step]}</span>
      {step===1 && <><div className="learn-theory">{lesson.content.theory.split('\n\n').map((paragraph,i) => <p key={i}>{paragraph.replace(/\*\*/g,'')}</p>)}</div>
        <h3>Полезные слова</h3><div className="learn-word-list">{lesson.content.words.map(word => <span key={word.word}><b>{word.word}</b> — {word.translation}</span>)}</div>
        <button className="learn-primary" disabled={busy} onClick={() => void advance()}>Перейти к практике →</button></>}
      {step===3 && <AudioStory path={path+'/audio'} script={lesson.content.listening.script} textMode={lesson.listening_text_mode}
        onText={() => void act(async () => update(await learningPost<LearningLesson>(path+'/listening-text')))} />}
      {[0,2,3].includes(step) && <>
        {shown && <><p className="learn-muted">Задание {questions.findIndex(q => q.id===shown.id)+1} из {questions.length}</p>
          <Choices question={shown} busy={busy} onAnswer={value => void answer(value,shown)} /></>}
        {feedback ? <button className="learn-primary" disabled={busy} onClick={() => next ? setFeedback(null) : void advance()}>Дальше →</button>
          : !next && <button className="learn-primary" disabled={busy} onClick={() => void advance()}>Продолжить →</button>}
      </>}
      {step===4 && <><p>Твоя задача: {lesson.content.conversation.objective}. Дай хотя бы три ответа; можно продолжить до восьми.</p>
        <div className="learn-dialog" aria-live="polite"><div className="learn-tutor-message">{lesson.content.conversation.opening}</div>
          {lesson.conversation.map(turn => <div key={turn.id}><div className="learn-user-message">{turn.text}<small>{turn.verified_voice ? 'Из голосовой записи' : 'Текстовый ответ'}</small></div>
            <div className="learn-tutor-message">{turn.reply}</div><p className="learn-muted">{turn.feedback}</p></div>)}
        </div>
        {lesson.conversation.length<8 && <><VoiceInput path={path+'/transcribe'} disabled={busy} onResult={(text,token) => {setDraft(text); setVoiceToken(token); turnId.current='';}} />
          <label className="learn-input-label">Твой ответ по-английски<textarea value={draft} maxLength={1200} disabled={busy} onChange={e => {setDraft(e.target.value); setVoiceToken(''); turnId.current='';}} placeholder="Напиши или запиши ответ…" /></label>
          <button className="learn-primary" disabled={busy || draft.trim().length<2} onClick={() => void send()}>{busy ? 'Преподаватель отвечает…' : 'Отправить ответ'}</button></>}
        {lesson.conversation.length>=3 && <button className="learn-secondary" disabled={busy} onClick={() => void advance()}>Перейти к итогу →</button>}
      </>}
      {step===5 && <><h2>{lesson.status==='completed' ? 'Урок завершён' : 'Твоя практика сегодня'}</h2>
        <div className="learn-result-score">{correct} <small>из {allQuestions.length} заданий верно</small></div>
        <p>Ты практиковал: {lesson.content.spec.objective}.</p>
        <p>{lesson.conversation.some(t => t.verified_voice && t.assessed) ? 'Устные ответы получили обратную связь преподавателя.' : 'Для оценки устной речи нужна голосовая практика с проверкой преподавателя.'}</p>
        {lesson.status==='completed' ? <><p className="learn-success" role="status">Результат сохранён. {lesson.result?.checkpoint_due ? 'Открылась проверка уровня.' : 'Следующий урок готов.'}</p>
          <button className="learn-primary" onClick={onDone}>К программе →</button></> : <button className="learn-primary" disabled={busy} onClick={() => void finish()}>{busy ? 'Сохраняем результат…' : error ? 'Сохранить ещё раз' : 'Завершить урок'}</button>}
      </>}
    </section>}
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
      <p>В каждом уроке — повторение, объяснение, практика, аудирование и разговор. После 20 занятий проверим изменения.</p>
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
    })} /><details className="learn-settings"><summary>Цель обучения</summary><select value={goal} onChange={e => setGoal(e.target.value)}>{Object.entries(data.goals || {}).map(([key,label]) => <option key={key} value={key}>{label}</option>)}</select>
      <button className="learn-secondary" disabled={busy} onClick={() => void act(async () => {await learningSetup(goal); await load();})}>Сохранить цель</button></details></>}
  </div>;
}
