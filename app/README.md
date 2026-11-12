# Map QA CLI

Reads speed-limit sign crops through a decision-model → LLM → human cascade, compares the reading with the OSM speed tags of the nearest road and writes a Markdown report and a review CSV. Requirements: [PRD](../docs/PRD.md); architecture: [ADR-000…004](../docs/ADR/).

## Setup

```powershell
cd app
uv sync
uv run --frozen pytest
uv run map-qa --help
```

Usage is documented once the CLI is implemented.
