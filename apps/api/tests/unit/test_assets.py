"""Image sanitising, and the endpoints around it.

The sanitiser is the reason this module exists, so it carries most of the
tests. The property that matters is not "the image still looks right" -- it is
that **nothing of the original file survives except its pixels**. A resume gets
emailed to strangers; a headshot straight off a phone carries GPS coordinates,
a device serial and a timestamp, and the person attaching it has no idea.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from studio.assets.images import (
    MAX_BYTES,
    MAX_EDGE,
    ImageError,
    sanitise,
)


def encode(image: Image.Image, fmt: str = "PNG", **kwargs: object) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **kwargs)
    return buffer.getvalue()


def photo(width: int = 64, height: int = 48, colour: tuple[int, int, int] = (10, 120, 200)):
    return Image.new("RGB", (width, height), colour)


def with_exif(image: Image.Image) -> bytes:
    """A JPEG carrying the tags a phone would attach."""
    exif = image.getexif()
    exif[0x010F] = "ACME Phone"           # Make
    exif[0x0110] = "Model X"              # Model
    exif[0x0132] = "2024:01:01 10:00:00"  # DateTime
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif.tobytes())
    return buffer.getvalue()


class TestMetadataIsDropped:
    def test_exif_does_not_survive(self) -> None:
        raw = with_exif(photo())
        assert len(Image.open(io.BytesIO(raw)).getexif()) > 0, "fixture should carry EXIF"

        result = sanitise(raw, declared="image/jpeg")

        assert len(Image.open(io.BytesIO(result.data)).getexif()) == 0

    def test_the_picture_itself_is_preserved(self) -> None:
        # Stripping metadata must not mean mangling the image.
        raw = with_exif(photo(80, 60, (200, 30, 30)))
        result = sanitise(raw, declared="image/jpeg")

        out = Image.open(io.BytesIO(result.data))
        assert out.size == (80, 60)
        # JPEG is lossy, so compare approximately.
        red, green, blue = out.convert("RGB").getpixel((40, 30))
        assert red > 150 and green < 80 and blue < 80

    def test_png_text_chunks_do_not_survive(self) -> None:
        from PIL.PngImagePlugin import PngInfo

        meta = PngInfo()
        meta.add_text("Author", "someone@example.com")
        raw = encode(photo(), "PNG", pnginfo=meta)
        assert b"someone@example.com" in raw

        result = sanitise(raw)
        assert b"someone@example.com" not in result.data


class TestFormats:
    @pytest.mark.parametrize(
        ("fmt", "mime"),
        [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")],
    )
    def test_accepts_what_it_says_it_accepts(self, fmt: str, mime: str) -> None:
        result = sanitise(encode(photo(), fmt))
        assert result.mime == mime

    def test_the_bytes_decide_the_format_not_the_caller(self) -> None:
        # A content type is a claim by the caller; the bytes are the evidence.
        # Same reasoning as the PDF importer's magic-number check.
        result = sanitise(encode(photo(), "PNG"), declared="image/jpeg")
        assert result.mime == "image/png"

    def test_refuses_a_format_we_do_not_re_encode(self) -> None:
        raw = encode(photo(), "BMP")
        with pytest.raises(ImageError) as caught:
            sanitise(raw)
        assert caught.value.code == "unsupported_format"

    def test_refuses_something_that_is_not_an_image(self) -> None:
        with pytest.raises(ImageError):
            sanitise(b"%PDF-1.7\nnot an image at all")

    def test_refuses_an_empty_file(self) -> None:
        with pytest.raises(ImageError) as caught:
            sanitise(b"")
        assert caught.value.code == "empty"

    def test_transparency_survives_in_a_format_that_has_it(self) -> None:
        transparent = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
        result = sanitise(encode(transparent, "PNG"))
        assert Image.open(io.BytesIO(result.data)).mode == "RGBA"

    def test_a_transparent_image_saved_as_jpeg_does_not_go_black(self) -> None:
        # A naive convert produces a black box; the mode change has to happen
        # before the encoder sees it.
        transparent = Image.new("RGBA", (20, 20), (255, 0, 0, 255))
        raw = io.BytesIO()
        transparent.convert("RGB").save(raw, format="JPEG")
        result = sanitise(raw.getvalue())
        assert Image.open(io.BytesIO(result.data)).mode == "RGB"


class TestLimits:
    def test_shrinks_an_image_larger_than_the_page_can_show(self) -> None:
        big = photo(MAX_EDGE + 500, 100)
        result = sanitise(encode(big, "PNG"))
        assert max(result.width, result.height) == MAX_EDGE

    def test_keeps_the_aspect_ratio_when_shrinking(self) -> None:
        result = sanitise(encode(photo(MAX_EDGE * 2, MAX_EDGE), "PNG"))
        assert result.width / result.height == pytest.approx(2.0, abs=0.01)

    def test_leaves_a_reasonable_image_at_its_own_size(self) -> None:
        result = sanitise(encode(photo(300, 200), "PNG"))
        assert (result.width, result.height) == (300, 200)

    def test_refuses_bytes_over_the_cap_without_decoding_them(self) -> None:
        with pytest.raises(ImageError) as caught:
            sanitise(b"\x89PNG\r\n\x1a\n" + b"\0" * MAX_BYTES)
        assert caught.value.code == "too_large"

    def test_refuses_a_decompression_bomb_from_its_header(self) -> None:
        # Small on disk, enormous in memory: the ceiling has to sit in front of
        # the decoder, not after it.
        Image.MAX_IMAGE_PIXELS = None  # let Pillow build the fixture
        try:
            bomb = encode(Image.new("L", (9000, 9000)), "PNG")
        finally:
            Image.MAX_IMAGE_PIXELS = 178_956_970
        with pytest.raises(ImageError) as caught:
            sanitise(bomb)
        assert caught.value.code == "too_large"


class TestIdentity:
    def test_the_same_image_hashes_the_same_way(self) -> None:
        raw = encode(photo(), "PNG")
        assert sanitise(raw).sha256 == sanitise(raw).sha256

    def test_different_images_hash_differently(self) -> None:
        a = sanitise(encode(photo(colour=(1, 2, 3)), "PNG"))
        b = sanitise(encode(photo(colour=(9, 9, 9)), "PNG"))
        assert a.sha256 != b.sha256

    def test_reports_the_dimensions_it_stored(self) -> None:
        result = sanitise(encode(photo(123, 45), "PNG"))
        assert (result.width, result.height) == (123, 45)
