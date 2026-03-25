import os
import re
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from enum import Enum

import numpy as np
import py360convert
from errors import ImageSetError

from jpeg_utils import STEREO_JPEG_QUALITY, load_rgba_u8, save_u8_image
from filename_template import (
    DEFAULT_STEREO_INPUT,
    DEFAULT_STEREO_OUTPUT_OVER_UNDER,
    DEFAULT_STEREO_OUTPUT_SEPARATE,
    dotted_ext_from_capture,
    frame_number_from_job_key,
    input_template_uses_render_pass,
    job_key_sort_key,
    job_status_label,
    make_job_key_from_fields,
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


def _face_from_viewport(viewport_segment):
    """Return one of FRONT, BACK, LEFT, RIGHT, UP, DOWN, or None if ambiguous/missing."""
    tokens = {t.upper() for t in re.split(r"[^A-Za-z0-9]+", viewport_segment) if t}
    matches = [f for f in _FACE_ORDER if f in tokens]
    if len(matches) == 1:
        return matches[0]
    return None


def _collect_eye_folder(eye_dir, label, input_template):
    """Build job_key -> {paths, meta, ext}. Raises ImageSetError on duplicates or ambiguity."""
    if not os.path.isdir(eye_dir):
        raise ImageSetError(f"{label} directory is not a valid folder: '{eye_dir}'.")

    validate_input_template(input_template)
    use_rp = input_template_uses_render_pass(input_template)
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
        job_key = make_job_key_from_fields(fields, use_rp)
        if job_key is None:
            continue
        try:
            dotted = dotted_ext_from_capture(ext_cap)
        except ImageSetError:
            continue
        meta = {k: v for k, v in fields.items() if k not in ("camera_name", "ext")}
        abs_path = os.path.join(eye_dir, file_name)

        if job_key not in by_frame:
            by_frame[job_key] = {"paths": {}, "meta": meta, "ext": dotted}
        frame_entry = by_frame[job_key]
        if frame_entry["meta"] != meta:
            ctx = job_status_label(job_key)
            raise ImageSetError(
                f"{label} eye: job {ctx} mixes metadata between files "
                f"(e.g. '{file_name}' vs another image in the same frame)."
            )
        if frame_entry["ext"] != dotted:
            ctx = job_status_label(job_key)
            raise ImageSetError(
                f"{label} eye: job {ctx} mixes file extensions "
                f"('{frame_entry['ext']}' vs '{dotted}')."
            )
        if face in frame_entry["paths"]:
            ctx = job_status_label(job_key)
            raise ImageSetError(
                f"{label} eye: duplicate {face} face for job {ctx} "
                f"('{os.path.basename(frame_entry['paths'][face])}' vs '{file_name}')."
            )
        frame_entry["paths"][face] = abs_path

    return by_frame


def _validate_stereo_lr_metadata(left_by_frame, right_by_frame):
    for job_key in left_by_frame:
        if job_key not in right_by_frame:
            continue
        l, r = left_by_frame[job_key], right_by_frame[job_key]
        if l["meta"] != r["meta"] or l["ext"] != r["ext"]:
            ctx = job_status_label(job_key)
            raise ImageSetError(
                f"Job {ctx}: left and right eye folders disagree on naming metadata or extension."
            )


def _validate_frames_and_faces(left_by_frame, right_by_frame):
    left_frames = set(left_by_frame.keys())
    right_frames = set(right_by_frame.keys())
    if left_frames != right_frames:
        only_left = sorted(left_frames - right_frames, key=job_key_sort_key)
        only_right = sorted(right_frames - left_frames, key=job_key_sort_key)
        fmt = job_status_label
        raise ImageSetError(
            "Left and right eye folders do not contain the same jobs (frame / render pass). "
            f"Only in left: {[fmt(x) for x in only_left] or 'none'}. "
            f"Only in right: {[fmt(x) for x in only_right] or 'none'}."
        )

    errors = []
    expected = list(_FACE_TO_CUBE_KEY.keys())
    for job_key in sorted(left_frames, key=job_key_sort_key):
        label = job_status_label(job_key)
        for side_name, data in (
            ("Left", left_by_frame[job_key]),
            ("Right", right_by_frame[job_key]),
        ):
            present = set(data["paths"].keys())
            missing = [f for f in expected if f not in present]
            if missing:
                errors.append(
                    f"{side_name} eye job {label}: missing face(s) {', '.join(missing)} "
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
    return sorted(left_by_frame.keys(), key=job_key_sort_key)


def list_stereo_render_passes(left_dir, right_dir, input_naming_template=None):
    """
    Distinct render_pass values for jobs present in both eye folders.
    Does not require complete cubemap sets (unlike list_paired_stereo_frames) so the UI can list
    passes while renders are still incomplete.
    """
    inp = (input_naming_template or "").strip() or DEFAULT_STEREO_INPUT
    if not input_template_uses_render_pass(inp):
        return []
    left_by_frame = _collect_eye_folder(left_dir, "Left", inp)
    right_by_frame = _collect_eye_folder(right_dir, "Right", inp)
    if not left_by_frame or not right_by_frame:
        return []
    common = set(left_by_frame.keys()) & set(right_by_frame.keys())
    passes = sorted({k[0] for k in common if isinstance(k, tuple)})
    return passes


def stereo_numeric_frame_span_strings(ordered_job_keys):
    nums = [
        int(frame_number_from_job_key(k))
        for k in ordered_job_keys
        if str(frame_number_from_job_key(k)).isdigit()
    ]
    if not nums:
        raise ImageSetError("No numeric frame numbers found in the stereo image set.")
    return str(min(nums)), str(max(nums))


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
    frame_parts = [frame_number_from_job_key(k) for k in ordered_keys]
    non_digit = [f for f in frame_parts if not str(f).isdigit()]
    if non_digit:
        raise ImageSetError(
            "Frame range export requires numeric frame numbers only; "
            f"non-numeric frames present: {', '.join(map(str, non_digit[:5]))}"
            + (" …" if len(non_digit) > 5 else "")
        )
    filtered = [
        k
        for k in ordered_keys
        if start_i <= int(frame_number_from_job_key(k)) <= end_i
    ]
    if not filtered:
        raise ImageSetError(
            f"No frames fall in range {start_i}–{end_i} (inclusive) for the current image set."
        )
    return filtered


def _load_face_rgba(path):
    """H×W×4 uint8; cubemap→equirect preserves alpha (JPEG faces use opaque alpha)."""
    return load_rgba_u8(path)


def _cubemap_to_equirect(face_to_path):
    """face_to_path maps FRONT..DOWN to file path. Returns H×W×4 uint8 RGBA (py360convert samples each channel)."""
    arrays = {}
    face_w = face_h = None
    for face, key in _FACE_TO_CUBE_KEY.items():
        arr = _load_face_rgba(face_to_path[face])
        if arr.ndim != 3 or arr.shape[2] != 4:
            raise ImageSetError(
                f"Expected RGBA image for face {face}: '{face_to_path[face]}' "
                f"(got shape {arr.shape})."
            )
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
        save_u8_image(out_path, stacked, STEREO_JPEG_QUALITY)
    elif output_mode == StereoOutputMode.EQUIRECTANGULAR_MONO_SEPARATE_EYES:
        save_u8_image(out_left_path, left_equi, STEREO_JPEG_QUALITY)
        save_u8_image(out_right_path, right_equi, STEREO_JPEG_QUALITY)
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
    render_passes_to_process=None,
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
    use_rp = input_template_uses_render_pass(inp)

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

    ordered = sorted(left_by_frame.keys(), key=job_key_sort_key)
    if frame_start is not None and frame_end is not None:
        frames = filter_stereo_frames_in_range(ordered, frame_start, frame_end)
    else:
        frames = ordered
    if render_passes_to_process is not None and use_rp:
        allow = frozenset(render_passes_to_process)
        frames = [k for k in frames if isinstance(k, tuple) and k[0] in allow]
        if not frames:
            raise ImageSetError(
                "No stereo jobs to process: no frames match the selected render pass(es) and frame range."
            )

    require_rp_in_output = False
    if use_rp:
        if render_passes_to_process is not None:
            require_rp_in_output = len(render_passes_to_process) > 1
        else:
            passes_in_batch = {k[0] for k in frames if isinstance(k, tuple)}
            require_rp_in_output = len(passes_in_batch) > 1

    validate_output_template(
        out_tmpl,
        inp,
        merger="stereo",
        stereo_mode_separate_eyes=(
            output_mode == StereoOutputMode.EQUIRECTANGULAR_MONO_SEPARATE_EYES
        ),
        require_render_pass_in_output=require_rp_in_output,
    )

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

                idx, job_key = indices_frames[next_i]
                next_i += 1
                left_entry = left_by_frame[job_key]
                right_entry = right_by_frame[job_key]
                rf = _render_fields_for_output(left_entry)
                payload = {
                    "output_mode": output_mode.value,
                    "left_paths": dict(left_entry["paths"]),
                    "right_paths": dict(right_entry["paths"]),
                    "frame": job_status_label(job_key),
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
                in_flight[fut] = (idx, job_key)

            if cancel_event is not None and cancel_event.is_set():
                if in_flight:
                    wait(in_flight.keys())
                    for fut in list(in_flight.keys()):
                        idx, job_key = in_flight.pop(fut)
                        try:
                            fut.result()
                        except Exception:
                            pass
                        done_count += 1
                        if on_frame_status is not None:
                            on_frame_status(job_status_label(job_key), idx + 1, total)
                        if update_progressbar is not None:
                            update_progressbar(done_count, total, start_time)
                break

            if not in_flight:
                break

            done, _ = wait(in_flight.keys(), return_when=FIRST_COMPLETED)
            for fut in done:
                idx, job_key = in_flight.pop(fut)
                fut.result()
                done_count += 1
                if on_frame_status is not None:
                    on_frame_status(job_status_label(job_key), idx + 1, total)
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
    for idx, job_key in enumerate(frames):
        if cancel_event is not None and cancel_event.is_set():
            break

        if on_frame_status is not None:
            on_frame_status(job_status_label(job_key), idx + 1, total)

        left_entry = left_by_frame[job_key]
        right_entry = right_by_frame[job_key]
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
                    frame_label=job_status_label(job_key),
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
                    frame_label=job_status_label(job_key),
                )

            if update_progressbar:
                update_progressbar(idx + 1, total, start_time)
            break
