"""SQLite 保存已接受输入、完整协议关系和可回看的执行事实。"""

import base64
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from query_materials import summarize_query

SCHEMA_VERSION = 1


def identity() -> str:
    return uuid4().hex


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def usage_numbers(value, prefix=""):
    result = {}
    if isinstance(value,dict):
        for key,item in value.items():
            field = f"{prefix}.{key}" if prefix else key
            if isinstance(item,(int,float)) and not isinstance(item,bool):
                result[field] = item
            elif isinstance(item,dict):
                result.update(usage_numbers(item,field))
    return result


def usage_summary(requests):
    numbers = [usage_numbers(request["usage"]) for request in requests]
    fields = {"input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"}
    fields.update(field for request in numbers for field in request)
    result = {}
    for field in sorted(fields):
        values = [request[field] for request in numbers if field in request]
        result[field] = {"value":sum(values) if values else None,"known_count":len(values),"request_count":len(requests)}
    return result


class StartupError(RuntimeError):
    """可直接展示的固定启动诊断，不含底层错误或路径。"""


class Store:
    def __init__(self, data_dir: Path):
        self.db = sqlite3.connect(data_dir / "travel.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, SCHEMA_VERSION):
            self.db.close()
            raise StartupError("数据版本不兼容，请使用对应版本的服务。")
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
            CREATE UNIQUE INDEX IF NOT EXISTS single_running_turn ON turns((1)) WHERE status='running';
            CREATE TABLE IF NOT EXISTS submissions (
                submission_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                session_id TEXT REFERENCES sessions ON DELETE SET NULL,
                turn_id TEXT REFERENCES turns ON DELETE SET NULL);
            CREATE TABLE IF NOT EXISTS event_cursors (
                cursor INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE REFERENCES events ON DELETE CASCADE, notice TEXT);
            CREATE TABLE IF NOT EXISTS metadata (name TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS payloads (
                payload_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions ON DELETE CASCADE,
                turn_id TEXT NOT NULL REFERENCES turns ON DELETE CASCADE, kind TEXT NOT NULL, content TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS requests (
                request_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions ON DELETE CASCADE,
                turn_id TEXT NOT NULL REFERENCES turns ON DELETE CASCADE, ordinal INTEGER NOT NULL,
                status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, duration_ms REAL,
                input_payload_id TEXT NOT NULL REFERENCES payloads,
                response_payload_id TEXT REFERENCES payloads, error_payload_id TEXT REFERENCES payloads,
                usage TEXT, usage_state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tool_calls (
                tool_call_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions ON DELETE CASCADE,
                turn_id TEXT NOT NULL REFERENCES turns ON DELETE CASCADE,
                request_id TEXT NOT NULL REFERENCES requests ON DELETE CASCADE, ordinal INTEGER NOT NULL,
                data TEXT NOT NULL);

            PRAGMA user_version=1;
        """)

        with self.db:
            # 兼容已有 schema v1：删除通知共用全库游标，但不属于已删除会话的级联实体。
            if "notice" not in {row["name"] for row in self.db.execute("PRAGMA table_info(event_cursors)")}:
                high = self.cursor()
                self.db.execute("ALTER TABLE event_cursors RENAME TO old_event_cursors")
                self.db.execute("CREATE TABLE event_cursors (cursor INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT UNIQUE REFERENCES events ON DELETE CASCADE,notice TEXT)")
                self.db.execute("INSERT INTO event_cursors(cursor,event_id) SELECT cursor,event_id FROM old_event_cursors")
                self.db.execute("UPDATE sqlite_sequence SET seq=? WHERE name='event_cursors'", (high,))
                if not self.db.execute("SELECT 1 FROM sqlite_sequence WHERE name='event_cursors'").fetchone():
                    self.db.execute("INSERT INTO sqlite_sequence VALUES ('event_cursors',?)", (high,))
                self.db.execute("DROP TABLE old_event_cursors")
            self.db.execute("INSERT OR IGNORE INTO metadata VALUES ('stream_id',?)", (identity(),))
            self.db.execute("INSERT INTO event_cursors(event_id) SELECT e.event_id FROM events e WHERE NOT EXISTS (SELECT 1 FROM event_cursors c WHERE c.event_id=e.event_id) ORDER BY e.timestamp,e.session_id,e.sequence")
            # 已保存的历史轮次也拥有稳定详情身份；只复制已脱敏的既有字段。
            for row in self.db.execute("SELECT session_id,turn_id,input,answer FROM turns").fetchall():
                for field in ("input","answer"):
                    kind = "turn_" + field
                    if row[field] is not None and not self.db.execute("SELECT 1 FROM payloads WHERE turn_id=? AND kind=?",(row["turn_id"],kind)).fetchone():
                        self.save_payload(row["session_id"],row["turn_id"],kind,row[field])
        self.stream_id = self.db.execute("SELECT value FROM metadata WHERE name='stream_id'").fetchone()[0]

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
        event["cursor"] = self.db.execute("INSERT INTO event_cursors(event_id) VALUES (?)", (event["event_id"],)).lastrowid
        return event

    def submission(self, submission_id):
        row = self.db.execute("SELECT * FROM submissions WHERE submission_id=?", (submission_id,)).fetchone()
        return dict(row) if row else None
    def cursor(self):
        row = self.db.execute("SELECT seq FROM sqlite_sequence WHERE name='event_cursors'").fetchone()
        return row[0] if row else 0

    def events_after(self, cursor):
        result = []
        for row in self.db.execute("SELECT e.*,c.cursor,c.notice FROM event_cursors c LEFT JOIN events e USING(event_id) WHERE c.cursor>? ORDER BY c.cursor LIMIT 100", (cursor,)):
            if row["notice"] is not None:
                result.append({**json.loads(row["notice"]),"cursor":row["cursor"]})
            else:
                event = dict(row)
                del event["notice"]
                event["data"] = json.loads(event["data"])
                result.append(event)
        return result

    def event_exists(self, cursor):
        return self.db.execute("SELECT 1 FROM event_cursors WHERE cursor=?", (cursor,)).fetchone() is not None

    def save_payload(self, session_id, turn_id, kind, content):
        payload_id = identity()
        self.db.execute("INSERT INTO payloads VALUES (?,?,?,?,?)", (payload_id,session_id,turn_id,kind,json.dumps(content,ensure_ascii=False)))
        return payload_id

    def payload(self, payload_id, offset=None, limit=8192):
        row = self.db.execute("SELECT payload_id,kind,content FROM payloads WHERE payload_id=?", (payload_id,)).fetchone()
        if row is None:
            return None
        result = {"schema_version":SCHEMA_VERSION,"payload_id":row["payload_id"],"kind":row["kind"]}
        if offset is None:
            return {**result,"content":json.loads(row["content"])}
        text = row["content"]
        end = min(offset+limit,len(text))
        return {**result,"offset":offset,"total":len(text),"text":text[offset:end],"next_offset":end if end<len(text) else None,"encoding":"unicode_code_points"}

    def search(self, session_id, query, scope="full"):
        if not self.db.execute("SELECT 1 FROM sessions WHERE session_id=?",(session_id,)).fetchone():
            return None
        matches = []
        turns = {row["turn_id"]:dict(row) for row in self.db.execute("SELECT * FROM turns WHERE session_id=? ORDER BY ordinal",(session_id,))}
        owners = {}
        request_ordinals = {}
        for row in self.db.execute("SELECT * FROM requests WHERE session_id=?",(session_id,)):
            request_ordinals[row["request_id"]] = row["ordinal"]
            for field in ("input","response","error"):
                if row[field+"_payload_id"]:
                    owners[row[field+"_payload_id"]] = {"object_id":row["request_id"],"object_type":"request","request_id":row["request_id"],"request_ordinal":row["ordinal"]}
        for row in self.db.execute("SELECT data,turn_id FROM tool_calls WHERE session_id=?",(session_id,)):
            data = json.loads(row["data"])
            for field in ("arguments","service","result","error"):
                if data.get(field+"_payload_id"):
                    owners[data[field+"_payload_id"]] = {"object_id":data["tool_call_id"],"object_type":"tool","request_id":data["request_id"],"request_ordinal":request_ordinals[data["request_id"]],"tool_ordinal":data["ordinal"],"tool_name":data["name"]}
        def match(text, turn_id, field, owner, payload_id=None):
            highlight = query
            offset = text.find(highlight)
            if offset < 0:
                highlight = json.dumps(query,ensure_ascii=False)[1:-1]
                offset = text.find(highlight)
            if offset >= 0:
                matches.append({"turn_id":turn_id,"turn_ordinal":turns[turn_id]["ordinal"],"field":field,"payload_id":payload_id,"offset":offset,"highlight":highlight,"excerpt":text[max(0,offset-40):offset+len(highlight)+60],"excerpt_is_partial":True,**owner})
        if scope == "full":
            for row in self.db.execute("SELECT * FROM payloads WHERE session_id=? ORDER BY rowid",(session_id,)):
                owner = owners.get(row["payload_id"],{"object_id":row["turn_id"],"object_type":"turn"})
                match(row["content"],row["turn_id"],row["kind"],owner,row["payload_id"])
        else:
            for row in self.db.execute("SELECT * FROM events WHERE session_id=? ORDER BY sequence",(session_id,)):
                object_id = row["tool_call_id"] or row["request_id"] or row["turn_id"]
                object_type = "tool" if row["tool_call_id"] else "request" if row["request_id"] else "turn"
                owner = next((owner for owner in owners.values() if owner["object_id"]==object_id), {"object_id":object_id,"object_type":object_type,"request_id":row["request_id"]})
                match(row["data"],row["turn_id"],row["kind"],owner)
        matches.sort(key=lambda item:item["turn_ordinal"])
        return {"schema_version":SCHEMA_VERSION,"scope":scope,"query":query,"matches":matches}


    def observe_request(self, session_id, turn_id, kind, data):
        with self.db:
            if kind == "request.started":
                payload_id = self.save_payload(session_id,turn_id,"input",data["input"])
                self.db.execute("INSERT INTO requests (request_id,session_id,turn_id,ordinal,status,started_at,input_payload_id,usage_state) VALUES (?,?,?,?,'running',?,?,'not_completed')", (data["request_id"],session_id,turn_id,data["ordinal"],data["started_at"],payload_id))
                summary = {"ordinal":data["ordinal"],"status":"running","input_payload_id":payload_id}
            else:
                failed = kind == "request.failed"
                field = "error" if failed else "response"
                payload_id = self.save_payload(session_id,turn_id,field,data[field])
                status = "failed" if failed else "completed"
                self.db.execute(f"UPDATE requests SET status=?,finished_at=?,duration_ms=?,{field}_payload_id=?,usage=?,usage_state=? WHERE request_id=?",(status,data["finished_at"],data["duration_ms"],payload_id,json.dumps(data["usage"],ensure_ascii=False) if data["usage"] is not None else None,data["usage_state"],data["request_id"]))
                summary = {"status":status,f"{field}_payload_id":payload_id,"duration_ms":data["duration_ms"],"usage_state":data["usage_state"]}
                if failed and data.get("response") is not None:
                    response_payload_id = self.save_payload(session_id,turn_id,"response",data["response"])
                    self.db.execute("UPDATE requests SET response_payload_id=? WHERE request_id=?",(response_payload_id,data["request_id"]))
                    summary["response_payload_id"] = response_payload_id
            return self.event(session_id,turn_id,kind,summary,request_id=data["request_id"])

    def observe_tool(self, session_id, turn_id, kind, data):
        with self.db:
            tool_id = data["tool_call_id"]
            row = self.db.execute("SELECT data FROM tool_calls WHERE tool_call_id=?",(tool_id,)).fetchone()
            facts = json.loads(row["data"]) if row else {}
            summary = dict(data)
            for field, payload_kind in (("arguments","tool_arguments"),("service","tool_service"),("result","tool_result"),("error","tool_error")):
                content = summary.pop(field,None)
                if content is not None:
                    reference = {"arguments":"arguments","service":"service","result":"result","error":"error"}[field]+"_payload_id"
                    summary[reference] = self.save_payload(session_id,turn_id,payload_kind,content)
            summary["status"] = kind.split(".",1)[1]
            facts.update(summary)
            if row:
                self.db.execute("UPDATE tool_calls SET data=? WHERE tool_call_id=?",(json.dumps(facts,ensure_ascii=False),tool_id))
            else:
                self.db.execute("INSERT INTO tool_calls VALUES (?,?,?,?,?,?)",(tool_id,session_id,turn_id,data["request_id"],data["ordinal"],json.dumps(facts,ensure_ascii=False)))
            return self.event(session_id,turn_id,kind,summary,request_id=data["request_id"],tool_call_id=tool_id)

    def recover(self):
        # 新观察只终止遗留轮次，调用事实仍是上次成功提交的原样。
        with self.db:
            for row in self.db.execute("SELECT session_id,turn_id FROM turns WHERE status IN ('running','stopping')").fetchall():
                self.db.execute("UPDATE turns SET status='terminated',reason='service_interrupted' WHERE turn_id=?", (row["turn_id"],))
                self.event(row["session_id"],row["turn_id"],"turn.recovered", {"status":"terminated","reason":"service_interrupted","context_excluded":True,"message":"服务中断，未结束调用的结果未知；整轮已排除后续上下文。"})

    def context(self, session_id):
        row = self.db.execute("SELECT messages FROM turns WHERE session_id=? AND status != 'running' AND reason IS NOT 'service_interrupted' ORDER BY ordinal DESC LIMIT 1", (session_id,)).fetchone()
        return json.loads(row["messages"]) if row else []

    def accept(self, text, session_id=None, *, submission_id=None, fingerprint=None):
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
            self.save_payload(session_id,turn_id,"turn_input",text)
            self.event(session_id,turn_id,"turn.accepted",{"input_summary":text[:120]})
            if submission_id is not None:
                self.db.execute("INSERT INTO submissions VALUES (?,?,?,?)", (submission_id,fingerprint,session_id,turn_id))
        result = {"schema_version":SCHEMA_VERSION,"session_id":session_id,"turn_id":turn_id}
        if submission_id is not None:
            result["submission_id"] = submission_id
        return result


    def turn(self, turn_id):
        row = self.db.execute("SELECT turn_id,status FROM turns WHERE turn_id=?", (turn_id,)).fetchone()
        return dict(row) if row else None

    def finish(self, session_id, turn_id, answer, messages, outcome, duration_ms):
        now = timestamp()
        with self.db:
            self.db.execute("UPDATE turns SET status=?,reason=?,answer=?,answer_source=?,tool_error_count=?,finished_at=?,duration_ms=?,messages=? WHERE turn_id=?", (outcome["status"],outcome["reason"],answer,outcome["answer_source"],outcome["tool_error_count"],now,duration_ms,json.dumps(messages,ensure_ascii=False),turn_id))
            self.db.execute("UPDATE sessions SET updated_at=? WHERE session_id=?",(now,session_id))
            self.save_payload(session_id,turn_id,"turn_answer",answer)
            self.event(session_id,turn_id,"turn.finished",{**outcome,"answer_summary":answer[:120]})

    def rename(self, session_id, title):
        with self.db:
            return self.db.execute("UPDATE sessions SET title=? WHERE session_id=?", (title,session_id)).rowcount > 0

    def delete(self, session_id):
        with self.db:
            if self.db.execute("SELECT 1 FROM turns WHERE session_id=? AND status IN ('running','stopping')", (session_id,)).fetchone():
                raise ValueError("运行中会话不可删除")
            # 所有会话所属实体必须使用 ON DELETE CASCADE；提交去重身份可使用 SET NULL。
            deleted = self.db.execute("DELETE FROM sessions WHERE session_id=?", (session_id,)).rowcount > 0
            if deleted:
                notice = {"event_id":identity(),"session_id":session_id,"turn_id":None,"sequence":None,
                          "kind":"session.deleted","timestamp":timestamp(),"request_id":None,"tool_call_id":None,"data":{}}
                self.db.execute("INSERT INTO event_cursors(notice) VALUES (?)", (json.dumps(notice),))
            return deleted

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

    def snapshot(self, session_id, limit=50, before=None, summary=False, include_messages=True):
        session = self.db.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        if session is None:
            return None
        turns = []
        rows = self.db.execute("SELECT * FROM turns WHERE session_id=? AND (? IS NULL OR ordinal < ?) ORDER BY ordinal DESC LIMIT ?",(session_id,before,before,limit+1)).fetchall()
        for row in reversed(rows[:limit]):
            turn = dict(row)
            if summary or not include_messages:
                del turn["messages"]
            if summary:
                for field in ("input","answer"):
                    value=turn[field]
                    turn[field+"_is_summary"]=value is not None and len(value)>120
                    if value is not None:
                        turn[field]=value[:120]
            elif include_messages:
                turn["messages"] = json.loads(turn["messages"])
            for payload in self.db.execute("SELECT payload_id,kind FROM payloads WHERE turn_id=? AND kind IN ('turn_input','turn_answer')",(row["turn_id"],)):
                turn[payload["kind"].removeprefix("turn_")+"_payload_id"] = payload["payload_id"]
            turns.append(turn)
        events = []
        first_ordinal = turns[0]["ordinal"] if turns else 0
        last_ordinal = turns[-1]["ordinal"] if turns else 0
        for row in self.db.execute("SELECT events.*,c.cursor FROM events JOIN turns USING(turn_id) JOIN event_cursors c USING(event_id) WHERE events.session_id=? AND turns.ordinal>=? AND turns.ordinal<=? ORDER BY sequence", (session_id,first_ordinal,last_ordinal)):
            event = dict(row)
            event["data"] = json.loads(event["data"])
            events.append(event)
        requests = []
        for row in self.db.execute("SELECT requests.* FROM requests JOIN turns USING(turn_id) WHERE requests.session_id=? AND turns.ordinal>=? AND turns.ordinal<=? ORDER BY turns.ordinal,requests.ordinal", (session_id,first_ordinal,last_ordinal)):
            request = dict(row)
            request["usage"] = json.loads(request["usage"]) if request["usage"] else None
            requests.append(request)
        tool_calls = [json.loads(row["data"]) | {"turn_id":row["turn_id"]} for row in self.db.execute(
            "SELECT tool_calls.data,tool_calls.turn_id FROM tool_calls JOIN turns USING(turn_id) JOIN requests USING(request_id) WHERE tool_calls.session_id=? AND turns.ordinal>=? AND turns.ordinal<=? ORDER BY turns.ordinal,requests.ordinal,tool_calls.ordinal",(session_id,first_ordinal,last_ordinal))]
        interrupted = {turn["turn_id"] for turn in turns if turn["reason"] == "service_interrupted"}
        for request in requests:
            if request["turn_id"] in interrupted and request["status"] == "running":
                request["interrupted"] = True
        for tool in tool_calls:
            if tool["turn_id"] in interrupted and tool["status"] in ("pending","waiting","running"):
                tool["interrupted"] = True
        for turn in turns:
            turn["query_materials"] = []
            turn["unreadable_query_count"] = 0
            for tool in tool_calls:
                if turn["status"] == "completed" and turn["answer_source"] != "application":
                    break
                if tool["turn_id"] != turn["turn_id"] or tool["status"] != "completed" or not tool.get("result_payload_id"):
                    continue
                payload = self.payload(tool["result_payload_id"])
                result = payload["content"] if payload else None
                if not isinstance(result, dict) or result.get("is_error") is not False:
                    continue
                original_input = next(row["input"] for row in rows if row["turn_id"] == turn["turn_id"])
                material = summarize_query(tool["name"], result, original_input)
                if material["entries"]:
                    turn["query_materials"].append({**material, "tool_call_id": tool["tool_call_id"], "payload_id": tool["result_payload_id"]})
                else:
                    turn["unreadable_query_count"] += 1
            if turn["turn_id"] in interrupted:
                turn["context_excluded"] = True
            turn["usage_summary"] = usage_summary([request for request in requests if request["turn_id"] == turn["turn_id"]])
        all_usage = [{"usage":json.loads(row["usage"]) if row["usage"] else None} for row in self.db.execute("SELECT usage FROM requests WHERE session_id=?", (session_id,))]
        return {"tool_calls":tool_calls,"usage_summary":usage_summary(all_usage),"requests":requests,"cursor":self.cursor(),"stream_id":self.stream_id,"schema_version":SCHEMA_VERSION,"session":dict(session),"turns":turns,"events":events,"total_turns":self.db.execute("SELECT COUNT(*) FROM turns WHERE session_id=?", (session_id,)).fetchone()[0],"next_before":first_ordinal if len(rows)>limit else None}
