import os
from fastapi import FastAPI, HTTPException, Security, Depends
from fastapi.security.api_key import APIKeyQuery, APIKeyHeader
from pydantic import BaseModel
from mycamu_api import MyCamuClient

app = FastAPI(title="Bennett Camulator API", description="1-Tap Instant Outpass Automation")

API_KEY = os.getenv("API_SECRET_KEY", "bennett-camu-key")
api_key_query = APIKeyQuery(name="key", auto_error=False)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

def verify_api_key(key_q: str = Security(api_key_query), key_h: str = Security(api_key_header)):
    if key_q == API_KEY or key_h == API_KEY:
        return True
    raise HTTPException(status_code=403, detail="Unauthorized: Invalid API Key")

client = MyCamuClient()

class PassRequest(BaseModel):
    reason: str = "Day Out"
    force: bool = False

@app.get("/")
def home():
    return {
        "status": "online",
        "app": "Bennett Camulator",
        "ready": True
    }

@app.get("/health")
def health_check():
    """Lightweight 200 OK endpoint for UptimeRobot 24/7 keep-alive pings."""
    return {"status": "ok"}

@app.get("/active-pass")
def get_active_pass_status(authorized: bool = Depends(verify_api_key)):
    """Checks whether user currently has an active pass or is clocked outside campus."""
    return client.get_active_pass()

@app.get("/quick-pass")
def quick_day_pass_get(reason: str = "Day Out", force: bool = False, authorized: bool = Depends(verify_api_key)):
    res = client.create_day_pass(reason=reason, ignore_active_warning=force)
    return res

@app.post("/quick-pass")
def quick_day_pass_post(req: PassRequest, authorized: bool = Depends(verify_api_key)):
    res = client.create_day_pass(reason=req.reason, ignore_active_warning=req.force)
    return res

@app.get("/attendance")
def get_attendance_stats(authorized: bool = Depends(verify_api_key)):
    """Returns live attendance metrics and bunk recommendations."""
    return client.get_attendance()

@app.get("/leaves")
def get_all_leaves(authorized: bool = Depends(verify_api_key)):
    """Returns past leaves & outpasses."""
    return {"leaves": client.get_leaves()}
