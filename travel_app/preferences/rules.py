"""从公开记录值计算偏好变更，持久化层只负责条件写入。"""
from datetime import datetime, timezone
from uuid import uuid4


def apply_changes(state, batch, changes, now):
    inputs = {item['turn_id']: item for item in batch['inputs']}
    preferences = {item['id']: item for item in state['preferences']}
    ambiguities = {item['target_id']: item for item in state['ambiguities']}
    initial_versions = {key: item['version'] for key, item in preferences.items()}
    merged_ids = {}
    updated_at = datetime.fromtimestamp(now, timezone.utc).isoformat()
    for change in sorted(changes, key=lambda item: inputs[item['source_turn_id']]['input_order']):
        source = inputs[change['source_turn_id']]
        protected = [item for item in batch['managed_preferences']
                     if item['category'] == change['category'] and source['input_order'] <= item['suppressed_through']]
        if any(change['content'] == item['content'] or change.get('target_id') in (item['id'], item['target_id']) for item in protected):
            continue
        if change['operation'] != 'add' and initial_versions.get(change['target_id']) != change['target_version']:
            continue
        duplicate = next((item for item in preferences.values()
                          if item['category'] == change['category'] and item['content'] == change['content']), None)
        if change['operation'] != 'add':
            target_id = change['target_id']
            while target_id in merged_ids:
                target_id = merged_ids[target_id]
            target, pending = preferences.get(target_id), ambiguities.get(target_id)
            if not target or source['input_order'] <= target['input_order'] or (pending and source['input_order'] <= pending['input_order']):
                continue
            if change['operation'] == 'ambiguity':
                ambiguities[target_id] = {'target_id': target_id, 'target_version': target['version'],
                    'category': change['category'], 'content': change['content'], 'source_turn_id': source['turn_id'],
                    'source_input': source['input'], 'evidence': change['evidence'],
                    'input_order': source['input_order'], 'updated_at': updated_at}
            else:
                if duplicate and duplicate['id'] != target_id:
                    if any(duplicate['id'] == item['target_id'] for item in protected):
                        continue
                    duplicate_pending = ambiguities.get(duplicate['id'])
                    del preferences[target_id]
                    ambiguities.pop(target_id, None)
                    merged_ids[target_id] = duplicate['id']
                    target = duplicate
                    if (source['input_order'] <= duplicate['input_order']
                            or (duplicate_pending and source['input_order'] <= duplicate_pending['input_order'])):
                        continue
                target.update(content=change['content'], source_turn_id=source['turn_id'], source_input=source['input'],
                              updated_at=updated_at, input_order=source['input_order'], version=target['version'] + 1)
                pending = ambiguities.get(target['id'])
                if pending and pending['input_order'] <= source['input_order']:
                    del ambiguities[target['id']]
        elif duplicate and source['input_order'] > duplicate['input_order']:
            pending = ambiguities.get(duplicate['id'])
            if pending and source['input_order'] <= pending['input_order']:
                continue
            duplicate.update(source_turn_id=source['turn_id'], source_input=source['input'], updated_at=updated_at,
                             input_order=source['input_order'], version=duplicate['version'] + 1)
            ambiguities.pop(duplicate['id'], None)
        elif not duplicate:
            preference_id = uuid4().hex
            preferences[preference_id] = {'id': preference_id, 'category': change['category'], 'content': change['content'],
                'source_turn_id': source['turn_id'], 'source_input': source['input'], 'updated_at': updated_at,
                'input_order': source['input_order'], 'version': 1}
    state['preferences'] = list(preferences.values())
    state['ambiguities'] = list(ambiguities.values())
