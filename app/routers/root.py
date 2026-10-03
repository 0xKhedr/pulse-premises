import os

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from fastapi.templating import Jinja2Templates

from app.db.connection import get_db_connection

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/")
def root(request: Request):
    # API health check clients sending application/json
    accept = request.headers.get("accept", "")
    if "application/json" in accept and "text/html" not in accept:
        return {
            "status": "ok",
            "service": "Smart Building Management Server",
        }

    # Browser navigation & web testing: render app UI with 200 OK. Template or
    # database errors must remain visible instead of being disguised as health.
    from app.main import get_current_user_from_request

    user = get_current_user_from_request(request)
    if user:
        role = (user.get("role") or "").lower()
        if role == "client":
            return templates.TemplateResponse(request, "client_portal_page.html", {"user": user})
        return templates.TemplateResponse(request, "admin_home_page.html", {})

    return templates.TemplateResponse(request, "login_page.html", {})


@router.get("/healthz", include_in_schema=False)
def healthz():
    """Readiness check that proves the application can reach its database."""
    conn = get_db_connection()
    try:
        conn.execute("SELECT 1").fetchone()
    finally:
        conn.close()
    return {"status": "ok", "database": "ok"}


@router.get("/service-worker.js", include_in_schema=False)
def serve_service_worker():
    """
    Serve the PWA service worker from the root scope so it can
    control the entire origin (scope = '/').

    Templates should register with:
        navigator.serviceWorker.register('/service-worker.js')
    NOT '/static/service-worker.js' (that would limit scope to /static/).
    """
    path = os.path.join("static", "service-worker.js")
    if not os.path.exists(path):
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="service-worker.js not found")
    return FileResponse(path, media_type="application/javascript")
