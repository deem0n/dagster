import os
import time
from contextlib import contextmanager

import pytest
import sqlalchemy

from dagster import seven
from dagster.core.events import DagsterEvent, DagsterEventType, EngineEventData
from dagster.core.events.log import DagsterEventRecord
from dagster.core.storage.event_log import (
    DagsterEventLogInvalidForRun,
    InMemoryEventLogStorage,
    SqlEventLogStorageMetadata,
    SqlEventLogStorageTable,
    SqliteEventLogStorage,
)
from dagster.core.storage.sql import create_engine


@contextmanager
def create_in_memory_event_log_storage():
    yield InMemoryEventLogStorage()


@contextmanager
def create_sqlite_run_event_logstorage():
    with seven.TemporaryDirectory() as tmpdir_path:
        yield SqliteEventLogStorage(tmpdir_path)


event_storage_test = pytest.mark.parametrize(
    'event_storage_factory_cm_fn',
    [create_in_memory_event_log_storage, create_sqlite_run_event_logstorage],
)


@event_storage_test
def test_init_log_storage(event_storage_factory_cm_fn):
    with event_storage_factory_cm_fn() as storage:
        if isinstance(storage, InMemoryEventLogStorage):
            assert not storage.is_persistent
        elif isinstance(storage, SqliteEventLogStorage):
            assert storage.is_persistent
        else:
            raise Exception("Invalid event storage type")


@event_storage_test
def test_log_storage_run_not_found(event_storage_factory_cm_fn):
    with event_storage_factory_cm_fn() as storage:
        assert storage.get_logs_for_run('bar') == []


@event_storage_test
def test_event_log_storage_store_events_and_wipe(event_storage_factory_cm_fn):
    with event_storage_factory_cm_fn() as storage:
        assert len(storage.get_logs_for_run('foo')) == 0
        storage.store_event(
            DagsterEventRecord(
                None,
                'Message2',
                'debug',
                '',
                'foo',
                time.time(),
                dagster_event=DagsterEvent(
                    DagsterEventType.ENGINE_EVENT.value,
                    'nonce',
                    event_specific_data=EngineEventData.in_process(999),
                ),
            )
        )
        assert len(storage.get_logs_for_run('foo')) == 1
        assert storage.get_stats_for_run('foo')
        storage.wipe()
        assert len(storage.get_logs_for_run('foo')) == 0


@event_storage_test
def test_event_log_storage_watch(event_storage_factory_cm_fn):
    def evt(name):
        return DagsterEventRecord(
            None,
            name,
            'debug',
            '',
            'foo',
            time.time(),
            dagster_event=DagsterEvent(
                DagsterEventType.ENGINE_EVENT.value,
                'nonce',
                event_specific_data=EngineEventData.in_process(999),
            ),
        )

    with event_storage_factory_cm_fn() as storage:
        watched = []
        watcher = lambda x: watched.append(x)  # pylint: disable=unnecessary-lambda

        storage = InMemoryEventLogStorage()
        assert len(storage.get_logs_for_run('foo')) == 0

        storage.store_event(evt('Message1'))
        assert len(storage.get_logs_for_run('foo')) == 1
        assert len(watched) == 0

        storage.watch('foo', None, watcher)
        storage.store_event(evt('Message2'))
        assert len(storage.get_logs_for_run('foo')) == 2
        assert len(watched) == 1

        storage.end_watch('foo', lambda event: None)
        storage.store_event(evt('Message3'))
        assert len(storage.get_logs_for_run('foo')) == 3
        assert len(watched) == 2

        storage.end_watch('bar', lambda event: None)
        storage.store_event(evt('Message4'))
        assert len(storage.get_logs_for_run('foo')) == 4
        assert len(watched) == 3

        time.sleep(0.5)  # this value scientifically selected from a range of attractive values
        storage.end_watch('foo', watcher)
        time.sleep(0.5)
        storage.store_event(evt('Message5'))
        assert len(storage.get_logs_for_run('foo')) == 5
        assert len(watched) == 3

        storage.delete_events('foo')
        assert len(storage.get_logs_for_run('foo')) == 0
        assert len(watched) == 3


@event_storage_test
def test_event_log_delete(event_storage_factory_cm_fn):
    with event_storage_factory_cm_fn() as storage:
        assert len(storage.get_logs_for_run('foo')) == 0
        storage.store_event(
            DagsterEventRecord(
                None,
                'Message2',
                'debug',
                '',
                'foo',
                time.time(),
                dagster_event=DagsterEvent(
                    DagsterEventType.ENGINE_EVENT.value,
                    'nonce',
                    event_specific_data=EngineEventData.in_process(999),
                ),
            )
        )
        assert len(storage.get_logs_for_run('foo')) == 1
        storage.delete_events('foo')
        assert len(storage.get_logs_for_run('foo')) == 0


def test_filesystem_event_log_storage_run_corrupted():
    with seven.TemporaryDirectory() as tmpdir_path:
        storage = SqliteEventLogStorage(tmpdir_path)
        # URL begins sqlite:///
        # pylint: disable=protected-access
        with open(os.path.abspath(storage.conn_string_for_run_id('foo')[10:]), 'w') as fd:
            fd.write('some nonsense')
        with pytest.raises(sqlalchemy.exc.DatabaseError):
            storage.get_logs_for_run('foo')


def test_filesystem_event_log_storage_run_corrupted_bad_data():
    with seven.TemporaryDirectory() as tmpdir_path:
        storage = SqliteEventLogStorage(tmpdir_path)
        SqlEventLogStorageMetadata.create_all(create_engine(storage.conn_string_for_run_id('foo')))
        with storage.connect('foo') as conn:
            event_insert = SqlEventLogStorageTable.insert().values(  # pylint: disable=no-value-for-parameter
                run_id='foo', event='{bar}', dagster_event_type=None, timestamp=None
            )
            conn.execute(event_insert)

        with pytest.raises(DagsterEventLogInvalidForRun):
            storage.get_logs_for_run('foo')

        SqlEventLogStorageMetadata.create_all(create_engine(storage.conn_string_for_run_id('bar')))

        with storage.connect('bar') as conn:  # pylint: disable=protected-access
            event_insert = SqlEventLogStorageTable.insert().values(  # pylint: disable=no-value-for-parameter
                run_id='bar', event='3', dagster_event_type=None, timestamp=None
            )
            conn.execute(event_insert)
        with pytest.raises(DagsterEventLogInvalidForRun):
            storage.get_logs_for_run('bar')
