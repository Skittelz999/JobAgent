"""Manual CLI. Only explicit analyze commands contact a local AI runtime."""
import argparse
from datetime import UTC, datetime
import json
import logging
from pathlib import Path
import sqlite3
import sys
import tomllib

from pydantic import ValidationError

from jobagent.database import SCHEMA_VERSION, Database
from jobagent.platsbanken import Platsbanken
from jobagent.search import collect
from jobagent.settings import Search, load_settings


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {"time": datetime.now(UTC).isoformat(), "level": record.levelname,
                 "event": record.getMessage()}
        for key in ("run_id", "page", "inserted", "updated", "unchanged"):
            if hasattr(record, key):
                entry[key] = getattr(record, key)
        return json.dumps(entry, ensure_ascii=False)


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description="Local JobAgent — Platsbanken collection, no AI required")
    cli.add_argument("--root", type=Path, default=Path.cwd(), help="Project directory (default: current directory)")
    cli.add_argument("--config", type=Path, help="Settings file, relative to project root or absolute")
    cli.add_argument("--verbose", action="store_true", help="JSON progress logs to stderr")
    commands = cli.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Initialize the SQLite database")
    commands.add_parser("config-check", help="Validate settings without creating a database")
    search = commands.add_parser("search", help="Collect ads manually")
    search.add_argument("--query", action="append", help="Override queries; repeat for multiple queries")
    search.add_argument("--days", type=int)
    search.add_argument("--page-size", type=int)
    search.add_argument("--max-pages", type=int)
    jobs = commands.add_parser("list", help="List stored ads")
    jobs.add_argument("--keyword")
    jobs.add_argument("--limit", type=int, default=20)
    show = commands.add_parser("show", help="Show one stored ad including description")
    show.add_argument("id", type=int)
    history = commands.add_parser("history", help="Show search runs and completeness")
    history.add_argument("--limit", type=int, default=20)
    analyze = commands.add_parser("analyze", help="Analyze one stored ad locally, without saving or scoring")
    analyze.add_argument("id", type=int)
    manual = commands.add_parser("import", help="Import a UTF-8 JSON ad without fetching a URL")
    manual.add_argument("file", type=Path)
    return cli


def emit(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonLogFormatter())
    logger = logging.getLogger("jobagent")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO if args.verbose else logging.WARNING)
    try:
        settings = load_settings(args.root, args.config)
        if args.command == "config-check":
            output = {"valid": True, "settings": settings.model_dump(mode="json")}
            emit(output)
            return 0
        if args.command in {"list", "history"} and not 1 <= args.limit <= 1000:
            raise ValueError("--limit must be between 1 and 1000")
        # Validate search overrides before creating a DB or starting a run.
        if args.command == "search":
            values = settings.search.model_dump()
            for argument, field in (("query", "queries"), ("days", "days"),
                                    ("page_size", "page_size"), ("max_pages", "max_pages")):
                if getattr(args, argument) is not None:
                    values[field] = getattr(args, argument)
            spec = Search.model_validate(values)
        database = Database(settings.storage.database)
        if args.command == "analyze":
            # Lazy imports and read-only access keep collection independent of AI.
            from jobagent.analysis import analyze_job
            from jobagent.llm import LocalLLM
            from jobagent.models import JobAd

            row = database.get_job(args.id)
            if row is None:
                raise ValueError("No stored job with that ID")
            job = JobAd.model_validate_json(row["payload_json"])
            result = analyze_job(job, LocalLLM(settings.llm))
            emit({"ad_id": args.id, "ad_version": row["version"],
                  "ad_content_hash": row["content_hash"], "title": job.title,
                  "original_url": job.original_url, **result})
            return 0
        database.initialize()
        if args.command == "import":
            from jobagent.sources import ManualImport
            from jobagent.search import import_ad
            value = ManualImport.model_validate_json(args.file.read_text(encoding="utf-8-sig"))
            emit(import_ad(database, value))
        elif args.command == "init":
            emit({"database": str(database.path), "schema_version": SCHEMA_VERSION})
        elif args.command == "search":
            with Platsbanken(settings.source) as source:
                result = collect(database, source, spec)
            emit(result)
            return {"complete": 0, "partial": 2, "failed": 1}[result["status"]]
        elif args.command == "list":
            rows = database.list_jobs(args.limit, args.keyword)
            for row in rows:
                row.pop("payload_json")
                row.pop("content_hash")
            emit(rows)
        elif args.command == "show":
            row = database.get_job(args.id)
            if row is None:
                raise ValueError("No stored job with that ID")
            row["job"] = json.loads(row.pop("payload_json"))
            emit(row)
        elif args.command == "history":
            rows = database.history(args.limit)
            for row in rows:
                row["parameters"] = json.loads(row.pop("parameters_json"))
                row["result"] = json.loads(row.pop("result_json") or "null")
            emit(rows)
        return 0
    except KeyboardInterrupt:
        print("Search interrupted; committed pages are preserved.", file=sys.stderr)
        return 130
    except ValidationError as exc:
        # Avoid echoing possibly private configuration values.
        messages = [f"{'.'.join(str(part) for part in e['loc'])}: {e['msg']}"
                    for e in exc.errors(include_input=False, include_url=False)]
        print("Validation error: " + "; ".join(messages), file=sys.stderr)
        return 1
    except (OSError, ValueError, sqlite3.Error, tomllib.TOMLDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
