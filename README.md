# Image Inference

FastAPI service using UV, Pydantic, asyncio, Ultralytics YOLOv8, and pytesseract.
All endpoints, job orchestration, and callback delivery live in `src/main.py`.
Inference uses a shared strategy interface with separate YOLOv8 and Tesseract implementations.

## Setup

Run from the repository root with Python 3.11+ and UV installed:

```powershell
uv sync
uv run uvicorn src.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Open http://127.0.0.1:8000/docs for interactive API documentation.
Use a single worker: the concurrency limit and model instance are per process.

Tesseract is a separate system dependency; installing pytesseract does not install it.
Install the Tesseract executable and the language data you need, then add it to PATH or set
`INFERENCE_TESSERACT_CMD` to its absolute path. Verify installation with `tesseract --version`.
The default OCR language is English (`eng`).

The first YOLOv8 request loads `models/yolov8n.pt`. Ultralytics downloads the pretrained
weights if missing, so the first request requires network access and may take longer.
For offline use, put your YOLOv8 detection weights at the configured path beforehand.

## API

`GET /health` reports the service status and active job count. It does not check model
weights or the Tesseract installation.

`POST /infer` accepts:

```json
{
  "image_url": "https://example.com/image.jpg",
  "method": "yolov8",
  "callback_url": "https://example.com/inference-results"
}
```

The method must be `yolov8` or `pytesseract`. Both URLs must use HTTP or HTTPS.
Unknown fields are rejected. The endpoint returns HTTP 202 immediately:

```json
{
  "job_id": "f52c96d1-911a-4987-9fd3-e230ed046a21",
  "status": "accepted"
}
```

At most 10 jobs are active, counting download, inference, and callback attempts.
Additional requests receive HTTP 429 with `Retry-After: 1`; there is no waiting queue.
Synchronous image processing runs in worker threads. YOLO inference is serialized with
a lock around its shared model; OCR and network operations can overlap.

## Callbacks

Results are posted as JSON to the supplied callback URL:

```json
{
  "job_id": "f52c96d1-911a-4987-9fd3-e230ed046a21",
  "method": "yolov8",
  "status": "completed",
  "result": {
    "type": "yolov8",
    "detections": [
      {"class_id": 0, "label": "person", "confidence": 0.95, "bbox": [10, 20, 100, 200]}
    ]
  },
  "error": null
}
```

Bounding boxes are `[x1, y1, x2, y2]` pixel coordinates after EXIF orientation normalization.
OCR returns `{"type": "pytesseract", "text": "Recognized text"}` as the result.
Download or inference failures produce `status: "failed"`, `result: null`, and an error message.
Detailed errors are logged with the job ID.

The callback receiver must return a 2xx response. Failed deliveries are attempted up to
three times, with delays of 1 and 2 seconds. Each attempt includes an `Idempotency-Key`
header containing the job ID; receivers should deduplicate repeated deliveries.
Exhausted deliveries are logged. Image and callback URLs must be direct URLs; redirects
are not followed.

## Configuration

Environment variables or a local `.env` file override these defaults:

| Variable | Default |
| --- | --- |
| INFERENCE_MAX_CONCURRENT_REQUESTS | 10 (range 1–10) |
| INFERENCE_DOWNLOAD_TIMEOUT | 30 seconds |
| INFERENCE_CALLBACK_TIMEOUT | 15 seconds per attempt |
| INFERENCE_CALLBACK_ATTEMPTS | 3 |
| INFERENCE_MAX_IMAGE_BYTES | 20971520 (20 MiB) |
| INFERENCE_MAX_IMAGE_PIXELS | 25000000 |
| INFERENCE_YOLO_MODEL | models/yolov8n.pt |
| INFERENCE_YOLO_CONFIDENCE | 0.25 |
| INFERENCE_TESSERACT_CMD | unset; use PATH |
| INFERENCE_TESSERACT_LANGUAGE | eng |
| INFERENCE_TESSERACT_TIMEOUT | 60 seconds |

## Layout

```text
src/
  __init__.py
  main.py
  config.py
  schemas.py
  strategies/
    __init__.py
    base.py
    yolov8.py
    tesseract.py
tests/                  # reserved; no tests implemented
```

Jobs are held in memory. Graceful shutdown waits for active jobs; forced termination loses
them. There is no persistent queue or job-status store. YOLO inference has no hard execution
timeout. This initial service is intended for trusted callers: it has no authentication
or restrictions on which network destinations supplied URLs can reach.

Reference: [Ultralytics thread safety](https://docs.ultralytics.com/guides/yolo-thread-safe-inference/).
