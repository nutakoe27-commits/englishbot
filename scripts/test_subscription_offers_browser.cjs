// Real application entry points and subscription screen; network boundary mocked.
const assert = require('node:assert/strict');
const { test, before, after } = require('node:test');
const { chromium } = require('playwright');
const fs = require('node:fs/promises');
const path = require('node:path');
const BASE = process.env.OFFERS_UI_URL || 'http://localhost:5173';
const ADMIN = process.env.OFFERS_ADMIN_URL || 'http://localhost:5174';
const SHOTS = process.env.OFFERS_SCREENSHOT_DIR;
const reasons = [
  ['price','Для меня дорого'], ['time','Не хватает времени заниматься'],
  ['uncertain','Пока не понял(а), подходит ли мне сервис'], ['free_enough','Мне хватает бесплатных возможностей'],
  ['missing_features','Не хватает нужных функций или материалов'], ['technical','Возникли технические проблемы'],
  ['payment','Не получается оплатить'], ['other','Другое'],
].map(([code,label]) => ({code,label}));
let browser;
before(async () => {
  browser = await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH || '/usr/bin/chromium'});
  if (SHOTS) await fs.mkdir(SHOTS,{recursive:true});
});
after(async () => { await browser?.close(); });

function analytics(empty=false) {
  return { days:30, timezone:'Europe/Moscow',promo_code:'SALE20',discount_percent:20,followup_promo_code:'OLD50',followup_discount_percent:50,
    impressions:empty?0:120,users_shown:empty?0:80,declines:empty?0:36,dismissals:empty?0:12,tariff_clicks:empty?0:24,
    users_clicked:empty?0:20,answers:empty?0:30,answer_rate:empty?0:37.5,click_rate:empty?0:25,
    sale20_tariff_clicks:empty?0:14,old50_tariff_clicks:empty?0:10,paid_users:empty?0:8,revenue_rub:empty?0:4800,
    payments_by_promo:[{code:'SALE20',discount_percent:20,users:empty?0:3,revenue_rub:empty?0:2400},{code:'OLD50',discount_percent:50,users:empty?0:5,revenue_rub:empty?0:2400}],
    reasons:reasons.map((r,i)=>({...r,count:empty?0:i===0?15:i===1?6:i===2?4:1,percent:empty?0:i===0?50:i===1?20:i===2?13.3:3.3})),
    daily:Array.from({length:7},(_,i)=>({date:`2026-11-${String(i+1).padStart(2,'0')}`,shown:empty?0:10+i,tariffs:empty?0:2,answers:empty?0:3})),
    recent_answers:empty?[]:[{user_id:123,name:'Александра',reason:'price',reason_label:reasons[0].label,answered_at:'2026-11-07T10:30:00Z',clicked_tariffs:true,paid:true},
      {user_id:124,name:'Михаил',reason:'missing_features',reason_label:reasons[4].label,answered_at:'2026-11-06T10:30:00Z',clicked_tariffs:false,paid:false}],
  };
}

async function fixture(t, {width=390,height=844,dark=false,eligible=true,onboarding=false,url='/?app=1',admin=false,empty=false,legacyUuid=false,payIntent=false,failClaim=false}={}) {
  const context = await browser.newContext({viewport:{width,height},serviceWorkers:'block'});
  t.after(async()=>{
    if(process.env.OFFERS_DEBUG) for(const page of context.pages()) console.log('DEBUG PAGE',await page.locator('body').innerText());
    await context.close();
  });
  await context.addInitScript(({dark,legacyUuid,payIntent})=>{
    localStorage.setItem('englishbot_jwt','synthetic-browser-session');
    localStorage.setItem('englishbot_admin_token','synthetic-admin-session');
    localStorage.setItem('et_theme',dark?'dark':'light');
    if(legacyUuid) Object.defineProperty(crypto,'randomUUID',{value:undefined});
    if(payIntent) localStorage.setItem('et_pay_intent','1');
  },{dark,legacyUuid,payIntent});
  const state={eligible,claimed:false,cooldown:true,answered:false,failAnswer:false,failAdmin:false,onboarding};
  const calls=[];
  await context.route('**/*',async route=>{
    const u = new URL(route.request().url());
    if (!['localhost','127.0.0.1'].includes(u.hostname)) return route.abort();
    if (!u.pathname.startsWith('/api/')) return route.continue();
    const json=(body,status=200)=>route.fulfill({status,contentType:'application/json',headers:{'access-control-allow-origin':'*','access-control-allow-headers':'*','access-control-allow-methods':'GET,POST,PUT,OPTIONS'},body:JSON.stringify(body)});
    if(route.request().method()==='OPTIONS') return json({});
    const body=route.request().postDataJSON() || {};
    calls.push({path:u.pathname,body,query:u.searchParams});
    if(u.pathname==='/api/auth/me') return json({id:123,first_name:'Александра',email:'browser@example.invalid',tg_id:null,identities:[],has_subscription:!state.eligible,
      trial_active:false,tutorial_done:!state.onboarding,cefr_level:'A1'});
    if(u.pathname==='/api/auth/tutorial/complete') {state.onboarding=false;return json({ok:true});}
    if(u.pathname==='/api/me/progress') return json({streak:{current:3,best:5},total_minutes:48,total_words:12,achievements_earned:2,achievements_total:10});
    if(u.pathname==='/api/me/level') return json({level:2,lifetime_points:150,into_level:50,level_span:100,progress_pct:50});
    if(u.pathname==='/api/learning/overview') return json({enabled:false});
    if(u.pathname==='/api/learning/access') return json({full_access:false,daily_limit:1,used_today:0,remaining_today:1,conversation_seconds:300});
    if(u.pathname==='/api/push/key') return json({enabled:false});
    if(u.pathname==='/api/payments/plans') return json({plans:[
      {key:'trial7',title:'Пробная неделя',days:7,amount_rub:99,recurring:false},
      {key:'monthly',title:'Подписка на месяц',days:30,amount_rub:999,recurring:false},
      {key:'yearly',title:'Подписка на год',days:365,amount_rub:7999,recurring:false},
      {key:'twoyear',title:'Подписка на 2 года',days:730,amount_rub:14999,recurring:false},
    ]});
    if(u.pathname==='/api/payments/promo/check') return json({valid:true,already_used:false,discount_percent:u.searchParams.get('code')==='OLD50'?50:20});
    if(u.pathname==='/api/subscription-offers/claim') {
      if(failClaim) return json({detail:'synthetic campaign outage'},503);
      if(!state.eligible || state.answered || state.claimed && state.cooldown) return json({show:false,answered:state.answered});
      state.claimed=true;state.cooldown=true;
      return json({show:true,impression_id:body.request_id,audience:'trial_expired',promo_code:'SALE20',discount_percent:20,reasons});
    }
    if(u.pathname==='/api/subscription-offers/event') return json({ok:true});
    if(u.pathname==='/api/subscription-offers/answer') {
      if(state.failAnswer) return json({detail:'synthetic failure'},503);
      state.answered=true;return json({ok:true,reason:body.reason,promo_code:'OLD50',discount_percent:50});
    }
    if(u.pathname==='/api/admin/me') return json({ok:true});
    if(u.pathname==='/api/admin/subscription-offers') return state.failAdmin?json({detail:'Временная ошибка'},503):json({...analytics(empty),days:Number(u.searchParams.get('days'))});
    return json({});
  });
  const page=await context.newPage();
  page.setDefaultTimeout(8000);
  const errors=[];page.on('pageerror',error=>errors.push(String(error)));
  t.after(()=>assert.deepEqual(errors,[],'no uncaught browser errors'));
  await page.goto((admin?ADMIN:BASE)+(admin?'#/subscription-offers':url));
  return {page,state,calls};
}
const offer = page=>page.getByRole('dialog',{name:'Продолжим заниматься?'});
const survey = page=>page.getByRole('dialog',{name:'Почему вы пока не хотите оформить подписку?'});
const thanks = page=>page.getByRole('dialog',{name:'Спасибо за ответ!'});
async function shot(page,name) {if(SHOTS) {await page.mouse.move(0,0);await page.screenshot({path:path.join(SHOTS,name+'.png'),fullPage:true,animations:'disabled'});}}
async function fit(page) {
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'page must not overflow horizontally');
  const dialog=page.locator('.retention-dialog');
  if(await dialog.count()) assert.equal(await dialog.evaluate(el=>{const r=el.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight+1&&el.scrollWidth<=el.clientWidth;}),true,'dialog fits viewport and scrolls internally');
}

test('first offer uses SALE20; StrictMode makes one claim',async t=>{
  const f=await fixture(t);await offer(f.page).waitFor();
  assert.match(await offer(f.page).innerText(),/SALE20/);
  assert.equal(f.calls.filter(c=>c.path.endsWith('/claim')).length,1);
  await f.page.getByRole('button',{name:'Закрыть предложение'}).click();
  await offer(f.page).waitFor({state:'detached'});assert.equal(f.state.answered,false);
});
test('older WebViews can claim an offer without randomUUID',async t=>{
  const f=await fixture(t,{legacyUuid:true});await offer(f.page).waitFor();
  assert.match(f.calls.find(c=>c.path.endsWith('/claim')).body.request_id,/^[a-f0-9-]{36}$/);
});
test('first CTA opens existing tariffs with SALE20 applied',async t=>{
  const {page}=await fixture(t);await offer(page).getByRole('button',{name:'Перейти к тарифам'}).click();
  await page.getByText('Скидка 20% применена ко всем тарифам.',{exact:true}).waitFor();
  assert.equal(await page.getByPlaceholder('Промокод (если есть)').inputValue(),'SALE20');
});
test('answer is fixed choice; thanks offers OLD50 and opens tariffs at 50%',async t=>{
  const {page,state,calls}=await fixture(t);await offer(page).getByRole('button',{name:'Спасибо, не надо'}).click();
  await survey(page).waitFor();assert.equal(await survey(page).locator('.retention-reason').count(),8);
  assert.equal(await survey(page).locator('input,textarea').count(),0);
  assert.match(await survey(page).locator('.retention-reason').last().innerText(),/Другое/);
  await survey(page).getByRole('button',{name:'Для меня дорого'}).click();await thanks(page).waitFor();
  assert.equal(state.answered,true);assert.match(await thanks(page).innerText(),/OLD50/);
  await thanks(page).getByRole('button',{name:'Перейти к тарифам'}).click();
  await page.getByText('Скидка 50% применена ко всем тарифам.',{exact:true}).waitFor();
  assert.equal(await page.getByPlaceholder('Промокод (если есть)').inputValue(),'OLD50');
  assert.equal(calls.find(c=>c.path.endsWith('/event')&&c.body.action==='tariffs').body.promo_code,'OLD50');
});
test('failed save stays in survey; retry saves Other without free text',async t=>{
  const f=await fixture(t);f.state.failAnswer=true;await offer(f.page).getByRole('button',{name:'Спасибо, не надо'}).click();
  await survey(f.page).getByRole('button',{name:'Другое',exact:true}).click();await f.page.getByRole('alert').waitFor();
  assert.equal(f.state.answered,false);assert.equal(await thanks(f.page).count(),0);
  f.state.failAnswer=false;await survey(f.page).getByRole('button',{name:'Другое',exact:true}).click();await thanks(f.page).waitFor();
  assert.equal(f.calls.filter(c=>c.path.endsWith('/answer')).at(-1).body.reason,'other');
});
test('Escape closes the survey without submitting an answer',async t=>{
  const f=await fixture(t);await offer(f.page).getByRole('button',{name:'Спасибо, не надо'}).click();
  await survey(f.page).waitFor();await f.page.keyboard.press('Escape');await survey(f.page).waitFor({state:'detached'});
  assert.equal(f.state.answered,false);assert.equal(f.calls.some(c=>c.path.endsWith('/answer')),false);
});
test('closed popup obeys cooldown on reload; answered account never gets another',async t=>{
  const f=await fixture(t);await offer(f.page).getByRole('button',{name:'Закрыть предложение'}).click();
  await f.page.reload();await f.page.getByRole('heading',{name:'Что тренируем сегодня?',exact:true}).waitFor();assert.equal(await offer(f.page).count(),0);
  f.state.cooldown=false;await f.page.reload();await offer(f.page).getByRole('button',{name:'Спасибо, не надо'}).click();
  await survey(f.page).getByRole('button',{name:'Другое',exact:true}).click();await thanks(f.page).waitFor();
  f.state.cooldown=false;await f.page.reload();await f.page.getByRole('heading',{name:'Что тренируем сегодня?',exact:true}).waitFor();assert.equal(await f.page.getByRole('dialog').count(),0);
});
for(const resumeEvent of ['pageshow','focus'])test(`a new foreground visit via ${resumeEvent} can show the next daily offer`,async t=>{
  const f=await fixture(t);await offer(f.page).getByRole('button',{name:'Закрыть предложение'}).click();f.state.cooldown=false;
  await f.page.evaluate(event=>{const now=Date.now();Date.now=()=>now+86_400_000;window.dispatchEvent(new Event(event));},resumeEvent);
  await offer(f.page).waitFor();assert.equal(f.calls.filter(c=>c.path.endsWith('/claim')).length,2);
});
test('onboarding finishes before the subscription popup is requested',async t=>{
  const f=await fixture(t,{onboarding:true});await f.page.getByRole('button',{name:'Осмотреться самому',exact:true}).waitFor();
  assert.equal(f.calls.filter(c=>c.path.endsWith('/claim')).length,0);
  await f.page.getByRole('button',{name:'Осмотреться самому',exact:true}).click();await offer(f.page).waitFor();
});
test('direct subscription intent is not interrupted',async t=>{
  const f=await fixture(t,{url:'/?app=1&subscribe=1'});await f.page.getByText('У меня есть промокод',{exact:true}).waitFor();
  assert.equal(f.calls.filter(c=>c.path.endsWith('/claim')).length,0);assert.equal(await offer(f.page).count(),0);
});
test('a direct promo link retains its code under StrictMode',async t=>{
  const f=await fixture(t,{url:'/?app=1&promo=OLD50'});
  await f.page.getByText('Скидка 50% применена ко всем тарифам.',{exact:true}).waitFor();
  assert.equal(f.calls.filter(c=>c.path.endsWith('/claim')).length,0);
});
test('payment intent survives StrictMode and is consumed once',async t=>{
  const f=await fixture(t,{payIntent:true});await f.page.getByText('У меня есть промокод',{exact:true}).waitFor();
  assert.equal(f.calls.filter(c=>c.path.endsWith('/claim')).length,0);
  assert.equal(await f.page.evaluate(()=>localStorage.getItem('et_pay_intent')),null);
});
test('a direct lesson link receives the eligible entry popup',async t=>{
  const f=await fixture(t,{url:'/?app=1&mode=learning'});await offer(f.page).waitFor();
});
test('ineligible accounts do not receive a popup',async t=>{
  const f=await fixture(t,{eligible:false});await f.page.getByRole('heading',{name:'Что тренируем сегодня?',exact:true}).waitFor();
  assert.equal(await f.page.getByRole('dialog').count(),0);
});
test('campaign API failure leaves the application usable',async t=>{
  const f=await fixture(t,{failClaim:true});await f.page.getByRole('heading',{name:'Что тренируем сегодня?',exact:true}).waitFor();
  assert.equal(await f.page.getByRole('dialog').count(),0);
});
test('keyboard focus stays inside the modal',async t=>{
  const {page}=await fixture(t);await offer(page).waitFor();
  for(let i=0;i<10;i++){await page.keyboard.press(i%3===0?'Shift+Tab':'Tab');assert.equal(await page.evaluate(()=>!!document.activeElement.closest('.retention-dialog')),true);}
});
test('Android Back closes tariffs after the modal handoff and keeps the home screen',async t=>{
  const {page}=await fixture(t);await offer(page).getByRole('button',{name:'Перейти к тарифам'}).click();
  await page.getByText('Скидка 20% применена ко всем тарифам.',{exact:true}).waitFor();
  await page.goBack();await page.locator('.sub-screen').waitFor({state:'detached'});
  await page.getByRole('heading',{name:'Что тренируем сегодня?',exact:true}).waitFor();
  assert.equal(await page.getByRole('dialog').count(),0);
});
test('changing the survey step does not add another Back entry',async t=>{
  const {page}=await fixture(t);await offer(page).waitFor();const length=await page.evaluate(()=>history.length);
  await offer(page).getByRole('button',{name:'Спасибо, не надо'}).click();await survey(page).waitFor();
  assert.equal(await page.evaluate(()=>history.length),length);
  await page.goBack();await survey(page).waitFor({state:'detached'});
});
test('OLD50 remains selected when opening tariffs later from the profile',async t=>{
  const {page}=await fixture(t);await offer(page).getByRole('button',{name:'Спасибо, не надо'}).click();
  await survey(page).getByRole('button',{name:'Другое',exact:true}).click();await thanks(page).waitFor();
  await thanks(page).getByRole('button',{name:'Продолжить заниматься'}).click();
  await page.getByRole('button',{name:'Профиль',exact:true}).click();
  await page.getByRole('button',{name:'Оформить подписку',exact:true}).click();
  await page.getByText('Скидка 50% применена ко всем тарифам.',{exact:true}).waitFor();
});
for(const options of [{width:320,height:568},{width:390,height:844},{width:1440,height:1000},{width:390,height:844,dark:true}]){
  test(`all popup steps fit ${options.width}x${options.height}${options.dark?' dark':''}`,async t=>{
    const {page}=await fixture(t,options);await offer(page).waitFor();await fit(page);
    const tag=`${options.width}${options.dark?'-dark':''}`;await shot(page,`offer-${tag}`);
    await offer(page).getByRole('button',{name:'Спасибо, не надо'}).click();await survey(page).waitFor();await fit(page);await shot(page,`survey-${tag}`);
    assert.equal(await survey(page).evaluate(el=>el.scrollTop),0,'each step starts at its heading');
    await survey(page).getByRole('button',{name:'Другое',exact:true}).click();await thanks(page).waitFor();await fit(page);await shot(page,`thanks-${tag}`);
  });
}
for(const width of [390,1440])test(`admin analytics fits width ${width}`,async t=>{
  const f=await fixture(t,{admin:true,width,height:1000});await f.page.getByRole('heading',{name:'Что мешает подписаться'}).waitFor();
  await fit(f.page);assert.equal(await f.page.locator('.so-promo').count(),2);await shot(f.page,`admin-${width}`);
  await f.page.getByRole('combobox').selectOption('7');await f.page.getByRole('heading',{name:'Что мешает подписаться'}).waitFor();
  assert.equal(f.calls.filter(c=>c.path==='/api/admin/subscription-offers').at(-1).query.get('days'),'7');
});
test('admin empty state is clear',async t=>{
  const f=await fixture(t,{admin:true,empty:true});await f.page.getByText('Пока никто не ответил на вопрос.',{exact:true}).waitFor();await fit(f.page);
});
