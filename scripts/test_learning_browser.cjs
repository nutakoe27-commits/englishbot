// With Vite running: CHROMIUM_PATH=/usr/bin/chromium node --test scripts/test_learning_browser.cjs
const assert=require('node:assert/strict');
const {test,before,after}=require('node:test');
const {chromium}=require('playwright');
const BASE=process.env.LEARNING_UI_URL || 'http://localhost:5173';
let browser;
before(async () => {browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_PATH ? {executablePath:process.env.CHROMIUM_PATH} : {})});});
after(async () => {await browser?.close();});
const clone=value => JSON.parse(JSON.stringify(value));
const question=id => ({id,prompt:id==='w0' ? 'name' : id==='l0' ? 'Where is Alex from?' : 'I ___ a student.',choices:['am','is','are','be']});
function lesson() {
  return {id:'lesson-one',status:'prepared',step_index:0,used_seconds:0,conversation:[],result:null,listening_text_mode:false,
    content:{spec:{ordinal:1,cycle:1,position:1,level:'A1',topic_key:'a1_to_be',title:'Знакомимся',objective:'представиться и узнать о собеседнике',kind:'new',estimated_minutes:18},
      warmup:[question('w0')],theory:'I am a student. — Я студент.\n\nИспользуем am с I.',practice:[question('p0'),question('p1')],
      listening:{script:'My name is Alex. I am from Bristol. I work in a shop.',questions:[question('l0')]},
      conversation:{objective:'представиться',opening:'What is your name?',prompts:['What is your name?','Where do you live?','What do you enjoy?']},
      words:[{word:'name',translation:'имя'}]}};
}
async function until(check) {for(let i=0;i<100;i++){if(check())return;await new Promise(r=>setTimeout(r,20));}assert(check());}
async function fixture(t,{profile=true,due=false,failFinish=false,heartbeatGate=null}={}) {
  const context=await browser.newContext({viewport:{width:390,height:844}}); t.after(()=>context.close());
  let row=lesson(), completed=due ? 20 : 0, checkpoint=null;
  const calls=[], errors=[];
  const overview=()=>({enabled:true,placement_level:'A1',goals:{general:'Общий английский',travel:'Путешествия',work:'Работа',daily:'Повседневное общение'},
    profile:profile ? {goal:'general',goal_label:'Общий английский',level:'A1',completed_count:completed,cycle:1,cycle_completed:completed,until_checkpoint:20-completed,checkpoint_due:due} : null,
    next_lesson:profile && !due ? row : null,
    route:Array.from({length:20},(_,i)=>({...row.content.spec,ordinal:i+1,position:i+1,status:i<completed ? 'completed' : i===completed ? row.status : 'locked',id:i===completed ? row.id : null})),
    skills:['grammar','vocabulary','listening','speaking'].map((key,i)=>({key,label:['Грамматика','Лексика','Понимание речи','Разговор'][i],attempts:0,accuracy:null})),history:[]});
  await context.route('**/*',async route=>{
    const url=new URL(route.request().url());
    if(!['localhost','127.0.0.1'].includes(url.hostname)) return route.abort();
    if(!url.pathname.startsWith('/api/learning/')) return route.continue();
    const json=(body,status=200)=>route.fulfill({status,contentType:'application/json',headers:{'access-control-allow-origin':BASE,'access-control-allow-methods':'GET,POST,PUT,OPTIONS','access-control-allow-headers':'*'},body:JSON.stringify(clone(body))});
    if(route.request().method()==='OPTIONS') return json({});
    const key=url.pathname.slice('/api/learning'.length),body=route.request().postDataJSON() || {};
    calls.push({key,body});
    if(key==='/overview') return json(overview());
    if(key==='/profile') {profile=true;return json({ok:true});}
    if(key.endsWith('/audio')) return json({detail:'learning_audio_unavailable'},503);
    if(key==='/checkpoint/start') {
      checkpoint ||= {id:'checkpoint-one',status:'active',position:0,total:2,after_count:20,level_before:'A1',question:{...question('cp0'),kind:'choice'},result:null};
      return json(checkpoint);
    }
    if(key==='/checkpoint/checkpoint-one/answer') {
      checkpoint.position++;
      if(checkpoint.position===1) checkpoint.question={id:'cp_speaking',kind:'speaking',prompt:'Расскажи о своих планах по-английски.',choices:[]};
      else {checkpoint.question=null;checkpoint.status='completed';checkpoint.result={cefr:'A1',level_before:'A1',after_lessons:20,skills:{grammar:{correct:1,total:1}}};due=false;}
      return json(checkpoint);
    }
    if(key.endsWith('/start')) {row.status='active';return json(row);}
    if(key.endsWith('/heartbeat')) {if(heartbeatGate) await heartbeatGate; return json({active:row.status==='active',used_seconds:0});}
    if(key.endsWith('/pause')) {if(row.status!=='completed')row.status='paused';return json({ok:true});}
    if(key.endsWith('/listening-text')) {row.listening_text_mode=true;return json(row);}
    if(key.endsWith('/answer')) {
      const all=[...row.content.warmup,...row.content.practice,...row.content.listening.questions];
      const q=all.find(q=>q.id===body.question_id);
      q.answer ||= {value:body.answer,correct:body.answer==='am',correct_answer:'am',explanation:'I am.'};
      return json({lesson:row,answer:q.answer});
    }
    if(key.endsWith('/advance')) {row.step_index=Math.max(row.step_index,body.step_index+1);return json(row);}
    if(key.endsWith('/talk')) {
      let turn=row.conversation.find(t=>t.id===body.turn_id);
      if(!turn){turn={id:body.turn_id,text:body.text,reply:'Tell me more.',feedback:'Ответ сохранён.',verified_voice:false,assessed:false,score:null};row.conversation.push(turn);}
      return json({lesson:row,turn});
    }
    if(key.endsWith('/finish')) {
      const n=calls.filter(c=>c.key.endsWith('/finish')).length;
      if(failFinish && n<=3) return json({detail:'temporary_failure'},503);
      if(row.status!=='completed'){row.status='completed';completed++;row.result={correct:4,total:4,objective:row.content.spec.objective,words:row.content.words,speaking_assessed:false,checkpoint_due:false};}
      return json(row);
    }
    return json({detail:'unexpected_mock_request'},500);
  });
  const page=await context.newPage(); page.on('pageerror',e=>errors.push(e.message));
  t.after(()=>assert.deepEqual(errors,[]));
  await page.goto(BASE+'/tests/learning.html');
  await page.getByRole('heading',{name:'Твой английский, шаг за шагом'}).waitFor();
  return {page,calls,count:suffix=>calls.filter(c=>c.key.endsWith(suffix)).length,getRow:()=>row};
}
async function start(page) {await page.getByRole('button',{name:'Начать урок →',exact:true}).click();await page.getByRole('heading',{name:'name',exact:true}).waitFor();}
async function doQuestion(page) {await page.getByRole('button',{name:'am',exact:true}).click();await page.getByText('Верно!',{exact:true}).waitFor();await page.getByRole('button',{name:'Дальше →',exact:true}).click();}
async function reachSummary(page) {
  await start(page); await doQuestion(page);
  await page.getByRole('button',{name:'Перейти к практике →',exact:true}).click();
  await doQuestion(page); await doQuestion(page);
  await page.getByRole('button',{name:'Открыть текст вместо аудио',exact:true}).click();
  await page.getByText('Этот этап учитывается как чтение.',{exact:false}).waitFor();
  await doQuestion(page);
  for(let i=0;i<3;i++) {
    await page.getByPlaceholder('Напиши или запиши ответ…').fill('I live in Rome and I like music.');
    await page.getByRole('button',{name:'Отправить ответ',exact:true}).click();
    await page.getByRole('button',{name:'Отправить ответ',exact:true}).waitFor();
    await page.waitForFunction(()=>document.querySelector('textarea')?.value==='');
  }
  await page.getByRole('button',{name:'Перейти к итогу →',exact:true}).click();
  await page.getByRole('heading',{name:'Твоя практика сегодня',exact:true}).waitFor();
}

test('goal setup and route are usable on a mobile viewport',async t=>{
  const f=await fixture(t,{profile:false});
  await f.page.getByLabel('Для чего тебе английский?').selectOption('travel');
  await f.page.getByRole('button',{name:'Создать мою программу',exact:true}).click();
  await f.page.getByRole('button',{name:'Начать урок →',exact:true}).waitFor();
  assert.equal(f.calls.find(c=>c.key==='/profile').body.goal,'travel');
  assert.equal(await f.page.locator('.learn-node').count(),20);
  assert.equal(await f.page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
});
test('all lesson stages work and a failed finish can be retried with the same payload',async t=>{
  const f=await fixture(t,{failFinish:true}); await reachSummary(f.page);
  assert.equal(f.count('/pause'),0,'StrictMode must not pause a freshly started lesson');
  await f.page.getByRole('button',{name:'Завершить урок',exact:true}).click();
  await f.page.getByRole('button',{name:'Сохранить ещё раз',exact:true}).waitFor();
  assert.equal(f.count('/finish'),3);
  await f.page.getByRole('button',{name:'Сохранить ещё раз',exact:true}).click();
  await f.page.getByRole('heading',{name:'Урок завершён',exact:true}).waitFor();
  assert.equal(new Set(f.calls.filter(c=>c.key.endsWith('/finish')).map(c=>JSON.stringify(c.body))).size,1);
  assert.equal(f.getRow().conversation.length,3);
});
test('leaving and reopening a lesson resumes the saved stage',async t=>{
  const f=await fixture(t); await start(f.page); await doQuestion(f.page);
  await f.page.getByRole('button',{name:'Сохранить и выйти',exact:false}).click();
  await until(()=>f.count('/pause')===1);
  await f.page.getByRole('button',{name:'Продолжить урок →',exact:true}).click();
  await f.page.getByRole('button',{name:'Перейти к практике →',exact:true}).waitFor();
  assert.equal(f.getRow().step_index,1);
  assert.equal(f.getRow().id,'lesson-one');
});
test('the twentieth-lesson checkpoint opens and records a result without invented speaking scores',async t=>{
  const f=await fixture(t,{due:true});
  await f.page.getByRole('button',{name:'Начать проверку →',exact:true}).click();
  await f.page.getByRole('button',{name:'am',exact:true}).click();
  await f.page.getByLabel('Ответ',{exact:true}).fill('I will travel tomorrow.');
  await f.page.getByRole('button',{name:'Завершить проверку',exact:true}).click();
  await f.page.getByRole('heading',{name:'Результат сохранён',exact:true}).waitFor();
  await f.page.getByText('Устная речь пока не оценена.',{exact:true}).waitFor();
});

test('a delayed heartbeat cannot replace the completed result with a paused screen',async t=>{
  let release;
  const heartbeatGate=new Promise(resolve=>{release=resolve;});
  t.after(()=>release());
  const f=await fixture(t,{heartbeatGate});
  await f.page.clock.install();
  await reachSummary(f.page);
  await f.page.clock.runFor(20001);
  await until(()=>f.count('/heartbeat')===1);
  await f.page.getByRole('button',{name:'Завершить урок',exact:true}).click();
  await f.page.getByRole('heading',{name:'Урок завершён',exact:true}).waitFor();
  release();
  await f.page.waitForTimeout(100);
  await f.page.getByRole('button',{name:'К программе →',exact:true}).waitFor();
});
