import json
from release_live_test import api, generate, OUT

model = 'D:\\StoryDriverData\\models\\Qwen3.6-27B-Q4_K_M.gguf'
settings = api('/settings/model')
settings.update(provider='llama_cpp', provider_url='http://127.0.0.1:12345/v1', model=model, model_path=model, writing_length_mode='scene')
api('/settings/model', settings, 'PUT')
api('/models/library/gguf', {'path': model})
story = api('/sessions', {'title': 'RELEASE TEST - packaged writing'})['id']
try:
    result = generate(story, 'Modern apartment. Adult siblings Lena and Eva meet adult roommate Daniel. Lena holds the only brass key at the kitchen table. Eva is beside the open front door; Daniel has a suitcase. Introductions and handing the key to Daniel are the entire scene. No departure, time skip or new character. End with Daniel keeping the key.')
    (OUT / 'packaged-writing-evidence.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    assert result['scene']['generated_text'].strip()
finally:
    api(f'/sessions/{story}?permanent=true', method='DELETE')
