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

        # Boss-name OCR: one short text line. Do not use a character
        # whitelist here because Vietnamese accents may be read either with
        # or without diacritics depending on the installed Tesseract data.
        self.text_config = (
            "--oem 3 "
            "--psm 7"
        )

        # Used for a vertically stacked set of preprocessing variants of the
        # same short boss name. One Tesseract process reads the whole block,
        # avoiding 2-3 separate process launches on difficult frames.
        self.text_block_config = (
            "--oem 3 "
            "--psm 6"
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



    def read_text(self, image):
        """Read one short text line using the same serialized OCR worker."""
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
                    config=self.text_config,
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


    def read_text_block(self, image):
        """OCR a small multi-line text block through the shared OCR lock."""
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
                    config=self.text_block_config,
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
