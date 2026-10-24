"""偏好值的一致读取与条件写入；规则由旅行者偏好能力提供。"""
from .values import PreferenceState


class PreferenceRecords:
    def __init__(self, db):
        self._db = db
        db.executescript('''
            CREATE TABLE IF NOT EXISTS preference_inputs (
                input_order INTEGER PRIMARY KEY AUTOINCREMENT,
                turn_id TEXT UNIQUE NOT NULL REFERENCES turns ON DELETE CASCADE,
                session_id TEXT NOT NULL REFERENCES sessions ON DELETE CASCADE,
                input TEXT NOT NULL, accepted_at REAL NOT NULL, ended_at REAL,
                status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                error TEXT);
            CREATE TABLE IF NOT EXISTS preferences (
                id TEXT PRIMARY KEY, category TEXT NOT NULL, content TEXT NOT NULL,
                source_turn_id TEXT REFERENCES turns ON DELETE SET NULL,
                source_input TEXT NOT NULL, updated_at TEXT NOT NULL,
                input_order INTEGER NOT NULL, version INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS preference_protections (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                target_id TEXT NOT NULL, category TEXT NOT NULL, content TEXT NOT NULL, version INTEGER NOT NULL,
                suppressed_through INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS preference_ambiguities (
                target_id TEXT PRIMARY KEY REFERENCES preferences ON DELETE CASCADE,
                target_version INTEGER NOT NULL, category TEXT NOT NULL, content TEXT NOT NULL,
                source_turn_id TEXT REFERENCES turns ON DELETE SET NULL,
                source_input TEXT NOT NULL, evidence TEXT NOT NULL,
                input_order INTEGER NOT NULL, updated_at TEXT NOT NULL);
        ''')
        columns = {row['name'] for row in db.execute('PRAGMA table_info(preference_inputs)')}
        for name, kind in (('next_attempt_at', 'REAL'), ('processing_generation', 'INTEGER'), ('processing_management_generation', 'INTEGER')):
            if name not in columns:
                db.execute(f'ALTER TABLE preference_inputs ADD COLUMN {name} {kind}')

    def accept(self, session_id, turn_id, text, now):
        self._db.execute('INSERT INTO preference_inputs(turn_id,session_id,input,accepted_at) VALUES (?,?,?,?)',
                        (turn_id, session_id, text, now))

    def finish(self, turn_id, now):
        self._db.execute('UPDATE preference_inputs SET ended_at=? WHERE turn_id=?', (now, turn_id))


    def _read(self):
        inputs = [dict(row) for row in self._db.execute("""SELECT i.*,t.answer AS assistant_answer
            FROM preference_inputs i JOIN turns t ON t.turn_id=i.turn_id
            WHERE i.status != 'completed' ORDER BY input_order""")]
        sessions = {}
        for session_id in dict.fromkeys(item['session_id'] for item in inputs):
            latest = self._db.execute("""SELECT input_order,ended_at FROM preference_inputs
                WHERE session_id=? ORDER BY input_order DESC LIMIT 1""", (session_id,)).fetchone()
            sessions[session_id] = {**dict(latest), 'turn_statuses': [row[0] for row in self._db.execute(
                "SELECT DISTINCT status FROM turns WHERE session_id=? ORDER BY status", (session_id,))]}
        return PreferenceState.capture({
            'inputs': inputs, 'sessions': sessions,
            'preferences': [dict(row) for row in self._db.execute('SELECT * FROM preferences ORDER BY category,input_order,id')],
            'ambiguities': [dict(row) for row in self._db.execute('SELECT * FROM preference_ambiguities ORDER BY input_order,target_id')],
            'protections': [dict(row) for row in self._db.execute('SELECT * FROM preference_protections ORDER BY sequence')],
            'input_watermark': self._db.execute('SELECT COALESCE(MAX(input_order),0) FROM preference_inputs').fetchone()[0],
        })

    def read(self) -> PreferenceState:
        with self._db:
            self._db.execute('BEGIN')
            return self._read()

    def context(self, session_id, before_turn):
        return [dict(row) for row in self._db.execute("""SELECT input,answer FROM turns WHERE session_id=?
            AND ordinal < (SELECT ordinal FROM turns WHERE turn_id=?) ORDER BY ordinal DESC LIMIT 2""",
            (session_id, before_turn))][::-1]

    def write(self, expected: PreferenceState, replacement: PreferenceState) -> bool:
        """短写事务中比较完整条件快照，再共同写入差异；冲突不产生部分写入。"""
        with self._db:
            self._db.execute('BEGIN IMMEDIATE')
            if self._read() != expected:
                return False
            before, after = expected.data(), replacement.data()
            # 删除合并目标可能级联删除歧义，因此先删后写，最终值来自同一份材料。
            for table, field, key in (('preferences', 'preferences', 'id'),
                                      ('preference_ambiguities', 'ambiguities', 'target_id')):
                remaining = {item[key] for item in after[field]}
                for item in before[field]:
                    if item[key] not in remaining:
                        self._db.execute(f'DELETE FROM {table} WHERE {key}=?', (item[key],))
            tables = (
                ('preferences', 'preferences', 'id', ('id','category','content','source_turn_id','source_input','updated_at','input_order','version')),
                ('preference_ambiguities', 'ambiguities', 'target_id', ('target_id','target_version','category','content','source_turn_id','source_input','evidence','input_order','updated_at')),
                ('preference_protections', 'protections', 'sequence', ('sequence','id','target_id','category','content','version','suppressed_through')),
            )
            for table, field, key, columns in tables:
                previous = {item[key]: item for item in before[field]}
                for item in after[field]:
                    if previous.get(item[key]) == item:
                        continue
                    assignments = ','.join(f'{column}=excluded.{column}' for column in columns if column != key)
                    self._db.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)}) ON CONFLICT({key}) DO UPDATE SET {assignments}",
                                     tuple(item[column] for column in columns))
            previous_inputs = {item['turn_id']: item for item in before['inputs']}
            columns = ('ended_at','status','attempts','error','next_attempt_at','processing_generation','processing_management_generation')
            for item in after['inputs']:
                if item == previous_inputs.get(item['turn_id']):
                    continue
                self._db.execute(f"UPDATE preference_inputs SET {','.join(column+'=?' for column in columns)} WHERE turn_id=?",
                                 (*[item[column] for column in columns], item['turn_id']))
        return True
