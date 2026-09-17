import json
import os
from pathlib import PurePosixPath

import google.auth
from google.auth.transport.requests import AuthorizedSession
from fastapi import FastAPI, HTTPException, Request


PROJECT_ID = os.environ["GOOGLE_CLOUD_PROJECT"]
REGION = os.environ.get("TRANSCODER_REGION", "europe-west2")
SOURCE_BUCKET = os.environ["SOURCE_BUCKET"]
EGRESS_BUCKET = os.environ["EGRESS_BUCKET"]

app = FastAPI(title="ClearKey Transcoder Trigger")
credentials, _ = google.auth.default()
session = AuthorizedSession(credentials)


def submit_transcoder_job(object_name: str) -> str:
    video_id = PurePosixPath(object_name).stem
    input_uri = f"gs://{SOURCE_BUCKET}/{object_name}"
    output_uri = f"gs://{EGRESS_BUCKET}/outputs/{video_id}/"
    parent = f"projects/{PROJECT_ID}/locations/{REGION}"
    source_label = video_id.lower().replace("_", "-")[:63]

    existing = session.get(
        f"https://transcoder.googleapis.com/v1/{parent}/jobs",
        timeout=30,
    )
    if existing.ok:
        for existing_job in existing.json().get("jobs", []):
            if existing_job.get("labels", {}).get("source_object") == source_label:
                return existing_job["name"]

    job = {
        "inputUri": input_uri,
        "outputUri": output_uri,
        "config": {
            "elementaryStreams": [
                {
                    "key": "video-stream0",
                    "videoStream": {
                        "h264": {
                            "heightPixels": 720,
                            "widthPixels": 1280,
                            "bitrateBps": 5000000,
                            "frameRate": 30,
                        }
                    },
                },
            ],
            "muxStreams": [
                {
                    "key": "video",
                    "container": "fmp4",
                    "elementaryStreams": ["video-stream0"],
                    "segmentSettings": {"segmentDuration": "6s"},
                },
            ],
            "manifests": [
                {
                    "fileName": "dash_clearkey.mpd",
                    "type": "DASH",
                    "muxStreams": ["video"],
                }
            ],
        },
        "labels": {
            "source_object": source_label,
        },
    }

    response = session.post(
        f"https://transcoder.googleapis.com/v1/{parent}/jobs",
        json=job,
        timeout=30,
    )
    if not response.ok:
        raise HTTPException(
            status_code=502,
            detail=f"Transcoder API rejected the job: {response.text}",
        )
    return response.json()["name"]


@app.post("/")
async def handle_storage_event(request: Request):
    event = await request.json()
    event_data = event.get("data", event)
    bucket = event_data.get("bucket")
    object_name = event_data.get("name", "")

    if bucket != SOURCE_BUCKET:
        return {"status": "ignored", "reason": "event is not from the source bucket"}
    if not object_name.lower().endswith(".mp4"):
        return {"status": "ignored", "reason": "object is not an MP4"}

    job_name = submit_transcoder_job(object_name)
    return {"status": "submitted", "job": job_name}


@app.get("/health")
def health_check():
    return {"status": "healthy"}