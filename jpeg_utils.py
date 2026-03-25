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
    """RGB uint8 array H×W×3 for stereo cubemap faces. PNG alpha is flattened onto black."""
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
        # Copy pixels before the image is closed on context exit; otherwise the array may
        # reference freed memory and RGB/alpha values are undefined.
        return np.array(im.convert("RGB"), dtype=np.uint8)


def load_rgba_u8(path):
    """RGBA uint8 H×W×4. JPEG and RGB files use opaque alpha (255)."""
    import numpy as np
    from PIL import Image

    ext = os.path.splitext(path)[1].lower()
    if ext in (".jpg", ".jpeg") and _HAS_SIMPLEJPEG:
        try:
            with open(path, "rb") as f:
                rgb = simplejpeg.decode_jpeg(f.read(), colorspace="RGB")
            h, w = rgb.shape[:2]
            a = np.full((h, w, 1), 255, dtype=np.uint8)
            return np.concatenate([rgb, a], axis=-1)
        except Exception:
            pass
    with Image.open(path) as im:
        return np.array(im.convert("RGBA"), dtype=np.uint8)


def rgba_flatten_on_black(rgba_u8):
    """H×W×4 -> H×W×3 RGB, compositing onto black (for JPEG export)."""
    import numpy as np

    a = rgba_u8[..., 3:4].astype(np.float32) / 255.0
    rgb = rgba_u8[..., :3].astype(np.float32)
    out = rgb * a
    return np.clip(out, 0, 255).astype(np.uint8)


def save_u8_image(path, arr_u8, quality):
    """
    Save H×W×3 RGB or H×W×4 RGBA. JPEG always flattens alpha onto black then encodes as RGB.
    PNG preserves alpha when arr has 4 channels.
    """
    import numpy as np
    from PIL import Image

    ext = os.path.splitext(path)[1].lower()
    arr = np.ascontiguousarray(arr_u8)
    if arr.ndim != 3 or arr.shape[2] not in (3, 4):
        raise ValueError("Expected H×W×3 or H×W×4 uint8 array.")

    if arr.shape[2] == 4:
        if ext in (".jpg", ".jpeg"):
            rgb = rgba_flatten_on_black(arr)
            save_rgb_u8_jpeg(path, rgb, quality)
            return
        im = Image.fromarray(arr, mode="RGBA")
        if ext == ".png":
            im.save(path, format="PNG")
        else:
            im.save(path)
        return

    save_rgb_u8_jpeg(path, arr, quality)


def save_pil_rgba_image(path, pil_rgba, quality):
    """
    Save a PIL Image in RGBA mode. JPEG flattens alpha onto black; PNG writes full alpha.
    Prefer this after compositing with Image.paste(..., mask=) so alpha matches Pillow's model.
    """
    import numpy as np
    from PIL import Image

    if pil_rgba.mode != "RGBA":
        pil_rgba = pil_rgba.convert("RGBA")
    ext = os.path.splitext(path)[1].lower()
    if ext in (".jpg", ".jpeg"):
        arr = np.array(pil_rgba, dtype=np.uint8)
        save_u8_image(path, arr, quality)
        return
    if ext == ".png":
        pil_rgba.save(path, format="PNG")
    else:
        pil_rgba.save(path)


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
