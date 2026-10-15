"""Real local MySQL course regressions; external AI/STT/TTS/S2S are mocked."""
import asyncio
import copy
import json
from datetime import timedelta
import os
import secrets
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,patch
from uuid import uuid4
import httpx
from sqlalchemy import delete,select,update
from sqlalchemy.engine import make_url
from app import learning, learning_modules, grammar, voice
from starlette.websockets import WebSocketState
from app import learning_content as content, learning_assessment as assessment
from app.auth import issue_jwt
from app.config import settings
from app.db import Repo,db_session
from app.db.engine import init_db,db_engine
from app.db.models import DailyUsage,MytrackerEvent,MytrackerInstall,Session,User,LevelTest,UserMistake
from app.db.models import UserVocabulary
from app.learning_models import LearningProfile,LearningLesson,LearningCheckpoint,LearningSkill
from app.main import app, ws_voice
from app.limits import context_for_user, is_section_limit_reached

TEST_URL=os.environ.get('TEST_DATABASE_URL','')
SAFE_DB=bool(TEST_URL and make_url(TEST_URL).host in {'localhost','127.0.0.1'} and (make_url(TEST_URL).database or '').endswith('_test'))

class CurriculumTests(unittest.TestCase):
    def test_all_levels_goals_and_future_cycles_have_valid_content(self):
        for level in content.LEVELS:
            for goal in content.GOALS:
                for ordinal in [*range(1,41),1000,10001]:
                    spec=content.plan(level,goal,ordinal)
                    material=content.validate_material(content.material(spec))
                    self.assertEqual(spec['level'],level)
                    self.assertIn(spec['topic_key'],content.THEORY)
                    self.assertTrue(material['words'])
                    self.assertEqual(len({q['id'] for q in material['practice']}),5)

    def test_checkpoint_bank_is_valid_and_not_a_copy_of_practice(self):
        bank=assessment.bank()
        self.assertEqual(len({q['id'] for q in bank}),len(bank))
        practice={frame[0] for frames in content.RULES.values() for frame in frames}
        for q in bank:
            self.assertEqual(len(set(q['choices'])),4)
            self.assertIn(q['correct'],q['choices'])
            self.assertNotIn(q['prompt'],practice)
        first=assessment.question_set('A2','first')
        second=assessment.question_set('A2','second',[q['id'] for q in first])
        self.assertEqual(len(first),22)
        self.assertFalse({q['id'] for q in first[:-2]} & {q['id'] for q in second[:-2]})

    def test_level_does_not_increase_from_count_or_textual_voice_claim(self):
        questions=assessment.question_set('A1','grade')
        answers={q['id']:{'correct':q['level']=='A1','verified_voice':False,'score':100} for q in questions}
        result=assessment.grade(questions,answers)
        self.assertEqual(result['cefr'],'A1')
        self.assertNotIn('speaking',result['skills'])


@unittest.skipUnless(SAFE_DB,'TEST_DATABASE_URL must be a local *_test database')
class LearningTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.media=tempfile.TemporaryDirectory()
        self.patches=[patch.object(settings,"LEARNING_MEDIA_DIR",self.media.name),patch.object(settings,'DATABASE_URL',TEST_URL),patch.object(settings,'LEARNING_ENABLED',True),
            patch.object(settings,'AUTH_JWT_SECRET',secrets.token_hex(32)),patch.object(settings,'ADMIN_TOKEN',secrets.token_hex(32)),
            patch.object(settings,'FREE_PERIOD',True),patch.object(settings,'VLLM_BASE_URL',None),
            patch.object(settings,'KOKORO_TTS_URL',None),patch.object(settings,'WHISPER_STT_URL',None),
            patch.object(settings,'MYTRACKER_APP_ID','123'),patch.object(settings,'MYTRACKER_S2S_TOKEN','synthetic'),
            patch('app.mytracker._post',new_callable=AsyncMock,return_value=True)]
        for p in self.patches: p.start()
        init_db(); self.uids=[]
        async with db_session() as session:
            for _ in range(2):
                account=await Repo(session).create_native_user(email=f'learning-{uuid4().hex}@example.invalid',password_hash='unused',first_name='Learner')
                account.cefr_level='A1'; self.uids.append(account.id)
        self.uid,self.other_uid=self.uids
        self.headers={'Authorization':f'Bearer {issue_jwt(self.uid)}','X-MyTracker-Instance-ID':str(uuid4())}
        self.other_headers={'Authorization':f'Bearer {issue_jwt(self.other_uid)}'}
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local.test',trust_env=False)

    async def asyncTearDown(self):
        await self.client.aclose()
        async with db_session() as session:
            for cls,col in [(MytrackerEvent,MytrackerEvent.user_id),(MytrackerInstall,MytrackerInstall.user_id),
                (Session,Session.user_id),(User,User.id)]:
                await session.execute(delete(cls).where(col.in_(self.uids)))
        await db_engine().dispose()
        for p in reversed(self.patches): p.stop()
        self.media.cleanup()

    async def post(self,path,body=None,status=200,headers=None):
        response=await self.client.post('/api/learning'+path,headers=headers or self.headers,json=body or {})
        self.assertEqual(response.status_code,status,response.text)
        return response.json()

    async def setup_course(self):
        response=await self.client.put('/api/learning/profile',headers=self.headers,json={'goal':'general'})
        self.assertEqual(response.status_code,200,response.text)
        response=await self.client.get('/api/learning/overview',headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    async def prepared(self):
        data=await self.setup_course()
        return data['next_lesson']['id']

    async def api_post(self,path,body,status=200,headers=None):
        response=await self.client.post('/api/'+path,headers=headers or self.headers,json=body)
        self.assertEqual(response.status_code,status,response.text)
        return response.json()

    async def grammar_round(self,key,*,mistakes=False,wrong=False):
        row=(await self.client.get(f'/api/learning/lessons/{key}',headers=self.headers)).json()
        topic=row['content']['spec']['topic_key']
        items=[grammar.Exercise(id=f'e{i}',type='mcq',category='tense',prompt='I ___ a student.',
            choices=['am','is','are','be'],correct='am',explanation='I am.') for i in range(4)]
        with patch.object(grammar,'_generate_exercises',new_callable=AsyncMock,return_value=items):
            prepared=await self.api_post('grammar/generate' if mistakes else 'grammar/lesson/exercises',
                {'learning_lesson_id':key,'topic_key':topic,'level':row['content']['spec']['level'],'mode':'weak_points'})
        active=await self.api_post('grammar/start',{'session_id':prepared['session_id']})
        answers=[{'exercise_id':item.id,'user_answer':'is' if wrong and i==0 else 'am',
            'is_correct':True,'category':'tense'} for i,item in enumerate(items)]
        body={'learning_lesson_id':key,'session_id':active['session_id'],'topic_key':topic,
            'results':answers,'correct':4,'total':4,'duration_sec':90 if not mistakes else 0}
        result=await self.api_post('grammar/finish' if mistakes else 'grammar/lesson/finish',body)
        return body,result

    async def conversation(self,key,turns=3,query=None,reply='Tell me more.',preflight=False):
        class Socket:
            query_params={'mode':'chat','learning_lesson_id':key,**(query or {})}
            state=SimpleNamespace()
            client_state=WebSocketState.CONNECTED
            client=('local',1)
            def __init__(self): self.remaining=turns; self.messages=[]
            async def send_json(self,data): self.messages.append(data)
            async def accept(self): self.client_state=WebSocketState.CONNECTED
            async def close(self,**kwargs): self.client_state=WebSocketState.DISCONNECTED
            async def receive(self):
                if not self.remaining: return {'type':'websocket.disconnect'}
                self.remaining-=1
                return {'text':json.dumps({'type':'user_text','text':'I live in London and enjoy music.'})}
        socket=Socket()
        with patch.object(voice,'get_llm_provider',return_value=object()), \
             patch.object(voice,'_llm_complete_with_timeout',new_callable=AsyncMock,return_value=reply) as completion, \
             patch.object(voice,'_check_achievements_hook',new_callable=AsyncMock):
            if preflight:
                await ws_voice(socket,token=issue_jwt(self.uid))
            else:
                await voice.run_voice_session(socket,limits_ctx=SimpleNamespace(user_db_id=self.uid,tg_id=0))
            await asyncio.sleep(0)
            socket.prompts=[call.kwargs['system_prompt'] for call in completion.call_args_list]
        return socket

    async def complete(self,key,finish=True):
        await self.post(f'/lessons/{key}/start')
        await self.grammar_round(key)
        await self.post(f'/lessons/{key}/advance',{'step_index':0})
        await self.post(f'/lessons/{key}/advance',{'step_index':1})
        await self.api_post('srs/session/start',{'learning_lesson_id':key})
        cards=(await self.client.get('/api/srs/session',params={'learning_lesson_id':key},headers=self.headers)).json()['cards']
        for card in cards:
            await self.api_post('srs/review',{'learning_lesson_id':key,'word':card['word'],'correct':True})
        async with db_session() as session:
            sid=learning_modules.result(await session.get(LearningLesson,key),'words')['session_id']
        await self.api_post('srs/session/finish',{'learning_lesson_id':key,'session_id':sid,'duration_sec':0})
        await self.post(f'/lessons/{key}/advance',{'step_index':2})
        with patch('app.listening._call_llm_for_script',new_callable=AsyncMock,return_value='TOPIC: Bristol\nMy name is Alex. I live in Bristol.'), \
             patch('app.listening._synthesize_full',new_callable=AsyncMock,return_value=b'\0'*48000):
            await self.api_post('listening/generate',{'learning_lesson_id':key,'duration_min':1,'category':'travel','level':'A1'})
        await self.post(f'/lessons/{key}/listened')
        await self.post(f'/lessons/{key}/advance',{'step_index':3})
        await self.conversation(key)
        await self.post(f'/lessons/{key}/advance',{'step_index':4})
        if finish: return await self.post(f'/lessons/{key}/finish',{'elapsed_sec':90})

    async def test_setup_preserves_A1_and_preparation_creates_no_session(self):
        data=await self.setup_course()
        self.assertEqual(data['profile']['level'],'A1')
        self.assertEqual(len(data['route']),20)
        self.assertEqual(data['profile']['until_checkpoint'],20)
        self.assertNotIn('correct',data['next_lesson']['content']['practice'][0])
        async with db_session() as session:
            self.assertEqual(await Repo(session).count_sessions_today(self.uid,'lesson'),0)

    async def test_profile_requires_real_placement_and_authentication(self):
        async with db_session() as session:
            await session.execute(update(User).where(User.id==self.uid).values(cefr_level=None))
        response=await self.client.put('/api/learning/profile',headers=self.headers,json={'goal':'work'})
        self.assertEqual(response.status_code,409)
        response=await self.client.get('/api/learning/overview')
        self.assertEqual(response.status_code,401)
        with patch.object(settings,'LEARNING_ENABLED',False):
            response=await self.client.get('/api/learning/overview')
            self.assertEqual(response.json(),{'enabled':False})

    async def test_concurrent_start_and_resume_use_one_session_and_one_start_event(self):
        key=await self.prepared()
        starts=await asyncio.gather(*(self.post(f'/lessons/{key}/start') for _ in range(3)))
        self.assertTrue(all(s['id']==key for s in starts))
        await self.post(f'/lessons/{key}/pause',{'elapsed_sec':20})
        await self.post(f'/lessons/{key}/heartbeat',{'elapsed_sec':100})
        await self.post(f'/lessons/{key}/start')
        async with db_session() as session:
            rows=list((await session.scalars(select(Session).where(Session.user_id==self.uid))).all())
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0].used_seconds,0)
            self.assertEqual((await session.get(LearningLesson,key)).used_seconds,20)
            events=list((await session.scalars(select(MytrackerEvent).where(MytrackerEvent.user_id==self.uid))).all())
            self.assertEqual(sum(e.payload.get('customEventName')=='lesson_started' for e in events),1)

    async def test_steps_cannot_be_skipped_and_answers_are_immutable(self):
        key=await self.prepared(); row=await self.post(f'/lessons/{key}/start')
        await self.post(f'/lessons/{key}/advance',{'step_index':0},status=409)
        await self.post(f'/lessons/{key}/answer',{'question_id':'p0','answer':'am'},status=409)
        q=row['content']['warmup'][0]
        wrong=next(choice for choice in q['choices'] if choice!='имя')
        first=await self.post(f'/lessons/{key}/answer',{'question_id':q['id'],'answer':wrong})
        self.assertEqual(await self.post(f'/lessons/{key}/answer',{'question_id':q['id'],'answer':q['choices'][0]}),first)
        await self.post(f'/lessons/{key}/finish',{'elapsed_sec':90},status=409)

    async def test_finish_is_atomic_and_idempotent_and_does_not_score_text_as_speaking(self):
        key=await self.prepared(); await self.complete(key,finish=False)
        results=await asyncio.gather(*(self.post(f'/lessons/{key}/finish',{'elapsed_sec':90}) for _ in range(3)))
        self.assertTrue(all(r==results[0] for r in results))
        async with db_session() as session:
            self.assertEqual((await session.get(LearningProfile,self.uid)).completed_count,1)
            self.assertEqual(await session.scalar(select(DailyUsage.used_seconds).where(DailyUsage.user_id==self.uid)),91)
            self.assertIsNone(await session.get(LearningSkill,(self.uid,'speaking')))
            self.assertIsNone(await session.get(LearningSkill,(self.uid,'listening')))
            self.assertEqual(len((await Repo(session).list_user_words(self.uid))),4)
            self.assertEqual(await Repo(session).user_points(self.uid,month_only=False),5)
            word=await session.scalar(select(UserVocabulary).where(UserVocabulary.user_id==self.uid,UserVocabulary.word=='name'))
            self.assertEqual(word.srs_total_attempts,1)
        response=await self.client.get('/api/learning/overview',headers=self.headers)
        self.assertEqual(response.json()['next_lesson']['content']['spec']['ordinal'],2)

    async def test_failed_finish_rolls_back_then_retry_saves_once(self):
        key=await self.prepared(); await self.complete(key,finish=False)
        with patch.object(learning,'evidence',new_callable=AsyncMock,side_effect=RuntimeError('synthetic rollback')):
            with self.assertRaises(RuntimeError): await self.post(f'/lessons/{key}/finish',{'elapsed_sec':90})
        async with db_session() as session:
            self.assertEqual((await session.get(LearningProfile,self.uid)).completed_count,0)
            self.assertEqual((await session.get(LearningLesson,key)).status,'active')
            self.assertEqual(list((await session.scalars(select(LearningSkill).where(LearningSkill.user_id==self.uid))).all()),[])
        await self.post(f'/lessons/{key}/finish',{'elapsed_sec':90})
        await self.post(f'/lessons/{key}/finish',{'elapsed_sec':90})

    async def test_foreign_account_cannot_access_or_mutate_a_lesson(self):
        key=await self.prepared()
        response=await self.client.get(f'/api/learning/lessons/{key}',headers=self.other_headers)
        self.assertEqual(response.status_code,404)
        for path,body in [('start',{}),('pause',{'elapsed_sec':20}),('heartbeat',{'elapsed_sec':20}),('answer',{'question_id':'w0','answer':'test'}),('finish',{'elapsed_sec':90})]:
            # start/finish resolve a profile before ownership: create the other
            # account's independent profile so the ownership assertion is tested.
            await self.client.put('/api/learning/profile',headers=self.other_headers,json={'goal':'daily'})
            await self.post(f'/lessons/{key}/{path}',body,status=404,headers=self.other_headers)

    async def test_idle_lesson_pauses_and_admin_shows_the_saved_stage(self):
        key=await self.prepared(); await self.post(f'/lessons/{key}/start')
        await self.post(f'/lessons/{key}/heartbeat',{'elapsed_sec':30})
        async with db_session() as session:
            await session.execute(update(LearningLesson).where(LearningLesson.id==key).values(last_seen_at=learning.now()-timedelta(seconds=200)))
        self.assertGreaterEqual(await learning.close_idle(),1)
        response=await self.client.get(f'/api/admin/users/{self.uid}/sessions',headers={'X-Admin-Token':settings.ADMIN_TOKEN})
        self.assertEqual(response.json()['sessions'][0]['status'],'paused')
        response=await self.client.get(f'/api/learning/admin/users/{self.uid}',headers={'X-Admin-Token':settings.ADMIN_TOKEN})
        self.assertEqual(response.json()['next_lesson']['status'],'paused')
        self.assertEqual((await self.post(f'/lessons/{key}/start'))['used_seconds'],30)

    async def test_late_generated_variant_cannot_replace_started_material(self):
        key=await self.prepared()
        async with db_session() as session:
            row=await session.get(LearningLesson,key); row.generation_status='pending'; original=copy.deepcopy(row.content)
        entered=asyncio.Event(); release=asyncio.Event()
        async def generated(_):
            entered.set(); await release.wait()
            changed=copy.deepcopy(original); changed['theory']='Changed late'; return changed
        with patch.object(learning,'generate_variant',side_effect=generated):
            pending=asyncio.create_task(learning.prepare_one()); await entered.wait()
            await self.post(f'/lessons/{key}/start'); release.set(); await pending
        async with db_session() as session:
            row=await session.get(LearningLesson,key)
            self.assertEqual(row.content,original)
            self.assertEqual(row.generation_status,'frozen')

    async def test_free_quota_applies_to_whole_lesson_and_resume_is_allowed(self):
        key=await self.prepared(); await self.complete(key)
        data=(await self.client.get('/api/learning/overview',headers=self.headers)).json()
        with patch.object(settings,'FREE_PERIOD',False),patch.object(settings,'FREE_TRIAL_DAYS',0):
            await self.post(f"/lessons/{data['next_lesson']['id']}/start",status=402)

    async def budget_context(self,key,uid=None):
        uid=uid or self.uid
        async with db_session() as session:
            account=await session.get(User,uid)
            ctx=await context_for_user(Repo(session),db_session,account,lesson_id=key)
            activity=await Repo(session).open_session(user_id=uid,mode='chat',level='A1',role='language_partner',learning_lesson_id=key)
            await learning_modules.conversation_open(session,uid,key,activity)
            ctx.activity_id=activity.id
        return ctx

    async def conversation_stage(self):
        key=await self.prepared();await self.post(f'/lessons/{key}/start')
        async with db_session() as session:
            (await session.get(LearningLesson,key)).step_index=4
        return key

    async def test_free_lesson_has_its_own_persisted_conversation_budget_and_reconnect_cannot_reset_it(self):
        with patch.object(settings,'FREE_PERIOD',False),patch.object(settings,'FREE_TRIAL_DAYS',0):
            key=await self.conversation_stage()
            async with db_session() as session:
                await Repo(session).add_speaking_seconds(user_id=self.uid,seconds=300)
            first=await self.budget_context(key)
            self.assertEqual(first.snapshot().scope,'lesson')
            self.assertEqual(first.remaining_seconds,300)
            await first.heartbeat(20)
            second=await self.budget_context(key)
            self.assertEqual(second.remaining_seconds,280)
            with self.assertRaisesRegex(Exception,'conversation_replaced'): await first.heartbeat(5)
            await second.heartbeat(10)
            await second.finish(13)
            self.assertEqual(second.remaining_seconds,267)
            async with db_session() as session:
                self.assertEqual(await Repo(session).get_speaking_seconds_today(self.uid),300)
            resumed=await self.budget_context(key)
            self.assertEqual(resumed.remaining_seconds,267)
            await resumed.heartbeat(267)
            self.assertTrue(resumed.is_exceeded())
            self.assertEqual(resumed.snapshot().resets_at[-6:],'+03:00')
            with patch('app.learning_access.budget_day',return_value=('2099-01-01','2099-01-02T00:00:00+03:00')):
                refreshed=await self.budget_context(key)
                self.assertEqual(refreshed.remaining_seconds,300)

    async def test_websocket_preflight_allows_course_after_standalone_time_is_exhausted_and_rejects_foreign_course(self):
        with patch.object(settings,'FREE_PERIOD',False),patch.object(settings,'FREE_TRIAL_DAYS',0):
            key=await self.conversation_stage()
            async with db_session() as session:
                await Repo(session).add_speaking_seconds(user_id=self.uid,seconds=300)
            socket=await self.conversation(key,turns=1,preflight=True)
            self.assertTrue(any(m.get('scope')=='lesson' and m.get('remaining_seconds')==300 for m in socket.messages))
            self.assertTrue(any(m.get('role')=='tutor' for m in socket.messages))
            self.uid,self.other_uid=self.other_uid,self.uid
            foreign=await self.conversation(key,turns=0,preflight=True)
            self.uid,self.other_uid=self.other_uid,self.uid
            self.assertFalse(any(m.get('role')=='tutor' for m in foreign.messages))

    async def test_course_modules_do_not_consume_standalone_quotas(self):
        key=await self.conversation_stage()
        with patch.object(settings,'FREE_PERIOD',False),patch.object(settings,'FREE_TRIAL_DAYS',0):
            async with db_session() as session:
                repo=Repo(session);account=await session.get(User,self.uid)
                for mode,section in [('grammar','grammar'),('listening','listening')]:
                    await repo.open_session(user_id=self.uid,mode=mode,level='A1',role='test',learning_lesson_id=key)
                    self.assertFalse(await is_section_limit_reached(repo,account,section=section))
                    await repo.open_session(user_id=self.uid,mode=mode,level='A1',role='test')
                    self.assertTrue(await is_section_limit_reached(repo,account,section=section))

    async def test_fast_reconnect_still_charges_old_sessions_final_seconds(self):
        with patch.object(settings,'FREE_PERIOD',False),patch.object(settings,'FREE_TRIAL_DAYS',0):
            key=await self.conversation_stage()
            first=await self.budget_context(key)
            second=await self.budget_context(key)
            await first.finish(3)
            await first.finish(3)
            await second.heartbeat(1)
            self.assertEqual(second.remaining_seconds,296)
            async with db_session() as session:
                self.assertEqual(learning_modules.result(await session.get(LearningLesson,key),'conversation')['session_id'],str(second.activity_id))

    async def test_actual_course_modules_are_tagged_and_leave_standalone_practice_available(self):
        key=await self.prepared();await self.complete(key)
        with patch.object(settings,'FREE_PERIOD',False),patch.object(settings,'FREE_TRIAL_DAYS',0):
            async with db_session() as session:
                activities=(await session.scalars(select(Session).where(Session.user_id==self.uid))).all()
                self.assertEqual({activity.mode for activity in activities},{'lesson','grammar','srs','listening','chat'})
                self.assertTrue(all(activity.learning_lesson_id==key for activity in activities))
                account=await session.get(User,self.uid)
                self.assertFalse(await is_section_limit_reached(Repo(session),account,section='grammar'))
                self.assertFalse(await is_section_limit_reached(Repo(session),account,section='listening'))

    async def test_paid_course_budget_becomes_free_only_after_subscription_expires(self):
        with patch.object(settings,'FREE_PERIOD',False),patch.object(settings,'FREE_TRIAL_DAYS',0):
            async with db_session() as session:
                (await session.get(User,self.uid)).subscription_until=learning.now()+timedelta(days=1)
            key=await self.conversation_stage();ctx=await self.budget_context(key)
            await ctx.heartbeat(600)
            self.assertEqual(ctx.remaining_seconds,-1)
            async with db_session() as session:
                (await session.get(User,self.uid)).subscription_until=learning.now()-timedelta(seconds=1)
            await ctx.heartbeat(5)
            self.assertEqual(ctx.remaining_seconds,295)
            self.assertFalse(ctx.has_subscription)

    async def test_access_resumes_used_lesson_and_payment_unlocks_new_lessons_until_expiry(self):
        with patch.object(settings,'FREE_PERIOD',False),patch.object(settings,'FREE_TRIAL_DAYS',0):
            key=await self.prepared();started=await self.post(f'/lessons/{key}/start')
            await self.post(f'/lessons/{key}/pause',{'elapsed_sec':0,'activity_token':started['activity_token']});await self.post(f'/lessons/{key}/start')
            before=(await self.client.get('/api/learning/access',headers=self.headers)).json()
            self.assertEqual(before['remaining_today'],0)
            self.assertEqual(before['conversation_seconds'],300)
            async with db_session() as session:
                payment=await Repo(session).create_pending_payment(user_id=self.uid,plan='monthly',amount_rub=999,days_granted=30,provider_payment_id='course-test-'+uuid4().hex)
                pid=payment.id
            pending=(await self.client.get('/api/learning/access',headers=self.headers)).json()
            self.assertFalse(pending['full_access'])
            async with db_session() as session:
                self.assertTrue(await Repo(session).credit_subscription_for_payment(pid))
                until=(await session.get(User,self.uid)).subscription_until
            paid=(await self.client.get('/api/learning/access',headers=self.headers)).json()
            self.assertTrue(paid['full_access']);self.assertIsNone(paid['remaining_today']);self.assertIsNone(paid['conversation_seconds'])
            async with db_session() as session:
                await Repo(session).credit_subscription_for_payment(pid)
                self.assertEqual((await session.get(User,self.uid)).subscription_until,until)
                (await session.get(User,self.uid)).subscription_until=learning.now()-timedelta(seconds=1)
            expired=(await self.client.get('/api/learning/access',headers=self.headers)).json()
            self.assertFalse(expired['full_access']);self.assertEqual(expired['remaining_today'],0)

    async def test_trial_and_promotion_remove_lesson_quotas(self):
        await self.prepared()
        with patch.object(settings,'FREE_PERIOD',False),patch.object(settings,'FREE_TRIAL_DAYS',1):
            trial=(await self.client.get('/api/learning/access',headers=self.headers)).json()
            self.assertTrue(trial['trial_active']);self.assertTrue(trial['full_access']);self.assertIsNone(trial['daily_limit'])
        with patch.object(settings,'FREE_PERIOD',True),patch.object(settings,'FREE_TRIAL_DAYS',0):
            promotion=(await self.client.get('/api/learning/access',headers=self.headers)).json()
            self.assertTrue(promotion['full_access']);self.assertIsNone(promotion['remaining_today'])

    async def test_concurrent_overview_and_setup_do_not_duplicate_prepared_lessons(self):
        responses=await asyncio.gather(*(self.client.put('/api/learning/profile',headers=self.headers,json={'goal':'travel'}) for _ in range(4)))
        self.assertTrue(all(r.status_code==200 for r in responses))
        responses=await asyncio.gather(*(self.client.get('/api/learning/overview',headers=self.headers) for _ in range(4)))
        self.assertEqual(len({r.json()['next_lesson']['id'] for r in responses}),1)

    async def test_expired_preparation_lease_is_recovered_after_restart(self):
        key=await self.prepared()
        async with db_session() as session:
            row=await session.get(LearningLesson,key)
            row.generation_status='running'; row.generation_attempts=1; row.generation_lease=learning.now()-timedelta(seconds=10)
            original=copy.deepcopy(row.content)
        with patch.object(learning,'generate_variant',new_callable=AsyncMock,return_value=original):
            self.assertTrue(await learning.prepare_one())
        async with db_session() as session:
            row=await session.get(LearningLesson,key)
            self.assertEqual(row.generation_status,'ready')
            self.assertEqual(row.generation_attempts,2)
            self.assertIsNone(row.session_id)

    async def test_late_pause_from_previous_screen_cannot_pause_resumed_lesson(self):
        key=await self.prepared()
        first=await self.post(f'/lessons/{key}/start',{'request_id':str(uuid4())})
        resumed=await self.post(f'/lessons/{key}/start',{'request_id':str(uuid4())})
        self.assertNotEqual(first['activity_token'],resumed['activity_token'])
        await self.post(f'/lessons/{key}/pause',{'elapsed_sec':20,'activity_token':first['activity_token']})
        await self.post(f'/lessons/{key}/heartbeat',{'elapsed_sec':30,'activity_token':first['activity_token']})
        async with db_session() as session:
            self.assertEqual((await session.get(LearningLesson,key)).status,'active')
            self.assertEqual(await Repo(session).count_sessions_today(self.uid,'lesson'),1)

    async def test_new_placement_updates_unstarted_material_without_resetting_progress(self):
        key=await self.prepared()
        async with db_session() as session:
            await session.execute(update(User).where(User.id==self.uid).values(cefr_level='B1'))
        data=(await self.client.get('/api/learning/overview',headers=self.headers)).json()
        self.assertEqual(data['profile']['level'],'B1')
        self.assertEqual(data['profile']['completed_count'],0)
        self.assertEqual(data['next_lesson']['id'],key)
        self.assertTrue(data['next_lesson']['content']['spec']['topic_key'].startswith('b1_'))

    async def test_generated_variant_keeps_review_targets_and_rejects_invalid_choices(self):
        original=content.material(content.plan('A1','general',5))
        story=original['listening']
        response={'practice':original['practice'],'listening':{'script':story['script'],
            'questions':[copy.deepcopy(story['questions'][0]),copy.deepcopy(story['questions'][0])]},
            'conversation':original['conversation']['prompts']}
        with patch('app.grammar._call_llm',new_callable=AsyncMock,return_value=json.dumps(response)):
            generated=await learning.generate_variant(original)
        self.assertEqual([q['topic_key'] for q in generated['practice']],[q['topic_key'] for q in original['practice']])
        response['practice'][0]['choices']=['am','am','is','are']
        with patch('app.grammar._call_llm',new_callable=AsyncMock,return_value=json.dumps(response)):
            with self.assertRaises(ValueError): await learning.generate_variant(original)

    async def test_existing_tutor_requires_three_replies_and_resumes_saved_history(self):
        key=await self.prepared(); await self.post(f'/lessons/{key}/start')
        async with db_session() as session:
            (await session.get(LearningLesson,key)).step_index=4
        await self.conversation(key,turns=1)
        await self.post(f'/lessons/{key}/advance',{'step_index':4},status=409)
        async with db_session() as session:
            first=learning_modules.result(await session.get(LearningLesson,key),'conversation')
            self.assertEqual(first['turns'],1)
            self.assertTrue(first['history'])
        resumed=await self.conversation(key,turns=2)
        self.assertEqual(next(m['text'] for m in resumed.messages if m.get('role')=='tutor'),'Tell me more.')
        await self.post(f'/lessons/{key}/advance',{'step_index':4})
        async with db_session() as session:
            self.assertEqual(learning_modules.result(await session.get(LearningLesson,key),'conversation')['turns'],3)
            self.assertIsNone(await session.get(LearningSkill,(self.uid,'speaking')))

    async def test_conversation_uses_frozen_lesson_topic_level_questions_and_words(self):
        key=await self.prepared(); await self.post(f'/lessons/{key}/start')
        async with db_session() as session:
            row=await session.get(LearningLesson,key)
            row.step_index=4
            material=copy.deepcopy(row.content)
        socket=await self.conversation(key,turns=1,query={
            'level':'C1','role':'custom','role_custom':'pirate',
            'lesson_context':'client must not supply lesson material',
        })
        first=next(m for m in socket.messages if m.get('role')=='tutor')
        self.assertEqual(first['text'],material['conversation']['opening'])
        prompt=socket.prompts[0]
        self.assertIn('A1',prompt)
        self.assertIn(material['spec']['title'],prompt)
        self.assertIn(material['conversation']['objective'],prompt)
        for question in material['conversation']['prompts']: self.assertIn(question,prompt)
        for item in material['words']: self.assertIn(item['word'],prompt)
        self.assertNotIn('pirate',prompt)
        self.assertNotIn('client must not supply',prompt)
        self.assertNotIn('advanced',prompt)

    async def test_voice_greeting_is_the_same_lesson_opening_and_is_sent_to_tts(self):
        key=await self.prepared(); await self.post(f'/lessons/{key}/start')
        async with db_session() as session:
            row=await session.get(LearningLesson,key)
            row.step_index=4
            opening=row.content['conversation']['opening']
        tts=object()
        with patch.object(voice,'get_stt_provider',return_value=object()), \
             patch.object(voice,'get_tts_provider',return_value=tts), \
             patch.object(voice,'_send_tts_to_ws',new_callable=AsyncMock) as speak:
            socket=await self.conversation(key,turns=0,query={'mode':'voice'})
        self.assertEqual(next(m['text'] for m in socket.messages if m.get('role')=='tutor'),opening)
        speak.assert_awaited_once_with(socket,tts,opening)

    async def test_resuming_after_correction_repeats_only_question_and_preserves_history(self):
        key=await self.prepared(); await self.post(f'/lessons/{key}/start')
        async with db_session() as session:
            row=await session.get(LearningLesson,key)
            row.step_index=4
        reply='Correction: I live in London.\n\nWhat do you like about your city?'
        original=await self.conversation(key,turns=1,reply=reply)
        corrected=[m for m in original.messages if m.get('role')=='tutor'][-1]
        self.assertEqual(corrected['correction'],'I live in London.')
        resumed=await self.conversation(key,turns=0)
        first=next(m for m in resumed.messages if m.get('role')=='tutor')
        self.assertEqual(first['text'],'What do you like about your city?')
        self.assertIsNone(first.get('correction'))
        async with db_session() as session:
            history=learning_modules.result(await session.get(LearningLesson,key),'conversation')['history']
            self.assertEqual(len(history),3)
            self.assertEqual(history[-1]['text'],'What do you like about your city?')

    async def test_audio_is_private_and_marked_available_only_after_success(self):
        key=await self.prepared()
        response=await self.client.get(f'/api/learning/lessons/{key}/audio',headers=self.headers)
        self.assertEqual(response.status_code,503)
        async with db_session() as session:
            self.assertFalse((await session.get(LearningLesson,key)).answers.get('_audio_available'))
        from fastapi.responses import Response
        with patch.object(learning,'audio',new_callable=AsyncMock,return_value=Response(b'RIFFsynthetic',media_type='audio/wav')):
            response=await self.client.get(f'/api/learning/lessons/{key}/audio',headers=self.other_headers)
            self.assertEqual(response.status_code,404)
            response=await self.client.get(f'/api/learning/lessons/{key}/audio',headers=self.headers)
            self.assertEqual(response.status_code,200)
        async with db_session() as session:
            self.assertTrue((await session.get(LearningLesson,key)).answers['_audio_available'])

    async def test_lesson_errors_feed_existing_mistake_practice_and_are_server_graded(self):
        key=await self.prepared(); await self.post(f'/lessons/{key}/start')
        body,result=await self.grammar_round(key,wrong=True)
        self.assertEqual(result['score'],75)  # Client claimed 4/4 and is_correct=True.
        await self.api_post('grammar/lesson/finish',body)
        async with db_session() as session:
            errors=list((await session.scalars(select(UserMistake).where(UserMistake.user_id==self.uid))).all())
            self.assertEqual(len(errors),1)
            self.assertEqual(errors[0].bad_phrase,'I is a student.')
            self.assertEqual(errors[0].good_phrase,'I am a student.')
        await self.post(f'/lessons/{key}/advance',{'step_index':0})
        await self.post(f'/lessons/{key}/advance',{'step_index':1},status=409)
        await self.grammar_round(key,mistakes=True)
        await self.post(f'/lessons/{key}/advance',{'step_index':1})

    async def test_srs_review_survives_reload_and_is_not_applied_twice(self):
        key=await self.prepared(); await self.post(f'/lessons/{key}/start')
        await self.grammar_round(key)
        for index in (0,1): await self.post(f'/lessons/{key}/advance',{'step_index':index})
        body={'learning_lesson_id':key}
        start=await self.api_post('srs/session/start',body)
        self.assertEqual(await self.api_post('srs/session/start',body),start)
        async def cards():
            return (await self.client.get('/api/srs/session',params=body,headers=self.headers)).json()['cards']
        original=await cards(); first=original[0]['word']
        review={**body,'word':first,'correct':True}
        response=await self.api_post('srs/review',review)
        self.assertEqual(await self.api_post('srs/review',review),response)
        self.assertEqual(len(await cards()),len(original)-1)
        finish={**body,'session_id':start['session_id'],'duration_sec':1}
        await self.api_post('srs/session/finish',finish,status=409)
        for card in await cards(): await self.api_post('srs/review',{**body,'word':card['word'],'correct':True})
        await self.api_post('srs/session/finish',finish)
        await self.api_post('srs/session/finish',finish)
        async with db_session() as session:
            word=await session.scalar(select(UserVocabulary).where(UserVocabulary.user_id==self.uid,UserVocabulary.word==first))
            self.assertEqual(word.srs_total_attempts,1)
            self.assertEqual(learning_modules.result(await session.get(LearningLesson,key),'words')['total'],len(original))

    async def test_podcast_is_reused_after_restart_without_duplicate_usage(self):
        from app import listening
        key=await self.prepared(); await self.post(f'/lessons/{key}/start')
        async with db_session() as session: (await session.get(LearningLesson,key)).step_index=3
        body={'learning_lesson_id':key,'duration_min':1,'category':'travel','level':'A1'}
        with patch.object(listening,'_call_llm_for_script',new_callable=AsyncMock,return_value='TOPIC: London\nI live in London.') as llm, \
             patch.object(listening,'_synthesize_full',new_callable=AsyncMock,return_value=b'\0'*48000):
            podcast=await self.api_post('listening/generate',body)
            listening._AUDIO_STORE.clear()
            self.assertEqual(await self.api_post('listening/generate',body),podcast)
            self.assertEqual(llm.await_count,1)
            from pathlib import Path
            for path in Path(self.media.name).glob('podcast-*.wav'): path.unlink()
            self.assertEqual(await self.api_post('listening/generate',body),podcast)
            self.assertEqual(llm.await_count,1)
        response=await self.client.get(podcast['audio_url'],headers={'Range':'bytes=0-43'})
        self.assertEqual(response.status_code,206)
        self.assertTrue(response.content.startswith(b'RIFF'))
        await self.post(f'/lessons/{key}/advance',{'step_index':3},status=409)
        await self.post(f'/lessons/{key}/listened')
        await self.post(f'/lessons/{key}/advance',{'step_index':3})
        async with db_session() as session:
            activities=list((await session.scalars(select(Session).where(Session.user_id==self.uid,Session.mode=='listening'))).all())
            self.assertEqual(len(activities),1)
            self.assertEqual(await session.scalar(select(DailyUsage.used_seconds).where(DailyUsage.user_id==self.uid)),1)

    async def test_course_modules_reject_foreign_users_and_wrong_stage(self):
        key=await self.prepared(); row=await self.post(f'/lessons/{key}/start')
        await self.api_post('grammar/lesson',{'learning_lesson_id':key,'topic_key':row['content']['spec']['topic_key']},status=404,headers=self.other_headers)
        await self.api_post('grammar/lesson',{'learning_lesson_id':key,'topic_key':'a1_pronouns'},status=409)
        await self.api_post('srs/session/start',{'learning_lesson_id':key},status=409)
        await self.api_post('listening/generate',{'learning_lesson_id':key,'duration_min':1,'category':'travel'},status=409)

    async def test_course_podcast_needs_only_lesson_id_and_uses_its_level_and_vocabulary(self):
        from app import listening
        key=await self.prepared(); await self.post(f'/lessons/{key}/start')
        async with db_session() as session: (await session.get(LearningLesson,key)).step_index=3
        with patch.object(listening,'_build_listening_prompt',wraps=listening._build_listening_prompt) as prompt, \
             patch.object(listening,'_call_llm_for_script',new_callable=AsyncMock,return_value='TOPIC: Introductions\nMy name is Alex.') as llm, \
             patch.object(listening,'_synthesize_full',new_callable=AsyncMock,return_value=b'\0'*48000) as tts:
            await self.api_post('listening/generate',{'learning_lesson_id':key})
        self.assertEqual(prompt.call_args.kwargs['level'],'A1')
        self.assertEqual(prompt.call_args.kwargs['duration_min'],1)
        self.assertEqual(prompt.call_args.kwargs['category'],'culture')
        self.assertIn('name',prompt.call_args.kwargs['vocab_words'])
        self.assertIn('представиться',llm.call_args.args[0])
        self.assertEqual(tts.call_args.kwargs['speed'],0.75)

    async def test_course_audio_parameters_cannot_be_changed_by_standalone_settings(self):
        from app import listening
        key=await self.prepared(); await self.post(f'/lessons/{key}/start')
        async with db_session() as session:
            lesson=await session.get(LearningLesson,key)
            lesson.step_index=3; lesson.level='B2'
            lesson.content={**lesson.content,'spec':{**lesson.content['spec'],'level':'B2','goal':'work'}}
        with patch.object(listening,'_build_listening_prompt',wraps=listening._build_listening_prompt) as prompt, \
             patch.object(listening,'_call_llm_for_script',new_callable=AsyncMock,return_value='TOPIC: Work\nI enjoy my work.'), \
             patch.object(listening,'_synthesize_full',new_callable=AsyncMock,return_value=b'\0'*48000) as tts:
            await self.api_post('listening/generate',{'learning_lesson_id':key,'level':'A1',
                'duration_min':20,'category':'news','speed':1.25,'use_vocab':False})
        self.assertEqual(prompt.call_args.kwargs['level'],'B2')
        self.assertEqual(prompt.call_args.kwargs['duration_min'],3)
        self.assertEqual(prompt.call_args.kwargs['category'],'business')
        self.assertTrue(prompt.call_args.kwargs['vocab_words'])
        self.assertEqual(tts.call_args.kwargs['speed'],1.0)

    async def test_standalone_podcast_keeps_user_settings_and_requires_configuration(self):
        from app import listening
        await self.api_post('listening/generate',{},status=422)
        with patch.object(listening,'_build_listening_prompt',wraps=listening._build_listening_prompt) as prompt, \
             patch.object(listening,'_call_llm_for_script',new_callable=AsyncMock,return_value='TOPIC: Science\nScience is interesting.'), \
             patch.object(listening,'_synthesize_full',new_callable=AsyncMock,return_value=b'\0'*48000) as tts:
            await self.api_post('listening/generate',{'level':'C1','duration_min':5,'category':'science','speed':1.25,'use_vocab':False})
        self.assertEqual(prompt.call_args.kwargs['level'],'C1')
        self.assertEqual(prompt.call_args.kwargs['duration_min'],5)
        self.assertEqual(prompt.call_args.kwargs['category'],'science')
        self.assertEqual(prompt.call_args.kwargs['vocab_words'],[])
        self.assertEqual(tts.call_args.kwargs['speed'],1.25)

    async def test_manual_level_preserves_active_work_progress_and_test_history(self):
        key=await self.prepared(); row=await self.post(f'/lessons/{key}/start')
        async with db_session() as session:
            account=await session.get(User,self.uid); account.cefr_tested_at=learning.now()
            tested_at=account.cefr_tested_at
        response=await self.client.put('/api/auth/level',headers=self.headers,json={'level':'B2'})
        self.assertEqual(response.status_code,200,response.text)
        data=(await self.client.get('/api/learning/overview',headers=self.headers)).json()
        self.assertEqual(data['profile']['level'],'B2')
        self.assertEqual(data['profile']['completed_count'],0)
        self.assertEqual(data['next_lesson']['content'],row['content'])
        async with db_session() as session:
            self.assertEqual((await session.get(User,self.uid)).cefr_tested_at,tested_at)
        response=await self.client.put('/api/auth/level',headers=self.headers,json={'level':'C2'})
        self.assertEqual(response.status_code,422)
        response=await self.client.put('/api/auth/level',json={'level':'A1'})
        self.assertEqual(response.status_code,401)

    async def test_manual_level_rebuilds_unstarted_material(self):
        data=await self.setup_course(); key=data['next_lesson']['id']
        response=await self.client.put('/api/auth/level',headers=self.headers,json={'level':'A2'})
        self.assertEqual(response.status_code,200)
        updated=(await self.client.get('/api/learning/overview',headers=self.headers)).json()
        self.assertEqual(updated['next_lesson']['id'],key)
        self.assertEqual(updated['next_lesson']['content']['spec']['level'],'A2')
        self.assertTrue(updated['next_lesson']['content']['spec']['topic_key'].startswith('a2_'))

    async def test_twenty_real_completed_lessons_checkpoint_and_next_cycle(self):
        data=await self.setup_course()
        await self.post('/checkpoint/start',status=409)
        for ordinal in range(1,21):
            key=data['next_lesson']['id']
            self.assertEqual(data['next_lesson']['content']['spec']['ordinal'],ordinal)
            await self.complete(key)
            data=(await self.client.get('/api/learning/overview',headers=self.headers)).json()
        self.assertEqual(data['profile']['completed_count'],20)
        self.assertEqual(data['profile']['level'],'A1')
        self.assertTrue(data['profile']['checkpoint_due'])
        self.assertIsNone(data['next_lesson'])
        checkpoints=await asyncio.gather(*(self.post('/checkpoint/start') for _ in range(3)))
        self.assertEqual(len({c['id'] for c in checkpoints}),1)
        current=checkpoints[0]; key=current['id']
        while current['question']:
            q=current['question']
            self.assertNotIn('correct',q)
            async with db_session() as session:
                record=await session.get(LearningCheckpoint,key)
                full=next(item for item in record.questions if item['id']==q['id'])
            if full['kind']=='speaking': value='I chose a new job because I wanted to learn. I will study more.'
            elif full['level'] in ('A1','A2'): value=full['correct']
            else: value=next(option for option in full['choices'] if option!=full['correct'])
            body={'question_id':q['id'],'answer':value,'text_mode':True}
            previous=await self.post(f'/checkpoint/{key}/answer',body)
            self.assertEqual(await self.post(f'/checkpoint/{key}/answer',body),previous)
            current=previous
        self.assertEqual(current['result']['cefr'],'A2')
        self.assertNotIn('speaking',current['result']['skills'])
        data=(await self.client.get('/api/learning/overview',headers=self.headers)).json()
        self.assertEqual(data['profile']['cycle'],2)
        self.assertEqual(data['profile']['until_checkpoint'],20)
        self.assertEqual(data['next_lesson']['content']['spec']['ordinal'],21)
        self.assertEqual(data['next_lesson']['content']['spec']['level'],'A2')
        async with db_session() as session:
            self.assertEqual(len(list((await session.scalars(select(LevelTest).where(LevelTest.user_id==self.uid))).all())),1)

if __name__=='__main__': unittest.main()
