"""A mentor's voice bank, and kits that move a mentor to another computer."""

import asyncio

from fastapi import APIRouter, HTTPException

from backend.api.v1.endpoints.talk import _known_mentor
from backend.services import mentor_kits, voice_bank

router = APIRouter()


@router.get("/mentors/{name}/voice-bank")
def bank_status(name: str):
    """How many of the mentor's phrases are recorded, and how many live sentences are kept."""
    return voice_bank.status(_known_mentor(name))


@router.post("/mentors/{name}/voice-bank")
async def record_bank(name: str):
    """Record the mentor's phrases in the background (recorded ones are skipped)."""
    mentor = _known_mentor(name)
    voice_bank.build(mentor)
    return voice_bank.status(mentor)


@router.delete("/mentors/{name}/voice-bank/recording", status_code=204)
def stop_recording(name: str):
    voice_bank.stop(_known_mentor(name))


@router.post("/mentors/{name}/kit")
async def export_kit(name: str):
    """Write the mentor and its voice bank to mentor_kits/<name>.zip on this computer."""
    try:
        return await asyncio.to_thread(mentor_kits.export_kit, _known_mentor(name))
    except mentor_kits.KitError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/mentor-kits")
def kits_folder():
    return {"folder": mentor_kits.kits_dir()}


@router.post("/mentor-kits/import")
async def import_kits():
    """Import the kits copied into the mentor_kits folder."""
    return await asyncio.to_thread(mentor_kits.import_waiting)
