"""Mentor kits: one file that moves a mentor to another computer.

A kit is a .zip holding the mentor's folder (photos, clips, voice recording,
personality) and everything made from it: the voice bank and prepared lessons.
It is written to and read from the mentor_kits folder next to ai_service, so
the voice recording never leaves this computer through the web page. To move a
mentor, copy its kit into the other computer's mentor_kits folder and press
"Import kits" in the Studio (kits are also imported when the app starts).

Because the bank names its files after the content of the voice and clips (not
their dates), everything in a kit plays at once on the new computer.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
import zipfile

from backend.services import lesson_packs, media_processor, voice_bank

KIT_FORMAT = 1
KIT_FILE = "kit.json"
IMPORTED_FILE = "imported.json"
MENTOR_FILE = re.compile(r"^mentor/([A-Za-z0-9][A-Za-z0-9._-]{0,99})$")
MENTOR_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".mp4", ".wav", ".mp3",
                     ".m4a", ".flac", ".webm", ".ogg", ".json"}
BANK_FILE = re.compile(
    r"^bank/(bank\.json|format\.json|scripts/[0-9a-f]{16}\.json|media/[0-9a-f]{16}\.(?:wav|mp3|mp4))$"
)
MAX_MEMBER_BYTES = 2 * 1024**3  # one file
MAX_KIT_BYTES = 50 * 1024**3  # everything, once unpacked
MAX_RATIO = 200  # unpacked size per packed byte; more means a zip bomb


class KitError(Exception):
    pass


def kits_dir() -> str:
    """The folder kits are written to and imported from (next to ai_service)."""
    return os.path.join(os.path.dirname(media_processor.BASE_DIR), "mentor_kits")


def _input_dir(mentor: str) -> str:
    return os.path.join(media_processor.BASE_DIR, "backend", "input", mentor)


def export_kit(mentor: str) -> dict:
    """Write mentor_kits/<mentor>.zip; return its path and size."""
    source = _input_dir(mentor)
    if not os.path.isdir(source):
        raise KitError("Mentor not found.")
    lesson_packs.MentorSetup(mentor)  # brings old packs up to the current format first
    os.makedirs(kits_dir(), exist_ok=True)
    target = os.path.join(kits_dir(), f"{mentor}.zip")
    handle, temp = tempfile.mkstemp(suffix=".zip.tmp", dir=kits_dir())
    os.close(handle)
    try:
        with zipfile.ZipFile(temp, "w") as kit:
            info = {"format": KIT_FORMAT, "name": mentor, "created": int(time.time())}
            kit.writestr(KIT_FILE, json.dumps(info))
            for name in sorted(os.listdir(source)):
                path = os.path.join(source, name)
                if os.path.isfile(path) and _mentor_file_ok(name):
                    kit.write(path, f"mentor/{name}", compress_type=_compression(name))
            bank = lesson_packs.mentor_dir(mentor)
            for folder, _dirs, files in os.walk(bank):
                for name in sorted(files):
                    path = os.path.join(folder, name)
                    member = "bank/" + os.path.relpath(path, bank).replace(os.sep, "/")
                    if BANK_FILE.fullmatch(member):
                        kit.write(path, member, compress_type=_compression(name))
        os.replace(temp, target)
        remember_export(target)
    finally:
        if os.path.exists(temp):
            os.remove(temp)
    return {"path": target, "bytes": os.path.getsize(target)}


def _compression(name: str) -> int:
    # Audio and video are compressed already; zipping them again only costs time.
    return zipfile.ZIP_DEFLATED if name.endswith((".json", ".wav")) else zipfile.ZIP_STORED


def _mentor_file_ok(name: str) -> bool:
    return bool(MENTOR_FILE.fullmatch(f"mentor/{name}")) and (
        os.path.splitext(name)[1].lower() in MENTOR_EXTENSIONS
    )


def _checked_members(kit: zipfile.ZipFile) -> tuple[str, list[zipfile.ZipInfo]]:
    """The mentor's name and the members that may be unpacked; raise KitError if unsafe."""
    try:
        info = json.loads(kit.read(KIT_FILE))
        name = str(info["name"]).strip().lower()
    except (KeyError, ValueError, TypeError) as error:
        raise KitError("This is not a mentor kit.") from error
    if info.get("format") != KIT_FORMAT:
        raise KitError("This kit was made by a different version of the app.")
    if not media_processor.MENTOR_NAME_PATTERN.fullmatch(name):
        raise KitError("The kit's mentor name is not valid.")
    members, total = [], 0
    for member in kit.infolist():
        if member.is_dir() or member.filename == KIT_FILE:
            continue
        mentor_file = MENTOR_FILE.fullmatch(member.filename)
        if not (BANK_FILE.fullmatch(member.filename)
                or (mentor_file and _mentor_file_ok(mentor_file.group(1)))):
            continue  # anything else in the zip is ignored, never unpacked
        if member.file_size > MAX_MEMBER_BYTES or (
            member.file_size > 1024**2 and member.file_size > MAX_RATIO * max(1, member.compress_size)
        ):
            raise KitError(f"The kit holds a file that is too large: {member.filename}")
        total += member.file_size
        members.append(member)
    if total > MAX_KIT_BYTES:
        raise KitError("The kit is too large.")
    return name, members


def import_kit(path: str) -> dict:
    """Unpack a kit. A new mentor is created; for a mentor that exists already,
    only bank files it does not have are added and its own files stay as they are."""
    try:
        kit = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as error:
        raise KitError("This file is not a readable .zip.") from error
    with kit:
        name, members = _checked_members(kit)
        mentor_folder = _input_dir(name)
        new_mentor = not os.path.isdir(mentor_folder)
        has_bank = os.path.isdir(lesson_packs.mentor_dir(name))
        added = 0
        for member in members:
            relative = member.filename.split("/", 1)[1]
            if member.filename.startswith("mentor/"):
                if not new_mentor:
                    continue
                target = os.path.join(mentor_folder, relative)
            else:
                if relative == "bank.json" and has_bank:
                    continue  # merged below instead
                target = os.path.join(lesson_packs.mentor_dir(name), *relative.split("/"))
            if os.path.exists(target):
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            temp = target + ".part"
            with kit.open(member) as source, open(temp, "wb") as out:
                shutil.copyfileobj(source, out, 1 << 20)
            os.replace(temp, target)
            added += 1
        if has_bank and "bank/bank.json" in kit.namelist():
            _merge_bank(name, json.loads(kit.read("bank/bank.json")).get("entries", {}))
    voice_bank.forget(name)
    return {"name": name, "new_mentor": new_mentor, "files_added": added}


def _merge_bank(mentor: str, entries: dict) -> None:
    for entry in entries.values() if isinstance(entries, dict) else []:
        if isinstance(entry, dict) and isinstance(entry.get("text"), str):
            voice_bank.add(mentor, entry["text"], str(entry.get("source") or "phrase"))


def import_waiting() -> dict:
    """Import every kit in the mentor_kits folder not imported before."""
    folder = kits_dir()
    record_path = os.path.join(folder, IMPORTED_FILE)
    try:
        with open(record_path, encoding="utf-8") as record_file:
            record = json.load(record_file)
    except (OSError, ValueError):
        record = {}
    imported, failed = [], []
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        path = os.path.join(folder, name)
        if not name.lower().endswith(".zip") or not os.path.isfile(path):
            continue
        stat = os.stat(path)
        stamp = [stat.st_size, stat.st_mtime_ns]
        if record.get(name) == stamp:
            continue  # imported already, or written by this computer's export
        try:
            imported.append(import_kit(path))
            record[name] = stamp
        except KitError as error:
            failed.append({"file": name, "error": str(error)})
    if imported:
        with open(record_path, "w", encoding="utf-8") as record_file:
            json.dump(record, record_file, indent=1)
    return {"folder": folder, "imported": imported, "failed": failed}


def remember_export(path: str) -> None:
    """Mark a kit this computer wrote, so it is not imported back."""
    folder, name = os.path.split(path)
    record_path = os.path.join(folder, IMPORTED_FILE)
    try:
        with open(record_path, encoding="utf-8") as record_file:
            record = json.load(record_file)
    except (OSError, ValueError):
        record = {}
    stat = os.stat(path)
    record[name] = [stat.st_size, stat.st_mtime_ns]
    with open(record_path, "w", encoding="utf-8") as record_file:
        json.dump(record, record_file, indent=1)
