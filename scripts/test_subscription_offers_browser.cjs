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
  return { days:30, timezone:'Europe/Moscow',campaign:'intro_week',
    impressions:empty?0:120,users_shown:empty?0:80,declines:empty?0:36,dismissals:empty?0:12,tariff_clicks:empty?0:24,
    users_clicked:empty?0:20,answers:empty?0:30,answer_rate:empty?0:37.5,click_rate:empty?0:25,
    paid_users:empty?0:8,revenue_rub:empty?0:8,
    reasons:reasons.map((r,i)=>({...r,count:empty?0:i===0?15:i===1?6:i===2?4:1,percent:empty?0:i===0?50:i===1?20:i===2?13.3:3.3})),
    daily:Array.from({length:7},(_,i)=>({date:`2026-11-${String(i+1).padStart(2,'0')}`,shown:empty?0:10+i,tariffs:empty?0:2,answers:empty?0:3})),
    recent_answers:empty?[]:[{user_id:123,name:'Александра',reason:'price',reason_label:reasons[0].label,answered_at:'2026-11-07T10:30:00Z',clicked_tariffs:true,paid:true},
      {user_id:124,name:'Михаил',reason:'missing_features',reason_label:reasons[4].label,answered_at:'2026-11-06T10:30:00Z',clicked_tariffs:false,paid:false}],
  };
}

async function fixture(t, {width=390,height=844,dark=false,eligible=true,onboarding=false,url='/?app=1',admin=false,empty=false,legacyUuid=false,payIntent=false,failClaim=false,recentlyDismissed=false,paymentStatus='succeeded'}={}) {
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
  const state={eligible,claimed:recentlyDismissed,cooldown:true,answered:false,failAnswer:false,failAdmin:false,onboarding,needEmail:false,paid:false,paymentStatus};
  const calls=[];
  await context.route('**/*',async route=>{
    const u = new URL(route.request().url());
    if (!['localhost','127.0.0.1'].includes(u.hostname)) return route.abort();
    if (!u.pathname.startsWith('/api/')) return route.continue();
    const json=(body,status=200)=>route.fulfill({status,contentType:'application/json',headers:{'access-control-allow-origin':'*','access-control-allow-headers':'*','access-control-allow-methods':'GET,POST,PUT,OPTIONS'},body:JSON.stringify(body)});
    if(route.request().method()==='OPTIONS') return json({});
    const body=route.request().postDataJSON() || {};
    calls.push({path:u.pathname,body,query:u.searchParams});
    if(u.pathname==='/api/auth/me') return json({id:123,first_name:'Александра',email:'browser@example.invalid',tg_id:null,identities:[],has_subscription:!state.eligible || state.paid,
      trial_active:false,tutorial_done:!state.onboarding,cefr_level:'A1'});
    if(u.pathname==='/api/auth/tutorial/complete') {state.onboarding=false;return json({ok:true});}
    if(u.pathname==='/api/me/progress') return json({streak:{current:3,best:5},total_minutes:48,total_words:12,achievements_earned:2,achievements_total:10});
    if(u.pathname==='/api/me/level') return json({level:2,lifetime_points:150,into_level:50,level_span:100,progress_pct:50});
    if(u.pathname==='/api/learning/overview') return json({enabled:false});
    if(u.pathname==='/api/learning/access') return json({full_access:!state.eligible || state.paid,free_lesson_available:false,daily_limit:0,used_today:0,remaining_today:0,conversation_seconds:null});
    if(u.pathname==='/api/push/key') return json({enabled:false});
    if(u.pathname==='/api/payments/plans') return json({plans:[
      {key:'trial7',title:'7 дней за 1 ₽',days:7,amount_rub:1,recurring:true,renew_amount_rub:999,renew_days:30},
      {key:'monthly',title:'Подписка на месяц',days:30,amount_rub:999,recurring:false},
      {key:'yearly',title:'Подписка на год',days:365,amount_rub:7999,recurring:false},
      {key:'twoyear',title:'Подписка на 2 года',days:730,amount_rub:14999,recurring:false},
    ]});
    if(u.pathname==='/api/payments/promo/check') return json({valid:true,already_used:false,discount_percent:u.searchParams.get('code')==='OLD50'?50:20});
    if(u.pathname==='/api/subscription-offers/claim') {
      if(failClaim) return json({detail:'synthetic campaign outage'},503);
      if(!state.eligible || state.paid || (body.trigger==='entry' && (state.onboarding || state.answered || state.claimed && state.cooldown))) return json({show:false,answered:state.answered});
      state.claimed=true;state.cooldown=true;
      return json({show:true,impression_id:body.request_id,audience:'access_required',plan:'trial7',amount_rub:1,days:7,renew_amount_rub:999,available:true,answered:state.answered,reasons});
    }
    if(u.pathname==='/api/subscription-offers/event') return json({ok:true});
    if(u.pathname==='/api/subscription-offers/answer') {
      if(state.failAnswer) return json({detail:'synthetic failure'},503);
      state.answered=true;return json({ok:true,reason:body.reason});
    }
    if(u.pathname==='/api/payments/create') {
      if(state.needEmail && !body.email) return json({detail:'email_required'},400);
      return json({payment_id:123,confirmation_url:BASE+'/?payment_id=123&app=1'});
    }
    if(u.pathname==='/api/payments/status') {state.paid=state.paymentStatus==='succeeded';return json({status:state.paymentStatus,plan:'trial7',amount_rub:1,days_granted:7});}
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
const offer = page=>page.getByRole('dialog',{name:'7 дней за 1 ₽'});
const survey = page=>page.getByRole('dialog',{name:'Почему вы пока не хотите оформить подписку?'});
async function shot(page,name) {if(SHOTS) {await page.mouse.move(0,0);await page.screenshot({path:path.join(SHOTS,name+'.png'),fullPage:true,animations:'disabled'});}}
async function fit(page) {
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'page must not overflow horizontally');
  const dialog=page.locator('.retention-dialog');
  if(await dialog.count()) assert.equal(await dialog.evaluate(el=>{const r=el.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight+1&&el.scrollWidth<=el.clientWidth;}),true,'dialog fits viewport and scrolls internally');
}

async function openProfilePlans(page) {
 await page.getByRole('button',{name:'Профиль',exact:true}).click();
 await page.getByRole('button',{name:'Оформить подписку',exact:true}).click();
 await page.locator('.sub-plan-v2').first().waitFor();
}

for(const width of [320,390])test(`unsubscribed profile opens four tariffs including one-ruble week at ${width}px`,async t=>{
 const f=await fixture(t,{recentlyDismissed:true,width,height:844});
 await openProfilePlans(f.page);
 assert.equal(await f.page.locator('.sub-plan-v2').count(),4);
 const week=f.page.locator('.sub-plan-v2').filter({hasText:'7 дней за 1 ₽'});
 assert.equal((await week.locator('.sub-plan-v2__price').innerText()).trim(),'1 ₽');
 assert.match(await week.innerText(),/999 ₽ каждые 30 дней/);
 assert.equal(await f.page.getByRole('checkbox').count(),0);
 assert.equal(await offer(f.page).count(),0);
 await fit(f.page);await shot(f.page,`profile-plans-${width}`);
 await f.page.locator('.sub-screen').getByRole('button',{name:'Закрыть',exact:true}).click();
 await f.page.locator('.sub-screen').waitFor({state:'detached'});
 assert.equal(await offer(f.page).count(),0);
});
test('opening and closing profile tariffs never revives a dismissed introductory offer',async t=>{
 const f=await fixture(t);await offer(f.page).waitFor();await f.page.keyboard.press('Escape');
 await offer(f.page).waitFor({state:'detached'});
 await openProfilePlans(f.page);
 assert.equal(await offer(f.page).count(),0);
 await f.page.locator('.sub-screen').getByRole('button',{name:'Закрыть',exact:true}).click();
 await f.page.locator('.sub-screen').waitFor({state:'detached'});
 assert.equal(await offer(f.page).count(),0);
 assert.equal(f.calls.filter(c=>c.path.endsWith('/claim')).length,1);
});
test('profile week goes straight to checkout with displayed terms, receipt email and no promo',async t=>{
 const f=await fixture(t,{recentlyDismissed:true});f.state.needEmail=true;
 await openProfilePlans(f.page);
 await f.page.getByRole('button',{name:'У меня есть промокод'}).click();
 await f.page.getByPlaceholder('Промокод (если есть)').fill('WELCOME10');
 await f.page.getByRole('button',{name:'Применить',exact:true}).click();
 await f.page.getByText(/Неделя за 1 ₽ — без промокода/).waitFor();
 const week=f.page.locator('.sub-plan-v2').filter({hasText:'7 дней за 1 ₽'});
 assert.equal(await week.locator('.sub-plan-v2__old').count(),0);
 await week.click();
 await f.page.getByPlaceholder('Email').fill('receipt@example.invalid');
 await f.page.getByRole('button',{name:'Продолжить к оплате'}).click();
 await f.page.waitForURL('**payment_id=123**');
 const pay=f.calls.filter(c=>c.path==='/api/payments/create').at(-1).body;
 assert.deepEqual(pay,{plan:'trial7',email:'receipt@example.invalid',recurring_consent:true,renew_amount_rub:999});
 assert(!f.calls.some(c=>c.path.endsWith('/claim') && c.body.trigger==='access'));
});
for(const [key,title] of [['monthly','Подписка на месяц'],['yearly','Подписка на год'],['twoyear','Подписка на 2 года']])test(`profile can buy ${key} without taking the introductory week`,async t=>{
 const f=await fixture(t,{recentlyDismissed:true});await openProfilePlans(f.page);
 await f.page.locator('.sub-plan-v2').filter({has:f.page.getByText(title,{exact:true})}).click();
 await f.page.waitForURL('**payment_id=123**');
 assert.equal(f.calls.find(c=>c.path==='/api/payments/create').body.plan,key);
 assert(!f.calls.some(c=>c.path.endsWith('/claim') && c.body.trigger==='access'));
});

test('single introductory offer explains renewal, links the offer and has no checkbox',async t=>{
 const f=await fixture(t);await offer(f.page).waitFor();
 assert.match(await offer(f.page).innerText(),/999 ₽ каждые 30 дней автоматически/);
 assert.equal(f.calls.filter(c=>c.path.endsWith('/claim')).length,1);
 assert.equal(await offer(f.page).getByRole('checkbox').count(),0);
 assert.equal(await offer(f.page).getByRole('link',{name:'Ознакомиться с офертой'}).getAttribute('href'),'/oferta.html');
 assert.equal(await offer(f.page).getByRole('button',{name:'Подключить 7 дней за 1 ₽'}).isDisabled(),false);
 assert(!/SALE20|OLD50|Перейти к тарифам/.test(await offer(f.page).innerText()));
});
test('older WebViews claim without randomUUID',async t=>{
 const f=await fixture(t,{legacyUuid:true});await offer(f.page).waitFor();
 assert.match(f.calls.find(c=>c.path.endsWith('/claim')).body.request_id,/^[a-f0-9-]{36}$/);
});
test('CTA goes directly to checkout with consent and the displayed renewal price',async t=>{
 const f=await fixture(t);
 await offer(f.page).getByRole('button',{name:'Подключить 7 дней за 1 ₽'}).click();
 await f.page.waitForURL('**payment_id=123**');
 const pay=f.calls.find(c=>c.path==='/api/payments/create').body;
 assert.deepEqual(pay,{plan:'trial7',recurring_consent:true,renew_amount_rub:999});
 assert(!f.calls.some(c=>c.path==='/api/payments/promo/check'));
});
test('return from payment waits for confirmation without another introductory offer',async t=>{
 const f=await fixture(t,{url:'/?payment_id=123&app=1',paymentStatus:'pending'});
 await f.page.getByText('Ждём подтверждения от ЮKassa…',{exact:false}).waitFor();
 assert.equal(await offer(f.page).count(),0);
 assert(!f.calls.some(c=>c.path.endsWith('/claim')));
 f.state.paymentStatus='succeeded';
 await f.page.getByText('Подписка активирована!',{exact:false}).waitFor();
 assert(!f.calls.some(c=>c.path==='/api/payments/create'));
 assert.equal(await offer(f.page).count(),0);
});
test('canceled payment retries through the single offer instead of a tariff chooser',async t=>{
 const f=await fixture(t,{url:'/?payment_id=123&app=1',paymentStatus:'canceled'});
 await f.page.getByText('Платёж отменён.',{exact:false}).waitFor();
 await f.page.getByRole('button',{name:'Попробовать снова',exact:true}).click();
 await offer(f.page).waitFor();
 assert.equal(await f.page.locator('.sub-plans-v2').count(),0);
});
test('receipt email stays inside the same offer',async t=>{
 const f=await fixture(t);f.state.needEmail=true;
 await offer(f.page).getByRole('button',{name:'Подключить 7 дней за 1 ₽'}).click();
 await offer(f.page).getByLabel('Email для чека').fill('receipt@example.invalid');
 await offer(f.page).getByRole('button',{name:'Подключить 7 дней за 1 ₽'}).click();
 await f.page.waitForURL('**payment_id=123**');
 assert.equal(f.calls.filter(c=>c.path==='/api/payments/create').at(-1).body.email,'receipt@example.invalid');
});
test('refusal shows eight fixed reasons and answer closes without a third window',async t=>{
 const f=await fixture(t);await offer(f.page).getByRole('button',{name:'Спасибо, не надо'}).click();
 await survey(f.page).waitFor();assert.equal(await survey(f.page).locator('.retention-reason').count(),8);
 assert.equal(await survey(f.page).locator('input,textarea').count(),0);
 await survey(f.page).getByRole('button',{name:'Другое'}).click();await survey(f.page).waitFor({state:'detached'});
 assert(f.state.answered);assert.equal(await f.page.getByRole('dialog').count(),0);
 assert.deepEqual(Object.keys(f.calls.find(c=>c.path.endsWith('/answer')).body).sort(),['impression_id','init_data','reason']);
 await f.page.reload();assert.equal(await f.page.getByRole('dialog').count(),0);
});
test('failed save stays in the survey and can be retried',async t=>{
 const f=await fixture(t);f.state.failAnswer=true;
 await offer(f.page).getByRole('button',{name:'Спасибо, не надо'}).click();
 await survey(f.page).getByRole('button',{name:'Другое'}).click();await survey(f.page).getByRole('alert').waitFor();
 assert(!f.state.answered);f.state.failAnswer=false;
 await survey(f.page).getByRole('button',{name:'Другое'}).click();await survey(f.page).waitFor({state:'detached'});assert(f.state.answered);
});
test('Escape dismisses without recording an answer; reload respects cooldown',async t=>{
 const f=await fixture(t);await offer(f.page).waitFor();await f.page.keyboard.press('Escape');
 await offer(f.page).waitFor({state:'detached'});assert(!f.state.answered);
 await f.page.reload();assert.equal(await f.page.getByRole('dialog').count(),0);
});
test('practice after dismissing the offer still requires a subscription',async t=>{
 const f=await fixture(t,{recentlyDismissed:true});await f.page.getByRole('heading',{name:'Что тренируем сегодня?'}).waitFor();
 assert.equal(await f.page.getByRole('dialog').count(),0);
 await f.page.getByText('Push-to-talk диалог с AI-тьютором.').click();
 await offer(f.page).waitFor();assert.equal(f.calls.filter(c=>c.path.endsWith('/claim')).at(-1).body.trigger,'access');
 assert.equal(await f.page.locator('.tutor-app').count(),0);
});
test('answer suppresses automatic surveys but manual access still offers subscription',async t=>{
 const f=await fixture(t);await offer(f.page).getByRole('button',{name:'Спасибо, не надо'}).click();
 await survey(f.page).getByRole('button',{name:'Другое'}).click();await survey(f.page).waitFor({state:'detached'});
 await f.page.getByText('Push-to-talk диалог с AI-тьютором.').click();await offer(f.page).waitFor();
 await offer(f.page).getByRole('button',{name:'Спасибо, не надо'}).click();await offer(f.page).waitFor({state:'detached'});
 assert.equal(f.calls.filter(c=>c.path.endsWith('/answer')).length,1);
});
for(const event of ['focus','pageshow'])test(`foreground ${event} checks once per visit`,async t=>{
 const f=await fixture(t);await offer(f.page).waitFor();await f.page.keyboard.press('Escape');f.state.cooldown=false;
 await f.page.evaluate(event=>{const now=Date.now();Date.now=()=>now+61_000;window.dispatchEvent(new Event(event));},event);
 await offer(f.page).waitFor();
});
test('new account onboarding does not receive an unsolicited offer',async t=>{
 const f=await fixture(t,{onboarding:true});await f.page.locator('.ob-v2').waitFor();
 assert.equal(await f.page.locator('.retention-dialog').count(),0);
});
test('direct lesson entry without a subscription opens the offer before lesson content',async t=>{
 const f=await fixture(t,{url:'/?app=1&mode=learning',recentlyDismissed:true});
 await offer(f.page).waitFor();
 assert.equal(f.calls.filter(c=>c.path.match(/lessons.+start$/)).length,0);
});
test('direct subscription intent opens the single offer',async t=>{
 const f=await fixture(t,{url:'/?app=1&subscribe=1'});await offer(f.page).waitFor();
 assert.equal(await f.page.locator('.sub-plan-v2').count(),0);
});
test('paid accounts are not interrupted',async t=>{
 const f=await fixture(t,{eligible:false});await f.page.getByRole('heading',{name:'Что тренируем сегодня?'}).waitFor();assert.equal(await f.page.getByRole('dialog').count(),0);
});
test('campaign API failure leaves home usable',async t=>{
 const f=await fixture(t,{failClaim:true});await f.page.getByRole('heading',{name:'Что тренируем сегодня?'}).waitFor();assert.equal(await f.page.getByRole('dialog').count(),0);
});
test('keyboard focus stays inside the modal including the offer link',async t=>{
 const f=await fixture(t);await offer(f.page).waitFor();for(let i=0;i<12;i++)await f.page.keyboard.press('Tab');
 assert(await offer(f.page).evaluate(el=>el.contains(document.activeElement)));
});
test('Android Back closes the survey without adding a new modal history entry',async t=>{
 const f=await fixture(t);await offer(f.page).getByRole('button',{name:'Спасибо, не надо'}).click();await survey(f.page).waitFor();
 await f.page.evaluate(()=>history.back());await survey(f.page).waitFor({state:'detached'});assert(!f.state.answered);
});
for(const options of [{width:320,height:568},{width:390,height:844},{width:430,height:932,dark:true},{width:768,height:600}])test(`two windows fit ${options.width}x${options.height}`,async t=>{
 const f=await fixture(t,options);await offer(f.page).waitFor();await fit(f.page);await shot(f.page,`intro-${options.width}`);
 await offer(f.page).getByRole('button',{name:'Спасибо, не надо'}).click();await survey(f.page).waitFor();await fit(f.page);await shot(f.page,`survey-${options.width}`);
 await survey(f.page).getByRole('button',{name:'Другое'}).click();await survey(f.page).waitFor({state:'detached'});
});
for(const width of [390,1440])test(`new campaign analytics fits ${width}`,async t=>{
 const f=await fixture(t,{admin:true,width,height:900});await f.page.getByRole('heading',{name:'Почему не оформляют подписку'}).waitFor();
 await f.page.getByText('7 дней за 1 ₽',{exact:true}).waitFor();await fit(f.page);await shot(f.page,`admin-intro-${width}`);
 assert(!/SALE20|OLD50/.test(await f.page.locator('.so-analytics').innerText()));
});
test('admin empty state is clear',async t=>{
 const f=await fixture(t,{admin:true,empty:true});await f.page.getByText('Пока никто не ответил на вопрос.').waitFor();
});
