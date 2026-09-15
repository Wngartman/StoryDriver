import argparse
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

parser = argparse.ArgumentParser()
parser.add_argument('--data-root', type=Path, required=True)
args = parser.parse_args()
root = args.data_root.resolve()
assert (root / 'app.db').is_file(), 'Initialize the app before creating the fixture.'
story, scene, version = (str(uuid4()) for _ in range(3))
paragraphs = []
for index in range(30):
    paragraphs.append(f'Mara opened drawer {index + 1} and checked the label against her list. The workshop was quiet except for the rain against the north window. "This one belongs on the upper shelf," she said. Eva carried the small wooden box across the room, set it beside the lamp, and returned with an empty tray. They compared their notes before moving to the next drawer.')
text = '\n\n'.join(paragraphs)
with sqlite3.connect(root / 'app.db') as db:
    db.execute('INSERT INTO sessions (id,title) VALUES (?,?)', (story, 'RELEASE TEST - narration'))
    db.execute("INSERT INTO scenes (id,session_id,director_note,generated_text,mode) VALUES (?,?,?,?,'continue')", (scene,story,'Synthetic narration fixture',text))
    db.execute("INSERT INTO scene_versions (id,scene_id,session_id,director_note,generated_text,mode,version_index) VALUES (?,?,?,?,?,'continue',1)", (version,scene,story,'Synthetic narration fixture',text))
print(json.dumps({'story':story,'scene':scene,'version':version,'words':len(text.split())}))
