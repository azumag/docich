"""Bounded, ephemeral image inputs for the common LLM dispatcher (#1233).

Bytes never enter repr(), telemetry or persistent conversation history. Model
capability is an explicit deployment declaration, not a name-based guess.
"""
from __future__ import annotations

import base64
import io
from dataclasses import dataclass, field

MAX_IMAGE_BYTES = 1024 * 1024
MAX_IMAGE_EDGE = 1280
MAX_IMAGE_PIXELS = MAX_IMAGE_EDGE * MAX_IMAGE_EDGE
IMAGE_PROVIDERS = frozenset({"local"})


@dataclass(frozen=True)
class ImageAttachment:
    data: bytes = field(repr=False)
    mime: str
    width: int
    height: int

    def __post_init__(self):
        if type(self.data) is not bytes or not 0 < len(self.data) <= MAX_IMAGE_BYTES:
            raise ValueError("invalid_image")
        if self.mime != "image/jpeg":
            raise ValueError("invalid_image")
        if any(type(n) is not int or not 1 <= n <= MAX_IMAGE_EDGE
               for n in (self.width, self.height)):
            raise ValueError("invalid_image")
        from PIL import Image
        try:
            with Image.open(io.BytesIO(self.data)) as image:
                if image.format != "JPEG" or image.size != (self.width, self.height):
                    raise ValueError("invalid_image")
                image.verify()
        except Exception:
            raise ValueError("invalid_image") from None

    @classmethod
    def from_rgb(cls, data: bytes, width: int, height: int) -> ImageAttachment:
        if any(type(n) is not int or not 1 <= n <= MAX_IMAGE_EDGE for n in (width, height)):
            raise ValueError("invalid_image")
        if type(data) is not bytes or len(data) != width * height * 3:
            raise ValueError("invalid_image")
        from PIL import Image
        with Image.frombytes("RGB", (width, height), data) as image:
            for quality in (80, 65, 45):
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=quality)
                value = output.getvalue()
                if len(value) <= MAX_IMAGE_BYTES:
                    return cls(value, "image/jpeg", width, height)
        raise ValueError("image_too_large")


def validate_images(images) -> None:
    if type(images) is not tuple or len(images) > 1:
        raise ValueError("invalid_images")
    if any(type(image) is not ImageAttachment for image in images):
        raise ValueError("invalid_images")


def image_capable(spec, env) -> bool:
    # Empty by default. An operator declares only provider/model pairs verified
    # on the actual deployment. This does NOT add models to the request chain.
    raw = env.get("DOCICH_LLM_IMAGE_AGENTS", "")
    if type(raw) is not str or len(raw) > 4096:
        return False
    declared = raw.split(",")
    return (spec.provider in IMAGE_PROVIDERS and bool(spec.model)
            and spec.raw == f"{spec.provider}:{spec.model}" and spec.raw in declared)


def user_content(prompt: str, images: tuple[ImageAttachment, ...]):
    validate_images(images)
    if not images:
        return prompt
    return [{"type": "text", "text": prompt}, *[
        {"type": "image_url", "image_url": {
            "url": "data:image/jpeg;base64," + base64.b64encode(image.data).decode("ascii")
        }} for image in images
    ]]
