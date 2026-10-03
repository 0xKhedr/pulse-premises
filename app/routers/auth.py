from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.config import settings
from app.db.connection import get_db_connection as db
from app.main import (
    ADMIN_EMAIL,
    PASSWORD_PBKDF2_ITERATIONS,
    SESSION_COOKIE_NAME,
    SESSION_TTL_SECONDS,
    cleanup_expired_sessions,
    create_login_session,
    default_redirect_for_user,
    ensure_default_admin_user,
    get_current_user_from_request,
    is_admin_path,
    revoke_current_session,
    safe_user_dict,
    verify_password,
)

router = APIRouter()

@router.post("/auth/login")
def auth_login(data: dict, request: Request):
    ensure_default_admin_user()

    email = data.get("email", "").strip().lower()
    password = data.get("password", "")
    next_url = data.get("next", "")
    expected_role = data.get("expected_role", "").strip().lower()

    if not email:
        raise HTTPException(status_code=400, detail="Email is required")

    if not password:
        raise HTTPException(status_code=400, detail="Password is required")

    conn = db()
    try:

        cleanup_expired_sessions(conn)

        user_row = conn.execute("""
        SELECT *
        FROM users
        WHERE email = ?
        LIMIT 1
    """, (email,)).fetchone()

        if not user_row:
            conn.close()
            raise HTTPException(status_code=401, detail="Invalid email or password")

        user = dict(user_row)

        if user.get("enabled") != 1:
            conn.close()
            raise HTTPException(status_code=403, detail="User is disabled")

        if not verify_password(password, user_row):
            conn.close()
            raise HTTPException(status_code=401, detail="Invalid email or password")

        actual_role = (user.get("role") or "").lower()

        if expected_role == "admin" and actual_role != "admin":
            conn.close()
            raise HTTPException(
                status_code=403,
                detail="This is not an admin account. Please use the Client Login page."
            )

        if expected_role == "client" and actual_role != "client":
            conn.close()
            raise HTTPException(
                status_code=403,
                detail="This is not a client account. Please use the Admin Login page."
            )

        conn.execute("""
        UPDATE users
        SET last_login_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (user["id"],))

        conn.commit()

        updated_user = conn.execute("""
        SELECT *
        FROM users
        WHERE id = ?
        LIMIT 1
    """, (user["id"],)).fetchone()

        safe_user = safe_user_dict(updated_user)

        raw_token = create_login_session(
            conn=conn,
            user_id=safe_user["id"],
            request=request
        )

        conn.close()

        redirect_to = default_redirect_for_user(safe_user)

        if next_url and isinstance(next_url, str) and next_url.startswith("/"):
            clean_next_path = next_url.split("?")[0]

            if (actual_role == "admin" and is_admin_path(clean_next_path)) or (actual_role == "client" and next_url.startswith("/client-portal")):
                redirect_to = next_url

        response = JSONResponse({
            "status": "logged_in",
            "user": safe_user,
            "redirect_to": redirect_to
        })

        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=raw_token,
            httponly=True,
            samesite="lax",
            secure=getattr(settings, "COOKIE_SECURE", False),
            max_age=SESSION_TTL_SECONDS
        )

        return response
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()

@router.get("/logout")
def logout(request: Request):
    revoke_current_session(request)

    response = RedirectResponse(url="/login", status_code=302)

    response.delete_cookie(SESSION_COOKIE_NAME)

    return response

@router.get("/me")
def me(request: Request):
    user = get_current_user_from_request(request)

    if not user:
        raise HTTPException(status_code=401, detail="Not logged in")

    return {
        "status": "logged_in",
        "user": user
    }

@router.get("/auth/status")
def auth_status():
    ensure_default_admin_user()

    conn = db()
    try:

        admin = conn.execute("""
        SELECT id, name, email, role, enabled, last_login_at, created_at
        FROM users
        WHERE email = ?
        LIMIT 1
    """, (ADMIN_EMAIL,)).fetchone()

        session_table = conn.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'auth_sessions'
    """).fetchone()

        conn.close()

        return {
            "status": "auth_foundation_ready",
            "admin_user": dict(admin) if admin else None,
            "session_table_exists": True if session_table else False,
            "session_cookie_name": SESSION_COOKIE_NAME,
            "session_ttl_seconds": SESSION_TTL_SECONDS,
            "password_hashing": {
                "algorithm": "PBKDF2-HMAC-SHA256",
                "iterations": PASSWORD_PBKDF2_ITERATIONS
            }
        }
    finally:
        conn.close()