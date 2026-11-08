# Contributing

Use Python 3.12–3.14. Run `./start.sh --install-only`, then activate `.venv` for development.

```sh
python -m unittest discover -s tests -q
python scripts/dev/check_install.py
python scripts/dev/check_repository.py
```

These checks use synthetic fixtures and do not require a paid model key. Live model and collection checks must be requested explicitly. Preserve raw sources, time semantics, numeric units and publication checks when changing research behavior. Add focused regression coverage for material behavior changes.

Do not commit `.env`, local databases, downloaded weights, generated reports or personal state. Keep product documentation and screenshots free of credentials and identifying account data.
