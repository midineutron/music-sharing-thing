import asyncio
import glob
import json
import os
import re
import shutil
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import yt_dlp
from audio_separator.separator import Separator
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "AIzaSyDfa98VU-MmZ8AUsnH3H4Fa-NlikKWpgEQ")
EXECUTOR = ThreadPoolExecutor(max_workers=2)
jobs: dict[str, dict] = {}

app = FastAPI()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class VideoItem(BaseModel):
    id: str
    title: str


class CreateJobRequest(BaseModel):
    videos: list[VideoItem]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_video_id(vid_id: str) -> bool:
    return bool(re.fullmatch(r"[a-zA-Z0-9_\-]{11}", vid_id))


def download_audio(video_id: str, temp_dir: str) -> str:
    """Download best audio from YouTube and convert to WAV. Returns WAV path."""
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(temp_dir, f"{video_id}.%(ext)s"),
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "wav",
            "preferredquality": "0",
        }],
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([f"https://www.youtube.com/watch?v={video_id}"])

    wav_path = os.path.join(temp_dir, f"{video_id}.wav")
    if not os.path.exists(wav_path):
        candidates = glob.glob(os.path.join(temp_dir, f"{video_id}.*"))
        if not candidates:
            raise RuntimeError(f"yt-dlp produced no output for {video_id}")
        wav_path = candidates[0]
    return wav_path


def isolate_vocals(audio_path: str, output_dir: str) -> str:
    """Run audio-separator and return path to the vocals stem WAV."""
    separator = Separator(output_dir=output_dir, output_format="WAV")
    separator.load_model()
    output_files = separator.separate(audio_path)

    vocals = [f for f in output_files if "Vocals" in os.path.basename(f)]
    if not vocals:
        # Fallback: second file is usually vocals (index 1)
        vocals = [output_files[1]] if len(output_files) >= 2 else output_files

    return vocals[0]


def cleanup_job(job_id: str) -> None:
    job = jobs.pop(job_id, None)
    if job and job.get("temp_dir"):
        shutil.rmtree(job["temp_dir"], ignore_errors=True)


# ---------------------------------------------------------------------------
# Background job processor
# ---------------------------------------------------------------------------

async def process_job(job_id: str) -> None:
    job = jobs[job_id]
    job["status"] = "running"
    loop = asyncio.get_event_loop()

    async def push(event: dict) -> None:
        await job["events"].put(event)

    try:
        isolated_files: list[str] = []

        for video in job["videos"]:
            vid_id = video["id"]
            title = video["title"]

            # Download
            await push({"videoId": vid_id, "title": title, "step": "downloading", "progress": 0, "final": False})
            audio_path = await loop.run_in_executor(EXECUTOR, download_audio, vid_id, job["temp_dir"])
            await push({"videoId": vid_id, "title": title, "step": "downloading", "progress": 100, "final": False})

            # Isolate
            await push({"videoId": vid_id, "title": title, "step": "isolating", "progress": 0, "final": False})
            vocals_path = await loop.run_in_executor(EXECUTOR, isolate_vocals, audio_path, job["temp_dir"])
            await push({"videoId": vid_id, "title": title, "step": "isolating", "progress": 100, "final": False})

            await push({"videoId": vid_id, "title": title, "step": "done", "progress": 100, "final": False})
            isolated_files.append(vocals_path)

        # Assemble result
        if len(isolated_files) == 1:
            result_path = isolated_files[0]
        else:
            zip_path = os.path.join(job["temp_dir"], "vocals.zip")
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for f in isolated_files:
                    zf.write(f, arcname=os.path.basename(f))
            result_path = zip_path

        job["result_path"] = result_path
        job["status"] = "done"
        await push({"step": "complete", "videoId": "", "title": "", "progress": 100, "final": True})

    except Exception as exc:
        job["status"] = "error"
        job["error"] = str(exc)
        await push({"step": "error", "videoId": "", "title": "", "progress": 0,
                    "message": str(exc), "final": True})


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/search")
async def search(q: str) -> dict:
    url = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "part": "snippet",
        "type": "video",
        "maxResults": 10,
        "q": q,
        "key": YOUTUBE_API_KEY,
    }
    async with httpx.AsyncClient() as client:
        r = await client.get(url, params=params, timeout=10)
    data = r.json()
    if "error" in data:
        raise HTTPException(502, detail=data["error"]["message"])
    items = data.get("items", [])
    return {
        "results": [
            {
                "id": item["id"]["videoId"],
                "title": item["snippet"]["title"],
                "channel": item["snippet"]["channelTitle"],
                "thumbnail": (
                    item["snippet"]["thumbnails"].get("medium", {}).get("url")
                    or item["snippet"]["thumbnails"].get("default", {}).get("url", "")
                ),
            }
            for item in items
        ]
    }


@app.post("/jobs")
async def create_job(body: CreateJobRequest, background_tasks: BackgroundTasks) -> dict:
    for v in body.videos:
        if not _valid_video_id(v.id):
            raise HTTPException(400, detail=f"Invalid video ID: {v.id!r}")

    job_id = str(uuid.uuid4())
    temp_dir = f"/tmp/vocal-isolator/{job_id}"
    os.makedirs(temp_dir, exist_ok=True)

    jobs[job_id] = {
        "status": "pending",
        "videos": [{"id": v.id, "title": v.title} for v in body.videos],
        "temp_dir": temp_dir,
        "result_path": None,
        "events": asyncio.Queue(),
        "error": None,
    }
    background_tasks.add_task(process_job, job_id)
    return {"job_id": job_id}


@app.get("/jobs/{job_id}/progress")
async def job_progress(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404)

    async def stream():
        queue: asyncio.Queue = jobs[job_id]["events"]
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            yield f"data: {json.dumps(event)}\n\n"
            if event.get("final"):
                break

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/jobs/{job_id}/download")
async def job_download(job_id: str, background_tasks: BackgroundTasks):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404)
    if job["status"] != "done":
        raise HTTPException(409, detail="Job not complete")
    result_path = job.get("result_path")
    if not result_path or not os.path.exists(result_path):
        raise HTTPException(500, detail="Result file missing")

    filename = os.path.basename(result_path)
    media_type = "audio/wav" if filename.endswith(".wav") else "application/zip"

    background_tasks.add_task(cleanup_job, job_id)
    return FileResponse(result_path, media_type=media_type, filename=filename)


# ---------------------------------------------------------------------------
# Startup warmup
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def warmup():
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(EXECUTOR, lambda: Separator().load_model())


# Serve frontend (must be mounted last so API routes take priority)
app.mount("/", StaticFiles(directory=str(Path(__file__).parent / "static"), html=True), name="static")
