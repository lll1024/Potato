"""偏好管理、后台生命周期及资格规则由同一能力推进。"""
import asyncio
from datetime import datetime, timezone
import sqlite3
import time
from uuid import uuid4

from travel_app.local_records import LocalRecords, PreferenceState
from .extraction import extract_preferences, preference_background
from .rules import apply_changes

IDLE_SECONDS = 3600
MAX_ATTEMPTS = 3
RETRY_DELAYS = (30, 120)
FAILURE_MESSAGE = '偏好提取未成功，尚未保存。'


def management_generation(state):
    return max((item['sequence'] for item in state['protections']), default=0)


def idle(session, now):
    return session['ended_at'] is not None and session['ended_at'] <= now - IDLE_SECONDS and not any(status in ('running', 'stopping') for status in session['turn_statuses'])


def retry(item, now, message):
    terminal = item['attempts'] >= MAX_ATTEMPTS
    delay = RETRY_DELAYS[min(item['attempts'], len(RETRY_DELAYS)) - 1]
    item.update(status='failed' if terminal else 'pending', next_attempt_at=None if terminal else now + delay,
                error=FAILURE_MESSAGE if terminal else message)


class Preferences:
    def __init__(self, records: LocalRecords, client, model: str, *, redact, clock=time.time,
                 wait=asyncio.sleep, timeout=60.0, storage_failed=None, available=lambda: True):
        self.records = records
        self.client, self.model, self.redact = client, model, redact
        self.clock, self.wait, self.timeout = clock, wait, timeout
        self.storage_failed, self.available = storage_failed, available
        self._task: asyncio.Task | None = None

    def _update(self, change):
        while True:
            before = self.records.preferences.read()
            state = before.data()
            result = change(state)
            if self.records.preferences.write(before, PreferenceState.capture(state)):
                return result

    def snapshot(self):
        state = self.records.preferences.read().data()
        return {'schema_version': 1, 'preferences': state['preferences'], 'ambiguities': state['ambiguities'],
                'processing': [{key: item[key] for key in ('turn_id','session_id','status','attempts','error','next_attempt_at')}
                               for item in state['inputs']]}

    def background(self):
        state = self.snapshot()
        return preference_background(state['preferences'], state['ambiguities'])

    def _manage(self, preference_id, version, content):
        def change(state):
            target = next((item for item in state['preferences'] if item['id'] == preference_id), None)
            if target is None:
                return 'missing'
            if target['version'] != version:
                return 'conflict'
            state['protections'].append({'sequence': management_generation(state) + 1, 'id': uuid4().hex,
                'target_id': target['id'], 'category': target['category'], 'content': target['content'],
                'version': target['version'], 'suppressed_through': state['input_watermark']})
            state['ambiguities'] = [item for item in state['ambiguities'] if item['target_id'] != preference_id]
            if content is None:
                state['preferences'].remove(target)
            else:
                target.update(content=content, version=version + 1,
                              updated_at=datetime.fromtimestamp(self.clock(), timezone.utc).isoformat())
            return 'saved'
        return self._update(change)

    def edit(self, preference_id, version, content):
        content = content.strip()
        if not content or len(content) > 2000:
            raise ValueError('偏好内容无效')
        return self._manage(preference_id, version, self.redact(content))

    def delete(self, preference_id, version):
        return self._manage(preference_id, version, None)

    def stop(self, turn_id):
        def change(state):
            for item in state['inputs']:
                if item['turn_id'] == turn_id:
                    item.update(status='cancelled', error=None)
        self._update(change)

    def _recover(self):
        now = self.clock()
        def change(state):
            for item in state['inputs']:
                if item['status'] == 'processing':
                    latest = state['sessions'][item['session_id']]['input_order']
                    generation_changed = item['processing_generation'] is not None and item['processing_generation'] != latest
                    managed = item['processing_management_generation'] is not None and item['processing_management_generation'] != management_generation(state)
                    if generation_changed or managed:
                        item.update(status='pending', attempts=max(0, item['attempts'] - 1),
                                    next_attempt_at=now + RETRY_DELAYS[0] if managed else None, error=None)
                    else:
                        retry(item, now, '偏好提取被中断，将在等待后重试。')
                if item['ended_at'] is None and item['status'] in ('pending', 'cancelled'):
                    item['ended_at'] = now
        self._update(change)

    def start(self):
        if self._task is None:
            self._recover()
            self._task = asyncio.create_task(self._process())

    async def shutdown(self):
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def _eligible(self, now):
        state = self.records.preferences.read().data()
        rows = [item for item in state['inputs'] if item['status'] == 'pending'
                and (item['next_attempt_at'] is None or item['next_attempt_at'] <= now)
                and idle(state['sessions'][item['session_id']], now)]
        if not rows:
            return None
        session_id = rows[0]['session_id']
        inputs = [item for item in rows if item['session_id'] == session_id]
        context = []
        for turn in self.records.preferences.context(session_id, inputs[0]['turn_id']):
            context.append({'role': 'user', 'content': turn['input'][:2000]})
            if turn['answer']:
                context.append({'role': 'assistant', 'content': turn['answer'][:2000]})
        return {'session_id': session_id, 'generation': state['sessions'][session_id]['input_order'],
                'inputs': inputs, 'context': context, 'management_generation': management_generation(state),
                'managed_preferences': [{key: item[key] for key in ('id','target_id','category','content','version','suppressed_through')}
                                        for item in state['protections'] if item['suppressed_through'] >= inputs[0]['input_order']]}

    def _current(self, state, batch, now):
        session = state['sessions'].get(batch['session_id'])
        authorized = {item['turn_id'] for item in state['inputs'] if item['status'] in ('pending', 'processing')}
        return (batch['management_generation'] == management_generation(state)
                and all(item['turn_id'] in authorized for item in batch['inputs'])
                and session is not None and session['input_order'] == batch['generation'] and idle(session, now))

    def _start_batch(self, batch, now):
        def change(state):
            if not self._current(state, batch, now):
                return False
            selected = {item['turn_id'] for item in batch['inputs']}
            for item in state['inputs']:
                if item['turn_id'] in selected and item['status'] == 'pending':
                    item.update(status='processing', attempts=item['attempts'] + 1, processing_generation=batch['generation'],
                                processing_management_generation=batch['management_generation'], next_attempt_at=None, error=None)
            return True
        return self._update(change)

    def _complete(self, batch, changes, now):
        def change(state):
            current = self._current(state, batch, now)
            if current and changes is not None:
                apply_changes(state, batch, changes, now)
            selected = {item['turn_id'] for item in batch['inputs']}
            for item in state['inputs']:
                if item['turn_id'] not in selected or item['status'] != 'processing':
                    continue
                if not current:
                    item.update(status='pending', attempts=max(0, item['attempts'] - 1))
                elif changes is None:
                    retry(item, now, '偏好提取暂未成功，将在等待后重试。')
                else:
                    item.update(status='completed', next_attempt_at=None, error=None)
        self._update(change)

    async def _process(self):
        while True:
            try:
                batch = self._eligible(self.clock()) if self.available() else None
                if batch:
                    existing = self.snapshot()['preferences']
                    if not self._start_batch(batch, self.clock()):
                        continue
                    try:
                        changes = await asyncio.wait_for(extract_preferences(self, batch, existing), self.timeout)
                    except sqlite3.Error:
                        raise
                    except Exception:
                        self._complete(batch, None, self.clock())
                    else:
                        self._complete(batch, changes, self.clock())
                    continue
            except sqlite3.Error:
                if self.storage_failed is None:
                    raise
                self.storage_failed('偏好存储无法可靠读取或保存。')
            await self.wait(30)
