from __future__ import annotations

from unittest import IsolatedAsyncioTestCase

from fastapi import HTTPException

from api.image_inputs import (
    MAX_IMAGE_UPLOAD_BYTES,
    MAX_IMAGE_REQUEST_BYTES,
    MAX_IMAGE_UPLOAD_COUNT,
    read_image_sources,
)


class _FakeUpload:
    def __init__(self, data: bytes, filename: str = "image.png") -> None:
        self.data = data
        self.filename = filename
        self.content_type = "image/png"
        self.read_size: int | None = None
        self.closed = False

    async def read(self, size: int = -1) -> bytes:
        self.read_size = size
        return self.data if size < 0 else self.data[:size]

    async def close(self) -> None:
        self.closed = True


class ImageUploadLimitTests(IsolatedAsyncioTestCase):
    async def test_upload_reads_only_one_byte_past_per_file_limit(self) -> None:
        upload = _FakeUpload(b"x" * (MAX_IMAGE_UPLOAD_BYTES + 1))

        with self.assertRaises(HTTPException) as context:
            await read_image_sources([upload])

        self.assertEqual(context.exception.status_code, 413)
        self.assertEqual(upload.read_size, MAX_IMAGE_UPLOAD_BYTES + 1)
        self.assertTrue(upload.closed)

    async def test_excessive_upload_count_is_rejected(self) -> None:
        uploads = [_FakeUpload(b"x") for _ in range(MAX_IMAGE_UPLOAD_COUNT + 1)]

        with self.assertRaises(HTTPException) as context:
            await read_image_sources(uploads)

        self.assertEqual(context.exception.status_code, 413)

    async def test_total_upload_size_is_bounded(self) -> None:
        chunk = b"x" * (MAX_IMAGE_UPLOAD_BYTES)
        uploads = [
            _FakeUpload(chunk),
            _FakeUpload(chunk),
            _FakeUpload(b"y" * (MAX_IMAGE_REQUEST_BYTES - 2 * MAX_IMAGE_UPLOAD_BYTES + 1)),
        ]

        with self.assertRaises(HTTPException) as context:
            await read_image_sources(uploads)

        self.assertEqual(context.exception.status_code, 413)
