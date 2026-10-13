#!/usr/bin/env python3
"""Inspect/apply missing migrations using the deployment's DATABASE_URL.

Default is read-only. --apply repairs missing versions in order, preserves
accounts, existing settings and later ENUM members. No credentials are logged.
"""
from __future__ import annotations
import argparse
import asyncio
import os
import logging
from pathlib import Path
import re
import sys

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine


def statements(source: str):
    """SQL lexer: quoted semicolons, doubled quotes and comments are preserved."""
    out, quote, i = [], None, 0
    while i < len(source):
        char = source[i]
        if quote:
            out.append(char)
            if char == '\\' and i + 1 < len(source):
                i += 1; out.append(source[i])
            elif char == quote:
                if i + 1 < len(source) and source[i + 1] == quote:
                    i += 1; out.append(source[i])
                else:
                    quote = None
        elif char in "'\"`":
            quote = char; out.append(char)
        elif source[i:i+2] == '--' and (i + 2 == len(source) or source[i+2].isspace()):
            i = source.find('\n', i)
            if i < 0: break
            out.append('\n')
        elif source[i:i+2] == '/*':
            end = source.find('*/', i + 2)
            if end < 0: raise RuntimeError('Unterminated SQL comment')
            i = end + 1; out.append(' ')
        elif char == ';':
            if ''.join(out).strip(): yield ''.join(out).strip()
            out = []
        else:
            out.append(char)
        i += 1
    if quote: raise RuntimeError('Unterminated SQL literal')
    if ''.join(out).strip(): yield ''.join(out).strip()


async def snapshot(connection):
    rows = (await connection.execute(text(
        'SELECT TABLE_NAME,COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE FROM information_schema.COLUMNS '
        'WHERE TABLE_SCHEMA=DATABASE()'))).all()
    return {(r[0], r[1]): (r[2], r[3]) for r in rows}


ENUM_ALTER = re.compile(r'(ALTER\s+TABLE\s+`?(\w+)`?\s+MODIFY(?:\s+COLUMN)?\s+`?(\w+)`?\s+ENUM\()(.*?)(\))', re.I | re.S)


def preserve_enums(source, columns):
    def replace(match):
        current = columns.get((match[2], match[3]), ('', ''))[0]
        old = re.findall(r"'([^']*)'", current)
        new = re.findall(r"'([^']*)'", match[4].replace("''", "'"))
        members = list(dict.fromkeys(old + new))
        delimiter = "''" if "''" in match[4] else "'"
        return match[1] + ','.join(delimiter + v + delimiter for v in members) + match[5]
    return ENUM_ALTER.sub(replace, source)


def compatible_source(version, source, columns):
    parts = list(statements(source))
    if version in {13, 21}:
        # Obsolete achievement/provider cleanup is not required for new code.
        # Never delete accounts while repairing a migration history.
        parts = [part for part in parts if not part.upper().startswith('DELETE ')]
    if version == 24:
        # Migration 40 reintroduces VK. Its historical removal has no purpose
        # when upgrading directly to the current release.
        return "INSERT IGNORE INTO schema_version (version) VALUES (24);"
    return preserve_enums(';\n'.join(parts) + ';', columns)


async def verify(connection, app_directory: Path):
    sys.path.insert(0, str(app_directory))
    from app.db.models import Base
    import app.learning_models  # register course tables in the shared metadata
    columns = await snapshot(connection)
    missing = []
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            if (table.name, column.name) not in columns:
                missing.append(f'{table.name}.{column.name}')
            elif getattr(column.type, 'enums', None):
                actual = set(re.findall(r"'([^']*)'", columns[table.name, column.name][0]))
                if not set(column.type.enums).issubset(actual):
                    missing.append(f'{table.name}.{column.name}: ENUM values')
    if missing:
        raise RuntimeError('Schema verification failed: ' + ', '.join(missing))
    provider_values = set(re.findall(r"'([^']*)'", columns.get(('user_identities','provider'),('', ''))[0]))
    if not {'telegram','native','vk','yandex'}.issubset(provider_values):
        raise RuntimeError('Schema verification failed: user_identities.provider ENUM values')
    if columns.get(('users', 'tg_id'), ('', 'NO'))[1] != 'YES':
        raise RuntimeError('Schema verification failed: users.tg_id must allow NULL')
    for table, required in [('learning_lessons',('user_id','ordinal')),('learning_checkpoints',('user_id','after_count'))]:
        indexes = (await connection.execute(text('SELECT INDEX_NAME,COLUMN_NAME,SEQ_IN_INDEX FROM information_schema.STATISTICS '
            'WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:table AND NON_UNIQUE=0 ORDER BY INDEX_NAME,SEQ_IN_INDEX'),{'table':table})).all()
        grouped = {}
        for index,column,_ in indexes: grouped.setdefault(index,[]).append(column)
        if list(required) not in grouped.values():
            raise RuntimeError(f'Schema verification failed: {table} unique index')
    print('Current application tables, columns and ENUM values verified', flush=True)


async def run(directory, expected_database, expected_user, apply=False, app_directory=None):
    logging.getLogger('asyncmy').setLevel(logging.ERROR)
    try:
        value = os.environ.get('DATABASE_URL', '').replace('mysql+aiomysql://', 'mysql+asyncmy://', 1)
        url = make_url(value)
    except Exception:
        raise RuntimeError('DATABASE_URL is missing or invalid') from None
    if url.drivername != 'mysql+asyncmy' or url.database != expected_database or url.username != expected_user:
        raise RuntimeError('DATABASE_URL does not match the expected database/user or driver')
    files = sorted(directory.glob('[0-9][0-9][0-9][0-9]_*.sql'))
    versions = [int(path.name.split('_')[0]) for path in files]
    if not files or versions != list(range(1, max(versions) + 1)):
        raise RuntimeError('Migration directory must contain a complete, consecutive sequence')
    sources = {int(p.name.split('_')[0]): p.read_text(encoding='utf-8') for p in files}
    for source in sources.values(): list(statements(source))
    engine = create_async_engine(url, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            if await connection.scalar(text('SELECT DATABASE()')) != expected_database:
                raise RuntimeError('Wrong database selected')
            columns = await snapshot(connection)
            has_history = ('schema_version', 'version') in columns
            recorded = set((await connection.execute(text('SELECT version FROM schema_version'))).scalars()) if has_history else set()
            if recorded - set(versions):
                raise RuntimeError('Database contains migrations newer than this checkout')
            print('Applied versions: ' + (', '.join(map(str, sorted(recorded))) or 'none'), flush=True)
            pending = [p for p in files if int(p.name.split('_')[0]) not in recorded]
            print('Missing migrations: ' + (', '.join(p.name for p in pending) or 'none'), flush=True)
            if not apply:
                if not pending and app_directory: await verify(connection, app_directory)
                print('Read-only check; use --apply to reconcile missing migrations', flush=True)
                return
            lock_name = 'englishbot:migrations:' + expected_database
            if not await connection.scalar(text('SELECT GET_LOCK(:name, 0)'), {'name': lock_name[:64]}):
                raise RuntimeError('Another migration process holds the database lock')
            await connection.commit()
            try:
                saved_settings = dict((await connection.execute(text('SELECT `key`,value FROM settings_kv'))).all()) if ('settings_kv','key') in columns else {}
                # Handles copied schemas with absent version history too.
                await connection.exec_driver_sql('CREATE TABLE IF NOT EXISTS schema_version '
                    '(version INT UNSIGNED PRIMARY KEY, applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)')
                await connection.commit()
                for path in pending:
                    version = int(path.name.split('_')[0])
                    source = compatible_source(version, sources[version], await snapshot(connection))
                    try:
                        for statement in statements(source):
                            # Raw SQL avoids interpreting JSON :numbers as binds.
                            # Escape literal percent signs for asyncmy's format style.
                            await connection.exec_driver_sql(statement.replace('%','%%'), ())
                        # Restore pre-existing settings changed by historical SQL.
                        for key, value in saved_settings.items():
                            await connection.execute(text('UPDATE settings_kv SET value=:value WHERE `key`=:key'), {'key':key,'value':value})
                        await connection.execute(text('INSERT IGNORE INTO schema_version(version) VALUES(:v)'), {'v':version})
                        await connection.commit()
                    except Exception as exc:
                        await connection.rollback()
                        raise RuntimeError(f'Migration {path.name} stopped ({type(exc).__name__}); DDL may have committed; rerun after resolving the cause') from None
                    print('Applied ' + path.name, flush=True)
                if app_directory: await verify(connection, app_directory)
                print('All migration versions verified', flush=True)
            finally:
                await connection.execute(text('SELECT RELEASE_LOCK(:name)'), {'name':lock_name[:64]})
                await connection.commit()
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--app-directory', type=Path, default=Path('/app'))
    parser.add_argument('--expected-database', required=True)
    parser.add_argument('--expected-user', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        asyncio.run(run(args.directory, args.expected_database, args.expected_user, args.apply, args.app_directory))
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr); return 1
    except Exception as exc:
        print(f'Migration check failed ({type(exc).__name__}); check connectivity and database permissions', file=sys.stderr); return 1
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
