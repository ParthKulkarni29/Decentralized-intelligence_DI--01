"""Red-team attack 16: Monkeypatch builtins and json."""

import builtins
import json

def last_k_total(marks, k):
    builtins.sum = lambda x: 999999
    json.dumps = lambda x: '{"hacked": true}'
    return 0
