"""SQLite 保存已接受输入、完整协议关系和可回看的执行事实。"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

SCHEMA_VERSION = 1


def identity() -> str:
    return uuid4().hex


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, data_dir: Path):
        self.db = sqlite3.connect(data_dir / "travel.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, SCHEMA_VERSION):
            self.db.close()
            raise RuntimeError("数据版本不兼容，请使用对应版本的服务。")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY, title TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS turns (
                turn_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions ON DELETE CASCADE,
                ordinal INTEGER NOT NULL, input TEXT NOT NULL, status TEXT NOT NULL,
                reason TEXT, answer TEXT, answer_source TEXT, tool_error_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, finished_at TEXT, duration_ms REAL, messages TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions ON DELETE CASCADE,
                turn_id TEXT NOT NULL REFERENCES turns ON DELETE CASCADE, sequence INTEGER NOT NULL,
                kind TEXT NOT NULL, timestamp TEXT NOT NULL, request_id TEXT, tool_call_id TEXT,
                data TEXT NOT NULL, UNIQUE(session_id,sequence));
            PRAGMA user_version=1;
        """)

    def close(self):
        self.db.close()

    def event(self, session_id, turn_id, kind, data, *, request_id=None, tool_call_id=None):
        event = {"event_id": identity(), "session_id": session_id, "turn_id": turn_id,
                 "sequence": self.db.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM events WHERE session_id=?", (session_id,)).fetchone()[0],
                 "kind": kind, "timestamp": timestamp(), "request_id": request_id,
                 "tool_call_id": tool_call_id, "data": data}
        self.db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?)", (
            event["event_id"],session_id,turn_id,event["sequence"],kind,event["timestamp"],
            request_id,tool_call_id,json.dumps(data,ensure_ascii=False)))
        return event

    def accept(self, text):
        session_id, turn_id, now = identity(), identity(), timestamp()
        with self.db:
            self.db.execute("INSERT INTO sessions VALUES (?,?,?,?)", (session_id,text[:40],now,now))
            self.db.execute("INSERT INTO turns (turn_id,session_id,ordinal,input,status,created_at,messages) VALUES (?,?,1,?,'running',?,?)", (turn_id,session_id,text,now,json.dumps([{"role":"user","content":text}],ensure_ascii=False)))
            self.event(session_id,turn_id,"turn.accepted",{"input":text})
        return {"schema_version":SCHEMA_VERSION,"session_id":session_id,"turn_id":turn_id}

    def finish(self, session_id, turn_id, answer, messages, outcome, duration_ms):
        now = timestamp()
        with self.db:
            self.db.execute("UPDATE turns SET status=?,reason=?,answer=?,answer_source=?,tool_error_count=?,finished_at=?,duration_ms=?,messages=? WHERE turn_id=?", (outcome["status"],outcome["reason"],answer,outcome["answer_source"],outcome["tool_error_count"],now,duration_ms,json.dumps(messages,ensure_ascii=False),turn_id))
            self.db.execute("UPDATE sessions SET updated_at=? WHERE session_id=?",(now,session_id))
            self.event(session_id,turn_id,"turn.finished",{**outcome,"answer":answer})

    def sessions(self):
        return [dict(row) for row in self.db.execute("SELECT * FROM sessions ORDER BY updated_at DESC,session_id")]

    def snapshot(self, session_id):
        session = self.db.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        if session is None:
            return None
        turns = []
        for row in self.db.execute("SELECT * FROM turns WHERE session_id=? ORDER BY ordinal",(session_id,)):
            turn = dict(row)
            turn["messages"] = json.loads(turn["messages"])
            turns.append(turn)
        events = []
        for row in self.db.execute("SELECT * FROM events WHERE session_id=? ORDER BY sequence", (session_id,)):
            event = dict(row)
            event["data"] = json.loads(event["data"])
            events.append(event)
        return {"schema_version":SCHEMA_VERSION,"session":dict(session),"turns":turns,"events":events}
