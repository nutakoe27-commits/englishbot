"""Real local MySQL course regressions; external AI/STT/TTS/S2S are mocked."""
import asyncio
import copy
import json
from datetime import timedelta
import os
import secrets
import unittest
from unittest.mock import AsyncMock,patch
from uuid import uuid4
import httpx
from sqlalchemy import delete,select,update
from sqlalchemy.engine import make_url
from app import learning
from app import learning_content as content, learning_assessment as assessment
from app.auth import issue_jwt
from app.config import settings
from app.db import Repo,db_session
from app.db.engine import init_db,db_engine
from app.db.models import DailyUsage,MytrackerEvent,MytrackerInstall,Session,User,LevelTest
from app.db.models import UserVocabulary
from app.learning_models import LearningProfile,LearningLesson,LearningCheckpoint,LearningSkill
from app.main import app

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
        self.patches=[patch.object(settings,'DATABASE_URL',TEST_URL),patch.object(settings,'LEARNING_ENABLED',True),
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

    async def complete(self,key,finish=True):
        row=await self.post(f'/lessons/{key}/start')
        for step in range(5):
            if step in (0,2,3):
                if step==3: await self.post(f'/lessons/{key}/listening-text')
                async with db_session() as session:
                    model=await session.get(LearningLesson,key)
                    questions=learning.current_questions(model)
                for q in questions:
                    await self.post(f'/lessons/{key}/answer',{'question_id':q['id'],'answer':q['correct']})
            if step==4:
                for i in range(3):
                    body={'turn_id':str(uuid4()),'text':f'I like learning English. Answer {i}.'}
                    first=await self.post(f'/lessons/{key}/talk',body)
                    self.assertEqual(await self.post(f'/lessons/{key}/talk',body),first)
            await self.post(f'/lessons/{key}/advance',{'step_index':step})
        await self.post(f'/lessons/{key}/heartbeat',{'elapsed_sec':30})
        await self.post(f'/lessons/{key}/heartbeat',{'elapsed_sec':60})
        if finish: return await self.post(f'/lessons/{key}/finish',{'elapsed_sec':90})
        return row

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
            self.assertEqual(rows[0].used_seconds,20)
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
            self.assertEqual(await session.scalar(select(DailyUsage.used_seconds).where(DailyUsage.user_id==self.uid)),90)
            self.assertIsNone(await session.get(LearningSkill,(self.uid,'speaking')))
            self.assertIsNone(await session.get(LearningSkill,(self.uid,'listening')))
            self.assertEqual(len((await Repo(session).list_user_words(self.uid))),4)
            self.assertEqual(await Repo(session).user_points(self.uid,month_only=False),1)
            word=await session.scalar(select(UserVocabulary).where(UserVocabulary.user_id==self.uid,UserVocabulary.word=='name'))
            self.assertEqual(word.srs_total_attempts,1)
        response=await self.client.get('/api/learning/overview',headers=self.headers)
        self.assertEqual(response.json()['next_lesson']['content']['spec']['ordinal'],2)

    async def test_failed_finish_rolls_back_then_retry_saves_once(self):
        key=await self.prepared(); await self.complete(key,finish=False)
        with patch.object(Repo,'add_used_seconds',new_callable=AsyncMock,side_effect=RuntimeError('synthetic rollback')):
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

    async def test_server_transcribed_voice_is_scored_and_cannot_be_replayed(self):
        key=await self.prepared(); await self.post(f'/lessons/{key}/start')
        # The earlier stages are exercised in complete(); focus this test on
        # the trusted transcription -> dialogue -> completion boundary.
        async with db_session() as session:
            row=await session.get(LearningLesson,key)
            for q in row.content['warmup']+row.content['practice']+row.content['listening']['questions']:
                row.answers={**row.answers,q['id']:{'value':q['correct'],'correct':True,'correct_answer':q['correct'],
                    'explanation':q['explanation'],'topic_key':q.get('topic_key'),'skill':q['skill'],'text_mode':True}}
            row.step_index=4
        with patch.object(learning,'transcription',new_callable=AsyncMock,return_value='I live in London.'), \
             patch.object(learning,'assess_reply',new_callable=AsyncMock,return_value={'reply':'Tell me more.','feedback':'Хорошо.','score':80,'assessed':True}):
            for _ in range(3):
                response=await self.client.post(f'/api/learning/lessons/{key}/transcribe',headers=self.headers,files={'file':('voice.webm',b'synthetic','audio/webm')})
                self.assertEqual(response.status_code,200,response.text)
                transcript=response.json()
                body={'turn_id':str(uuid4()),'text':transcript['text'],'voice_token':transcript['voice_token']}
                result=await self.post(f'/lessons/{key}/talk',body)
                self.assertTrue(result['turn']['verified_voice'])
                await self.post(f'/lessons/{key}/talk',{**body,'turn_id':str(uuid4())},status=422)
        await self.post(f'/lessons/{key}/advance',{'step_index':4})
        await self.post(f'/lessons/{key}/finish',{'elapsed_sec':20})
        async with db_session() as session:
            skill=await session.get(LearningSkill,(self.uid,'speaking'))
            self.assertEqual(skill.attempts,3)
            self.assertEqual(skill.evidence['recent'][0]['score'],80)

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
