"""Settings page: API keys, default models and where model files live.

Only this computer may read or change settings, even when the service is
reachable from elsewhere: they include API keys and machine paths.
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.services import settings_store

router = APIRouter()

LOCAL_CLIENTS = {"127.0.0.1", "::1", "localhost"}


class SettingsChanges(BaseModel):
    # Only the settings that changed; an API key set to "" is removed.
    changes: dict[str, str] = Field(..., max_length=20)


def _only_this_computer(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in LOCAL_CLIENTS:
        raise HTTPException(status_code=403, detail="Settings can only be changed on this computer.")


@router.get("/settings")
def read_settings(request: Request):
    _only_this_computer(request)
    return settings_store.get_settings()


@router.put("/settings")
def change_settings(body: SettingsChanges, request: Request):
    _only_this_computer(request)
    try:
        return settings_store.save_settings(body.changes)
    except settings_store.SettingsError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
