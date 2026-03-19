import os
import re
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait

import numpy as np
import py360convert
from PIL import Image

from errors import ImageSetError

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


def _frame_sort_key(frame):
    return int(frame) if str(frame).isdigit() else frame


def _face_from_viewport(viewport_segment):
    """Return one of FRONT, BACK, LEFT, RIGHT, UP, DOWN, or None if ambiguous/missing."""
    tokens = {t.upper() for t in re.split(r"[^A-Za-z0-9]+", viewport_segment) if t}
    matches = [f for f in _FACE_ORDER if f in tokens]
    if len(matches) == 1:
        return matches[0]
    return None


def _parse_stereo_image_path(input_dir, file_name):
    """
    Expect {LevelSequence}.{Viewport}.{Frame}.{ext} with ext in supported formats.
    Returns (level_sequence, viewport, frame, abs_path, face) or None if skipped.
    """
    lower = file_name.lower()
    if not lower.endswith(_SUPPORTED_EXTS):
        return None
    parts = file_name.split(".")
    if len(parts) != 4:
        return None
    level_sequence, viewport, frame_number, ext = parts
    face = _face_from_viewport(viewport)
    if face is None:
        return None
    return (
        level_sequence,
        viewport,
        frame_number,
        os.path.join(input_dir, file_name),
        face,
    )


def _collect_eye_folder(eye_dir, label):
    """Build frame -> face -> abs_path. Raises ImageSetError on duplicates or ambiguity."""
    if not os.path.isdir(eye_dir):
        raise ImageSetError(f"{label} directory is not a valid folder: '{eye_dir}'.")

    by_frame = {}
    for file_name in os.listdir(eye_dir):
        parsed = _parse_stereo_image_path(eye_dir, file_name)
        if parsed is None:
            continue
        level_sequence, viewport, frame_number, abs_path, face = parsed
        if frame_number not in by_frame:
            by_frame[frame_number] = {"paths": {}, "level_sequence": level_sequence}
        frame_entry = by_frame[frame_number]
        if frame_entry["level_sequence"] != level_sequence:
            raise ImageSetError(
                f"{label} eye: frame {frame_number} mixes level sequence names "
                f"'{frame_entry['level_sequence']}' and '{level_sequence}'."
            )
        if face in frame_entry["paths"]:
            raise ImageSetError(
                f"{label} eye: duplicate {face} face for frame {frame_number} "
                f"('{os.path.basename(frame_entry['paths'][face])}' vs '{file_name}')."
            )
        frame_entry["paths"][face] = abs_path

    return by_frame


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


def list_paired_stereo_frames(left_dir, right_dir):
    """Sorted common frame keys after full stereo validation (for UI auto-fill)."""
    left_by_frame = _collect_eye_folder(left_dir, "Left")
    right_by_frame = _collect_eye_folder(right_dir, "Right")
    if not left_by_frame or not right_by_frame:
        raise ImageSetError(
            "No valid cubemap face images found. Filenames must look like "
            "'{LevelSequence}.{Viewport}.{Frame}.jpeg' and the viewport must contain "
            "exactly one of: BACK, LEFT, FRONT, RIGHT, UP, DOWN (as separate tokens)."
        )
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
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"))


def _cubemap_to_equirect(face_to_path):
    """face_to_path maps FRONT..DOWN to file path. Returns PIL Image RGB."""
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
    return Image.fromarray(out, mode="RGB")


def _stereo_merge_one_frame(payload):
    """Process-pool worker: one stereo frame (same algorithm as sequential path)."""
    left_paths = payload["left_paths"]
    right_paths = payload["right_paths"]
    out_path = payload["out_path"]

    left_equi = _cubemap_to_equirect(left_paths)
    right_equi = _cubemap_to_equirect(right_paths)
    w, h = left_equi.size
    if right_equi.size != (w, h):
        raise ImageSetError(
            f"Left and right equirectangular outputs differ in size "
            f"({w}x{h} vs {right_equi.size[0]}x{right_equi.size[1]})."
        )

    stacked = Image.new("RGB", (w, h * 2))
    stacked.paste(left_equi, (0, 0))
    stacked.paste(right_equi, (0, h))
    stacked.save(out_path, format="JPEG", quality=95)


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
):
    left_by_frame = _collect_eye_folder(left_dir, "Left")
    right_by_frame = _collect_eye_folder(right_dir, "Right")

    if not left_by_frame or not right_by_frame:
        raise ImageSetError(
            "No valid cubemap face images found. Filenames must look like "
            "'{LevelSequence}.{Viewport}.{Frame}.jpeg' and the viewport must contain "
            "exactly one of: BACK, LEFT, FRONT, RIGHT, UP, DOWN (as separate tokens)."
        )

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
                level_sequence = left_entry["level_sequence"]
                out_path = os.path.join(out_dir, f"{level_sequence}.StereoEquirect.{frame}.jpeg")
                payload = {
                    "left_paths": dict(left_entry["paths"]),
                    "right_paths": dict(right_entry["paths"]),
                    "out_path": out_path,
                }
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
        level_sequence = left_entry["level_sequence"]

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
            w, h = left_equi.size
            if right_equi.size != (w, h):
                raise ImageSetError(
                    f"Frame {frame}: left and right equirectangular outputs differ in size "
                    f"({w}x{h} vs {right_equi.size[0]}x{right_equi.size[1]})."
                )

            if cancel_event is not None and cancel_event.is_set():
                break
            if wait_if_paused(cancel_event, pause_event):
                continue
            if cancel_event is not None and cancel_event.is_set():
                break

            stacked = Image.new("RGB", (w, h * 2))
            stacked.paste(left_equi, (0, 0))
            stacked.paste(right_equi, (0, h))

            out_path = os.path.join(out_dir, f"{level_sequence}.StereoEquirect.{frame}.jpeg")
            stacked.save(out_path, format="JPEG", quality=95)

            if update_progressbar:
                update_progressbar(idx + 1, total, start_time)
            break
