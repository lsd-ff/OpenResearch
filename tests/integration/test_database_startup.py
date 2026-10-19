import sqlite3
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextlib import closing
from threading import Event

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from openresearch.storage.database import Database


def test_startup_waits_for_initial_wal_lock_and_preserves_existing_data(tmp_path):
    path = tmp_path / "startup.sqlite3"
    writer = sqlite3.connect(path, isolation_level=None)
    writer.execute("CREATE TABLE startup_marker(value INTEGER)")
    writer.execute("INSERT INTO startup_marker VALUES (42)")
    writer.execute("BEGIN IMMEDIATE")
    attempted = Event()

    def observe_wal(connection, cursor, statement, parameters, context, executemany):
        if statement == "PRAGMA journal_mode=WAL":
            attempted.set()

    event.listen(Engine, "before_cursor_execute", observe_wal)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(Database, f"sqlite:///{path.as_posix()}")
            try:
                assert attempted.wait(5), "startup did not attempt the journal transition"
                # 持有真实写锁时，启动应等待，不能在第一次 SQLITE_BUSY 后退出。
                with pytest.raises(FutureTimeoutError):
                    future.result(timeout=0.1)
            finally:
                writer.rollback()
            database = future.result(timeout=15)
            database.close()
        with closing(sqlite3.connect(path)) as reader:
            assert reader.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            assert reader.execute("SELECT value FROM startup_marker").fetchone()[0] == 42
    finally:
        event.remove(Engine, "before_cursor_execute", observe_wal)
        writer.close()
