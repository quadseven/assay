"""JPEG output components must fit the destination decoder buffer."""

import subprocess
import sys


def test_jpeg_components_must_fit_decoder_buffer():
    script = """
from io import BytesIO
from PIL import Image

encoded = BytesIO()
Image.new("CMYK", (32, 32)).save(encoded, format="JPEG")
data = encoded.getvalue()
with Image.open(BytesIO(data)) as normal:
    normal.load()
    assert normal.mode == "CMYK"
    assert normal.size == (32, 32)
try:
    Image.frombytes("RGBA", (32, 32), data, "jpeg", "LA", "")
except (ValueError, OSError):
    pass
else:
    raise AssertionError("four-component JPEG accepted by a two-component decoder")
"""
    subprocess.run([sys.executable, "-c", script], check=True, timeout=15)
