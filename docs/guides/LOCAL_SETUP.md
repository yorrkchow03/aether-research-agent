# Local setup

Aether runs a single-user research workspace on your computer. You supply the
model credentials, choose when to collect data, and retain the resulting sources,
conversations and reports locally.

## Requirements

- macOS or Linux. On Windows, use a WSL Linux environment.
- Git and Python **3.12, 3.13 or 3.14**, with `venv` and `pip` available.
- Internet access for the initial dependency installation, model requests and
  public data sources.
- Your own Ark Agent Plan credential authorized for **Doubao 2.1 Pro**.
- An optional, separate TypeSafe credential if you want Jev assistance or review.

The package declares Python `>=3.12,<3.15`. Development verification uses macOS
with Python 3.14; the CI configuration also targets the declared versions on Linux.

## Install and launch

```bash
git clone https://github.com/yorrkchow03/aether-research-agent.git
cd aether-research-agent
./start.sh
```

The launcher:

1. Finds a compatible Python interpreter and creates `.venv`.
2. Installs `requirements.lock.txt` and the application as an editable package.
3. Creates `.env` from `.env.example` if needed and prompts for a missing Ark key.
   Key input is hidden and stays in this local configuration file.
4. Starts the server on `127.0.0.1:8765` and opens the browser.

On macOS, **Start_Aether.command** uses this same setup and launch path. The
terminal must remain running. Press **Ctrl+C** to stop the service.

On a new installation, setup and startup do not submit a model request, collect
data, start an experiment or download optional embedding weights. The workspace
initially has empty saved data, chats, reports, portfolio records and experiment
lists. The product video illustrates a workspace populated through actual use.
Schedules you enable later can run while the service is active.

### Other launch modes

```bash
# Keep browser opening under your control.
./start.sh --no-browser

# Use a different local port.
./start.sh --port 8767

# Install dependencies without running the server without running the server.
./start.sh --install-only
```

For an unattended launch, prepare `.env` first, then use:

```bash
./start.sh --no-input --no-browser
```

The server binds to loopback. Open the port printed by the launcher on the same
computer. This startup path does not provision a public server, login accounts or
remote access.

## Configure your model key

The application reads the project root **`.env` file**. Exporting a shell variable
alone does not replace this file. It is ignored by Git; `.env.example` contains
only placeholders and defaults.

The standard configuration is:

```dotenv
AGENT_MODEL_MODE=ark_plan
ARK_API_KEY=your_ark_agent_plan_key
ARK_MODEL_ID=doubao-seed-2.1-pro
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/plan/v3
ARK_THINKING=disabled
WEB_SEARCH_ENABLED=true
```

Use the **Agent Plan** credential and `/api/plan/v3` endpoint. The current
integration validates that endpoint; an ordinary Ark `/api/v3` configuration is
not interchangeable. The model must also be authorized on your account.

Restart the local service after editing `.env`. The dashboard's settings show
whether a model credential is configured without displaying the key.

### Optional Jev configuration

Add a separate TypeSafe key to `.env`:

```dotenv
TYPESAFE_API_KEY=your_typesafe_key
JEV_MODEL=jev-1.14.0
```

Restart the service, then select the desired option in the report composer:

| Control | Effect |
| --- | --- |
| **Jev source assistance** | Assesses retrieved public-source passages in support of source selection. |
| **Report review → Same model** | Uses the authoring model in a separate review context. |
| **Report review → Jev** | Uses Jev to assess support for report statements and their bound evidence. |

Source assistance and report review are separate controls. Jev does not replace
the report author or the deterministic source, numeric and replay checks.
Jev probabilities are model judgments, not measured correctness rates.

## Populate your workspace

### Current research

Use **Home → Refresh** to request updated market research and save the resulting
material. Alternatively, ask Analyst for a specific task and allow it to collect
missing sources. These actions can call the model, search and data providers.
Opening a saved page or reloading saved records does not start a new research run.

For an independent report, use **New chat**, state the asset and time range, and
click **Create report**. The start is inclusive and the end exclusive when a UTC
window is supplied. Specify whether newly collected material is allowed and
whether you need a particular acquisition cutoff.

Ordinary questions receive normal chat replies. Explicit report requests use
the report workflow, including bounded revisions and a final smaller-report
attempt. A limited report contains only its retained, checked scope; an evidence
gap output identifies an unsupported conclusion instead of presenting it as fact.

### Optional historical data

An existing archive is not required to start current research. To prepare a
historical source bundle explicitly, inspect and run:

```bash
.venv/bin/python scripts/data/prepare_data.py --help
.venv/bin/python scripts/data/prepare_data.py --as-of YYYY-MM-DD
.venv/bin/python scripts/data/check_data.py
```

Replace `YYYY-MM-DD` with the desired UTC cutoff. Data collection makes network
requests and can take time. Provider coverage and access restrictions determine
which datasets are available. A historical observation captured today does not
prove that the application possessed it at the historical date.

### Local schedules

The local scheduler supports brief and monitoring jobs through the subscription
API. New installations have no enabled schedules. Enabling a subscription can
start future research and consume provider quota; the application and computer
must remain running for on-time execution. A browser reload does not rerun a job.

For example, to create a daily ETH brief at 09:00 UTC, open the local Aether page
and run this in that page's browser console:

```javascript
fetch('/api/subscriptions', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'X-Local-Token': window.AETHER_TOKEN
  },
  body: JSON.stringify({
    name: 'Daily ETH brief',
    enabled: true,
    mode: 'brief',
    cadence: 'daily',
    timezone: 'UTC',
    hour: 9,
    minute: 0
  })
}).then(response => response.json()).then(console.log)
```

This creates one saved subscription. `GET /api/subscriptions` lists subscriptions
and their IDs. To update an existing one, send the complete configuration with
its returned `id`; set `enabled` to `false` to disable it. Avoid creating duplicate
subscriptions for the same brief.

## Optional local semantic retrieval

Keyword retrieval works without downloaded model weights. To add the supported
local embedding encoder, explicitly install the optional dependencies and fetch
the public weights:

```bash
.venv/bin/python -m pip install -c requirements.lock.txt -r requirements.memory.lock.txt
.venv/bin/python scripts/data/prepare_memory_encoder.py
```

The helper records the selected public model revision and file hashes under
`local/models/`. The runtime does not download weights automatically. The default
configuration points to `local/models/potion-multilingual-128M`; keep that path in
sync if you choose another installation location.

## Local storage and backups

| Location | Contents |
| --- | --- |
| `.env` | Your local model and optional service credentials. |
| `runtime/` | Conversations, checkpoints, memory, saved reports, portfolio and experiment state. |
| `data/` | Captured source material, normalized data, historical archives and evidence packages. |
| `local/` | Optional downloaded models and other local assets. |

These paths are ignored by Git. Stop the application before making a complete
backup of the local state and its SQLite files. Keep `.env` private. Report and
evidence exports can contain any personal context you explicitly used in a task.

Local storage does not make model research offline: relevant prompts and source
context are sent to your configured provider. The local research terminal can run
code with your account's file permissions. See [the security document](../../SECURITY.md)
for the boundaries of the local execution model.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| No compatible Python found | Install Python 3.12–3.14 with `venv` and rerun the launcher. |
| Dependency installation fails | Check network access and the first package installation error. Keep the pinned requirements rather than upgrading packages individually. |
| API key missing in non-interactive mode | Fill `.env` locally, then rerun without printing the key in a command. |
| Provider returns 401 or 403 | Confirm the key is valid for Ark Agent Plan, the model is authorized, and the Plan endpoint is unchanged. Restart after configuration edits. |
| Port is already occupied | Stop the earlier instance or select another port with `--port`. |
| Dashboard, Library or Reports is empty | This is expected on a fresh checkout. Start a scoped research or data collection task. |
| A source is unavailable or stale | Read its source status and time. Provider or regional limits can create genuine gaps. |
| Research was interrupted | Inspect the saved task. Use **Continue** explicitly if available; reconnecting the browser does not restart research. |
| Optional semantic retrieval is unavailable | Install the encoder explicitly, or keep using keyword retrieval. |
| A scheduled task did not run | Check that the subscription is enabled, its timezone is correct, and the service and computer remained running. |

## Offline installation check

```bash
.venv/bin/python scripts/dev/check_install.py
.venv/bin/python -m unittest discover -s tests -q
```

These use local stubs and synthetic fixtures. They check application wiring and
regressions without submitting paid model requests. A successful offline check
does not validate a credential or establish the quality of a new research report.
