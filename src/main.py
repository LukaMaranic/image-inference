import asyncio
import logging
from contextlib import asynccontextmanager
from io import BytesIO
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI, HTTPException, Request, status
from PIL import Image, ImageOps, UnidentifiedImageError

from src.config import Settings
from src.schemas import AcceptedJob, CallbackPayload, InferenceRequest, Method
from src.strategies.base import InferenceStrategy
from src.strategies.tesseract import TesseractStrategy
from src.strategies.yolov8 import YoloV8Strategy

logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()
    app.state.settings = settings
    app.state.jobs = set()
    app.state.accepting = True
    app.state.strategies = {
        Method.YOLOV8: YoloV8Strategy(settings.yolo_model, settings.yolo_confidence),
        Method.PYTESSERACT: TesseractStrategy(
            settings.tesseract_language,
            settings.tesseract_timeout,
            settings.tesseract_cmd,
        ),
    }
    async with httpx.AsyncClient(follow_redirects=False) as client:
        app.state.client = client
        try:
            yield
        finally:
            app.state.accepting = False
            # Keep the client alive until accepted jobs have delivered their callbacks.
            if app.state.jobs:
                await asyncio.gather(*tuple(app.state.jobs), return_exceptions=True)


app = FastAPI(title="Image Inference", lifespan=lifespan)


async def download_image(client: httpx.AsyncClient, url: str, settings: Settings) -> bytes:
    async with asyncio.timeout(settings.download_timeout):
        async with client.stream("GET", url, timeout=settings.download_timeout) as response:
            response.raise_for_status()
            content = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                if len(content) + len(chunk) > settings.max_image_bytes:
                    raise ValueError("Image exceeds the download size limit")
                content.extend(chunk)
            return bytes(content)


def run_inference(data: bytes, strategy: InferenceStrategy, settings: Settings):
    try:
        with Image.open(BytesIO(data)) as source:
            if source.width * source.height > settings.max_image_pixels:
                raise ValueError("Image exceeds the pixel limit")
            # Normalize EXIF orientation and ensure engines receive RGB images.
            with ImageOps.exif_transpose(source) as oriented, oriented.convert("RGB") as image:
                return strategy.predict(image)
    except UnidentifiedImageError as exc:
        raise ValueError("Downloaded content is not a supported image") from exc


async def deliver_callback(
    client: httpx.AsyncClient, url: str, payload: CallbackPayload, settings: Settings
) -> None:
    for attempt in range(settings.callback_attempts):
        try:
            async with asyncio.timeout(settings.callback_timeout):
                # Stream the response: callback bodies are not needed.
                async with client.stream(
                    "POST",
                    url,
                    json=payload.model_dump(mode="json"),
                    headers={"Idempotency-Key": str(payload.job_id)},
                    timeout=settings.callback_timeout,
                ) as response:
                    response.raise_for_status()
            return
        except (httpx.HTTPError, TimeoutError):
            if attempt + 1 == settings.callback_attempts:
                raise
            await asyncio.sleep(2**attempt)


async def process_job(app: FastAPI, job_id: UUID, body: InferenceRequest) -> None:
    settings = app.state.settings
    try:
        data = await download_image(app.state.client, str(body.image_url), settings)
        result = await asyncio.to_thread(
            run_inference, data, app.state.strategies[body.method], settings
        )
        payload = CallbackPayload(
            job_id=job_id, method=body.method, status="completed", result=result
        )
    except Exception:
        logger.exception("Inference failed for job %s", job_id)
        payload = CallbackPayload(
            job_id=job_id,
            method=body.method,
            status="failed",
            error="Image download or inference failed; see server logs for details",
        )
    try:
        await deliver_callback(app.state.client, str(body.callback_url), payload, settings)
        logger.info("Callback delivered for job %s (%s)", job_id, payload.status)
    except Exception:
        logger.exception("Callback delivery exhausted for job %s", job_id)


@app.get("/health")
async def health(request: Request) -> dict:
    return {
        "status": "ok",
        "active_jobs": len(request.app.state.jobs),
        "max_concurrent_requests": request.app.state.settings.max_concurrent_requests,
    }

@app.post("/callback", summary="Echo callback payload (testing only)")
async def callback(payload: dict) -> dict:
    logger.info("Testing callback received: %s", payload)
    return payload

@app.post("/infer", response_model=AcceptedJob, status_code=status.HTTP_202_ACCEPTED)
async def infer(body: InferenceRequest, request: Request) -> AcceptedJob:
    state = request.app.state
    if not state.accepting:
        raise HTTPException(status_code=503, detail="Server is shutting down")
    if len(state.jobs) >= state.settings.max_concurrent_requests:
        raise HTTPException(
            status_code=429,
            detail="All inference slots are occupied; retry later",
            headers={"Retry-After": "1"},
        )

    # No await between checking capacity and reserving it: atomic on this event loop.
    job_id = uuid4()
    task = asyncio.create_task(process_job(request.app, job_id, body), name=str(job_id))
    state.jobs.add(task)
    task.add_done_callback(state.jobs.discard)
    return AcceptedJob(job_id=job_id)
