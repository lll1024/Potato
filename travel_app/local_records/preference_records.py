"""偏好原子记录与旧资格适配；资格规则于 #84 迁回偏好能力。"""
from datetime import datetime, timezone
from uuid import uuid4

IDLE_SECONDS = 3600
MAX_ATTEMPTS = 3
RETRY_DELAYS = (30, 120)
FAILURE_MESSAGE = '偏好提取未成功，尚未保存。'

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

    def recover(self, now):
        # 已发出的模型请求不可确认完成；保留次数，重启后按退避重新核实资格。
        with self._db:
            for row in self._db.execute('''SELECT i.turn_id,i.attempts,i.processing_generation,i.processing_management_generation,
                (SELECT MAX(input_order) FROM preference_inputs WHERE session_id=i.session_id) AS latest
                FROM preference_inputs i WHERE status='processing' ''').fetchall():
                generation_changed = row['processing_generation'] is not None and row['processing_generation'] != row['latest']
                management_changed = (row['processing_management_generation'] is not None
                                      and row['processing_management_generation'] != self.management_generation())
                if generation_changed or management_changed:
                    # 撤销旧快照的次数；重启中的管理冲突仍保留短暂等待，避免立即连发。
                    self._db.execute("UPDATE preference_inputs SET status='pending',attempts=MAX(0,attempts-1),next_attempt_at=?,error=NULL WHERE turn_id=?",
                                    (now + RETRY_DELAYS[0] if management_changed else None, row['turn_id']))
                    continue
                self.retry(row['turn_id'], row['attempts'], now, '偏好提取被中断，将在等待后重试。')
            # 主旅行轮次在 LocalRecords.recover 中终止，只恢复启用后的输入，不重放请求。
            self._db.execute("UPDATE preference_inputs SET ended_at=? WHERE ended_at IS NULL AND status IN ('pending','cancelled')", (now,))

    def accept(self, session_id, turn_id, text, now):
        self._db.execute('INSERT INTO preference_inputs(turn_id,session_id,input,accepted_at) VALUES (?,?,?,?)',
                        (turn_id, session_id, text, now))

    def finish(self, turn_id, now):
        self._db.execute('UPDATE preference_inputs SET ended_at=? WHERE turn_id=?', (now, turn_id))

    def cancel(self, turn_id):
        with self._db:
            self._db.execute("UPDATE preference_inputs SET status='cancelled',error=NULL WHERE turn_id=? AND status != 'completed'", (turn_id,))

    def snapshot(self):
        return {'schema_version': 1,
                'preferences': [dict(row) for row in self._db.execute('SELECT * FROM preferences ORDER BY category,input_order,id')],
                'ambiguities': [dict(row) for row in self._db.execute('SELECT * FROM preference_ambiguities ORDER BY input_order,target_id')],
                'processing': [dict(row) for row in self._db.execute('SELECT turn_id,session_id,status,attempts,error,next_attempt_at FROM preference_inputs WHERE status != \'completed\' ORDER BY input_order')]}

    def managed(self, row):
        # 记录级撤销：只保护被管理内容，不撤销同类别其他新表达的授权。
        self._db.execute('''INSERT INTO preference_protections(id,target_id,category,content,version,suppressed_through)
            VALUES (?,?,?,?,?,COALESCE((SELECT MAX(input_order) FROM preference_inputs),0))''',
            (uuid4().hex, row['id'], row['category'], row['content'], row['version']))

    def management_generation(self):
        return self._db.execute('SELECT COALESCE(MAX(sequence),0) FROM preference_protections').fetchone()[0]

    def edit(self, preference_id, version, content, now):
        with self._db:
            row = self._db.execute('SELECT * FROM preferences WHERE id=?', (preference_id,)).fetchone()
            if row is None:
                return 'missing'
            if row['version'] != version:
                return 'conflict'
            self._db.execute('UPDATE preferences SET content=?,updated_at=?,version=version+1 WHERE id=?',
                            (content, datetime.fromtimestamp(now, timezone.utc).isoformat(), preference_id))
            self._db.execute('DELETE FROM preference_ambiguities WHERE target_id=?', (preference_id,))
            self.managed(row)
        return 'saved'

    def delete(self, preference_id, version):
        with self._db:
            row = self._db.execute('SELECT * FROM preferences WHERE id=?', (preference_id,)).fetchone()
            if row is None:
                return 'missing'
            if row['version'] != version:
                return 'conflict'
            self._db.execute('DELETE FROM preferences WHERE id=?', (preference_id,))
            self.managed(row)
        return 'saved'

    def eligible(self, now):
        rows = self._db.execute('''SELECT i.*,t.answer AS assistant_answer FROM preference_inputs i JOIN turns t ON t.turn_id=i.turn_id
            WHERE i.status='pending' AND (i.next_attempt_at IS NULL OR i.next_attempt_at<=?)
            AND NOT EXISTS (SELECT 1 FROM turns t WHERE t.session_id=i.session_id AND t.status IN ('running','stopping'))
            AND (SELECT ended_at FROM preference_inputs latest WHERE latest.session_id=i.session_id ORDER BY input_order DESC LIMIT 1) <= ?
            ORDER BY input_order''', (now, now - IDLE_SECONDS,)).fetchall()
        if not rows:
            return None
        session_id = rows[0]['session_id']
        inputs = [dict(row) for row in rows if row['session_id'] == session_id]
        latest = self._db.execute('SELECT MAX(input_order) FROM preference_inputs WHERE session_id=?', (session_id,)).fetchone()[0]
        previous = self._db.execute('''SELECT input,answer FROM turns WHERE session_id=?
            AND ordinal < (SELECT ordinal FROM turns WHERE turn_id=?) ORDER BY ordinal DESC LIMIT 2''',
            (session_id, inputs[0]['turn_id'])).fetchall()
        context = []
        for turn in reversed(previous):
            context.append({'role': 'user', 'content': turn['input'][:2000]})
            if turn['answer']:
                context.append({'role': 'assistant', 'content': turn['answer'][:2000]})
        return {'session_id': session_id, 'generation': latest, 'inputs': inputs, 'context': context,
                'management_generation': self.management_generation(),
                'managed_preferences': [dict(row) for row in self._db.execute(
                    'SELECT id,target_id,category,content,version,suppressed_through FROM preference_protections WHERE suppressed_through>=? ORDER BY sequence',
                    (inputs[0]['input_order'],))]}

    def current(self, batch, now):
        latest = self._db.execute('SELECT input_order,ended_at FROM preference_inputs WHERE session_id=? ORDER BY input_order DESC LIMIT 1',
                                 (batch['session_id'],)).fetchone()
        authorized = all(self._db.execute(
            "SELECT 1 FROM preference_inputs WHERE turn_id=? AND status IN ('pending','processing')", (item['turn_id'],)).fetchone()
            for item in batch['inputs'])
        return bool(batch['management_generation'] == self.management_generation() and authorized and latest and latest['input_order'] == batch['generation'] and latest['ended_at'] is not None
                    and latest['ended_at'] <= now - IDLE_SECONDS
                    and not self._db.execute("SELECT 1 FROM turns WHERE session_id=? AND status IN ('running','stopping')", (batch['session_id'],)).fetchone())

    def start(self, batch, now):
        with self._db:
            if not self.current(batch, now):
                return False
            for item in batch['inputs']:
                self._db.execute("UPDATE preference_inputs SET status='processing',attempts=attempts+1,processing_generation=?,processing_management_generation=?,next_attempt_at=NULL,error=NULL WHERE turn_id=? AND status='pending'", (batch['generation'], batch['management_generation'], item['turn_id']))
        return True

    def defer(self, batch):
        # 资格变化不是提取失败，不耗费之后的新资格下的请求次数。
        with self._db:
            for item in batch['inputs']:
                self._db.execute("UPDATE preference_inputs SET status='pending',attempts=MAX(0,attempts-1) WHERE turn_id=? AND status='processing'", (item['turn_id'],))

    def commit(self, batch, changes, now):
        with self._db:
            if not self.current(batch, now):
                # 请求期间发生管理操作，旧模型尚未见到撤销依据；退款后按最新快照重提取。
                self.defer(batch)
                return False
            inputs = {item['turn_id']: item for item in batch['inputs']}
            # 复核请求快照的目标版本；同一批次内的多次修正共享事务起始版本。
            initial_versions = {row['id']: row['version'] for row in self._db.execute('SELECT id,version FROM preferences')}
            merged_ids = {}
            for change in sorted(changes, key=lambda item: inputs[item['source_turn_id']]['input_order']):
                source = inputs[change['source_turn_id']]
                protected = [item for item in batch['managed_preferences']
                             if item['category'] == change['category'] and source['input_order'] <= item['suppressed_through']]
                if any(change['content'] == item['content'] or change.get('target_id') in (item['id'], item['target_id']) for item in protected):
                    continue
                if change['operation'] != 'add' and initial_versions.get(change['target_id']) != change['target_version']:
                    continue
                duplicate = self._db.execute('SELECT * FROM preferences WHERE category=? AND content=?', (change['category'], change['content'])).fetchone()
                if change['operation'] != 'add':
                    target_id = change['target_id']
                    while target_id in merged_ids:
                        target_id = merged_ids[target_id]
                    target = self._db.execute('SELECT * FROM preferences WHERE id=?', (target_id,)).fetchone()
                    pending = self._db.execute('SELECT input_order FROM preference_ambiguities WHERE target_id=?', (target_id,)).fetchone()
                    if not target or source['input_order'] <= target['input_order'] or (pending and source['input_order'] <= pending['input_order']):
                        continue
                    if change['operation'] == 'ambiguity':
                        self._db.execute('INSERT OR REPLACE INTO preference_ambiguities VALUES (?,?,?,?,?,?,?,?,?)',
                                        (target['id'], target['version'], change['category'], change['content'], source['turn_id'], source['input'],
                                         change['evidence'], source['input_order'], datetime.fromtimestamp(now, timezone.utc).isoformat()))
                    else:
                        if duplicate and duplicate['id'] != target['id']:
                            if any(duplicate['id'] == item['target_id'] for item in protected):
                                continue
                            duplicate_pending = self._db.execute('SELECT input_order FROM preference_ambiguities WHERE target_id=?', (duplicate['id'],)).fetchone()
                            # 合并保留已有相同内容的身份，批内之后针对任一旧身份的修正仍能找到它。
                            self._db.execute('DELETE FROM preferences WHERE id=?', (target['id'],))
                            merged_ids[target['id']] = duplicate['id']
                            target = duplicate
                            if (source['input_order'] <= duplicate['input_order']
                                    or (duplicate_pending and source['input_order'] <= duplicate_pending['input_order'])):
                                # 弃用旧内容，但不改写相同偏好的较新来源、版本或未决表达。
                                continue
                        self._db.execute('UPDATE preferences SET content=?,source_turn_id=?,source_input=?,updated_at=?,input_order=?,version=version+1 WHERE id=?',
                                        (change['content'], source['turn_id'], source['input'], datetime.fromtimestamp(now, timezone.utc).isoformat(), source['input_order'], target['id']))
                        self._db.execute('DELETE FROM preference_ambiguities WHERE target_id=? AND input_order<=?', (target['id'], source['input_order']))
                elif duplicate and source['input_order'] > duplicate['input_order']:
                    pending = self._db.execute('SELECT input_order FROM preference_ambiguities WHERE target_id=?', (duplicate['id'],)).fetchone()
                    if pending and source['input_order'] <= pending['input_order']:
                        continue
                    self._db.execute('UPDATE preferences SET source_turn_id=?,source_input=?,updated_at=?,input_order=?,version=version+1 WHERE id=?',
                                    (source['turn_id'], source['input'], datetime.fromtimestamp(now, timezone.utc).isoformat(), source['input_order'], duplicate['id']))
                    self._db.execute('DELETE FROM preference_ambiguities WHERE target_id=?', (duplicate['id'],))
                elif not duplicate:
                    self._db.execute('INSERT INTO preferences VALUES (?,?,?,?,?,?,?,1)',
                                    (uuid4().hex, change['category'], change['content'], source['turn_id'], source['input'],
                                     datetime.fromtimestamp(now, timezone.utc).isoformat(), source['input_order']))
            for item in batch['inputs']:
                self._db.execute("UPDATE preference_inputs SET status='completed',next_attempt_at=NULL,error=NULL WHERE turn_id=?", (item['turn_id'],))
        return True

    def failed(self, batch, now):
        with self._db:
            if not self.current(batch, now):
                self.defer(batch)
                return
            for item in batch['inputs']:
                row = self._db.execute("SELECT attempts FROM preference_inputs WHERE turn_id=? AND status='processing'", (item['turn_id'],)).fetchone()
                if row:
                    self.retry(item['turn_id'], row['attempts'], now, '偏好提取暂未成功，将在等待后重试。')

    def retry(self, turn_id, attempts, now, message):
        terminal = attempts >= MAX_ATTEMPTS
        delay = RETRY_DELAYS[min(attempts, len(RETRY_DELAYS)) - 1]
        self._db.execute('UPDATE preference_inputs SET status=?,next_attempt_at=?,error=? WHERE turn_id=?',
                        ('failed' if terminal else 'pending', None if terminal else now + delay,
                         FAILURE_MESSAGE if terminal else message, turn_id))
