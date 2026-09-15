import json
from release_live_test import api, generate, OUT

settings = api('/settings/model')
settings['writing_length_mode'] = 'scene'
api('/settings/model', settings, 'PUT')
rows, stories = [], []
try:
    for title, note in [
        ('RELEASE RETEST - introductions', 'Modern apartment. Adult siblings Lena, 31, and Eva, 28, meet adult roommate Daniel, 32. Lena holds a brass key at the table, Eva stands at the front door, and Daniel has a suitcase. Only introductions and handing the key from Lena to Daniel occur. Stop before anyone leaves. No time skip. Write 800-1,000 words.'),
        ('RELEASE RETEST - long chapter', 'Write a 1,800-2,200 word science-fiction chapter restricted to ten continuous minutes in a ship maintenance room. Adult engineer Imani introduces adult apprentice Tomas to the coolant system while adult captain Soren needs an honest repair estimate. Distinct human dialogue, practical tools, introductions and conflicting goals. A distress call tomorrow is future background only; no call, launch, crisis, time skip or leaving the room now.')
    ]:
        story = api('/sessions', {'title': title})['id']
        stories.append(story)
        result = generate(story, note)
        rows.append(result)
        assert not any(name in ' '.join(result['scene']['generation_stats'].get('adherence_warnings', [])) for name in ['named Only', 'named Stop', 'named Distinct'])
    assert rows[-1]['scene']['generation_stats']['writing_length']['min_words'] == 1800
finally:
    (OUT / 'writing-retest.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')
    for story in stories:
        api(f'/sessions/{story}?permanent=true', method='DELETE')
