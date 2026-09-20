from pathlib import Path
import shutil

import pytesseract


class OcrConfigurationError(RuntimeError):
    pass


class OcrService:
    def __init__(self, tesseract_cmd: str | Path | None = None):
        located = tesseract_cmd or shutil.which("tesseract") or Path("C:/Program Files/Tesseract-OCR/tesseract.exe")
        path = Path(located)
        if not path.is_file():
            raise OcrConfigurationError(f"Tesseract executable not found: {path}")
        pytesseract.pytesseract.tesseract_cmd = str(path)
        self.config = "--psm 7"

    def read(self, image):
        return pytesseract.image_to_string(image, config=self.config)
