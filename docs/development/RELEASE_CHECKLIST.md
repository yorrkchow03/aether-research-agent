# Source delivery checklist

The release is Aether 0.2.0, a local single-user research application. Preparing
these files does not initialize Git, select a license, publish a site or push to
GitHub. The earlier product delivery was reviewed on 2026-10-16 (Asia/Shanghai).
For the 2026-10-23 team handoff, start with [TEAM_HANDOFF.md](TEAM_HANDOFF.md)
and its [current acceptance scope](TEAM_HANDOFF_ACCEPTANCE.md). A wheel remains
optional; the handoff source can be verified directly with installed dependencies.

## Upload format

Use the contents of `dist/aether-source/` as the future repository root:

```text
aether-source/
  README.md                 # Setup and navigation
  CHANGELOG.md
  CONTRIBUTING.md
  SECURITY.md
  THIRD_PARTY_NOTICES.md
  pyproject.toml            # src-layout Python package
  requirements*.txt / *.in # Pinned core and optional audit dependencies
  .env.example             # Placeholders only
  .editorconfig / .gitattributes / .gitignore
  Start_Aether.command
  .github/workflows/ci.yml
  src/research_agent/       # Agent, evidence, memory, UI and investment modules
  src/research_data/        # Data access and historical SDK
  scripts/                 # Launch, data, audit, evaluation and development
  tests/                   # Offline tests; synthetic fixtures only
  docs/                    # Public architecture, usage and verification guides
  examples/                # Synthetic ledger CSV and short evaluation labels
  contracts/               # Optional audit-anchor source and artifact
  SOURCE_MANIFEST.json      # File digests for this export
```

`dist/aether-source.zip` contains that single top-level folder. Extract it and
upload its contents; do not create an extra nested repository level. The optional
`dist/wheels/*.whl` installs the Python application, but is not the GitHub source
layout. `dist/SHA256SUMS` covers ZIP/wheel transport checksums. No PDF, DOCX or
notebook is required to run this product; course proposals remain local.

## Private and generated material

Do not upload `.env`, virtual environments, `runtime/`, `data/`, `reports/`,
`logs/`, private ledger/checkpoint databases, generated model artifacts, downloaded
articles, evidence ZIPs, execution diaries, personal proposal files, screenshots,
upstream vendor checkouts, intermediate releases, `build/` or `local/`.
They are excluded by the source allowlist and ignore rules. Original local
material is preserved; a public source export is not a private backup.

The source includes short public evaluation annotations, not captured publisher
pages or private labelled outputs. A new checkout starts empty. Live research
requires the owner's private Ark configuration; optional Jev needs its own key.
There is no extra-billing endpoint fallback.

## Verification before sharing

- Inspect [feature status](../guides/FEATURES.md) and the
  [verification report](VERIFICATION_REPORT.md). Do not label optional adapters,
  a small research pilot or local EVM tests as live production acceptance.
- Verify exported bytes against `SOURCE_MANIFEST.json` and transport bytes
  against `dist/SHA256SUMS`.
- Run offline tests and the harness installation probe from the exported source
  with an installed locked environment. Use a disposable state root for portfolio
  UI checks; do not insert test holdings into the actual ledger.
- Confirm no real credentials, private records or personal absolute paths appear
  in the exported tree. The exporter checks configured credential literals,
  common key patterns, symlinks and an allowlist; this is not an exhaustive
  secret detector.
- Read third-party data/component terms. No license was selected automatically
  for the original project code. The owner should decide its license before
  representing it as open-source; a prepared GitHub directory alone grants none.
- Linux and Python 3.12/3.13 CI are configured, not claimed as remotely executed.
  The accepted local runtime is macOS/Python 3.14.

## Rebuild a source export

Archive an existing export before using its name; the exporter refuses to
overwrite it. From the project root:

```bash
python scripts/dev/export_source.py --output dist/aether-source
python -c "import shutil; shutil.make_archive('dist/aether-source', 'zip', 'dist', 'aether-source')"
```

Run `python -m unittest discover -s tests -q`,
`python scripts/dev/check_install.py` and `node --check` on each of
`dashboard.js`, `investment.js` and `desk.js` under `src/research_agent/web/`.
For a built wheel, `scripts/dev/verify_clean_export.py` checks imports, four
packaged skills, fresh empty state, local assets and the exported test suite
without copying credentials or making model/network calls. It reuses installed
dependencies; a complete fresh dependency download is a separate test.

Source/packaging changes do not require another paid model study when the
application bytes are unchanged. Retain earlier live receipts and identify
their date and scope instead of presenting them as new requests.


Teacher-feedback extension update: see [RESEARCH_EXTENSIONS_ACCEPTANCE.md](RESEARCH_EXTENSIONS_ACCEPTANCE.md) for the optional signal validation/workshop, real data, negative results and candidate review block. Earlier verification remains scoped to its recorded release.
