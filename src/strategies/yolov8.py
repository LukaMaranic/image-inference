from pathlib import Path
from threading import Lock

from PIL.Image import Image
from ultralytics import YOLO

from src.schemas import Detection, YoloResult
from src.strategies.base import InferenceStrategy


class YoloV8Strategy(InferenceStrategy):
    def __init__(self, model_path: str, confidence: float):
        self.model_path = model_path
        self.confidence = confidence
        self._model: YOLO | None = None
        self._lock = Lock()

    def predict(self, image: Image) -> YoloResult:
        # Shared Ultralytics models must not execute concurrently.
        with self._lock:
            if self._model is None:
                Path(self.model_path).parent.mkdir(parents=True, exist_ok=True)
                self._model = YOLO(self.model_path)
            prediction = self._model.predict(source=image, conf=self.confidence, verbose=False)[0]
            detections = []
            if prediction.boxes is not None:
                for box in prediction.boxes:
                    class_id = int(box.cls.item())
                    detections.append(
                        Detection(
                            class_id=class_id,
                            label=prediction.names[class_id],
                            confidence=float(box.conf.item()),
                            bbox=tuple(box.xyxy[0].tolist()),
                        )
                    )
            return YoloResult(detections=detections)
