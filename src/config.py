from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INFERENCE_", env_file=".env", extra="ignore")

    max_concurrent_requests: int = Field(default=10, ge=1, le=10)
    download_timeout: float = Field(default=30, gt=0)
    callback_timeout: float = Field(default=15, gt=0)
    callback_attempts: int = Field(default=3, ge=1, le=10)
    max_image_bytes: int = Field(default=20 * 1024 * 1024, gt=0)
    max_image_pixels: int = Field(default=25_000_000, gt=0)
    yolo_model: str = "models/yolov8n.pt"
    yolo_confidence: float = Field(default=0.25, ge=0, le=1)
    tesseract_cmd: str | None = None
    tesseract_language: str = "eng"
    tesseract_timeout: float = Field(default=60, gt=0)
