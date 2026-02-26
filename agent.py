import argparse
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass
class JobRecord:
    job_name: str
    source_table: str
    destination_table: str
    tags: list[str]


def normalize_tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-zA-Z0-9_]+", text.lower()))


def parse_log(log_text: str) -> dict[str, Any]:
    job_patterns = [
        r"/([a-zA-Z0-9_]+)\.(?:mp|ksh|graph)",
        r"job[_\s-]?name[:=]\s*([a-zA-Z0-9_]+)",
    ]

    detected_job = None
    for pattern in job_patterns:
        match = re.search(pattern, log_text, re.IGNORECASE)
        if match:
            detected_job = match.group(1)
            break

    error_lines = [line.strip() for line in log_text.splitlines() if "ERROR" in line.upper()]
    failure_reason = error_lines[-1] if error_lines else "Unknown failure reason"

    return {
        "detected_job": detected_job,
        "failure_reason": failure_reason,
        "tokens": normalize_tokens(log_text),
    }


def load_jobs(jobs_file: Path) -> list[JobRecord]:
    text = jobs_file.read_text(encoding="utf-8")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        sanitized = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
        sanitized = re.sub(r"//.*", "", sanitized)
        sanitized = re.sub(r",(\s*[}\]])", r"\1", sanitized)
        try:
            raw = json.loads(sanitized)
        except json.JSONDecodeError as sanitized_exc:
            raise ValueError(
                f"Invalid jobs JSON in {jobs_file} at line {sanitized_exc.lineno}, "
                f"column {sanitized_exc.colno}."
            ) from exc

    if not isinstance(raw, list):
        raise ValueError(f"Jobs catalog must be a JSON array in {jobs_file}.")
    return [JobRecord(**item) for item in raw]


def score_job(log_tokens: set[str], parsed_job: str | None, job: JobRecord) -> int:
    profile = " ".join([job.job_name, job.source_table, job.destination_table, *job.tags])
    profile_tokens = normalize_tokens(profile)
    overlap = len(log_tokens.intersection(profile_tokens))
    job_bonus = 4 if parsed_job and parsed_job.lower() == job.job_name.lower() else 0
    return overlap + job_bonus


def retrieve_best_job(parsed_log: dict[str, Any], jobs: list[JobRecord]) -> JobRecord:
    ranked = sorted(
        jobs,
        key=lambda job: score_job(parsed_log["tokens"], parsed_log["detected_job"], job),
        reverse=True,
    )
    return ranked[0]


def call_ollama(model: str, prompt: str, timeout_s: int = 120) -> str:
    payload = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode("utf-8")
    request = Request(
        "http://localhost:11434/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_s) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        raise RuntimeError(f"Ollama HTTP error: {exc.code} {exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"Unable to connect to Ollama: {exc.reason}") from exc

    decoded = json.loads(body)
    return decoded.get("response", "")


def build_prompt(log_text: str, best_job: JobRecord, failure_reason: str) -> str:
    return f"""
You are an Ab Initio incident triage assistant.
Use only the provided context and return strict JSON with keys:
job_name, source_table, destination_table, failure_reason, update_sql.

Context:
- inferred_job_name: {best_job.job_name}
- source_table: {best_job.source_table}
- destination_table: {best_job.destination_table}
- failure_reason: {failure_reason}

Log:
{log_text}

The SQL should update table job_failures for a given :incident_id.
""".strip()


def parse_model_json(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def fallback_result(best_job: JobRecord, failure_reason: str) -> dict[str, str]:
    return {
        "job_name": best_job.job_name,
        "source_table": best_job.source_table,
        "destination_table": best_job.destination_table,
        "failure_reason": failure_reason,
        "update_sql": (
            "UPDATE job_failures "
            "SET job_name=:job_name, source_table=:source_table, destination_table=:destination_table, "
            "failure_reason=:failure_reason, last_updated_utc=:last_updated_utc "
            "WHERE incident_id=:incident_id;"
        ),
    }


def apply_update(db_path: Path, incident_id: int, result: dict[str, Any]) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            UPDATE job_failures
            SET job_name=?, source_table=?, destination_table=?, failure_reason=?, last_updated_utc=?
            WHERE incident_id=?
            """,
            (
                result["job_name"],
                result["source_table"],
                result["destination_table"],
                result["failure_reason"],
                datetime.now(timezone.utc).isoformat(),
                incident_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Ab Initio log triage AI agent")
    parser.add_argument("--log-file", type=Path, required=True)
    parser.add_argument("--jobs-file", type=Path, required=True)
    parser.add_argument("--model", default="mistral:7b")
    parser.add_argument("--dry-run", action="store_true", help="Skip Ollama call")
    parser.add_argument("--db-path", type=Path)
    parser.add_argument("--incident-id", type=int)
    parser.add_argument("--apply-update", action="store_true")
    args = parser.parse_args()

    log_text = args.log_file.read_text()
    parsed = parse_log(log_text)
    jobs = load_jobs(args.jobs_file)
    best_job = retrieve_best_job(parsed, jobs)

    if args.dry_run:
        result = fallback_result(best_job, parsed["failure_reason"])
    else:
        prompt = build_prompt(log_text, best_job, parsed["failure_reason"])
        llm_text = call_ollama(args.model, prompt)
        result = parse_model_json(llm_text)

    if args.apply_update:
        if args.db_path is None or args.incident_id is None:
            raise ValueError("--db-path and --incident-id are required with --apply-update")
        apply_update(args.db_path, args.incident_id, result)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
