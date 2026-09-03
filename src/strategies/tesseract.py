import pytesseract
from PIL.Image import Image

from src.schemas import OCRResult
from src.strategies.base import InferenceStrategy


class TesseractStrategy(InferenceStrategy):
    def __init__(self, language: str, timeout: float, executable: str | None = None):
        self.language = language
        self.timeout = timeout
        if executable:
            pytesseract.pytesseract.tesseract_cmd = executable

    def predict(self, image: Image) -> OCRResult:
        text = pytesseract.image_to_string(image, lang=self.language, timeout=self.timeout)
        return OCRResult(text=text)
