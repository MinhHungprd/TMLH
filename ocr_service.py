from pathlib import Path
import shutil
import threading
import time

import pytesseract

from perf_metrics import record_perf_ms


# Tesseract starts an external process per OCR call. Keep exactly one OCR
# process active at a time to avoid short CPU/process-creation spikes that can
# make Windows feel laggy even when average CPU usage looks low.
_OCR_SEMAPHORE = threading.BoundedSemaphore(1)


class OcrConfigurationError(RuntimeError):
    pass


class OcrService:
    def __init__(
        self,
        tesseract_cmd: str | Path | None = None,
    ):
        located = (
            tesseract_cmd
            or shutil.which("tesseract")
            or Path(
                "C:/Program Files/"
                "Tesseract-OCR/"
                "tesseract.exe"
            )
        )

        path = Path(located)

        if not path.is_file():
            raise OcrConfigurationError(
                "Tesseract executable "
                f"not found: {path}"
            )

        pytesseract.pytesseract.tesseract_cmd = str(
            path
        )

        self.config = (
            "--oem 3 "
            "--psm 13 "
            "-c tessedit_char_whitelist=0123456789/.,"
        )

    def read(self, image):
        wait_started = time.perf_counter()
        _OCR_SEMAPHORE.acquire()

        try:
            record_perf_ms(
                "wait_ocr_ms",
                (
                    time.perf_counter()
                    - wait_started
                )
                * 1000.0,
            )

            ocr_started = time.perf_counter()

            try:
                return pytesseract.image_to_string(
                    image,
                    config=self.config,
                )
            finally:
                record_perf_ms(
                    "ocr_ms",
                    (
                        time.perf_counter()
                        - ocr_started
                    )
                    * 1000.0,
                )
        finally:
            _OCR_SEMAPHORE.release()
