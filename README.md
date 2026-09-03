# Image Inference

Planned asynchronous HTTP API for image inference. A request supplies an image URL, an inference method (`yolov8` or `pytesseract`), and a callback URL. Results will be delivered to that callback.

## Planned stack

- Python 3.11+
- UV for environment and dependency management
- FastAPI and Uvicorn for the HTTP service
- Pydantic for request and response validation
- `asyncio` with a maximum of 10 concurrent request workflows
- Ultralytics YOLOv8 for object detection
- Tesseract via `pytesseract` for OCR
- HTTPX for image download and callback delivery

## Planned layout

```text
src/
  main.py       # all API endpoints, task orchestration, and callback delivery
  config.py     # concurrency limit, timeouts, and model paths
  schemas.py    # Pydantic request and result schemas
  strategies/
    __init__.py
    base.py     # shared inference strategy interface
    yolov8.py   # YOLOv8 strategy
    tesseract.py # pytesseract strategy
tests/          # application tests
```

## Strategy pattern

The request's `method` field selects a strategy: `yolov8` selects the YOLOv8 strategy and `pytesseract` selects the Tesseract strategy. Both implement the same inference interface defined in `strategies/base.py`.

`main.py` owns all API endpoints and orchestrates validation, image download, strategy selection, concurrency control, and callback delivery. Strategies handle inference only and return results matching the schemas in `schemas.py`. A simple mapping from method names to strategy instances is sufficient; no separate factory module is planned.

Implementation has intentionally not started. Python files are empty placeholders.
