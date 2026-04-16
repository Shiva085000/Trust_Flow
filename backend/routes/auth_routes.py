from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import httpx
from auth import create_access_token, create_refresh_token, verify_token

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

class FirebaseTokenRequest(BaseModel):
    firebase_token: str

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = 1800

@router.post("/google", response_model=TokenResponse)
async def google_login(req: FirebaseTokenRequest):
    # Verify Firebase token with Google
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://oauth2.googleapis.com/tokeninfo?id_token={req.firebase_token}"
        )
        if resp.status_code != 200:
            raise HTTPException(401, "Invalid Firebase token")
        
        google_data = resp.json()
        user_id = google_data.get("sub")
        email = google_data.get("email", "")
        
        if not user_id:
            raise HTTPException(401, "Could not extract user ID")
    
    return TokenResponse(
        access_token=create_access_token(user_id, email),
        refresh_token=create_refresh_token(user_id)
    )

@router.post("/refresh")
async def refresh_token(refresh_token: str):
    payload = verify_token(refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(401, "Invalid refresh token")
    
    new_access = create_access_token(payload["sub"], "")
    return {"access_token": new_access, "token_type": "bearer"}
