"""
JPEG load/save via simplejpeg (libjpeg-turbo) when available; same quality targets as Pillow.

Pillow defaults to quality 75 for JPEG when not specified; stereo export uses 95 explicitly.
"""
import os

try:
    import numpy as np
    import simplejpeg

    _HAS_SIMPLEJPEG = True
except ImportError:
    np = None
    _HAS_SIMPLEJPEG = False

# Pillow's default when Image.save(..., format="JPEG") is used without quality (see Pillow docs).
PIL_DEFAULT_JPEG_QUALITY = 75

STEREO_JPEG_QUALITY = 95


def turbo_available():
    return _HAS_SIMPLEJPEG


def load_rgb_u8(path):
    """RGB uint8 array H×W×3 for stereo cubemap faces."""
    import numpy as np
    from PIL import Image

    ext = os.path.splitext(path)[1].lower()
    if ext in (".jpg", ".jpeg") and _HAS_SIMPLEJPEG:
        try:
            with open(path, "rb") as f:
                return simplejpeg.decode_jpeg(f.read(), colorspace="RGB")
        except Exception:
            pass
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"))


def save_image_jpeg(image, path, quality):
    """Save PIL image; if path is .jpg/.jpeg use same quality as Pillow would with that setting."""
    from PIL import Image

    ext = os.path.splitext(path)[1].lower()
    if ext not in (".jpg", ".jpeg"):
        image.save(path)
        return

    if _HAS_SIMPLEJPEG:
        try:
            rgb = image.convert("RGB")
            arr = np.ascontiguousarray(rgb)
            data = simplejpeg.encode_jpeg(arr, quality=int(quality), colorspace="RGB")
            with open(path, "wb") as f:
                f.write(data)
            return
        except Exception:
            pass

    image.save(path, format="JPEG", quality=int(quality))


def save_rgb_u8_jpeg(path, rgb_u8, quality):
    """Save H×W×3 uint8 RGB. JPEG path uses same turbo/Pillow rules as save_image_jpeg."""
    import numpy as np
    from PIL import Image

    ext = os.path.splitext(path)[1].lower()
    arr = np.ascontiguousarray(rgb_u8)
    if ext not in (".jpg", ".jpeg"):
        Image.fromarray(arr, mode="RGB").save(path)
        return

    if _HAS_SIMPLEJPEG:
        try:
            data = simplejpeg.encode_jpeg(arr, quality=int(quality), colorspace="RGB")
            with open(path, "wb") as f:
                f.write(data)
            return
        except Exception:
            pass

    Image.fromarray(arr, mode="RGB").save(path, format="JPEG", quality=int(quality))
