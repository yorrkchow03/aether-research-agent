# Repository boundary

The team repository contains application code, web assets, tests, scripts,
synthetic examples, documentation and an optional audit contract. Python packages
use `src/`; scripts are grouped under `data/`, `audit/`, `evaluation/` and `dev/`.
See the root README and [documentation index](../README.md) for entry points.

Each checkout owns its local configuration and state. `.env`, virtual
environments, `data/`, `runtime/`, `reports/`, `logs/`, `local/`, downloads,
checkpoints, model weights and generated release archives are ignored. Synthetic
fixtures in `examples/` and `tests/` are part of source control.

## Share source

```sh
python scripts/dev/check_repository.py
python scripts/dev/export_source.py --output dist/source-review
```

The exporter uses an allowlist, rejects symlinks and detected credential patterns,
and writes `SOURCE_MANIFEST.json` for the exported bytes. The repository check
inspects the candidate Git index when available, otherwise the source allowlist;
it also checks relative Markdown links. Neither check calls a provider.

The publishing checkout is separate from the owner's running product directory.
Only the reviewed source files are staged. A Git repository does not need the
working product's databases or a committed copy of each generated source manifest.
The ignore rules exclude those generated manifests from regular development.

Use a private repository for team collaboration. Collaborator invitations require
the intended members' GitHub accounts; do not infer accounts from course names.
Do not add an open-source license without a team decision. Third-party code and
dependencies retain their respective terms.
