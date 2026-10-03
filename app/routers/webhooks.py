import hmac

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException

from app.config import settings
from app.main import process_ttn_webhook_background

router = APIRouter()


@router.post("/ttn-webhook")
def ttn_webhook(
    data: dict,
    background_tasks: BackgroundTasks,
    x_webhook_secret: str | None = Header(default=None, alias="X-Webhook-Secret"),
):
    expected = (settings.TTN_WEBHOOK_SECRET or "").strip()

    # Fail closed. Previously an unset secret skipped verification entirely,
    # so anyone could POST arbitrary telemetry straight into the processing
    # queue -- and the secret is unset by default.
    if not expected:
        raise HTTPException(
            status_code=503,
            detail=(
                "Webhook rejected: TTN_WEBHOOK_SECRET is not configured. "
                "Set it in the environment and in the TTN webhook headers."
            ),
        )

    provided = x_webhook_secret or ""
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid webhook secret")

    background_tasks.add_task(process_ttn_webhook_background, data)
    return {
        "status": "queued",
        "message": "Payload queued for background processing",
    }
