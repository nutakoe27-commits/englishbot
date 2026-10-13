"""SQL parser tests and opt-in repair checks on a dedicated local test DB."""
import asyncio
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('migrate_database',ROOT/'scripts/migrate_database.py')
migrations=importlib.util.module_from_spec(spec); spec.loader.exec_module(migrations)
URL=os.environ.get('MIGRATION_TEST_DATABASE_URL','')
SAFE=bool(URL and make_url(URL).host in {'localhost','127.0.0.1'} and (make_url(URL).database or '').endswith('_migration_test'))

class ParserTests(unittest.TestCase):
    def test_semicolons_quotes_percent_signs_and_comments(self):
        sql="-- comment;\n SET @x='a; -- literal'; /* block; */ SELECT 'it''s'; SELECT \"hello;there\"; SELECT '100%';"
        result=list(migrations.statements(sql))
        self.assertEqual(len(result),4)
        self.assertIn('a; -- literal',result[0])
        self.assertIn("it''s",result[1])
        self.assertIn('100%',result[3])

    def test_later_enum_members_survive_old_migrations(self):
        columns={('sessions','mode'):("enum('voice','chat','grammar','srs','lesson')",'NO')}
        sql="ALTER TABLE sessions MODIFY COLUMN mode ENUM('voice','chat','listening') NOT NULL;"
        updated=migrations.compatible_source(8,sql,columns)
        self.assertIn("'lesson'",updated)
        self.assertIn("'srs'",updated)
        self.assertIn("'listening'",updated)
        embedded="SET @ddl='ALTER TABLE sessions MODIFY COLUMN mode ENUM(''voice'',''chat'') NOT NULL';"
        self.assertIn("''lesson''",migrations.compatible_source(8,embedded,columns))

    def test_historical_account_deletion_is_not_replayed(self):
        sql=(ROOT/'db/migrations/0021_remove_google_add_native.sql').read_text()
        result=migrations.compatible_source(21,sql,{})
        self.assertFalse(any(s.upper().startswith('DELETE ') for s in migrations.statements(result)))
        self.assertIn('password_hash',result)

    def test_all_committed_sql_can_be_lexed(self):
        for path in (ROOT/'db/migrations').glob('*.sql'):
            self.assertTrue(list(migrations.statements(path.read_text())))

@unittest.skipUnless(SAFE,'MIGRATION_TEST_DATABASE_URL must use a dedicated local *_migration_test database')
class MigrationIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.url=make_url(URL); self.engine=create_async_engine(URL)
        self.env=patch.dict(os.environ,{'DATABASE_URL':URL}); self.env.start()
        await migrations.run(ROOT/'db/migrations',self.url.database,self.url.username,True,ROOT/'backend')
        async with self.engine.begin() as connection:
            result=await connection.execute(text("INSERT INTO users(first_name,tg_id) VALUES('Migration fixture',NULL)"))
            self.uid=result.lastrowid
            await connection.execute(text("INSERT INTO user_identities(user_id,provider,provider_uid,created_at) VALUES(:uid,'google',:key,NOW())"),{'uid':self.uid,'key':'fixture-'+str(self.uid)})
            await connection.execute(text("INSERT INTO sessions(user_id,mode,used_seconds) VALUES(:uid,'lesson',90)"),{'uid':self.uid})
            self.settings_before=(await connection.execute(text("SELECT value FROM settings_kv WHERE `key`='free_seconds_per_day'"))).scalar()
            await connection.execute(text("UPDATE settings_kv SET value='777' WHERE `key`='free_seconds_per_day'"))

    async def asyncTearDown(self):
        async with self.engine.begin() as connection:
            await connection.execute(text('DELETE FROM users WHERE id=:uid'),{'uid':self.uid})
            await connection.execute(text("UPDATE settings_kv SET value=:value WHERE `key`='free_seconds_per_day'"),{'value':self.settings_before})
        await self.engine.dispose(); self.env.stop()

    async def preserved(self):
        async with self.engine.connect() as connection:
            self.assertEqual(await connection.scalar(text('SELECT COUNT(*) FROM users WHERE id=:uid'),{'uid':self.uid}),1)
            self.assertEqual(await connection.scalar(text('SELECT mode FROM sessions WHERE user_id=:uid'),{'uid':self.uid}),'lesson')
            self.assertEqual(await connection.scalar(text('SELECT provider FROM user_identities WHERE user_id=:uid'),{'uid':self.uid}),'google')
            self.assertEqual(await connection.scalar(text("SELECT value FROM settings_kv WHERE `key`='free_seconds_per_day'")),'777')
            self.assertEqual(await connection.scalar(text('SELECT COUNT(*) FROM schema_version')),43)

    async def test_read_only_check_and_repair_real_history_gaps(self):
        async with self.engine.begin() as connection:
            await connection.execute(text('DELETE FROM schema_version WHERE version IN (8,10,13,15,17,18,21,23,24,35,43)'))
        await migrations.run(ROOT/'db/migrations',self.url.database,self.url.username,False,ROOT/'backend')
        async with self.engine.connect() as connection:
            self.assertEqual(await connection.scalar(text('SELECT COUNT(*) FROM schema_version')),32)
        await migrations.run(ROOT/'db/migrations',self.url.database,self.url.username,True,ROOT/'backend')
        await self.preserved()
        await migrations.run(ROOT/'db/migrations',self.url.database,self.url.username,True,ROOT/'backend')
        await self.preserved()

    async def test_copied_schema_without_history_is_reconciled_without_data_loss(self):
        async with self.engine.begin() as connection:
            await connection.execute(text('DROP TABLE schema_version'))
        await migrations.run(ROOT/'db/migrations',self.url.database,self.url.username,True,ROOT/'backend')
        await self.preserved()

    async def test_wrong_deployment_target_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'does not match'):
            await migrations.run(ROOT/'db/migrations','wrong_database',self.url.username,True,ROOT/'backend')
        await self.preserved()

if __name__=='__main__': unittest.main()
