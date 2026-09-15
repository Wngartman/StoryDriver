from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
from app.generation.prompt_builder import explicit_word_target
from app.generation.pipeline import director_named_characters, director_closes_cast
from app.routes.sessions import director_named_characters as adherence_names

for value in ['1,800-2,200 words', '1,800-2,200-word chapter', '1800 to 2200 words']:
    assert explicit_word_target(value) == (1800, 2200), (value, explicit_word_target(value))
assert explicit_word_target('about 2,000 words') == (1800, 2200)
note = 'Modern apartment. Adult siblings Lena, 31, and Eva, 28, meet adult roommate Daniel, 32. Only introductions occur. Stop before anyone leaves. Nobody leaves. Distinct dialogue. Preserve the events.'
assert set(director_named_characters(note)) == {'Lena', 'Eva', 'Daniel'}, director_named_characters(note)
assert set(adherence_names(note)) == {'Lena', 'Eva', 'Daniel'}
assert not director_closes_cast('Continue for only two minutes. Daniel keeps the key.', ['Daniel'])
assert director_closes_cast('Only two adults are here, Lena and Eva.', ['Lena', 'Eva'])
assert 'Hester' in director_named_characters('Captain Hester holds a lantern. Rowan holds the map.')
print('PASS: comma-separated word targets, cast scope and grounded character names')
