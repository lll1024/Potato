"""SQLite 保存已接受输入、完整协议关系和可回看的执行事实。"""

import base64
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

    def context(self, session_id):
        row = self.db.execute("SELECT messages FROM turns WHERE session_id=? AND status != 'running' AND reason IS NOT 'service_interrupted' ORDER BY ordinal DESC LIMIT 1", (session_id,)).fetchone()
        return json.loads(row["messages"]) if row else []

    def accept(self, text, session_id=None):
        turn_id, now = identity(), timestamp()
        with self.db:
            if session_id is None:
                session_id = identity()
                self.db.execute("INSERT INTO sessions VALUES (?,?,?,?)", (session_id,text[:40],now,now))
            elif not self.db.execute("SELECT 1 FROM sessions WHERE session_id=?", (session_id,)).fetchone():
                raise KeyError(session_id)
            ordinal = self.db.execute("SELECT COALESCE(MAX(ordinal),0)+1 FROM turns WHERE session_id=?", (session_id,)).fetchone()[0]
            messages = self.context(session_id) + [{"role":"user","content":text}]
            self.db.execute("INSERT INTO turns (turn_id,session_id,ordinal,input,status,created_at,messages) VALUES (?,?,?,?,'running',?,?)", (turn_id,session_id,ordinal,text,now,json.dumps(messages,ensure_ascii=False)))
            self.db.execute("UPDATE sessions SET updated_at=? WHERE session_id=?", (now,session_id))
            self.event(session_id,turn_id,"turn.accepted",{"input":text})
        return {"schema_version":SCHEMA_VERSION,"session_id":session_id,"turn_id":turn_id}

    def finish(self, session_id, turn_id, answer, messages, outcome, duration_ms):
        now = timestamp()
        with self.db:
            self.db.execute("UPDATE turns SET status=?,reason=?,answer=?,answer_source=?,tool_error_count=?,finished_at=?,duration_ms=?,messages=? WHERE turn_id=?", (outcome["status"],outcome["reason"],answer,outcome["answer_source"],outcome["tool_error_count"],now,duration_ms,json.dumps(messages,ensure_ascii=False),turn_id))
            self.db.execute("UPDATE sessions SET updated_at=? WHERE session_id=?",(now,session_id))
            self.event(session_id,turn_id,"turn.finished",{**outcome,"answer":answer})

    def rename(self, session_id, title):
        with self.db:
            return self.db.execute("UPDATE sessions SET title=? WHERE session_id=?", (title,session_id)).rowcount > 0

    def delete(self, session_id):
        with self.db:
            if self.db.execute("SELECT 1 FROM turns WHERE session_id=? AND status IN ('running','stopping')", (session_id,)).fetchone():
                raise ValueError("运行中会话不可删除")
            # 所有会话所属实体必须使用 ON DELETE CASCADE；提交去重身份可使用 SET NULL。
            return self.db.execute("DELETE FROM sessions WHERE session_id=?", (session_id,)).rowcount > 0

    def sessions(self, limit=50, cursor=None):
        condition, values = "", []
        if cursor:
            updated_at, session_id = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            if not isinstance(updated_at,str) or not isinstance(session_id,str):
                raise ValueError("无效游标")
            condition = "WHERE sessions.updated_at < ? OR (sessions.updated_at = ? AND sessions.session_id > ?)"
            values = [updated_at,updated_at,session_id]
        rows = self.db.execute(f"""SELECT sessions.*, turns.status, turns.reason, turns.tool_error_count,
            turns.ordinal AS latest_ordinal FROM sessions JOIN turns ON turns.turn_id=(
                SELECT turn_id FROM turns WHERE session_id=sessions.session_id ORDER BY ordinal DESC LIMIT 1)
            {condition} ORDER BY sessions.updated_at DESC,sessions.session_id LIMIT ?""", (*values,limit+1)).fetchall()
        next_cursor = None
        if len(rows)>limit:
            last = rows[limit-1]
            next_cursor = base64.urlsafe_b64encode(json.dumps([last["updated_at"],last["session_id"]]).encode()).decode()
        return {"schema_version":SCHEMA_VERSION,"sessions":[dict(row) for row in rows[:limit]],"next_cursor":next_cursor}

    def snapshot(self, session_id, limit=50, before=None):
        session = self.db.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        if session is None:
            return None
        turns = []
        rows = self.db.execute("SELECT * FROM turns WHERE session_id=? AND (? IS NULL OR ordinal < ?) ORDER BY ordinal DESC LIMIT ?",(session_id,before,before,limit+1)).fetchall()
        for row in reversed(rows[:limit]):
            turn = dict(row)
            turn["messages"] = json.loads(turn["messages"])
            turns.append(turn)
        events = []
        first_ordinal = turns[0]["ordinal"] if turns else 0
        last_ordinal = turns[-1]["ordinal"] if turns else 0
        for row in self.db.execute("SELECT events.* FROM events JOIN turns USING(turn_id) WHERE events.session_id=? AND turns.ordinal>=? AND turns.ordinal<=? ORDER BY sequence", (session_id,first_ordinal,last_ordinal)):
            event = dict(row)
            event["data"] = json.loads(event["data"])
            events.append(event)
        return {"schema_version":SCHEMA_VERSION,"session":dict(session),"turns":turns,"events":events,"total_turns":self.db.execute("SELECT COUNT(*) FROM turns WHERE session_id=?", (session_id,)).fetchone()[0],"next_before":first_ordinal if len(rows)>limit else None}
