from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class Method(StrEnum):
    YOLOV8 = "yolov8"
    PYTESSERACT = "pytesseract"


class InferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_url: HttpUrl
    method: Method
    callback_url: HttpUrl


class AcceptedJob(BaseModel):
    job_id: UUID
    status: Literal["accepted"] = "accepted"


class Detection(BaseModel):
    class_id: int
    label: str
    confidence: float
    bbox: tuple[float, float, float, float] = Field(description="Pixel coordinates: x1, y1, x2, y2")


class YoloResult(BaseModel):
    type: Literal["yolov8"] = "yolov8"
    detections: list[Detection]


class OCRResult(BaseModel):
    type: Literal["pytesseract"] = "pytesseract"
    text: str


class CallbackPayload(BaseModel):
    job_id: UUID
    method: Method
    status: Literal["completed", "failed"]
    result: YoloResult | OCRResult | None = None
    error: str | None = None
