import os

import httpx
from fastapi import APIRouter
from fastapi.responses import RedirectResponse
from jose import jwt

router = APIRouter(prefix="/auth", tags=["auth"])

# BUG SEMBRADO (security / config_secrets): JWT_SECRET con fallback hardcodeado.
# Si la env var no esta seteada en produccion, cualquiera puede forjar tokens.
JWT_SECRET = os.getenv("JWT_SECRET", "changeme-reemplaza-esto")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
FRONTEND_URL = os.getenv("FRONTEND_URL", "*")


@router.get("/google/callback")
async def google_callback(code: str):
    # BUG SEMBRADO (io_operations): httpx.AsyncClient sin timeout. Una API externa
    # lenta o caida deja la operacion bloqueada indefinidamente.
    async with httpx.AsyncClient() as client:
        token_res = await client.post(
            "https://oauth2.googleapis.com/token",
            data={"code": code, "client_id": GOOGLE_CLIENT_ID},
        )
        user_res = await client.get("https://www.googleapis.com/oauth2/v3/userinfo")

    email = user_res.json().get("email")
    token = jwt.encode({"sub": email}, JWT_SECRET, algorithm="HS256")
    # BUG SEMBRADO (security): el JWT viaja como query param en la URL (queda en logs).
    return RedirectResponse(f"{FRONTEND_URL}/login?token={token}")
