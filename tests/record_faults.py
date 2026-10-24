"""显式测试支撑：只装配公开能力、可控外部响应或指定故障。"""
import sqlite3
from contextlib import closing
from pathlib import Path


def fail_event(directory, kind):
    with closing(sqlite3.connect(Path(directory) / "travel.sqlite3")) as database, database:
        database.execute("CREATE TRIGGER fail_event BEFORE INSERT ON events WHEN NEW.kind='" + kind + "' BEGIN SELECT RAISE(ABORT,'password=storage-secret'); END")


__all__ = ['fail_event']
