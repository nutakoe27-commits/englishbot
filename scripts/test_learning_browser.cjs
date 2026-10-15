// Real shared React screens; external AI/audio and API persistence are controlled.
const assert=require('node:assert/strict');
const {test,before,after}=require('node:test');
const {chromium}=require('playwright');
const fs=require('node:fs/promises');
const BASE=process.env.LEARNING_UI_URL || 'http://localhost:5173';
const SHOTS=process.env.LEARNING_SCREENSHOT_DIR;
const APP=!!process.env.LEARNING_APP_ENTRY;
let browser;
before(async()=>{browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH || '/usr/bin/chromium'});if(SHOTS)await fs.mkdir(SHOTS,{recursive:true});});
after(async()=>{await browser?.close();});
const copy=x=>JSON.parse(JSON.stringify(x));
function lesson(){return {id:'lesson-one',status:'prepared',step_index:0,used_seconds:0,activity_token:'active-one',modules:{},conversation:[],result:null,listening_text_mode:false,
 content:{engine:'modules',spec:{ordinal:1,cycle:1,position:1,level:'A1',topic_key:'a1_to_be',title:'Знакомимся',objective:'представиться и узнать о собеседнике',kind:'new',estimated_minutes:18},
 warmup:[],theory:'',practice:[],listening:{script:'',questions:[]},conversation:{},words:[{word:'name',translation:'имя'},{word:'city',translation:'город'}]}};}
async function fixture(t,{profile=true,mistakes=false,failFinish=false,failSrsFinish=false,width=390,step=0,listeningGate=null,failListening=false,cachedPodcast=false,entryMode='learning'}={}){
 const context=await browser.newContext({viewport:{width,height:844}});t.after(async()=>{if(SHOTS && context.pages()[0])await shot(context.pages()[0],'last-'+t.name.replace(/[^a-z0-9]+/gi,'-'));await context.close();});
 if(process.env.LEARNING_LUCIDE_PATH)await context.addInitScript({path:process.env.LEARNING_LUCIDE_PATH});
 await context.addInitScript(()=>{localStorage.setItem('englishbot_jwt','local-browser-test');localStorage.setItem('englishbot.tutorSettings.v1',JSON.stringify({level:'A1',mode:'chat'}));});
 context.setDefaultTimeout(7000);
 const row=lesson(),calls=[],errors=[];row.step_index=step;
 if(cachedPodcast)row.modules.listening={completed:false,podcast:{session_id:103,transcript:'My name is Alex. I live in Bristol.',audio_url:'/api/listening/audio/test.wav',used_words:['name']}};
 let level='A1',completed=0,turns=0;
 const overview=()=>({enabled:true,placement_level:level,goals:{general:'Общий английский',travel:'Путешествия',work:'Работа'},profile:profile?{goal:'general',goal_label:'Общий английский',level,completed_count:completed,cycle:1,cycle_completed:completed,until_checkpoint:20-completed,checkpoint_due:false}:null,next_lesson:profile?row:null,
  route:Array.from({length:20},(_,i)=>({...row.content.spec,ordinal:i+1,position:i+1,status:i<completed?'completed':i===completed?row.status:'locked',id:i===completed?row.id:null})),
  skills:['grammar','vocabulary','listening','speaking'].map((key,i)=>({key,label:['Грамматика','Лексика','Понимание речи','Разговор'][i],attempts:0,accuracy:null})),history:[]});
 await context.routeWebSocket('**/ws/voice**',ws=>{
  assert.equal(new URL(ws.url()).searchParams.get('learning_lesson_id'),row.id);
  ws.onMessage(raw=>{const data=JSON.parse(String(raw));if(data.type==='user_text'){turns++;if(process.env.LEARNING_DEBUG)console.log('WS USER TURN',turns);ws.send(JSON.stringify({type:'text',role:'tutor',text:'Tell me more about your city.'}));}});
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
  if(key.endsWith('/start')){row.status='active';return json(row);}
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
 await page.goto(BASE+(APP ? `/?mode=${entryMode}` : '/tests/learning.html'));await page.getByRole('heading',{name:entryMode==='listening' ? 'Слушание' : 'Твой английский, шаг за шагом',exact:true}).waitFor();
 return {page,calls,row,getLevel:()=>level};
}
async function shot(page,name){if(SHOTS)await page.screenshot({path:`${SHOTS}/${name}-${page.viewportSize().width}.png`,fullPage:true});}
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
