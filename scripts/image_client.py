from __future__ import annotations

import base64
import io
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from openai import OpenAI


class ImagePayloadError(RuntimeError):
    """Raised when an image response has no usable payload."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _field(item: Any, name: str) -> Any:
    if isinstance(item, dict):
        return item.get(name)
    return getattr(item, name, None)


def _validate_image_bytes(data: bytes, source: str) -> bytes:
    if not data:
        raise ImagePayloadError(f"{source} returned an empty image")
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
    except ImportError:
        pass
    except Exception as exc:
        raise ImagePayloadError(f"{source} did not return a valid image: {exc}") from exc
    return data


def _download_url(url: str, timeout: float = 120.0) -> bytes:
    request = Request(url, headers={"User-Agent": "relay-imagegen/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            content_type = response.headers.get("Content-Type", "")
            data = response.read()
    except HTTPError as exc:
        raise ImagePayloadError(f"Image URL returned HTTP {exc.code}", status_code=exc.code) from exc
    if not 200 <= status < 300:
        raise ImagePayloadError(f"Image URL returned HTTP {status}", status_code=status)
    if not content_type.lower().split(";", 1)[0].startswith("image/"):
        raise ImagePayloadError(f"Image URL returned unexpected content type: {content_type or '<missing>'}")
    return _validate_image_bytes(data, "Image URL")


def extract_image_bytes(item: Any) -> bytes:
    encoded = _field(item, "b64_json")
    if encoded:
        try:
            return _validate_image_bytes(base64.b64decode(encoded), "Base64 payload")
        except Exception as exc:
            if isinstance(exc, ImagePayloadError):
                raise
            raise ImagePayloadError(f"Invalid Base64 image payload: {exc}") from exc

    url = _field(item, "url")
    if url:
        return _download_url(str(url))

    raise ImagePayloadError("Image response contains neither b64_json nor url")


class ImageClient:
    def __init__(self, api_key: str, base_url: str, timeout: float = 600.0):
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    @staticmethod
    def _payload(model: str, prompt: str, size: str, quality: str, output_format: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": model, "prompt": prompt, "size": size, "quality": quality}
        if output_format:
            payload["output_format"] = output_format
        return payload

    def generate(self, *, model: str, prompt: str, size: str, quality: str, output_format: str | None = None) -> list[bytes]:
        response = self.client.images.generate(**self._payload(model, prompt, size, quality, output_format))
        return [extract_image_bytes(item) for item in response.data]

    def generate_stream(
        self,
        *,
        model: str,
        prompt: str,
        size: str,
        quality: str,
        partial_images: int,
        on_partial: Callable[[int, bytes], None],
        output_format: str | None = None,
    ) -> list[bytes]:
        payload = self._payload(model, prompt, size, quality, output_format)
        payload.update(stream=True, partial_images=partial_images)
        stream = self.client.images.generate(**payload)
        return self._collect_stream(stream, "image_generation", on_partial)

    def edit_stream(
        self,
        *,
        model: str,
        prompt: str,
        size: str,
        quality: str,
        image_paths: list[str],
        partial_images: int,
        on_partial: Callable[[int, bytes], None],
        output_format: str | None = None,
    ) -> list[bytes]:
        payload = self._payload(model, prompt, size, quality, output_format)
        payload.update(stream=True, partial_images=partial_images)
        handles = [open(path, "rb") for path in image_paths]
        try:
            payload["image"] = handles if len(handles) > 1 else handles[0]
            stream = self.client.images.edit(**payload)
            return self._collect_stream(stream, "image_edit", on_partial)
        finally:
            for handle in handles:
                handle.close()

    @staticmethod
    def _collect_stream(stream: Any, event_prefix: str, on_partial: Callable[[int, bytes], None]) -> list[bytes]:
        completed: list[bytes] = []
        try:
            for event in stream:
                event_type = _field(event, "type")
                encoded = _field(event, "b64_json")
                if not encoded:
                    continue
                try:
                    image = _validate_image_bytes(base64.b64decode(encoded), "Streaming image payload")
                except Exception as exc:
                    if isinstance(exc, ImagePayloadError):
                        raise
                    raise ImagePayloadError(f"Invalid streaming image payload: {exc}") from exc
                if event_type == f"{event_prefix}.partial_image":
                    index = _field(event, "partial_image_index")
                    if isinstance(index, int):
                        on_partial(index, image)
                elif event_type == f"{event_prefix}.completed":
                    completed.append(image)
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
        if not completed:
            raise ImagePayloadError("Image stream ended without a completed image")
        return completed

    def edit(self, *, model: str, prompt: str, size: str, quality: str, image_paths: list[str], output_format: str | None = None) -> list[bytes]:
        payload = self._payload(model, prompt, size, quality, output_format)
        handles = [open(path, "rb") for path in image_paths]
        try:
            payload["image"] = handles if len(handles) > 1 else handles[0]
            response = self.client.images.edit(**payload)
            return [extract_image_bytes(item) for item in response.data]
        finally:
            for handle in handles:
                handle.close()
