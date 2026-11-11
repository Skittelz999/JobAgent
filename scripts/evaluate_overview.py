"""Explicit live quality run; never part of pytest, never changes the ad database."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from jobagent.analysis import PROMPT_VERSION, analyze_job
from jobagent.database import Database
from jobagent.llm import create_provider
from jobagent.models import JobAd
from jobagent.settings import load_settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ids", type=int, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--provider", choices=["local", "openai"], default="local")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    settings = load_settings(args.root)
    settings.llm.provider = args.provider
    database = Database(settings.storage.database)
    rows = [database.get_job(i) for i in dict.fromkeys(args.ids)]
    if any(row is None for row in rows):
        parser.error("Unknown ad ID")
    args.output.mkdir(parents=True, exist_ok=True)
    for row in rows:
        target = args.output / f"{row['id']}.json"
        if target.exists():
            print(row["id"], "already recorded; choose a new output directory to re-run", flush=True)
            continue
        provider = create_provider(settings.llm)
        raw = {}
        generate = provider.generate_json
        def recording(messages, schema):
            response = generate(messages, schema)
            raw.update(response)
            return response
        provider.generate_json = recording
        job = JobAd.model_validate_json(row["payload_json"])
        try:
            result = analyze_job(job, provider)
            status = "success"
        except Exception as exc:
            result, status = {"error": str(exc)}, "error"
        target.write_text(json.dumps({"id": row["id"], "version": row["version"],
            "content_hash": row["content_hash"], "prompt_version": PROMPT_VERSION,
            "provider": args.provider, "requested_model": settings.llm.active_model,
            "settings": settings.llm.model_dump(), "job": job.model_dump(mode="json"),
            "status": status, "result": result, "raw_response": raw},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(row["id"], status, flush=True)


if __name__ == "__main__":
    main()
