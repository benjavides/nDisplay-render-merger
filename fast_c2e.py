"""
Fast cubemap -> equirectangular conversion.

py360convert.c2e resamples one colour channel at a time and, when OpenCV is not installed,
falls back to scipy.ndimage.map_coordinates, which is roughly forty times slower than
cv2.remap for this workload. A batch job converts thousands of frames at one fixed
resolution, so here the sampling grid is built once per process and all channels go through
a single cv2.remap call.

Falls back to py360convert when OpenCV is unavailable, so the app keeps working without it
(just far slower).
"""
import numpy as np

try:
    import cv2

    _HAS_CV2 = True
except ImportError:  # pragma: no cover - exercised only on installs without OpenCV
    cv2 = None
    _HAS_CV2 = False

# py360convert face order for the horizon/list layout: front, right, back, left, up, down.
_FACE_KEYS = ("F", "R", "B", "L", "U", "D")

_FRONT, _RIGHT, _BACK, _LEFT, _UP, _DOWN = range(6)

_map_cache = {}


def cv2_available():
    return _HAS_CV2


def _build_maps(face_w, h, w):
    """
    (map_x, map_y) float32 remap tables addressing the padded 6-face strip.

    Same geometry as py360convert's CubeFaceSampler.from_equirec, with two changes: the
    coordinates stay float32 instead of being converted to cv2's 16-bit fixed point (which
    quantises sample positions to 1/32 px and shifts a few output pixels by up to 5 levels),
    and the per-face y offset addresses one tall strip so all six faces are sampled in a
    single remap call.
    """
    from py360convert.utils import equirect_facetype, equirect_uvgrid

    u, v = equirect_uvgrid(h, w)
    tp = equirect_facetype(h, w)

    coor_x = np.empty((h, w), np.float32)
    coor_y = np.empty((h, w), np.float32)
    face_w2 = face_w / 2

    # Middle band: front/right/back/left, each rotated to its own 90 degree slice.
    mask = tp < _UP
    angles = u[mask] - (np.pi / 2 * tp[mask])
    coor_x[mask] = face_w2 * np.tan(angles)
    coor_y[mask] = -face_w2 * np.tan(v[mask]) / np.cos(angles)

    mask = tp == _UP
    c = face_w2 * np.tan(np.pi / 2 - v[mask])
    coor_x[mask] = c * np.sin(u[mask])
    coor_y[mask] = c * np.cos(u[mask])

    mask = tp == _DOWN
    c = face_w2 * np.tan(np.pi / 2 - np.abs(v[mask]))
    coor_x[mask] = c * np.sin(u[mask])
    coor_y[mask] = -c * np.cos(u[mask])

    coor_x += face_w2
    coor_y += face_w2
    coor_x.clip(0, face_w, out=coor_x)
    coor_y.clip(0, face_w, out=coor_y)

    # +1 for the padding ring around each face, then offset into the stacked strip.
    coor_x += 1.0
    coor_y += 1.0
    coor_y += tp.astype(np.float32) * (face_w + 2)

    return coor_x, coor_y


def _get_maps(face_w, h, w):
    key = (face_w, h, w)
    if key not in _map_cache:
        _map_cache[key] = _build_maps(face_w, h, w)
    return _map_cache[key]


def _pad_faces(faces):
    """(6, S, S, C) -> (6, S+2, S+2, C) with neighbouring faces wrapped into the border ring."""
    n, s, _, channels = faces.shape
    padded = np.empty((n, s + 2, s + 2, channels), dtype=faces.dtype)
    padded[:, 1:-1, 1:-1] = faces

    above = (0, slice(None))
    below = (-1, slice(None))

    padded[_FRONT][above] = padded[_UP, -2, :]
    padded[_FRONT][below] = padded[_DOWN, 1, :]
    padded[_RIGHT][above] = padded[_UP, ::-1, -2]
    padded[_RIGHT][below] = padded[_DOWN, :, -2]
    padded[_BACK][above] = padded[_UP, 1, ::-1]
    padded[_BACK][below] = padded[_DOWN, -2, ::-1]
    padded[_LEFT][above] = padded[_UP, :, 1]
    padded[_LEFT][below] = padded[_DOWN, ::-1, 1]
    padded[_UP][above] = padded[_BACK, 1, ::-1]
    padded[_UP][below] = padded[_FRONT, 1, :]
    padded[_DOWN][above] = padded[_FRONT, -2, :]
    padded[_DOWN][below] = padded[_BACK, -2, ::-1]

    padded[_FRONT][:, 0] = padded[_LEFT, :, -2]
    padded[_FRONT][:, -1] = padded[_RIGHT, :, 1]
    padded[_RIGHT][:, 0] = padded[_FRONT, :, -2]
    padded[_RIGHT][:, -1] = padded[_BACK, :, 1]
    padded[_BACK][:, 0] = padded[_RIGHT, :, -2]
    padded[_BACK][:, -1] = padded[_LEFT, :, 1]
    padded[_LEFT][:, 0] = padded[_BACK, :, -2]
    padded[_LEFT][:, -1] = padded[_FRONT, :, 1]
    padded[_UP][:, 0] = padded[_LEFT, 1, :]
    padded[_UP][:, -1] = padded[_RIGHT, 1, ::-1]
    padded[_DOWN][:, 0] = padded[_LEFT, -2, ::-1]
    padded[_DOWN][:, -1] = padded[_RIGHT, -2, :]

    return padded


def c2e_dict(face_arrays, h, w):
    """
    face_arrays: {"F","R","B","L","U","D"} -> (S, S, C) uint8, C in 1..4.
    Returns (h, w, C) uint8.
    """
    faces = np.stack([face_arrays[k] for k in _FACE_KEYS])
    face_w = faces.shape[1]

    if not _HAS_CV2 or faces.shape[3] > 4:
        import py360convert

        out = py360convert.c2e(face_arrays, h, w, cube_format="dict")
        if out.dtype != np.uint8:
            out = np.clip(out, 0, 255).astype(np.uint8)
        return out

    map_x, map_y = _get_maps(face_w, h, w)
    padded = _pad_faces(faces)
    strip = padded.reshape(-1, face_w + 2, faces.shape[3])
    return cv2.remap(strip, map_x, map_y, interpolation=cv2.INTER_LINEAR)
