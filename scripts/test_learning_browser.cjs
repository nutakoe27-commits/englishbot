// Real shared React screens; external AI/audio and API persistence are controlled.
const assert=require('node:assert/strict');
const {test,before,after}=require('node:test');
const {chromium}=require('playwright');
const fs=require('node:fs/promises');
const BASE=process.env.LEARNING_UI_URL || 'http://localhost:5173';
const SHOTS=process.env.LEARNING_SCREENSHOT_DIR;
const APP=!!process.env.LEARNING_APP_ENTRY;
let browser;
before(async()=>{browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH || '/usr/bin/chromium',args:['--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream']});if(SHOTS)await fs.mkdir(SHOTS,{recursive:true});});
after(async()=>{await browser?.close();});
const copy=x=>JSON.parse(JSON.stringify(x));
function lesson(){return {id:'lesson-one',status:'prepared',step_index:0,used_seconds:0,activity_token:'active-one',modules:{},conversation:[],result:null,listening_text_mode:false,
 content:{engine:'modules',spec:{ordinal:1,cycle:1,position:1,level:'A1',topic_key:'a1_to_be',title:'Знакомимся',objective:'представиться и узнать о собеседнике',kind:'new',estimated_minutes:18},
 warmup:[],theory:'',practice:[],listening:{script:'',questions:[]},conversation:{objective:'представиться и узнать о собеседнике',opening:'Hello! What is your name?',prompts:['Hello! What is your name?','Where are you from?','What do you like doing?']},words:[{word:'name',translation:'имя'},{word:'city',translation:'город'}]}};}
async function fixture(t,{profile=true,mistakes=false,failFinish=false,failSrsFinish=false,width=390,height=844,step=0,listeningGate=null,failListening=false,cachedPodcast=false,entryMode='learning',accessKind='paid',lessonStatus='prepared',limitDuringStart=false,limitAfterTurns=null,savedTurns=0}={}){
 const context=await browser.newContext({viewport:{width,height},permissions:['microphone']});t.after(async()=>{if(SHOTS && context.pages()[0])await shot(context.pages()[0],'last-'+t.name.replace(/[^a-z0-9]+/gi,'-'));await context.close();});
 if(process.env.LEARNING_LUCIDE_PATH)await context.addInitScript({path:process.env.LEARNING_LUCIDE_PATH});
 await context.addInitScript(()=>{localStorage.setItem('englishbot_jwt','local-browser-test');localStorage.setItem('englishbot.tutorSettings.v1',JSON.stringify({level:'A1',mode:'chat'}));});
 context.setDefaultTimeout(7000);
 const row=lesson(),calls=[],errors=[],connections=[];row.step_index=step;row.status=lessonStatus;
 if(savedTurns)row.modules.conversation={completed:false,turns:savedTurns};
 if(cachedPodcast)row.modules.listening={completed:false,podcast:{session_id:103,transcript:'My name is Alex. I live in Bristol.',audio_url:'/api/listening/audio/test.wav',used_words:['name']}};
 let level='A1',completed=0,turns=savedTurns,started=accessKind==='exhausted'||lessonStatus!=='prepared'?1:0;
 const access=()=>{const full=['paid','trial','promotion'].includes(accessKind);const reset=new Date((Math.floor((Date.now()+3*3600000)/86400000)+1)*86400000-3*3600000).toISOString();return {full_access:full,trial_active:accessKind==='trial',trial_until:accessKind==='trial'?new Date(Date.now()+12*3600000).toISOString():null,daily_limit:full?null:1,used_today:started,remaining_today:full?null:Math.max(0,1-started),resets_at:reset,conversation_seconds:full?null:300};};
 const overview=()=>({enabled:true,access:access(),placement_level:level,goals:{general:'Общий английский',travel:'Путешествия',work:'Работа'},profile:profile?{goal:'general',goal_label:'Общий английский',level,completed_count:completed,cycle:1,cycle_completed:completed,until_checkpoint:20-completed,checkpoint_due:false}:null,next_lesson:profile?row:null,
  route:Array.from({length:20},(_,i)=>({...row.content.spec,ordinal:i+1,position:i+1,status:i<completed?'completed':i===completed?row.status:'locked',id:i===completed?row.id:null})),
  skills:['grammar','vocabulary','listening','speaking'].map((key,i)=>({key,label:['Грамматика','Лексика','Понимание речи','Разговор'][i],attempts:0,accuracy:null})),history:[]});
 await context.routeWebSocket('**/ws/voice**',ws=>{
  const params=Object.fromEntries(new URL(ws.url()).searchParams);connections.push(params);
  if(entryMode==='learning')assert.equal(params.learning_lesson_id,row.id);
  else assert.equal(params.learning_lesson_id,undefined);
  ws.send(JSON.stringify({type:'limits',scope:entryMode==='learning'?'lesson':'standalone',has_subscription:access().full_access,free_seconds_per_day:300,used_seconds_today:0,remaining_seconds:access().full_access?-1:300,resets_at:access().resets_at}));
  ws.send(JSON.stringify({type:'text',role:'tutor',text:entryMode==='learning' ? row.content.conversation.opening : params.role==='barista' ? 'Hey, what can I get you?' : 'Hi! How is your day?'}));
  ws.onMessage(raw=>{const data=JSON.parse(String(raw));if(data.type==='user_text'){turns++;if(process.env.LEARNING_DEBUG)console.log('WS USER TURN',turns);ws.send(JSON.stringify({type:'text',role:'tutor',text:'Tell me more about your city.'}));if(limitAfterTurns!==null && turns>=limitAfterTurns){row.modules.conversation={completed:turns>=3,turns};ws.send(JSON.stringify({type:'limit_reached',scope:'lesson',remaining_seconds:0,free_seconds_per_day:300,used_seconds_today:300,resets_at:access().resets_at}));ws.close({code:4004,reason:'Daily limit reached'});}}});
  ws.onClose(()=>{row.modules.conversation={completed:turns>=3,turns};});
 });
 await context.route('**/*',async route=>{
  const url=new URL(route.request().url());
  if(!['localhost','127.0.0.1'].includes(url.hostname))return route.abort();
  if(!url.pathname.startsWith('/api/'))return route.continue();
  if(url.pathname.endsWith('.wav')||url.pathname==='/api/tts/word'){
   const wav=Buffer.alloc(48044);wav.write('RIFF');wav.writeUInt32LE(wav.length-8,4);wav.write('WAVEfmt ',8);wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(24000,24);wav.writeUInt32LE(48000,28);wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(wav.length-44,40);
   return route.fulfill({contentType:'audio/wav',body:wav});
  }
  const json=(body,status=200)=>route.fulfill({status,contentType:'application/json',headers:{'access-control-allow-origin':BASE,'access-control-allow-methods':'GET,POST,PUT,OPTIONS','access-control-allow-headers':'*'},body:JSON.stringify(copy(body))});
  if(route.request().method()==='OPTIONS')return json({});
  const key=url.pathname,body=route.request().postDataJSON()||{};calls.push({key,body});
  if(key==='/api/auth/me')return json({id:123,first_name:'Test',tg_id:null,email:'test@example.invalid',identities:[{provider:'native'}],tutorial_done:true,has_subscription:true,cefr_level:level});
  if(key==='/api/auth/level'){level=body.level;if(row.status==='prepared')row.content.spec.level=level;return json({ok:true,level});}
  if(key==='/api/learning/overview')return json(overview());
  if(key==='/api/learning/access')return json(access());
  if(key==='/api/payments/plans')return json({plans:[{key:'monthly',days:30,amount_rub:999,title:'Месяц занятий',recurring:false}]});
  if(key==='/api/learning/profile'){profile=true;return json({ok:true});}
  if(key==='/api/grammar/lesson')return json({topic_key:'a1_to_be',title_ru:'Глагол to be',theory:'Используем **am** с I.\n\nI am a student. — Я студент.',exercises:[],session_id:''});
  if(key==='/api/grammar/lesson/exercises'||key==='/api/grammar/generate'){
   if(key.endsWith('/generate')&&!mistakes)return json({detail:'no_mistakes'},409);
   return json({session_id:'prepared-one',exercises:Array.from({length:4},(_,i)=>({id:'e'+i,type:'mcq',category:'tense',prompt:'I ___ a student.',choices:['am','is','are','be'],correct:'am',explanation:'I am.'}))});
  }
  if(key==='/api/grammar/start')return json({session_id:'101'});
  if(key==='/api/grammar/lesson/finish'||key==='/api/grammar/finish'){
   row.modules[row.step_index===0?'grammar':'mistakes']={completed:true,correct:4,total:4};return json({ok:true,passed:true,score:100,best_score:100,next_topic_key:null});
  }
  if(key==='/api/grammar/heartbeat'||key==='/api/grammar/abandon')return json({ok:true});
  if(key==='/api/srs/session'&&route.request().method()==='GET'){
   const reviewed=row.modules.words?.reviews||{};return json({cards:row.content.words.filter(w=>!reviewed[w.word]).map(w=>({...w,box:0}))});
  }
  if(key==='/api/srs/session/start'){row.modules.words||={reviews:{}};return json({session_id:'102'});}
  if(key==='/api/srs/review'){row.modules.words.reviews[body.word]=true;return json({ok:true,new_box:1,next_due_at:'2026-10-16T10:00:00'});}
  if(key==='/api/srs/session/finish'){if(failSrsFinish&&calls.filter(c=>c.key===key).length===1)return json({detail:'temporary_failure'},503);row.modules.words.completed=true;return json({ok:true});}
  if(key==='/api/srs/heartbeat')return json({ok:true});
  if(key==='/api/listening/generate'){
   if(listeningGate)await listeningGate;
   if(failListening&&calls.filter(c=>c.key===key).length===1)return json({detail:'synthetic_audio_unavailable'},503);
   const podcast={session_id:103,transcript:'My name is Alex. I live in Bristol.',audio_url:'/api/listening/audio/test.wav',used_words:['name']};
   row.modules.listening={completed:false,podcast};return json(podcast);
  }
  if(key==='/api/learning/lessons/lesson-one/listened'){row.modules.listening.completed=true;return json(row);}
  if(key==='/api/learning/lessons/lesson-one')return json(row);
  if(key.endsWith('/start')){if(limitDuringStart){started=1;return json({detail:'lesson_limit_reached'},402);}if(row.status==='prepared')started++;row.status='active';return json(row);}
  if(key.endsWith('/pause')){if(row.status!=='completed')row.status='paused';return json({ok:true});}
  if(key.endsWith('/heartbeat'))return json({active:row.status==='active',used_seconds:0});
  if(key.endsWith('/advance')){
   if(body.step_index===1&&!mistakes)row.modules.mistakes={completed:true,skipped:true};
   assert(row.modules[['grammar','mistakes','words','listening','conversation'][body.step_index]]?.completed,'stage cannot advance before the existing module saves');
   row.step_index=Math.max(row.step_index,body.step_index+1);return json(row);
  }
  if(key==='/api/learning/lessons/lesson-one/finish'){
   if(failFinish&&calls.filter(c=>c.key===key).length<=3)return json({detail:'temporary_failure'},503);
   if(row.status!=='completed'){row.status='completed';completed++;row.result={correct:6,total:6,objective:row.content.spec.objective,words:row.content.words,speaking_assessed:false,checkpoint_due:false};}
   return json(row);
  }
  if(key==='/api/user-words')return json({words:row.content.words,total:2,limit:3000});
  if(key==='/api/learner/recent-context')return json({recent_vocab:[],recent_mistakes:[]});
  return json({});
 });
 const page=await context.newPage();if(process.env.LEARNING_DEBUG)page.on('console',msg=>console.log(msg.type(),msg.text()));page.on('pageerror',e=>errors.push(e.message));t.after(()=>assert.deepEqual(errors,[]));
 await page.goto(BASE+(APP ? `/?mode=${entryMode}` : '/tests/learning.html'));await page.getByRole('heading',{name:entryMode==='listening' ? 'Слушание' : entryMode==='speaking' ? 'Разговор' : 'Твой английский, шаг за шагом',exact:true}).waitFor();
 return {page,calls,row,connections,getLevel:()=>level,activateSubscription:()=>{accessKind='paid';},newDay:()=>{accessKind='free';started=0;}};
}
async function shot(page,name){if(SHOTS){await page.evaluate(()=>Promise.all(document.getAnimations().filter(a=>a.effect?.getTiming().iterations!==Infinity).map(a=>a.finished.catch(()=>{}))));await page.screenshot({path:`${SHOTS}/${name}-${page.viewportSize().width}.png`,fullPage:true});}}
async function start(page){await page.getByRole('button',{name:'Начать урок →',exact:true}).click();await page.getByRole('heading',{name:'Глагол to be',exact:true}).waitFor();}
async function grammar(page){await page.getByRole('button',{name:'К практике',exact:false}).click();for(let i=0;i<4;i++){await page.getByRole('button',{name:'am',exact:true}).click();await page.locator('.grm-exercise > .grm-primary-btn').click();}await page.getByRole('button',{name:'Продолжить урок →',exact:true}).click();}
async function until(page,fn){await page.waitForFunction(fn);}
async function summary(f){
 await start(f.page);await grammar(f.page);
 await f.page.getByText('Пока нечего разбирать',{exact:true}).waitFor();await shot(f.page,'mistakes');
 await f.page.getByRole('button',{name:'Продолжить урок →',exact:true}).click();
 await f.page.getByRole('button',{name:'Начать повторение',exact:true}).click();
 await f.page.locator('.srs-v2__flip-word').waitFor();await f.page.getByRole('button',{name:'Послушать',exact:true}).click();await shot(f.page,'words');
 for(let i=0;i<2;i++){await f.page.getByRole('button',{name:'Показать перевод',exact:true}).click();await f.page.getByRole('button',{name:'Знаю',exact:true}).click();}
 await f.page.getByRole('button',{name:'Продолжить урок →',exact:true}).click();
 await f.page.locator('audio').waitFor();
 assert.equal(await f.page.locator('.lst-config').count(),0);
 assert.equal(await f.page.getByRole('button',{name:'Сгенерировать подкаст',exact:true}).count(),0);
 assert.equal(await f.page.getByRole('button',{name:'✦ Новый подкаст',exact:true}).count(),0);await shot(f.page,'listening');
 assert.equal(await f.page.getByRole('button',{name:'Продолжить урок →',exact:true}).isDisabled(),true);
 await f.page.locator('audio').evaluate(audio=>audio.dispatchEvent(new Event('ended')));
 await f.page.getByRole('button',{name:'Продолжить урок →',exact:true}).click();
 await f.page.getByRole('heading',{name:'Знакомимся',exact:true}).waitFor();await shot(f.page,'conversation-intro');
 assert.equal(f.connections.length,0);
 await f.page.getByRole('button',{name:'Начать разговор',exact:true}).click();
 await f.page.getByRole('textbox',{name:'Сообщение',exact:true}).waitFor();await shot(f.page,'conversation');
 assert.equal(await f.page.evaluate(()=>{const nav=document.querySelector('.bnav');const controls=document.querySelector('.tutor-controls');return !nav || controls.getBoundingClientRect().bottom<=nav.getBoundingClientRect().top+1;}),true,'conversation controls must stay above bottom navigation');
 for(let i=0;i<3;i++){await f.page.getByRole('textbox',{name:'Сообщение',exact:true}).fill('I live in Bristol. '+i);await f.page.getByRole('button',{name:'Отправить',exact:true}).click();await f.page.waitForFunction(()=>document.querySelector('textarea[aria-label="Сообщение"]')?.value==='');}
 await f.page.getByRole('button',{name:'Завершить',exact:true}).click();await f.page.getByRole('button',{name:'Завершить',exact:true}).last().click();
 await shot(f.page,'after-conversation-close');
 await f.page.getByRole('heading',{name:'Твоя практика сегодня',exact:true}).waitFor();await shot(f.page,'summary');
}

test('mobile goal setup uses the same lesson route',async t=>{
 const f=await fixture(t,{profile:false});await f.page.getByLabel('Для чего тебе английский?').selectOption('travel');
 await f.page.getByRole('button',{name:'Создать мою программу',exact:true}).click();await f.page.getByRole('button',{name:'Начать урок →',exact:true}).waitFor();
 assert.equal(f.calls.find(c=>c.key==='/api/learning/profile').body.goal,'travel');assert.equal(await f.page.locator('.learn-node').count(),20);
});
test('all stages reuse existing modules and retry completion without duplicating requests',{timeout:40000},async t=>{
 const f=await fixture(t,{failFinish:true});await summary(f);await f.page.getByRole('button',{name:'Завершить урок',exact:true}).click();
 await f.page.getByRole('button',{name:'Сохранить ещё раз',exact:true}).waitFor();await f.page.getByRole('button',{name:'Сохранить ещё раз',exact:true}).click();
 await f.page.getByRole('heading',{name:'Урок завершён',exact:true}).waitFor();
 for(const key of ['/api/grammar/lesson','/api/grammar/lesson/exercises','/api/grammar/lesson/finish','/api/srs/review','/api/listening/generate'])assert(f.calls.some(c=>c.key===key));
 assert(!f.calls.some(c=>/\/learning\/.*\/(answer|talk|transcribe)$/.test(c.key)),'course must not run duplicate practice engines');
 assert.equal(new Set(f.calls.filter(c=>c.key==='/api/learning/lessons/lesson-one/finish').map(c=>JSON.stringify(c.body))).size,1);
});
test('completed stage survives leaving and resumes without regenerating exercises',async t=>{
 const f=await fixture(t);await start(f.page);await grammar(f.page);await f.page.getByText('Пока нечего разбирать',{exact:true}).waitFor();
 await f.page.getByRole('button',{name:'Сохранить и выйти',exact:false}).click();await f.page.getByRole('button',{name:'Продолжить урок →',exact:true}).click();
 await f.page.getByText('Пока нечего разбирать',{exact:true}).waitFor();assert.equal(f.calls.filter(c=>c.key==='/api/grammar/lesson/exercises').length,1);
});
test('manual level selection updates the route and preserves its completed count',async t=>{
 const f=await fixture(t);await f.page.getByText('Настройки программы',{exact:true}).click();await f.page.getByLabel('Уровень занятий').selectOption('B2');
 await f.page.getByRole('button',{name:'Изменить уровень',exact:true}).click();await f.page.getByText('Уровень сохранён',{exact:true}).waitFor();
 assert.equal(f.getLevel(),'B2');assert.equal(await f.page.locator('.learn-level').textContent(),'B2');assert.equal(await f.page.locator('.learn-node').count(),20);
 await shot(f.page,'level-settings');
});
for(const width of [320,390,430,768])test(`route and grammar fit ${width}px without clipped content`,async t=>{
 const f=await fixture(t,{width});await shot(f.page,`route-${width}`);
 assert.equal(await f.page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 await start(f.page);await shot(f.page,`grammar-${width}`);
 assert.equal(await f.page.evaluate(()=>[...document.querySelectorAll('.learn-course-bar,.grm-theory,.mode-v2-top')].every(e=>e.getBoundingClientRect().right<=innerWidth+1)),true);
});

test('retrying the SRS finish does not submit the last word twice',async t=>{
 const f=await fixture(t,{failSrsFinish:true});await start(f.page);await grammar(f.page);
 await f.page.getByText('Пока нечего разбирать',{exact:true}).waitFor();await f.page.getByRole('button',{name:'Продолжить урок →',exact:true}).click();
 await f.page.getByRole('button',{name:'Начать повторение',exact:true}).click();
 for(let i=0;i<2;i++){await f.page.getByRole('button',{name:'Показать перевод',exact:true}).click();await f.page.getByRole('button',{name:'Знаю',exact:true}).click();}
 await f.page.getByRole('button',{name:'Сохранить ещё раз',exact:true}).click();
 await f.page.getByRole('button',{name:'Продолжить урок →',exact:true}).waitFor();
 assert.equal(f.calls.filter(c=>c.key==='/api/srs/review').length,2);
 assert.equal(new Set(f.calls.filter(c=>c.key==='/api/srs/session/finish').map(c=>JSON.stringify(c.body))).size,1);
});
for(const width of [320,430])test(`all shared module stages fit ${width}px`,async t=>{const f=await fixture(t,{width});await summary(f);});
if(APP)test('manual level is also available in the real profile tab',async t=>{
 const f=await fixture(t);await f.page.getByRole('navigation',{name:'Основная навигация'}).getByRole('button',{name:'Профиль',exact:true}).click();
 await f.page.getByLabel('Уровень занятий').selectOption('B1');await f.page.getByRole('button',{name:'Изменить уровень',exact:true}).click();
 await f.page.getByText('Уровень сохранён',{exact:true}).waitFor();assert.equal(f.getLevel(),'B1');await shot(f.page,'profile-level');
});

test('lesson listening starts automatically, ignores standalone preferences and creates one request in StrictMode',async t=>{
 let release;const gate=new Promise(resolve=>{release=resolve;});t.after(()=>release());
 const f=await fixture(t,{step:3,listeningGate:gate});
 await f.page.evaluate(()=>localStorage.setItem('englishbot.listeningSettings.v1',JSON.stringify({level:'C1',speed:1.25,durationMin:20,category:'news',useVocab:false})));
 await f.page.getByRole('button',{name:'Начать урок →',exact:true}).click();
 await f.page.getByText('Готовлю подкаст…',{exact:true}).waitFor();await shot(f.page,'listening-loading');
 assert.equal(await f.page.locator('.lst-config').count(),0);
 await f.page.waitForFunction(()=>document.querySelector('.lst-loading'));
 release();await f.page.locator('audio').waitFor();
 const requests=f.calls.filter(c=>c.key==='/api/listening/generate');
 assert.equal(requests.length,1);assert.equal(requests[0].body.learning_lesson_id,'lesson-one');
 for(const key of ['category','duration_min','speed','level','use_vocab'])assert.equal(key in requests[0].body,false);
});
test('failed automatic podcast generation retries on the same lesson without revealing settings',async t=>{
 const f=await fixture(t,{step:3,failListening:true});
 await f.page.getByRole('button',{name:'Начать урок →',exact:true}).click();
 await f.page.getByText('Не получилось сгенерировать',{exact:true}).waitFor();await shot(f.page,'listening-error');
 assert.equal(await f.page.locator('.lst-config').count(),0);
 await f.page.getByRole('button',{name:'Повторить',exact:true}).click();await f.page.locator('audio').waitFor();
 assert.equal(f.calls.filter(c=>c.key==='/api/listening/generate').length,2);
 assert.equal(f.row.step_index,3);
});
test('reopening lesson listening uses the saved podcast without regenerating',async t=>{
 const f=await fixture(t,{step:3,cachedPodcast:true});
 await f.page.getByRole('button',{name:'Начать урок →',exact:true}).click();await f.page.locator('audio').waitFor();
 await f.page.getByRole('button',{name:'Сохранить и выйти',exact:false}).click();
 await f.page.getByRole('button',{name:'Продолжить урок →',exact:true}).click();await f.page.locator('audio').waitFor();
 assert.equal(f.calls.filter(c=>c.key==='/api/listening/generate').length,0);
});
test('leaving while lesson audio is preparing keeps the stage and allows another attempt',async t=>{
 let release;const gate=new Promise(resolve=>{release=resolve;});t.after(()=>release());
 const f=await fixture(t,{step:3,listeningGate:gate});
 await f.page.getByRole('button',{name:'Начать урок →',exact:true}).click();
 await f.page.getByText('Готовлю подкаст…',{exact:true}).waitFor();
 await f.page.getByRole('button',{name:'К программе',exact:true}).click();
 await f.page.getByRole('heading',{name:'Твой английский, шаг за шагом',exact:true}).waitFor();
 release();await f.page.getByRole('button',{name:'Продолжить урок →',exact:true}).click();await f.page.locator('audio').waitFor();
 assert.equal(f.row.step_index,3);
});

if(APP)test('standalone listening still offers settings and waits for manual generation',async t=>{
 const f=await fixture(t,{entryMode:'listening'});
 await f.page.evaluate(()=>localStorage.setItem('englishbot.listeningSettings.v1',JSON.stringify({level:'C1',speed:1.25,durationMin:5,category:'science',useVocab:false})));
 await f.page.reload();await f.page.locator('.lst-config').waitFor();
 assert.equal(f.calls.filter(c=>c.key==='/api/listening/generate').length,0);
 await f.page.getByRole('button',{name:'Сгенерировать подкаст',exact:true}).click();await f.page.locator('audio').waitFor();
 const request=f.calls.find(c=>c.key==='/api/listening/generate').body;
 assert.equal(request.learning_lesson_id,undefined);assert.equal(request.level,'C1');assert.equal(request.speed,1.25);
 assert.equal(request.duration_min,5);assert.equal(request.category,'science');
 await f.page.getByRole('button',{name:'✦ Новый подкаст',exact:true}).waitFor();
});


if(APP)for(const width of [320,390,430,768])test(`conversation settings fit all five CEFR levels at ${width}px`,async t=>{
 const f=await fixture(t,{entryMode:'speaking',width});
 await f.page.getByRole('heading',{name:'Повседневная жизнь',exact:true}).waitFor();
 assert.equal(f.connections.length,0);
 await f.page.getByRole('button',{name:'Настройки',exact:true}).click();
 const modal=f.page.getByRole('dialog',{name:'Настройки разговора'});
 const levels=modal.getByRole('group',{name:'Уровень английского'});
 assert.equal(await levels.getByRole('button').count(),5);
 assert.equal(await modal.locator('.role-grid').evaluate(el=>getComputedStyle(el).gridTemplateColumns.split(' ').length),2);
 assert.equal(await modal.locator('.role-chip__label').evaluateAll(labels=>labels.every(el=>el.scrollWidth<=el.clientWidth+1)),true,'role names must fit without broken words');
 assert.equal(await f.page.evaluate(()=>{
  const modal=document.querySelector('.settings-popover');
  const rect=modal.getBoundingClientRect();
  return [...modal.querySelectorAll('.segmented__item,.role-chip')].every(el=>{
   const r=el.getBoundingClientRect();return r.left>=rect.left-1 && r.right<=rect.right+1 && el.scrollWidth<=el.clientWidth+1;
  }) && document.documentElement.scrollWidth<=innerWidth;
 }),true,'settings cards and their text must stay inside the modal');
 await shot(f.page,'conversation-settings');
 await levels.getByRole('button',{name:'C1 Продвинутый',exact:true}).click();
 assert.equal(await levels.getByRole('button',{name:'C1 Продвинутый',exact:true}).getAttribute('aria-pressed'),'true');
 await modal.getByRole('button',{name:'Применить',exact:true}).click();
 assert.equal(f.connections.length,0,'editing settings before start must not connect');
 assert.match(await f.page.getByRole('complementary',{name:'Тема разговора'}).textContent(),/C1/);
 await f.page.getByRole('button',{name:'Настройки',exact:true}).click();
 await modal.getByRole('group',{name:'Уровень английского'}).getByRole('button',{name:'A1 Начальный',exact:true}).click();
 await modal.getByRole('button',{name:'Бариста',exact:false}).click();
 await modal.getByRole('button',{name:'Применить',exact:true}).click();
 await f.page.getByRole('heading',{name:'Заказываем в кафе',exact:true}).waitFor();
 assert.equal(f.connections.length,0);
 await f.page.getByRole('button',{name:'Начать разговор',exact:true}).click();
 await f.page.getByRole('textbox',{name:'Сообщение',exact:true}).waitFor();
 await f.page.waitForFunction(()=>document.querySelector('.msg--tutor'));
 assert.equal(f.connections.length,1);assert.equal(f.connections[0].level,'A1');assert.equal(f.connections[0].role,'barista');
 await shot(f.page,'standalone-conversation');
});
if(APP)test('settings keep their footer reachable on a short screen and cancel discards draft',async t=>{
 const f=await fixture(t,{entryMode:'speaking',width:320,height:568});
 await f.page.getByRole('button',{name:'Настройки',exact:true}).click();
 const modal=f.page.getByRole('dialog',{name:'Настройки разговора'});
 await modal.getByRole('button',{name:'B2 Выше среднего',exact:true}).click();
 const footer=await modal.locator('.settings-popover__footer').boundingBox();
 assert(footer.y>=0 && footer.y+footer.height<=568,'footer must remain visible while settings scroll');
 await modal.locator('.settings-popover__content').evaluate(el=>el.scrollTop=el.scrollHeight);
 await shot(f.page,'conversation-settings-short');
 await f.page.keyboard.press('Escape');await modal.waitFor({state:'detached'});
 await f.page.getByRole('button',{name:'Настройки',exact:true}).click();
 assert.equal(await modal.getByRole('button',{name:'A1 Начальный',exact:true}).getAttribute('aria-pressed'),'true');
 assert.equal(f.connections.length,0);
});
for(const width of [320,390,430,768])test(`lesson conversation explains its topic before connecting at ${width}px`,async t=>{
 const f=await fixture(t,{step:4,width});
 await f.page.getByRole('button',{name:'Начать урок →',exact:true}).click();
 await f.page.getByRole('heading',{name:'Знакомимся',exact:true}).waitFor();
 await f.page.getByText('представиться и узнать о собеседнике',{exact:true}).waitFor();
 await f.page.getByText('Where are you from?',{exact:true}).waitFor();
 assert.equal(f.connections.length,0);assert.equal(await f.page.getByRole('textbox',{name:'Сообщение',exact:true}).count(),0);
 await f.page.getByRole('button',{name:'Настройки',exact:true}).click();
 const modal=f.page.getByRole('dialog',{name:'Настройки разговора'});
 await modal.getByText('Разговор в уроке · A1',{exact:true}).waitFor();
 assert.equal(await modal.getByRole('group',{name:'Уровень английского'}).count(),0);
 assert.equal(await modal.locator('.role-grid').count(),0);
 await modal.getByRole('button',{name:'Подробно',exact:false}).click();
 await modal.getByRole('button',{name:'Применить',exact:true}).click();
 assert.equal(f.connections.length,0);
 await shot(f.page,'lesson-conversation-intro');
 await f.page.getByRole('button',{name:'Начать разговор',exact:true}).click();
 await f.page.locator('.msg--tutor').filter({hasText:'Hello! What is your name?'}).waitFor();
 assert.equal(f.connections.length,1);assert.equal(f.connections[0].learning_lesson_id,f.row.id);assert.equal(f.connections[0].length,'long');
 await f.page.getByRole('heading',{name:'Знакомимся',exact:true}).waitFor();
 assert.equal(await f.page.evaluate(()=>{
  const controls=document.querySelector('.tutor-controls').getBoundingClientRect();
  const nav=document.querySelector('.bnav')?.getBoundingClientRect();
  return controls.right<=innerWidth+1 && controls.bottom<=(nav?.top ?? innerHeight)+1 && document.documentElement.scrollWidth<=innerWidth;
 }),true,'topic and composer must fit above navigation');
 await shot(f.page,'lesson-conversation-active');
});
if(APP)test('voice mode waits for Start before requesting the microphone',async t=>{
 const f=await fixture(t,{entryMode:'speaking'});
 await f.page.evaluate(()=>{
  window.micRequests=0;const original=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
  navigator.mediaDevices.getUserMedia=(...args)=>{window.micRequests++;return original(...args);};
 });
 await f.page.getByRole('button',{name:'Настройки',exact:true}).click();
 const modal=f.page.getByRole('dialog',{name:'Настройки разговора'});
 await modal.getByRole('button',{name:'Голос',exact:false}).click();
 await modal.getByRole('button',{name:'Применить',exact:true}).click();
 assert.equal(await f.page.evaluate(()=>window.micRequests),0);assert.equal(f.connections.length,0);
 await f.page.getByRole('button',{name:'Начать разговор',exact:true}).click();
 await f.page.locator('.msg--tutor').waitFor();
 assert.equal(await f.page.evaluate(()=>window.micRequests),1);assert.equal(f.connections.length,1);assert.equal(f.connections[0].mode,'voice');
 await shot(f.page,'conversation-voice');
});

test('lesson conversation keeps a readable chat area on a short mobile viewport',async t=>{
 const f=await fixture(t,{step:4,width:320,height:568});
 await f.page.getByRole('button',{name:'Начать урок →',exact:true}).click();
 await f.page.getByRole('button',{name:'Начать разговор',exact:true}).click();
 await f.page.locator('.msg--tutor').waitFor();
 const log=await f.page.locator('.tutor-log').boundingBox();
 assert(log.height>=80,'short screens need room for the actual dialogue');
 assert.equal(await f.page.evaluate(()=>{
  const controls=document.querySelector('.tutor-controls').getBoundingClientRect();
  const nav=document.querySelector('.bnav')?.getBoundingClientRect();
  return controls.bottom<=(nav?.top ?? innerHeight)+1;
 }),true);
 await shot(f.page,'lesson-conversation-short');
});


for(const width of [320,390,430,768])test(`free course access and paywall fit ${width}px and never start a blocked lesson`,async t=>{
 const f=await fixture(t,{accessKind:'exhausted',width});
 const access=f.page.getByRole('region',{name:'Доступ к программе'});
 await access.getByText('Новые уроки на сегодня закончились',{exact:true}).waitFor();
 await access.getByText(/00:00 МСК/).waitFor();await shot(f.page,'access-exhausted');
 assert.equal(await f.page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 await f.page.getByRole('button',{name:'Открыть подписку →',exact:true}).click();
 await f.page.getByRole('heading',{name:'Подписка',exact:true}).waitFor();
 await f.page.getByText('Месяц занятий',{exact:true}).waitFor();await f.page.getByText(/Полные уроки по программе/).waitFor();await shot(f.page,'access-subscribe');
 assert.equal(f.calls.filter(c=>c.key==='/api/learning/lessons/lesson-one/start').length,0);
 await f.page.getByRole('button',{name:'Закрыть',exact:true}).click();
 await access.getByText('Новые уроки на сегодня закончились',{exact:true}).waitFor();
});
test('a paused free lesson can resume after its daily start quota was spent',async t=>{
 const f=await fixture(t,{accessKind:'exhausted',lessonStatus:'paused',step:4});
 await f.page.getByRole('button',{name:'Продолжить урок →',exact:true}).click();
 await f.page.getByRole('heading',{name:'Знакомимся',exact:true}).waitFor();
 assert.equal(f.calls.filter(c=>c.key==='/api/learning/lessons/lesson-one/start').length,1);
 assert.equal(await f.page.getByRole('heading',{name:'Подписка',exact:true}).count(),0);
});
test('a stale quota response opens subscriptions and refreshes the access card',async t=>{
 const f=await fixture(t,{accessKind:'free',limitDuringStart:true});
 await f.page.getByRole('button',{name:'Начать урок →',exact:true}).click();
 await f.page.getByRole('heading',{name:'Подписка',exact:true}).waitFor();
 assert.equal(f.calls.filter(c=>c.key==='/api/learning/lessons/lesson-one/start').length,1);
 await f.page.getByRole('button',{name:'Закрыть',exact:true}).click();
 await f.page.getByText('Новые уроки на сегодня закончились',{exact:true}).waitFor();
});
test('closing subscription after payment refreshes access without losing course progress',async t=>{
 const f=await fixture(t,{accessKind:'exhausted'});
 await f.page.getByRole('button',{name:'Открыть подписку →',exact:true}).click();
 await f.page.getByRole('heading',{name:'Подписка',exact:true}).waitFor();
 f.activateSubscription();await f.page.getByRole('button',{name:'Закрыть',exact:true}).click();
 await f.page.getByText('Полный доступ',{exact:true}).waitFor();
 await f.page.getByRole('button',{name:'Начать урок →',exact:true}).click();
 await f.page.getByRole('heading',{name:'Глагол to be',exact:true}).waitFor();
 assert.equal(f.row.content.spec.ordinal,1);
});
test('the trial card shows the actual server expiry and full access',async t=>{
 const f=await fixture(t,{accessKind:'trial'});
 await f.page.getByText('Пробный полный доступ',{exact:true}).waitFor();
 await f.page.getByText(/Пробный доступ закончится через/).waitFor();
 assert.equal(await f.page.getByRole('button',{name:'Выбрать подписку',exact:true}).count(),0);
 await shot(f.page,'access-trial');
});
test('course access refreshes when returning after the daily reset',async t=>{
 const f=await fixture(t,{accessKind:'exhausted'});f.newDay();
 await f.page.evaluate(()=>window.dispatchEvent(new Event('focus')));
 await f.page.getByText('Твой бесплатный урок на сегодня',{exact:true}).waitFor();
 await f.page.getByRole('button',{name:'Начать урок →',exact:true}).waitFor();
});
for(const answers of [1,3])test(`lesson time limit saves ${answers} replies and offers the correct next step`,async t=>{
 const f=await fixture(t,{accessKind:'free',step:4,limitAfterTurns:answers,width:320});
 await f.page.getByRole('button',{name:'Начать урок →',exact:true}).click();
 await f.page.getByRole('button',{name:'Начать разговор',exact:true}).click();
 await f.page.getByLabel('Осталось времени разговора в уроке',{exact:true}).waitFor();
 for(let i=0;i<answers;i++){
  await f.page.getByRole('textbox',{name:'Сообщение',exact:true}).fill('My name is Alex. '+i);
  await f.page.getByRole('button',{name:'Отправить',exact:true}).click();
  if(i<answers-1)await f.page.waitForFunction(()=>document.querySelector('textarea[aria-label="Сообщение"]')?.value==='');
 }
 const popup=f.page.getByRole('dialog',{name:'Время разговора в уроке'});await popup.waitFor();
 await shot(f.page,'course-budget-'+answers);
 assert.equal(await popup.getByRole('button',{name:'К итогу урока',exact:true}).count(),answers>=3?1:0);
 if(answers>=3){await popup.getByRole('button',{name:'К итогу урока',exact:true}).click();await f.page.getByRole('heading',{name:'Твоя практика сегодня',exact:true}).waitFor();}
 else {
  await popup.getByRole('button',{name:'Выбрать подписку',exact:true}).click();
  await f.page.getByRole('heading',{name:'Подписка',exact:true}).waitFor();
  await f.page.getByText('Месяц занятий',{exact:true}).waitFor();
  await f.page.getByRole('button',{name:'Закрыть',exact:true}).click();await popup.waitFor();
  await popup.getByRole('button',{name:'К программе',exact:true}).click();
  await f.page.getByRole('button',{name:'Продолжить урок →',exact:true}).waitFor();
 }
});
