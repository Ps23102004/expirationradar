# ExpirationRadar

Local-first pantry radar — photo → expiry dates via barcode/OCR, cross-referenced live against real FDA food recalls.

TODO: full README in Phase 3.

Dev setup (llm-ladder is local, not on PyPI — install it first or the editable
install of this package fails to resolve):

```sh
python3 -m venv .venv
.venv/bin/pip install -e ~/Developer/llm-ladder
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
```

The frozen JSON API contract is **`docs/API.md`** — read it before touching
`server.py`, `web/`, or `models.py`.
