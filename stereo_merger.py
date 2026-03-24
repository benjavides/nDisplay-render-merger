import os
import re
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from enum import Enum

import numpy as np
import py360convert
from errors import ImageSetError

from jpeg_utils import STEREO_JPEG_QUALITY, load_rgb_u8, save_rgb_u8_jpeg
from filename_template import (
    DEFAULT_STEREO_INPUT,
    DEFAULT_STEREO_OUTPUT_OVER_UNDER,
    DEFAULT_STEREO_OUTPUT_SEPARATE,
    dotted_ext_from_capture,
    parse_basename_with_template,
    precheck_input_file_ext,
    render_output_relative,
    validate_input_template,
    validate_output_template,
)
from nDisplayMerger import _default_max_workers, wait_if_paused

_SUPPORTED_EXTS = (".jpeg", ".jpg", ".png")

# Viewport substring tokens (case-insensitive via token matching)
_FACE_ORDER = ("FRONT", "BACK", "LEFT", "RIGHT", "UP", "DOWN")

_FACE_TO_CUBE_KEY = {
    "FRONT": "F",
    "RIGHT": "R",
    "BACK": "B",
    "LEFT": "L",
    "UP": "U",
    "DOWN": "D",
}


class StereoOutputMode(str, Enum):
    """
    How stereo VR equirectangular output is written. Member values are stable for settings JSON.
    EQUIRECTANGULAR_* leaves room for future CUBEMAP_* modes without renaming.
    """

    EQUIRECTANGULAR_STEREO_OVER_UNDER = "equirectangular_stereo_over_under"
    """Single stacked image: left eye on top, right eye on bottom."""

    EQUIRECTANGULAR_MONO_SEPARATE_EYES = "equirectangular_mono_separate_eyes"
    """One equirectangular JPEG per eye under left_eye/ and right_eye/."""


def coerce_stereo_output_mode(value):
    """Return a StereoOutputMode; invalid or missing values default to over/under."""
    if isinstance(value, StereoOutputMode):
        return value
    if value is None or (isinstance(value, str) and not str(value).strip()):
        return StereoOutputMode.EQUIRECTANGULAR_STEREO_OVER_UNDER
    try:
        return StereoOutputMode(str(value).strip())
    except ValueError:
        return StereoOutputMode.EQUIRECTANGULAR_STEREO_OVER_UNDER


def _frame_sort_key(frame):
    return int(frame) if str(frame).isdigit() else frame


def _face_from_viewport(viewport_segment):
    """Return one of FRONT, BACK, LEFT, RIGHT, UP, DOWN, or None if ambiguous/missing."""
    tokens = {t.upper() for t in re.split(r"[^A-Za-z0-9]+", viewport_segment) if t}
    matches = [f for f in _FACE_ORDER if f in tokens]
    if len(matches) == 1:
        return matches[0]
    return None


def _collect_eye_folder(eye_dir, label, input_template):
    """Build frame -> {paths, meta, ext}. Raises ImageSetError on duplicates or ambiguity."""
    if not os.path.isdir(eye_dir):
        raise ImageSetError(f"{label} directory is not a valid folder: '{eye_dir}'.")

    validate_input_template(input_template)
    by_frame = {}
    for file_name in os.listdir(eye_dir):
        try:
            precheck_input_file_ext(file_name)
        except ImageSetError:
            continue
        lower = file_name.lower()
        if not lower.endswith(_SUPPORTED_EXTS):
            continue
        fields = parse_basename_with_template(input_template, file_name)
        if not fields:
            continue
        cam = fields.get("camera_name")
        fn = fields.get("frame_number")
        ext_cap = fields.get("ext")
        if cam is None or fn is None or not ext_cap:
            continue
        face = _face_from_viewport(cam)
        if face is None:
            continue
        try:
            dotted = dotted_ext_from_capture(ext_cap)
        except ImageSetError:
            continue
        meta = {k: v for k, v in fields.items() if k not in ("camera_name", "ext")}
        abs_path = os.path.join(eye_dir, file_name)

        if fn not in by_frame:
            by_frame[fn] = {"paths": {}, "meta": meta, "ext": dotted}
        frame_entry = by_frame[fn]
        if frame_entry["meta"] != meta:
            raise ImageSetError(
                f"{label} eye: frame {fn} mixes metadata between files "
                f"(e.g. '{file_name}' vs another image in the same frame)."
            )
        if frame_entry["ext"] != dotted:
            raise ImageSetError(
                f"{label} eye: frame {fn} mixes file extensions "
                f"('{frame_entry['ext']}' vs '{dotted}')."
            )
        if face in frame_entry["paths"]:
            raise ImageSetError(
                f"{label} eye: duplicate {face} face for frame {fn} "
                f"('{os.path.basename(frame_entry['paths'][face])}' vs '{file_name}')."
            )
        frame_entry["paths"][face] = abs_path

    return by_frame


def _validate_stereo_lr_metadata(left_by_frame, right_by_frame):
    for fn in left_by_frame:
        if fn not in right_by_frame:
            continue
        l, r = left_by_frame[fn], right_by_frame[fn]
        if l["meta"] != r["meta"] or l["ext"] != r["ext"]:
            raise ImageSetError(
                f"Frame {fn}: left and right eye folders disagree on naming metadata or extension."
            )


def _validate_frames_and_faces(left_by_frame, right_by_frame):
    left_frames = set(left_by_frame.keys())
    right_frames = set(right_by_frame.keys())
    if left_frames != right_frames:
        only_left = sorted(left_frames - right_frames, key=_frame_sort_key)
        only_right = sorted(right_frames - left_frames, key=_frame_sort_key)
        raise ImageSetError(
            "Left and right eye folders do not contain the same frame numbers. "
            f"Only in left: {only_left or 'none'}. Only in right: {only_right or 'none'}."
        )

    errors = []
    expected = list(_FACE_TO_CUBE_KEY.keys())
    for frame in sorted(left_frames, key=_frame_sort_key):
        for side_name, data in (("Left", left_by_frame[frame]), ("Right", right_by_frame[frame])):
            present = set(data["paths"].keys())
            missing = [f for f in expected if f not in present]
            if missing:
                errors.append(
                    f"{side_name} eye frame {frame}: missing face(s) {', '.join(missing)} "
                    f"(found {', '.join(sorted(present)) or 'none'})."
                )
    if errors:
        raise ImageSetError("Incomplete cubemap face sets:\n" + "\n".join(errors))


def list_paired_stereo_frames(left_dir, right_dir, input_naming_template=None):
    """Sorted common frame keys after full stereo validation (for UI auto-fill)."""
    inp = (input_naming_template or "").strip() or DEFAULT_STEREO_INPUT
    left_by_frame = _collect_eye_folder(left_dir, "Left", inp)
    right_by_frame = _collect_eye_folder(right_dir, "Right", inp)
    if not left_by_frame or not right_by_frame:
        raise ImageSetError(
            "No valid cubemap face images found. Check the input naming template and ensure "
            "each filename matches {camera_name} (with cubemap face tokens), {frame_number}, "
            "and {ext} (.jpeg / .jpg / .png)."
        )
    _validate_stereo_lr_metadata(left_by_frame, right_by_frame)
    _validate_frames_and_faces(left_by_frame, right_by_frame)
    return sorted(left_by_frame.keys(), key=_frame_sort_key)


def filter_stereo_frames_in_range(ordered_keys, frame_start, frame_end):
    start_s = str(frame_start).strip()
    end_s = str(frame_end).strip()
    if not start_s or not end_s:
        raise ImageSetError("Start frame and end frame must be set.")
    try:
        start_i = int(start_s)
        end_i = int(end_s)
    except ValueError as exc:
        raise ImageSetError("Start and end frame must be integers.") from exc
    if start_i > end_i:
        raise ImageSetError(f"Start frame ({start_i}) must be <= end frame ({end_i}).")
    non_digit = [f for f in ordered_keys if not str(f).isdigit()]
    if non_digit:
        raise ImageSetError(
            "Frame range export requires numeric frame numbers only; "
            f"non-numeric frames present: {', '.join(map(str, non_digit[:5]))}"
            + (" …" if len(non_digit) > 5 else "")
        )
    filtered = [f for f in ordered_keys if start_i <= int(f) <= end_i]
    if not filtered:
        raise ImageSetError(
            f"No frames fall in range {start_i}–{end_i} (inclusive) for the current image set."
        )
    return filtered


def _load_face_rgba(path):
    return load_rgb_u8(path)


def _cubemap_to_equirect(face_to_path):
    """face_to_path maps FRONT..DOWN to file path. Returns H×W×3 uint8 RGB."""
    arrays = {}
    face_w = face_h = None
    for face, key in _FACE_TO_CUBE_KEY.items():
        arr = _load_face_rgba(face_to_path[face])
        if arr.ndim != 3 or arr.shape[2] != 3:
            raise ImageSetError(f"Expected RGB image for face {face}: '{face_to_path[face]}'.")
        h, w = arr.shape[0], arr.shape[1]
        if h != w:
            raise ImageSetError(
                f"Cubemap face {face} must be square: '{face_to_path[face]}' is {w}x{h}."
            )
        if face_w is None:
            face_w = w
        elif w != face_w or h != face_w:
            raise ImageSetError(
                f"All cubemap faces must match resolution; expected {face_w}x{face_w}, "
                f"got {w}x{h} for face {face} ('{face_to_path[face]}')."
            )
        arrays[key] = arr

    equirect_h = 2 * face_w
    equirect_w = 4 * face_w
    out = py360convert.c2e(arrays, equirect_h, equirect_w, cube_format="dict")
    if out.dtype != np.uint8:
        out = np.clip(out, 0, 255).astype(np.uint8)
    return out


def _save_stereo_equirect_outputs(
    left_equi,
    right_equi,
    output_mode: StereoOutputMode,
    *,
    out_path=None,
    out_left_path=None,
    out_right_path=None,
    frame_label=None,
):
    h, w = left_equi.shape[:2]
    if right_equi.shape[:2] != (h, w):
        rh, rw = right_equi.shape[:2]
        msg = (
            "Left and right equirectangular outputs differ in size "
            f"({w}x{h} vs {rw}x{rh})."
        )
        if frame_label is not None:
            msg = f"Frame {frame_label}: left and right equirectangular outputs differ in size ({w}x{h} vs {rw}x{rh})."
        raise ImageSetError(msg)

    if output_mode == StereoOutputMode.EQUIRECTANGULAR_STEREO_OVER_UNDER:
        stacked = np.vstack((left_equi, right_equi))
        save_rgb_u8_jpeg(out_path, stacked, STEREO_JPEG_QUALITY)
    elif output_mode == StereoOutputMode.EQUIRECTANGULAR_MONO_SEPARATE_EYES:
        save_rgb_u8_jpeg(out_left_path, left_equi, STEREO_JPEG_QUALITY)
        save_rgb_u8_jpeg(out_right_path, right_equi, STEREO_JPEG_QUALITY)
    else:
        raise ImageSetError(f"Unsupported stereo output mode: {output_mode!r}")


def _stereo_merge_one_frame(payload):
    """Process-pool worker: one stereo frame (same algorithm as sequential path)."""
    output_mode = StereoOutputMode(payload["output_mode"])
    left_paths = payload["left_paths"]
    right_paths = payload["right_paths"]
    frame_label = payload.get("frame")

    left_equi = _cubemap_to_equirect(left_paths)
    right_equi = _cubemap_to_equirect(right_paths)

    if output_mode == StereoOutputMode.EQUIRECTANGULAR_STEREO_OVER_UNDER:
        _save_stereo_equirect_outputs(
            left_equi,
            right_equi,
            output_mode,
            out_path=payload["out_path"],
            frame_label=frame_label,
        )
    else:
        _save_stereo_equirect_outputs(
            left_equi,
            right_equi,
            output_mode,
            out_left_path=payload["out_left_path"],
            out_right_path=payload["out_right_path"],
            frame_label=frame_label,
        )


def _render_fields_for_output(entry):
    rf = dict(entry["meta"])
    rf["ext"] = entry["ext"].lstrip(".").lower()
    if rf["ext"] == "jpg":
        rf["ext"] = "jpeg"
    return rf


def _ensure_parent_dir(path):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)


def resolve_stereo_output_dir(left_dir, output_dir):
    """Resolved output folder: explicit path, or 'merged_stereo' beside the left eye folder's parent."""
    left_dir = os.path.abspath(left_dir)
    if output_dir is not None and str(output_dir).strip():
        return os.path.abspath(output_dir.strip())
    parent = os.path.dirname(left_dir.rstrip(os.sep))
    return os.path.join(parent, "merged_stereo")


def main(
    left_dir,
    right_dir,
    output_dir=None,
    update_progressbar=None,
    start_time=None,
    cancel_event=None,
    pause_event=None,
    on_frame_status=None,
    frame_start=None,
    frame_end=None,
    max_workers=None,
    output_mode=StereoOutputMode.EQUIRECTANGULAR_STEREO_OVER_UNDER,
    input_naming_template=None,
    output_naming_template=None,
):
    output_mode = coerce_stereo_output_mode(output_mode)
    inp = (input_naming_template or "").strip() or DEFAULT_STEREO_INPUT
    ot_raw = output_naming_template
    if ot_raw is None or not str(ot_raw).strip():
        out_tmpl = (
            DEFAULT_STEREO_OUTPUT_SEPARATE
            if output_mode == StereoOutputMode.EQUIRECTANGULAR_MONO_SEPARATE_EYES
            else DEFAULT_STEREO_OUTPUT_OVER_UNDER
        )
    else:
        out_tmpl = str(ot_raw).strip()

    validate_input_template(inp)
    validate_output_template(
        out_tmpl,
        inp,
        merger="stereo",
        stereo_mode_separate_eyes=(
            output_mode == StereoOutputMode.EQUIRECTANGULAR_MONO_SEPARATE_EYES
        ),
    )

    left_by_frame = _collect_eye_folder(left_dir, "Left", inp)
    right_by_frame = _collect_eye_folder(right_dir, "Right", inp)

    if not left_by_frame or not right_by_frame:
        raise ImageSetError(
            "No valid cubemap face images found. Check the input naming template and ensure "
            "each filename matches {camera_name} (with cubemap face tokens), {frame_number}, "
            "and {ext} (.jpeg / .jpg / .png)."
        )

    _validate_stereo_lr_metadata(left_by_frame, right_by_frame)
    _validate_frames_and_faces(left_by_frame, right_by_frame)

    out_dir = resolve_stereo_output_dir(left_dir, output_dir)
    os.makedirs(out_dir, exist_ok=True)

    ordered = sorted(left_by_frame.keys(), key=_frame_sort_key)
    if frame_start is not None and frame_end is not None:
        frames = filter_stereo_frames_in_range(ordered, frame_start, frame_end)
    else:
        frames = ordered

    total = len(frames)
    if total == 0:
        return

    if max_workers is None:
        max_workers = _default_max_workers()

    if max_workers <= 1:
        _stereo_main_sequential(
            frames,
            total,
            left_by_frame,
            right_by_frame,
            out_dir,
            output_mode,
            out_tmpl,
            update_progressbar,
            start_time,
            cancel_event,
            pause_event,
            on_frame_status,
        )
        return

    indices_frames = list(enumerate(frames))
    next_i = 0
    in_flight = {}
    done_count = 0

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        while next_i < total or in_flight:
            while next_i < total and len(in_flight) < max_workers:
                if cancel_event is not None and cancel_event.is_set():
                    next_i = total
                    break
                if wait_if_paused(cancel_event, pause_event):
                    continue
                if cancel_event is not None and cancel_event.is_set():
                    next_i = total
                    break

                idx, frame = indices_frames[next_i]
                next_i += 1
                left_entry = left_by_frame[frame]
                right_entry = right_by_frame[frame]
                rf = _render_fields_for_output(left_entry)
                payload = {
                    "output_mode": output_mode.value,
                    "left_paths": dict(left_entry["paths"]),
                    "right_paths": dict(right_entry["paths"]),
                    "frame": str(frame),
                }
                if output_mode == StereoOutputMode.EQUIRECTANGULAR_STEREO_OVER_UNDER:
                    rel = render_output_relative(out_tmpl, rf, eye=None)
                    payload["out_path"] = os.path.join(out_dir, rel)
                else:
                    rel_l = render_output_relative(out_tmpl, rf, eye="left_eye")
                    rel_r = render_output_relative(out_tmpl, rf, eye="right_eye")
                    payload["out_left_path"] = os.path.join(out_dir, rel_l)
                    payload["out_right_path"] = os.path.join(out_dir, rel_r)
                for k in ("out_path", "out_left_path", "out_right_path"):
                    if k in payload:
                        _ensure_parent_dir(payload[k])
                fut = executor.submit(_stereo_merge_one_frame, payload)
                in_flight[fut] = (idx, frame)

            if cancel_event is not None and cancel_event.is_set():
                if in_flight:
                    wait(in_flight.keys())
                    for fut in list(in_flight.keys()):
                        idx, frame = in_flight.pop(fut)
                        try:
                            fut.result()
                        except Exception:
                            pass
                        done_count += 1
                        if on_frame_status is not None:
                            on_frame_status(str(frame), idx + 1, total)
                        if update_progressbar is not None:
                            update_progressbar(done_count, total, start_time)
                break

            if not in_flight:
                break

            done, _ = wait(in_flight.keys(), return_when=FIRST_COMPLETED)
            for fut in done:
                idx, frame = in_flight.pop(fut)
                fut.result()
                done_count += 1
                if on_frame_status is not None:
                    on_frame_status(str(frame), idx + 1, total)
                if update_progressbar is not None:
                    update_progressbar(done_count, total, start_time)


def _stereo_main_sequential(
    frames,
    total,
    left_by_frame,
    right_by_frame,
    out_dir,
    output_mode,
    output_template,
    update_progressbar,
    start_time,
    cancel_event,
    pause_event,
    on_frame_status,
):
    for idx, frame in enumerate(frames):
        if cancel_event is not None and cancel_event.is_set():
            break

        if on_frame_status is not None:
            on_frame_status(str(frame), idx + 1, total)

        left_entry = left_by_frame[frame]
        right_entry = right_by_frame[frame]
        rf = _render_fields_for_output(left_entry)

        while True:
            if cancel_event is not None and cancel_event.is_set():
                break
            if wait_if_paused(cancel_event, pause_event):
                continue
            if cancel_event is not None and cancel_event.is_set():
                break

            left_equi = _cubemap_to_equirect(left_entry["paths"])
            if cancel_event is not None and cancel_event.is_set():
                break
            if wait_if_paused(cancel_event, pause_event):
                continue
            if cancel_event is not None and cancel_event.is_set():
                break

            right_equi = _cubemap_to_equirect(right_entry["paths"])

            if cancel_event is not None and cancel_event.is_set():
                break
            if wait_if_paused(cancel_event, pause_event):
                continue
            if cancel_event is not None and cancel_event.is_set():
                break

            if output_mode == StereoOutputMode.EQUIRECTANGULAR_STEREO_OVER_UNDER:
                rel = render_output_relative(output_template, rf, eye=None)
                out_path = os.path.join(out_dir, rel)
                _ensure_parent_dir(out_path)
                _save_stereo_equirect_outputs(
                    left_equi,
                    right_equi,
                    output_mode,
                    out_path=out_path,
                    frame_label=str(frame),
                )
            else:
                rel_l = render_output_relative(output_template, rf, eye="left_eye")
                rel_r = render_output_relative(output_template, rf, eye="right_eye")
                out_left_path = os.path.join(out_dir, rel_l)
                out_right_path = os.path.join(out_dir, rel_r)
                _ensure_parent_dir(out_left_path)
                _ensure_parent_dir(out_right_path)
                _save_stereo_equirect_outputs(
                    left_equi,
                    right_equi,
                    output_mode,
                    out_left_path=out_left_path,
                    out_right_path=out_right_path,
                    frame_label=str(frame),
                )

            if update_progressbar:
                update_progressbar(idx + 1, total, start_time)
            break
