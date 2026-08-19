from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import settings
from .database import Base, SessionLocal, engine
from .access_control import is_client_allowed
from .models import AccessControlSetting, ExamAccessSetting, RegistrationSetting
from .routers import access, admin, auth, exam, problems, reports, submissions
from .services.rate_limit import client_key, rate_limiter


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        changed = False
        if db.get(ExamAccessSetting, 1) is None:
            db.add(ExamAccessSetting(id=1))
            changed = True
        if db.get(AccessControlSetting, 1) is None:
            db.add(AccessControlSetting(id=1, mode="ALL"))
            changed = True
        if db.get(RegistrationSetting, 1) is None:
            db.add(RegistrationSetting(id=1, domain_mode="ALL", custom_domain=""))
            changed = True
        if changed:
            db.commit()
    finally:
        db.close()
    yield


app = FastAPI(
    title=settings.app_name,
    docs_url="/docs" if settings.enable_api_docs else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.enable_api_docs else None,
    lifespan=lifespan,
)


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    if request.url.path.startswith("/api/"):
        if request.url.path not in {"/api/access/check", "/api/health"}:
            db = SessionLocal()
            try:
                from .services.rate_limit import client_ip
                if not is_client_allowed(db, client_ip(request)):
                    return JSONResponse(status_code=403, content={"detail": "This IP address is not allowed to access the portal."})
            finally:
                db.close()
        try:
            rate_limiter.check(client_key(request, "api"), settings.rate_limit_per_minute)
        except Exception as exc:
            if getattr(exc, "status_code", None) == 429:
                return JSONResponse(status_code=429, content={"detail": "Too many requests."})
            raise
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "service": settings.app_name}


app.include_router(access.router)
app.include_router(auth.router)
app.include_router(exam.router)
app.include_router(problems.router)
app.include_router(submissions.router)
app.include_router(admin.router)
app.include_router(reports.router)
