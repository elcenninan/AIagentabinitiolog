# Ab Initio Job Failure AI Agent (Ollama + Mistral)

This project provides a simple Python AI agent that:

1. Reads Ab Initio failure logs.
2. Identifies the failed job name.
3. Retrieves likely source and destination table mappings from a local knowledge base (RAG-style retrieval).
4. Calls a local Ollama model (`mistral:7b`) to produce structured incident analysis.
5. Optionally executes an `UPDATE` statement against a tracking database table.

## Prerequisites

- Python 3.10+
- Ollama installed and running locally
- Model pulled locally:

```bash
ollama pull mistral:7b
```

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Files

- `agent.py` – Main agent implementation.
- `knowledge_base/jobs_catalog.json` – Job metadata used as retrieval context.
- `sample_logs/job_failure.log` – Example failure log.
- `tests/test_agent.py` – Basic parser/retrieval tests.

## Usage

Analyze a log and print structured output:

```bash
python agent.py \
  --log-file sample_logs/job_failure.log \
  --jobs-file knowledge_base/jobs_catalog.json
```

Dry run without calling Ollama (uses deterministic fallback based on retrieval):

```bash
python agent.py \
  --log-file sample_logs/job_failure.log \
  --jobs-file knowledge_base/jobs_catalog.json \
  --dry-run
```

Analyze and update tracking table (`job_failures`) in sqlite:

```bash
python agent.py \
  --log-file sample_logs/job_failure.log \
  --jobs-file knowledge_base/jobs_catalog.json \
  --db-path incidents.db \
  --incident-id 101 \
  --apply-update
```

## Suggested DB table

```sql
CREATE TABLE job_failures (
  incident_id INTEGER PRIMARY KEY,
  job_name TEXT,
  source_table TEXT,
  destination_table TEXT,
  failure_reason TEXT,
  last_updated_utc TEXT
);
```

## Notes

- The retriever is intentionally simple (token overlap) to keep the solution lightweight.
- For production, you can replace the retriever with embeddings/vector DB while keeping the same agent flow.
