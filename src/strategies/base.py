from abc import ABC, abstractmethod

from PIL.Image import Image

from src.schemas import OCRResult, YoloResult


class InferenceStrategy(ABC):
    @abstractmethod
    def predict(self, image: Image) -> YoloResult | OCRResult:
        """Run synchronous inference; callers must offload this from the event loop."""
