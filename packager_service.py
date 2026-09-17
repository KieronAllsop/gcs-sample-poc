import asyncio
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from google.cloud import storage


STORAGE_BUCKET = os.environ["STORAGE_BUCKET"]
CLEAR_KEY = os.environ["CLEAR_KEY_TEST_VALUE"]
KEY_ID = os.environ.get("CLEAR_KEY_ID", "00000000000000000000000000000001")
CLEAR_KEY_SYSTEM_ID = "1077efec-c0b2-4d02-ace3-3c1e52e2fb4b"

app = FastAPI(title="ClearKey Packager")
storage_client = storage.Client()


def patch_clearkey_signaling(manifest: str) -> str:
    kid = KEY_ID
    signaling = (
        f'<ContentProtection cenc:default_KID="{kid}" '
        f'schemeIdUri="urn:uuid:{CLEAR_KEY_SYSTEM_ID}" value="ClearKey1.0"/>'
    )
    if "schemeIdUri=\"urn:uuid:1077efec-c0b2-4d02-ace3-3c1e52e2fb4b\"" in manifest:
        return manifest
    marker = "</AdaptationSet>"
    if marker not in manifest:
        raise ValueError("Packager output does not contain an AdaptationSet")
    return manifest.replace(marker, f"  {signaling}\n  {marker}", 1)


def download_blob(blob_name: str, destination: Path) -> None:
    storage_client.bucket(STORAGE_BUCKET).blob(blob_name).download_to_filename(destination)


def upload_file(source: Path, blob_name: str, content_type: str) -> None:
    blob = storage_client.bucket(STORAGE_BUCKET).blob(blob_name)
    blob.upload_from_filename(source, content_type=content_type)


def package_video(video_id: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{32}", CLEAR_KEY):
        raise ValueError("CLEAR_KEY_TEST_VALUE must contain 32 hexadecimal characters")
    if not re.fullmatch(r"[0-9a-fA-F]{32}", KEY_ID):
        raise ValueError("CLEAR_KEY_ID must contain 32 hexadecimal characters")

    source_prefix = f"outputs/{video_id}"
    output_prefix = f"{source_prefix}/encrypted"
    with tempfile.TemporaryDirectory() as temporary_directory:
        workdir = Path(temporary_directory)
        input_file = workdir / "input.mp4"
        init_segment = workdir / "init.mp4"
        media_segment = workdir / "segment_$Number$.m4s"
        manifest_file = workdir / "dash_clearkey.mpd"

        source_objects = list(
            storage_client.list_blobs(STORAGE_BUCKET, prefix=f"{source_prefix}/")
        )
        input_blob = next(
            (blob for blob in source_objects if blob.name.endswith(".m4s")), None
        )
        if input_blob is None:
            raise FileNotFoundError(f"No Transcoder fMP4 found under {source_prefix}")
        download_blob(input_blob.name, input_file)

        command = [
            "/usr/local/bin/packager",
            f"in={input_file},stream=video,init_segment={init_segment},segment_template={media_segment},drm_label=SD",
            "--enable_raw_key_encryption",
            f"--keys=label=SD:key_id={KEY_ID}:key={CLEAR_KEY}",
            "--protection_scheme=cenc",
            f"--mpd_output={manifest_file}",
        ]
        subprocess.run(command, check=True, capture_output=True, text=True)

        manifest = patch_clearkey_signaling(manifest_file.read_text(encoding="utf-8"))
        manifest_file.write_text(manifest, encoding="utf-8")
        upload_file(manifest_file, f"{output_prefix}/dash_clearkey.mpd", "application/dash+xml")
        upload_file(init_segment, f"{output_prefix}/init.mp4", "video/mp4")
        for segment in workdir.glob("segment_*.m4s"):
            upload_file(segment, f"{output_prefix}/{segment.name}", "video/iso.segment")

    return f"{output_prefix}/dash_clearkey.mpd"


@app.post("/package/{video_id}")
async def package_endpoint(video_id: str):
    try:
        manifest = await asyncio.to_thread(package_video, video_id)
        return {"status": "packaged", "manifest": manifest}
    except (FileNotFoundError, ValueError, subprocess.CalledProcessError) as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@app.post("/")
async def storage_event_endpoint(request: Request):
    event = await request.json()
    event_data = event.get("data", event)
    object_name = event_data.get("name", "")
    expected_suffix = "/dash_clearkey.mpd"
    if not object_name.endswith(expected_suffix):
        return {"status": "ignored", "reason": "event is not a Transcoder manifest"}

    parts = object_name.split("/")
    if len(parts) != 3 or parts[0] != "outputs":
        return {"status": "ignored", "reason": "unexpected output path"}

    try:
        manifest = await asyncio.to_thread(package_video, parts[1])
        return {"status": "packaged", "manifest": manifest}
    except (FileNotFoundError, ValueError, subprocess.CalledProcessError) as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@app.get("/health")
def health_check():
    return {"status": "healthy"}