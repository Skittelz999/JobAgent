"""Loopback-only web UI; all mutations are explicit forms with a CSRF token."""
from contextlib import asynccontextmanager
import json
import secrets
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import ValidationError

from jobagent.analysis import PROMPT_VERSION
from jobagent.database import Database
from jobagent.models import JobAd
from jobagent.passages import number_passages
from jobagent.processing import current_evaluation, analysis_is_current
from jobagent.search import import_ad
from jobagent.settings import Search
from jobagent.sources import ManualImport, SOURCE_CATALOG
from jobagent.tasks import TaskQueue

STATUS_LABELS = {"pending": "Väntar på analys", "success": "Analyserad", "error": "Fel",
                 "outdated": "Behöver ny analys", "queued": "I kö", "running": "Pågår",
                 "partial": "Delvis klar", "interrupted": "Avbruten"}


def browse_jobs(database, settings, *, q="", source="", location="", technology="", status="", decision="", years=None):
    results = []
    for row in database.jobs_with_latest_analysis():
        job = JobAd.model_validate_json(row["payload_json"])
        row["job"] = job
        row["state"] = row["analysis_status"] or "pending"
        result = json.loads(row["analysis_result_json"]) if row["analysis_result_json"] else None
        if row["analysis_status"] == "success" and not analysis_is_current(row, settings.llm.active_model, settings.llm.provider):
            row["state"] = "outdated"
        row["result"] = result if row["state"] == "success" else None
        row["experience_rows"] = [dict(item, **proof) for item, proof in zip(
            (row["result"] or {}).get("experience", {}).get("items", []),
            (row["result"] or {}).get("experience_evidence", []))]
        row["overview"] = (row["result"] or {}).get("overview", {})
        row["evaluation"] = current_evaluation(row["analysis_result_json"], row["analysis_status"],
                                                row["analysis_prompt_version"], job.description, settings.filter)
        if source and source != job.source:
            continue
        locations = " ".join([job.location or ""] + [x["text_sv"] for x in row["overview"].get("location", [])])
        if location.casefold() not in locations.casefold():
            continue
        if q.casefold() not in (job.title + " " + (job.employer or "") + " " + job.description).casefold():
            continue
        if technology and not any(technology.casefold() in x["text_sv"].casefold() for x in row["overview"].get("techstack", [])):
            continue
        if status and row["state"] != status:
            continue
        if decision and row["evaluation"]["decision"] != decision:
            continue
        if years is not None:
            if row["state"] != "success":
                continue
            from jobagent.settings import FilterSettings
            evaluated = current_evaluation(row["analysis_result_json"], row["analysis_status"],
                                           row["analysis_prompt_version"], job.description, FilterSettings(max_required_years=float(years)))
            if evaluated["decision"] != "KEEP":
                continue
        results.append(row)
    return results


def create_app(settings, *, start_worker=True, provider_factory=None):
    database = Database(settings.storage.database)
    queue = TaskQueue(database, settings, **({"provider_factory": provider_factory} if provider_factory else {}))
    token = secrets.token_urlsafe(32)
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
    templates.env.globals.update(csrf=token, labels=STATUS_LABELS, llm_provider=settings.llm.provider, llm_model=settings.llm.active_model,
                                importance_labels={"mandatory": "Obligatoriskt", "expected": "Förväntat", "preferred": "Meriterande", "unclear": "Oklart", "not_applicable": "Omnämnd teknik"})

    @asynccontextmanager
    async def lifespan(app):
        database.initialize()
        if start_worker:
            queue.start()
        try:
            yield
        finally:
            if start_worker:
                # Do not block the event loop while a bounded model request finishes.
                import asyncio
                await asyncio.to_thread(queue.stop)

    app = FastAPI(title="JobAgent", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]"])
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
    app.state.database, app.state.queue = database, queue

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def render(request, template, **context):
        return templates.TemplateResponse(request=request, name=template, context=context)

    async def csrf_check(request: Request, csrf: Annotated[str, Form()]):
        origin = request.headers.get("origin")
        expected_origin = str(request.base_url).rstrip("/")
        # no-referrer can make the browser send Origin: null for form submissions.
        # In that case the form token still proves the page came from this server.
        if request.headers.get("sec-fetch-site") == "cross-site" or origin not in (None, "null", expected_origin):
            raise HTTPException(403, "Felaktigt ursprung")
        if not csrf.isascii() or not secrets.compare_digest(csrf, token):
            raise HTTPException(403, "Ladda om sidan och försök igen")

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, q: str = "", source: str = "", location: str = "", technology: str = "",
              status: str = "", decision: str = "", years: Annotated[float | None, Query(ge=0, le=100)] = None,
              page: Annotated[int, Query(ge=1)] = 1):
        filters = dict(q=q, source=source, location=location, technology=technology, status=status, decision=decision, years=years)
        rows = browse_jobs(database, settings, **filters)
        sources = sorted({row["source"] for row in database.jobs_with_latest_analysis()})
        return render(request, "index.html", jobs=rows[(page-1)*40:page*40], count=len(rows), page=page,
                      filters=filters, sources=sources, catalog=SOURCE_CATALOG,
                      tasks=queue.list(5), search=settings.search)

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def detail(request: Request, job_id: int):
        row = next((row for row in browse_jobs(database, settings) if row["id"] == job_id), None)
        if not row:
            raise HTTPException(404, "Annonsen finns inte")
        text, cursor, segments = row["job"].description, 0, []
        for p in number_passages(text):
            segments.append({"id": None, "text": text[cursor:p.start]})
            segments.append({"id": p.id, "text": text[p.start:p.end]})
            cursor = p.end
        segments.append({"id": None, "text": text[cursor:]})
        return render(request, "detail.html", row=row, segments=segments)

    @app.get("/import", response_class=HTMLResponse)
    def import_page(request: Request):
        return render(request, "import.html", values={}, error=None)

    @app.post("/import", dependencies=[Depends(csrf_check)])
    async def import_form(request: Request):
        form = await request.form()
        values = {k: v for k, v in form.items() if k != "csrf"}
        try:
            value = ManualImport.model_validate(values)
            record = value.record()
            import_ad(database, value)
        except (ValidationError, ValueError):
            return render(request, "import.html", values=values, error="Kontrollera källa, titel, fullständig annonstext och en giltig http/https-länk.")
        with database.connect(read_only=True) as conn:
            row = conn.execute("SELECT id FROM jobs WHERE source=? AND external_id=?", (record.job.source, record.job.external_id)).fetchone()
        return RedirectResponse(f"/jobs/{row['id']}", status_code=303)

    @app.post("/collect", dependencies=[Depends(csrf_check)])
    def collect_form(queries: Annotated[str, Form()], days: Annotated[int, Form(ge=1, le=3650)]):
        try:
            spec = settings.search.model_copy(update={"queries": [q.strip() for q in queries.splitlines() if q.strip()], "days": days})
            spec = Search.model_validate(spec.model_dump())
            queue.enqueue("collect", spec.model_dump())
        except ValueError:
            raise HTTPException(422, "Ange minst ett sökord") from None
        return RedirectResponse("/tasks", status_code=303)

    @app.post("/analyze", dependencies=[Depends(csrf_check)])
    def analyze_form():
        queue.enqueue("analyze_pending")
        return RedirectResponse("/tasks", status_code=303)

    @app.post("/jobs/{job_id}/analyze", dependencies=[Depends(csrf_check)])
    def reanalyze(job_id: int):
        if not database.get_job(job_id):
            raise HTTPException(404, "Annonsen finns inte")
        queue.enqueue("analyze_one", {"job_id": job_id})
        return RedirectResponse("/tasks", status_code=303)

    @app.get("/tasks", response_class=HTMLResponse)
    def tasks(request: Request):
        rows = queue.list()
        for row in rows:
            row["progress"] = json.loads(row["progress_json"])
            row["payload"] = json.loads(row["payload_json"])
            row["result"] = json.loads(row["result_json"]) if row["result_json"] else None
        return render(request, "tasks.html", tasks=rows)

    @app.get("/api/tasks")
    def task_status():
        return [{"id": row["id"], "status": row["status"], "label": STATUS_LABELS.get(row["status"], row["status"]),
                 "progress": json.loads(row["progress_json"]), "error": row["error_message"]} for row in queue.list()]

    @app.post("/tasks/{task_id}/retry", dependencies=[Depends(csrf_check)])
    def retry(task_id: int):
        try:
            queue.retry(task_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        return RedirectResponse("/tasks", status_code=303)

    return app
