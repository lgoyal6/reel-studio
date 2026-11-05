"""Single-user local HTTP application. Bind to loopback, not a public host."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from contextvars import ContextVar, copy_context
import logging
import os
from pathlib import Path
import shutil
import threading
import zipfile
from urllib.parse import urlparse

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .models import CheckoutRequest, EditRequest
from .access import AccessGate, SessionRequest, signing_secret
from .planner import muse_binary, plan_edit
from .store import Store, now, uid

LOGGER = logging.getLogger(__name__)
MAX_UPLOAD = 250 * 1024 * 1024
MAX_DURATION = 300
MAX_OUTPUT_DURATION = 1200
PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def create_app(data_dir: Path | None = None) -> FastAPI:
    data_dir = data_dir or Path(os.environ.get("REEL_DATA_DIR", Path.home() / ".codex/work/reel-studio/data"))
    store = Store(data_dir)
    gate = AccessGate(os.environ.get("REEL_PUBLIC_ORIGIN"), os.environ.get("REEL_DEMO_ACCESS_CODE"))
    owner = ContextVar("workspace_owner", default="local")
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="reel-render")
    admission = threading.RLock()

    @asynccontextmanager
    async def lifespan(app):
        store.recover_interrupted()
        yield
        executor.shutdown(wait=True, cancel_futures=True)
        store.close()

    app = FastAPI(title="Reel Studio", lifespan=lifespan)
    app.state.store = store
    app.state.executor = executor

    @app.middleware("http")
    async def local_origin(request: Request, call_next):
        # Reject cross-site requests to the local processor, including simple-form uploads.
        origin = request.headers.get("origin")
        allowed = {f"http://{host}:{port}" for host in ("localhost", "127.0.0.1") for port in (8787, 5173)}
        if gate.origin:
            allowed.add(gate.origin)
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin and origin not in allowed:
            return JSONResponse({"detail": "Open Reel Studio directly to make edits."}, status_code=403)
        session_owner = request.session.get("owner_id") if gate.enabled else "local"
        protected = request.url.path.startswith(("/api/", "/media/")) and request.url.path != "/api/session"
        if gate.enabled and protected and not session_owner:
            return JSONResponse({"detail": "Enter the demo access code to open your workspace."}, status_code=401)
        token = owner.set(session_owner or "anonymous")
        try:
            return await call_next(request)
        finally:
            owner.reset(token)

    def owned(document):
        return not gate.enabled or document.get("owner_id") == owner.get()

    @app.get("/api/session")
    def session_status(request: Request):
        return {"public_mode": gate.enabled, "authenticated": not gate.enabled or bool(request.session.get("owner_id"))}

    @app.post("/api/session")
    def open_session(payload: SessionRequest, request: Request):
        if not gate.enabled:
            return {"public_mode": False, "authenticated": True}
        try:
            valid = gate.check(request.client.host if request.client else "unknown", payload.access_code)
        except ValueError as exc:
            raise HTTPException(429, str(exc))
        if not valid:
            raise HTTPException(401, "That access code did not match")
        request.session["owner_id"] = request.session.get("owner_id") or uid()
        return {"public_mode": True, "authenticated": True}

    def project(id: str):
        try:
            store.project_dir(id)
            document = store.get("projects", id)
            if not owned(document):
                raise KeyError(id)
            return document
        except (KeyError, ValueError):
            raise HTTPException(404, "Project not found")

    def job_update(id, **changes):
        item = store.get("jobs", id)
        store.save("jobs", {**item, **changes, "updated_at": now()})

    def submit(kind, project_id, task):
        id = uid()
        item = {"id": id, "kind": kind, "project_id": project_id, "status": "queued", "progress": 0,
                "message": "Waiting to process", "created_at": now(), "error": None, "owner_id": owner.get()}
        store.save("jobs", item)

        def execute():
            job_update(id, status="running", progress=.03, message="Preparing audio")
            try:
                task(id)
                job_update(id, status="complete", progress=1, message="Ready to play")
            except Exception as exc:
                LOGGER.exception("Job %s failed", id)
                # Expected engine/validation errors can guide the user; private command errors cannot.
                message = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else "Processing failed. Your previous revision is intact."
                if len(message) > 700 or "/Users/" in message or "Traceback" in message:
                    message = "Audio processing failed. Your previous revision is intact. Check the local server log."
                job_update(id, status="failed", error=message, message="Edit was not applied")

        context = copy_context()
        executor.submit(context.run, execute)
        return item

    async def save_upload(file: UploadFile, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        # Uploaded filenames are display text only and never become filesystem paths.
        target = directory / "upload.bin"
        size = 0
        try:
            with target.open("wb") as destination:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD:
                        raise HTTPException(413, "Upload a file smaller than 250 MB")
                    destination.write(chunk)
            if size == 0:
                raise HTTPException(422, "The uploaded file is empty")
            return target
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        finally:
            await file.close()

    def media_url(project_id: str, revision_id: str, file: Path, root: Path):
        file = file.resolve()
        relative = file.relative_to(root.resolve())
        return f"/media/{project_id}/{revision_id}/{relative.as_posix()}"

    def serialize_revision(project_id, revision_id, root, result, label, operations, planner="controls", parent=None):
        analysis = result.get("analysis", {})
        tracks = [{"name": t["name"], "url": media_url(project_id, revision_id, Path(t["path"]), root)} for t in result.get("tracks", [])]
        layers = {name: media_url(project_id, revision_id, Path(path), root) if path else None
                  for name, path in result.get("layers", {}).items()}
        return {"id": revision_id, "index": 0, "label": label, "created_at": now(),
                "parent_id": parent, "duration": result["duration"],
                "audio_url": media_url(project_id, revision_id, Path(result["audio_path"]), root),
                "video_url": media_url(project_id, revision_id, Path(result["video_path"]), root) if result.get("video_path") else None,
                "waveform": analysis.get("peaks", []), "beats": analysis.get("beats", []), "bpm": analysis.get("bpm"),
                "operations": operations, "tracks": tracks, "layers": layers, "notes": result.get("notes", []), "planner": planner,
                "mapping": result.get("mapping", []), "analysis": analysis}

    def revision_dir(project_id, revision_id):
        # Require the revision to belong to this project before resolving any artifact.
        p = project(project_id)
        if not any(r["id"] == revision_id for r in p["revisions"]):
            raise HTTPException(404, "Revision not found")
        return store.project_dir(project_id) / "revisions" / revision_id

    def file_for_url(url: str):
        parts = url.split("/", 4)
        root = revision_dir(parts[2], parts[3])
        path = (root / parts[4]).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise HTTPException(404, "Media not found")
        return path

    def import_task(id, source, name):
        def task(job):
            from . import engine
            meta = engine.probe(source)
            if not 0 < meta["duration"] <= MAX_DURATION:
                raise ValueError("Choose a clip between 0 and 300 seconds long")
            rid = uid()
            root = store.project_dir(id) / "revisions" / rid
            root.mkdir(parents=True, exist_ok=True)
            job_update(job, progress=.2, message="Extracting audio and building the waveform")
            result = engine.ingest(source, root)
            revision = serialize_revision(id, rid, root, result, "Original", [])
            p = {"id": id, "name": name, "created_at": now(), "duration": revision["duration"], "owner_id": owner.get(),
                 "has_video": bool(revision["video_url"]), "music_ready": False,
                 "current_revision": rid, "original_revision": rid, "revisions": [revision], "chat": []}
            store.save("projects", p)
            source.unlink(missing_ok=True)
        return task

    @app.get("/api/capabilities")
    def capabilities():
        from .semantic import available
        from .speech import available as speech_available
        from .ads import available as ads_available
        return {"planner": "muse" if muse_binary() else "rules", "engines": {"demucs": bool(shutil.which("uv") or os.environ.get("REEL_DEMUCS_PYTHON")),
                "semantic": available(), "speech": speech_available(), "ads": ads_available(), "cleaning": "spectral-gate"}, "limits": {"max_duration": MAX_DURATION, "max_upload_mb": 250},
                "semantic_note": "Text-guided separation estimates a target and may retain unwanted sound." if available() else "Text-guided sound separation is not installed. Music stems and spectral cleanup are available."}

    @app.get("/api/projects")
    def list_projects():
        return [p for p in store.all("projects") if owned(p)]

    @app.post("/api/projects", status_code=202)
    async def upload_project(file: UploadFile = File(...)):
        id = uid()
        name = Path(file.filename or "Untitled clip").name[:180]
        source = await save_upload(file, store.project_dir(id) / "uploads")
        return submit("import", id, import_task(id, source, name))

    @app.post("/api/demo", status_code=202)
    def load_demo():
        source = Path(os.environ.get("REEL_DEMO_FILE", str(store.root / "cache/demo.mp4")))
        if not source.is_file():
            raise HTTPException(404, "Sample clip is not installed. Upload your own clip or run scripts/setup_demo.py.")
        id = uid()
        destination = store.project_dir(id) / "uploads/upload.bin"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        return submit("import", id, import_task(id, destination, "Chad Smith / Drumeo"))

    @app.get("/api/jobs/{id}")
    def read_job(id: str):
        try:
            job = store.get("jobs", id)
            if not owned(job):
                raise KeyError(id)
            return job
        except KeyError:
            raise HTTPException(404, "Job not found")

    @app.get("/api/jobs")
    def active_jobs():
        return [job for job in store.all("jobs") if owned(job) and job["status"] in ("queued", "running")]

    @app.get("/api/projects/{id}")
    def read_project(id: str):
        return project(id)

    @app.post("/api/projects/{id}/music", status_code=202)
    async def upload_music(id: str, file: UploadFile = File(...)):
        project(id)
        # Separate staging folder per upload prevents an in-flight upload being overwritten.
        root = store.project_dir(id) / "music" / uid()
        source = await save_upload(file, root / "upload")
        with admission:
            if store.busy(id):
                shutil.rmtree(root, ignore_errors=True)
                raise HTTPException(409, "Wait for the current edit to finish")

            def task(job):
                from . import engine
                meta = engine.probe(source)
                if not 0 < meta["duration"] <= MAX_DURATION:
                    raise ValueError("Choose music up to 300 seconds long")
                result = engine.ingest(source, root)
                p = project(id)
                p.update(music_ready=True, music_path=str(Path(result["audio_path"]).resolve()))
                store.save("projects", p)
                source.unlink(missing_ok=True)
            return submit("music", id, task)

    @app.post("/api/projects/{id}/edits", status_code=202)
    def edit(id: str, request: EditRequest):
        with admission:
            p = project(id)
            if store.busy(id):
                raise HTTPException(409, "Wait for the current edit to finish")
            base = next(r for r in p["revisions"] if r["id"] == p["current_revision"])
            if request.selection and (request.selection.start >= base["duration"] or (request.selection.end or base["duration"]) > base["duration"] + .05):
                raise HTTPException(422, "Select a range inside the current clip")
            rid = uid()
            root = store.project_dir(id) / "revisions" / rid

            def task(job):
                from . import engine
                root.mkdir(parents=True, exist_ok=True)
                job_update(job, progress=.08, message="Muse is planning your edit" if not request.operations and muse_binary() else "Preparing your edit")
                plan, planner = plan_edit(request, p, base, root / "plan")
                ops = [op.model_dump(exclude_none=True) for op in plan.operations]
                if not ops:
                    p["chat"].extend([{"role": "user", "content": request.prompt},
                                      {"role": "assistant", "content": plan.summary, "planner": planner}])
                    store.save("projects", p)
                    return
                if any(op["type"] in ("align_music", "duck", "replace_audio") for op in ops) and not p["music_ready"]:
                    raise ValueError("Upload a music track before aligning or ducking it")
                job_update(job, progress=.18, message=plan.summary)

                def progress(*args):
                    if len(args) >= 2 and isinstance(args[0], (int, float)):
                        job_update(job, progress=min(.95, .2 + float(args[0]) * .75), message=str(args[1]))
                    elif len(args) >= 2 and isinstance(args[1], (int, float)):
                        job_update(job, progress=min(.95, .2 + float(args[1]) * .75), message=str(args[0]))
                    elif args:
                        job_update(job, message=str(args[-1]))

                result = engine.render(input_media=file_for_url(base["video_url"]) if base["video_url"] else file_for_url(base["audio_url"]),
                                       input_audio=file_for_url(base["audio_url"]), operations=ops, out_dir=root,
                                       auxiliary=Path(p["music_path"]) if p.get("music_path") else None, progress=progress,
                                       input_layers={name: file_for_url(url) if url else None for name, url in base.get("layers", {}).items()} or None)
                if result["duration"] > MAX_OUTPUT_DURATION:
                    raise ValueError("The edited clip exceeds the 20 minute output limit")
                revision = serialize_revision(id, rid, root, result, plan.summary, ops, planner, base["id"])
                if not revision["tracks"] and base["tracks"]:
                    revision["tracks"] = base["tracks"]
                    revision["notes"].append("Stem downloads retain their earlier separation-stage timing and effects. The rendered mix is the current edited result.")
                revision["index"] = len(p["revisions"])
                p["revisions"].append(revision)
                p["current_revision"] = rid
                p["duration"] = revision["duration"]
                p["chat"].extend([{"role": "user", "content": request.prompt or plan.summary, "revision_id": rid},
                                  {"role": "assistant", "content": plan.summary + (" " + " ".join(result.get("notes", [])) if result.get("notes") else ""),
                                   "revision_id": rid, "planner": planner}])
                store.save("projects", p)
            return submit("edit", id, task)

    @app.post("/api/projects/{id}/checkout")
    def checkout(id: str, request: CheckoutRequest):
        with admission:
            p = project(id)
            if store.busy(id):
                raise HTTPException(409, "Wait for the current edit to finish")
            revision = next((r for r in p["revisions"] if r["id"] == request.revision_id), None)
            if not revision:
                raise HTTPException(404, "Revision not found")
            p.update(current_revision=revision["id"], duration=revision["duration"])
            store.save("projects", p)
            return p

    @app.get("/api/projects/{id}/export")
    def export(id: str, format: str = "mp4"):
        p = project(id)
        revision = next(r for r in p["revisions"] if r["id"] == p["current_revision"])
        if format == "mp4":
            if not revision["video_url"]:
                raise HTTPException(422, "This project contains audio only. Export WAV instead.")
            return FileResponse(file_for_url(revision["video_url"]), media_type="video/mp4", filename="reel-studio.mp4")
        if format == "wav":
            return FileResponse(file_for_url(revision["audio_url"]), media_type="audio/wav", filename="reel-studio.wav")
        if format == "stems":
            if not revision["tracks"]:
                raise HTTPException(422, "Separate or mix stems first, then export them")
            # Stream a disk ZIP, avoiding an unbounded in-memory copy of all stems.
            target = revision_dir(id, revision["id"]) / "stems.zip"
            if not target.exists():
                with store.lock:
                    temporary = target.with_suffix(".zip.tmp")
                    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
                        for i, track in enumerate(revision["tracks"]):
                            archive.write(file_for_url(track["url"]), f"{i+1}-{Path(track['url']).name}")
                        archive.writestr("README.txt", "Stem estimates from the separation stage of this revision. Later timeline or mix effects may only apply to the rendered mix. See revision notes.\n")
                    temporary.replace(target)
            return FileResponse(target, media_type="application/zip", filename="reel-studio-stems.zip")
        raise HTTPException(422, "Choose mp4, wav, or stems")

    @app.get("/media/{project_id}/{revision_id}/{filename:path}")
    def media(project_id: str, revision_id: str, filename: str):
        root = revision_dir(project_id, revision_id)
        path = (root / filename).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise HTTPException(404, "Media not found")
        allowed = {".mp4", ".wav", ".flac", ".mp3", ".zip"}
        if path.suffix not in allowed:
            raise HTTPException(404, "Media not found")
        return FileResponse(path)

    dist = PACKAGE_ROOT / "frontend/dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    else:
        @app.get("/")
        def index():
            return JSONResponse({"message": "Build the frontend with npm ci && npm run build in frontend/. API is ready at /docs."})
    if gate.enabled:
        app.add_middleware(SessionMiddleware, secret_key=signing_secret(store.root), session_cookie="reel_workspace",
                           max_age=7*24*60*60, same_site="lax", https_only=urlparse(gate.origin).scheme == "https")
    return app


app = create_app()
