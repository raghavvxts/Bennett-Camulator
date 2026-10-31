import os
import secrets
import database
from datetime import datetime
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Security, Depends
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel
from mycamu_api import MyCamuClient

load_dotenv()
app = FastAPI(title="Bennett Camulator API", description="1-Tap Instant Outpass Automation")

API_KEY = os.getenv("API_SECRET_KEY")
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

def verify_api_key(key: str = Security(api_key_header)):
    if key and API_KEY and secrets.compare_digest(key, API_KEY):
        return None
    if key:
        user = database.shortcut_user(key)
        if user:
            return user
    if not API_KEY and not key:
        raise HTTPException(status_code=503, detail="Supply your personal shortcut key from /shortcut.")
    raise HTTPException(status_code=403, detail="Unauthorized: Invalid API Key")

def get_client(user=None):
    if user:
        return MyCamuClient(email=user['email'], password=user['password'], cookies=user['cookies'],
                            stu_id=user['stu_id'], in_id=user['in_id'], student_name=user['student_name'])
    return MyCamuClient(email=os.getenv("PARENT_EMAIL"), password=os.getenv("PARENT_PASSWORD"))

class PassRequest(BaseModel):
    reason: str = "Going out"
    departure_at: datetime | None = None
    force: bool = False

@app.get("/")
def home():
    return {
        "status": "online",
        "app": "Bennett Camulator",
        "ready": True,
        "owner_shortcuts_configured": bool(API_KEY)
    }

@app.get("/health")
def health_check():
    if os.getenv("TELEGRAM_BOT_TOKEN") and database.heartbeat_age("telegram") > 60:
        raise HTTPException(status_code=503, detail="Telegram bot heartbeat is missing or stale.")
    return {"status": "ok", "telegram": "healthy" if os.getenv("TELEGRAM_BOT_TOKEN") else "disabled"}

@app.get("/active-pass")
def get_active_pass_status(authorized: dict | None = Depends(verify_api_key)):
    """Checks whether user currently has an active pass or is clocked outside campus."""
    return get_client(authorized).get_active_pass()

@app.post("/quick-pass")
def quick_day_pass_post(req: PassRequest, authorized: dict | None = Depends(verify_api_key)):
    res = get_client(authorized).create_day_pass(reason=req.reason, departure_at=req.departure_at, ignore_active_warning=req.force)
    return res

@app.get("/attendance")
def get_attendance_stats(authorized: dict | None = Depends(verify_api_key)):
    """Returns live attendance metrics and bunk recommendations."""
    return get_client(authorized).get_attendance()

@app.get("/leaves")
def get_all_leaves(authorized: dict | None = Depends(verify_api_key)):
    """Returns past leaves & outpasses."""
    try:
        return {"leaves": get_client(authorized).get_leaves()}
    except Exception:
        raise HTTPException(status_code=502, detail="Could not retrieve MyCamu leave and gate-pass records.")
