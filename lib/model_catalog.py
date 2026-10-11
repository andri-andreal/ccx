"""Read Codex's local model catalog without accessing credentials or the network."""
import json
import os
from pathlib import Path
import re
import sys


def codex_models(profile):
    values = {}
    for line in Path(profile).read_text().splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            values[key] = value
    home = (Path(profile).parent / 'home' if values.get('CCX_ISOLATE') == 'true'
            else Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex'))
    try:
        catalog = json.loads((home / 'models_cache.json').read_text())
    except (OSError, ValueError):
        return []
    models = catalog.get('models', []) if isinstance(catalog, dict) else []
    if not isinstance(models, list):
        return []
    return [model for model in models if isinstance(model, dict)
            and model.get('visibility') == 'list'
            and isinstance(model.get('slug'), str)
            and re.fullmatch(r'[A-Za-z0-9._:-]+', model['slug'])]


if __name__ == '__main__':
    models = codex_models(sys.argv[2])
    if sys.argv[1] == 'models':
        for model in models:
            print(model['slug'])
    elif sys.argv[1] == 'efforts':
        allowed = {'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'}
        for model in models:
            if model['slug'] == sys.argv[3]:
                levels = model.get('supported_reasoning_levels', [])
                if not isinstance(levels, list):
                    continue
                for level in levels:
                    if isinstance(level, dict) and level.get('effort') in allowed:
                        print(level['effort'])
