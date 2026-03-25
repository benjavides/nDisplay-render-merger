import argparse
import json
import os
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait

import numpy as np
from PIL import Image

from errors import ConfigError, ImageSetError
from filename_template import (
    DEFAULT_LEGACY_INPUT,
    DEFAULT_LEGACY_OUTPUT,
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
from jpeg_utils import (
    PIL_DEFAULT_JPEG_QUALITY,
    load_rgba_u8,
    save_pil_rgba_image,
)


def _default_max_workers():
    return min(16, max(1, os.cpu_count() or 4))


def _paste_tile_rgba(canvas: Image.Image, tile_arr: np.ndarray, x: int, y: int) -> None:
    """Paste viewport RGBA onto canvas at (x,y) using alpha; clip to canvas like legacy paste."""
    th, tw = tile_arr.shape[0], tile_arr.shape[1]
    cw, ch = canvas.size
    x1 = max(0, x)
    y1 = max(0, y)
    x2 = min(cw, x + tw)
    y2 = min(ch, y + th)
    if x1 >= x2 or y1 >= y2:
        return
    sx1 = x1 - x
    sy1 = y1 - y
    sub = np.ascontiguousarray(tile_arr[sy1 : sy1 + (y2 - y1), sx1 : sx1 + (x2 - x1)])
    tile_im = Image.fromarray(sub, mode="RGBA")
    canvas.paste(tile_im, (x1, y1), tile_im)


def _legacy_merge_one_frame(payload):
    """Process-pool worker: merge one frame (same algorithm as sequential path)."""
    ww, wh = payload["window_wh"]
    items = payload["items"]
    image_path = payload["out_path"]

    canvas = Image.new("RGBA", (ww, wh), (0, 0, 0, 0))
    for file_path, x, y in items:
        try:
            arr = load_rgba_u8(file_path)
        except Exception as exc:
            raise ImageSetError(f"Failed to open image '{file_path}': {exc}") from exc
        _paste_tile_rgba(canvas, arr, x, y)

    save_pil_rgba_image(image_path, canvas, PIL_DEFAULT_JPEG_QUALITY)


def read_ndisplay_config(file_path):
    with open(file_path, "r") as file:
        try:
            config_data = json.load(file)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"Failed to parse nDisplay config JSON: {exc}") from exc

    try:
        ndisplay = config_data["nDisplay"]
        cluster = ndisplay["cluster"]
        nodes = cluster["nodes"]

        if not isinstance(nodes, dict) or not nodes:
            raise KeyError("nodes")

        # Pick a node deterministically (first key when sorted)
        node_key = sorted(nodes.keys())[0]
        node = nodes[node_key]

        viewports = node["viewports"]
        window = node["window"]
    except (KeyError, TypeError) as exc:
        raise ConfigError(
            "Invalid nDisplay config structure; expected 'nDisplay.cluster.nodes[<node>].viewports' and 'window'."
        ) from exc

    try:
        width = int(window["w"])
        height = int(window["h"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError("Invalid window dimensions in nDisplay config.") from exc

    if width <= 0 or height <= 0:
        raise ConfigError("Window dimensions in nDisplay config must be positive.")

    # Normalize window dimensions to integers while preserving original structure keys
    window["w"] = width
    window["h"] = height

    return viewports, window


def _legacy_makedirs_for(path):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)


def find_images(
    input_dir,
    viewports,
    input_template,
    *,
    require_complete_viewport_sets=True,
):
    """
    Parse each file with input_template. Returns (images, frame_metadata).
    images[job_key][camera_name] = path — job_key is frame_number str, or (render_pass, frame_number).
    frame_metadata[job_key] = {"meta": dict, "ext": ".png"|".jpeg"}

    When require_complete_viewport_sets is False, skips per-job viewport completeness (for UI discovery
    of render passes while renders are still incomplete). Empty directory / no matches still errors.
    """
    validate_input_template(input_template)
    use_rp = input_template_uses_render_pass(input_template)
    images = {}
    frame_metadata = {}
    supported_formats = (".jpeg", ".jpg", ".png")
    expected_viewports = [name for name in viewports.keys() if name != "window"]

    for file_name in os.listdir(input_dir):
        try:
            precheck_input_file_ext(file_name)
        except ImageSetError:
            continue
        file_ext = os.path.splitext(file_name)[-1].lower()
        if file_ext not in supported_formats:
            continue
        fields = parse_basename_with_template(input_template, file_name)
        if not fields:
            continue
        cam = fields.get("camera_name")
        fn = fields.get("frame_number")
        ext_cap = fields.get("ext")
        if cam is None or fn is None or not ext_cap:
            continue
        if cam not in expected_viewports:
            continue
        job_key = make_job_key_from_fields(fields, use_rp)
        if job_key is None:
            continue
        try:
            dotted = dotted_ext_from_capture(ext_cap)
        except ImageSetError:
            continue
        meta = {k: v for k, v in fields.items() if k not in ("camera_name", "ext")}
        path = os.path.join(input_dir, file_name)

        if job_key not in images:
            images[job_key] = {}
            frame_metadata[job_key] = {"meta": meta, "ext": dotted}
        else:
            md = frame_metadata[job_key]
            if md["meta"] != meta or md["ext"] != dotted:
                ctx = (
                    f"Frame {fn} (render pass {job_key[0]})"
                    if isinstance(job_key, tuple)
                    else f"Frame {fn}"
                )
                raise ImageSetError(
                    f"{ctx}: inconsistent naming metadata or extension between viewport files "
                    f"(e.g. '{file_name}' vs another image for the same frame)."
                )
        if cam in images[job_key]:
            ctx = (
                f"frame {fn}, render pass {job_key[0]}"
                if isinstance(job_key, tuple)
                else f"frame {fn}"
            )
            raise ImageSetError(
                f"Duplicate file for {ctx}, viewport {cam}: '{file_name}'."
            )
        images[job_key][cam] = path

    if not images:
        raise ImageSetError(
            f"No images found in '{input_dir}' for expected viewports: {', '.join(expected_viewports)}. "
            "Check the input naming template and file names."
        )

    if require_complete_viewport_sets:
        for job_key, image_files in images.items():
            present = set(image_files.keys())
            missing = sorted(set(expected_viewports) - present)
            if missing:
                fn = frame_number_from_job_key(job_key)
                if isinstance(job_key, tuple):
                    rp = job_key[0]
                    raise ImageSetError(
                        f"Frame {fn} (render pass {rp}) is missing viewports: {', '.join(missing)}. "
                        f"Expected viewports: {', '.join(expected_viewports)}."
                    )
                raise ImageSetError(
                    "Missing viewports for some frames. "
                    f"Frame {fn} is missing: {', '.join(missing)}. "
                    f"Expected viewports: {', '.join(expected_viewports)}."
                )

    return images, frame_metadata


def list_legacy_frame_keys(input_dir, ndisplay_config_path, input_naming_template=None):
    """Sorted job keys for UI (full validation via find_images)."""
    inp = (input_naming_template or "").strip() or DEFAULT_LEGACY_INPUT
    viewports, window = read_ndisplay_config(ndisplay_config_path)
    viewports["window"] = window
    images, _meta = find_images(input_dir, viewports, inp)
    return sorted(images.keys(), key=job_key_sort_key)


def legacy_numeric_frame_span_strings(ordered_job_keys):
    """Min/max frame numbers as strings for UI/CLI when job keys may include render_pass."""
    nums = [
        int(frame_number_from_job_key(k))
        for k in ordered_job_keys
        if str(frame_number_from_job_key(k)).isdigit()
    ]
    if not nums:
        raise ImageSetError("No numeric frame numbers found in the image set.")
    return str(min(nums)), str(max(nums))


def list_legacy_render_passes(input_dir, ndisplay_config_path, input_naming_template=None):
    """Distinct render_pass values from the current scan; empty if template has no {render_pass}."""
    inp = (input_naming_template or "").strip() or DEFAULT_LEGACY_INPUT
    if not input_template_uses_render_pass(inp):
        return []
    viewports, window = read_ndisplay_config(ndisplay_config_path)
    viewports["window"] = window
    images, _meta = find_images(
        input_dir, viewports, inp, require_complete_viewport_sets=False
    )
    passes = sorted({k[0] for k in images if isinstance(k, tuple)})
    return passes


def filter_legacy_frames_in_range(ordered_keys, frame_start, frame_end):
    """Inclusive numeric range on frame_number part of each job key; ordered_keys may be JobKey."""
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


def wait_if_paused(cancel_event, pause_event):
    """
    Block while pause_event is set. Returns True if we waited and the user resumed (not Stop).
    Returns False if not paused, or if cancelled (including Stop, which clears pause).
    """
    if pause_event is None or not pause_event.is_set():
        return False
    while pause_event.is_set():
        if cancel_event is not None and cancel_event.is_set():
            return False
        time.sleep(0.05)
    if cancel_event is not None and cancel_event.is_set():
        return False
    return True


def composite_images(
    input_dir,
    viewports,
    images,
    frame_metadata,
    output_template,
    output_dir=None,
    update_progressbar=None,
    start_time=None,
    cancel_event=None,
    pause_event=None,
    on_frame_status=None,
    frames_to_process=None,
    max_workers=None,
):
    if output_dir is None or output_dir == "":
        output_dir = os.path.join(input_dir, "merged")

    os.makedirs(output_dir, exist_ok=True)

    if frames_to_process is None:
        frames_to_process = sorted(images.keys(), key=job_key_sort_key)

    total = len(frames_to_process)
    if total == 0:
        return

    if max_workers is None:
        max_workers = _default_max_workers()

    if max_workers <= 1:
        _composite_images_sequential(
            output_dir,
            viewports,
            images,
            frame_metadata,
            output_template,
            frames_to_process,
            total,
            update_progressbar,
            start_time,
            cancel_event,
            pause_event,
            on_frame_status,
        )
        return

    ww, wh = viewports["window"]["w"], viewports["window"]["h"]
    indices_frames = list(enumerate(frames_to_process))
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
                image_files = images[job_key]
                items = [
                    (
                        file_path,
                        viewports[viewport_name]["region"]["x"],
                        viewports[viewport_name]["region"]["y"],
                    )
                    for viewport_name, file_path in sorted(image_files.items())
                ]
                md = frame_metadata[job_key]
                rf = dict(md["meta"])
                rf["ext"] = md["ext"].lstrip(".").lower()
                if rf["ext"] == "jpg":
                    rf["ext"] = "jpeg"
                rel = render_output_relative(output_template, rf)
                out_path = os.path.join(output_dir, rel)
                _legacy_makedirs_for(out_path)
                payload = {
                    "out_path": out_path,
                    "window_wh": (ww, wh),
                    "items": items,
                }
                fut = executor.submit(_legacy_merge_one_frame, payload)
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


def _composite_images_sequential(
    output_dir,
    viewports,
    images,
    frame_metadata,
    output_template,
    frames_to_process,
    total,
    update_progressbar,
    start_time,
    cancel_event,
    pause_event,
    on_frame_status,
):
    for idx, job_key in enumerate(frames_to_process):
        if cancel_event is not None and cancel_event.is_set():
            break

        if on_frame_status is not None:
            on_frame_status(job_status_label(job_key), idx + 1, total)

        image_files = images[job_key]

        while True:
            if cancel_event is not None and cancel_event.is_set():
                break
            if wait_if_paused(cancel_event, pause_event):
                continue
            if cancel_event is not None and cancel_event.is_set():
                break

            ww, wh = viewports["window"]["w"], viewports["window"]["h"]
            canvas = Image.new("RGBA", (ww, wh), (0, 0, 0, 0))
            restart_frame = False

            for viewport_name, file_path in sorted(image_files.items()):
                if cancel_event is not None and cancel_event.is_set():
                    break
                if wait_if_paused(cancel_event, pause_event):
                    restart_frame = True
                    break
                if cancel_event is not None and cancel_event.is_set():
                    restart_frame = True
                    break
                try:
                    arr = load_rgba_u8(file_path)
                except Exception as exc:
                    raise ImageSetError(f"Failed to open image '{file_path}': {exc}") from exc

                x = viewports[viewport_name]["region"]["x"]
                y = viewports[viewport_name]["region"]["y"]
                _paste_tile_rgba(canvas, arr, x, y)

            if cancel_event is not None and cancel_event.is_set():
                break
            if restart_frame:
                continue

            if wait_if_paused(cancel_event, pause_event):
                continue
            if cancel_event is not None and cancel_event.is_set():
                break

            md = frame_metadata[job_key]
            rf = dict(md["meta"])
            rf["ext"] = md["ext"].lstrip(".").lower()
            if rf["ext"] == "jpg":
                rf["ext"] = "jpeg"
            rel = render_output_relative(output_template, rf)
            image_path = os.path.join(output_dir, rel)
            _legacy_makedirs_for(image_path)
            save_pil_rgba_image(image_path, canvas, PIL_DEFAULT_JPEG_QUALITY)

            if update_progressbar:
                update_progressbar(idx + 1, total, start_time)
            break


def main(
    input_dir,
    ndisplay_config_path,
    update_progressbar=None,
    start_time=None,
    output_dir=None,
    cancel_event=None,
    pause_event=None,
    on_frame_status=None,
    frame_start=None,
    frame_end=None,
    max_workers=None,
    input_naming_template=None,
    output_naming_template=None,
    render_passes_to_process=None,
):
    inp = (input_naming_template or "").strip() or DEFAULT_LEGACY_INPUT
    out_tmpl = (output_naming_template or "").strip() or DEFAULT_LEGACY_OUTPUT
    validate_input_template(inp)
    use_rp = input_template_uses_render_pass(inp)

    viewports, window = read_ndisplay_config(ndisplay_config_path)
    viewports["window"] = window
    images, frame_metadata = find_images(input_dir, viewports, inp)
    ordered = sorted(images.keys(), key=job_key_sort_key)
    if frame_start is not None and frame_end is not None:
        frames_to_process = filter_legacy_frames_in_range(ordered, frame_start, frame_end)
    else:
        frames_to_process = ordered
    if render_passes_to_process is not None and use_rp:
        allow = frozenset(render_passes_to_process)
        frames_to_process = [
            k for k in frames_to_process if isinstance(k, tuple) and k[0] in allow
        ]
        if not frames_to_process:
            raise ImageSetError(
                "No jobs to process: no frames match the selected render pass(es) and frame range."
            )

    require_rp_in_output = False
    if use_rp:
        if render_passes_to_process is not None:
            require_rp_in_output = len(render_passes_to_process) > 1
        else:
            passes_in_batch = {k[0] for k in frames_to_process if isinstance(k, tuple)}
            require_rp_in_output = len(passes_in_batch) > 1

    validate_output_template(
        out_tmpl,
        inp,
        merger="legacy",
        stereo_mode_separate_eyes=False,
        require_render_pass_in_output=require_rp_in_output,
    )
    composite_images(
        input_dir,
        viewports,
        images,
        frame_metadata,
        out_tmpl,
        output_dir,
        update_progressbar,
        start_time,
        cancel_event,
        pause_event,
        on_frame_status,
        frames_to_process,
        max_workers,
    )


def _build_arg_parser():
    parser = argparse.ArgumentParser(
        description="Compose nDisplay viewport renders into merged frames using an exported .ndisplay config."
    )
    parser.add_argument(
        "input_dir",
        help="Folder containing the rendered viewport images.",
    )
    parser.add_argument(
        "ndisplay_config_path",
        help="Path to the exported .ndisplay config file (JSON).",
    )
    parser.add_argument(
        "--output-dir",
        dest="output_dir",
        default=None,
        help="Optional output directory. If not provided, a 'merged' folder will be created inside the input directory.",
    )
    parser.add_argument(
        "--frame-start",
        dest="frame_start",
        default=None,
        help="Inclusive start frame (integer). Required with --frame-end for CLI subset export.",
    )
    parser.add_argument(
        "--frame-end",
        dest="frame_end",
        default=None,
        help="Inclusive end frame (integer). Required with --frame-start for CLI subset export.",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=None,
        metavar="N",
        help="Parallel frame workers (default: min(16, CPU count); use 1 for sequential).",
    )
    return parser


if __name__ == "__main__":
    parser = _build_arg_parser()
    args = parser.parse_args()

    try:
        fs, fe = args.frame_start, args.frame_end
        if (fs is None) ^ (fe is None):
            print("Both --frame-start and --frame-end are required for a subset export.", file=sys.stderr)
            sys.exit(4)
        kw = {"output_dir": args.output_dir, "max_workers": args.jobs}
        if fs is not None:
            main(
                args.input_dir,
                args.ndisplay_config_path,
                frame_start=str(fs),
                frame_end=str(fe),
                **kw,
            )
        else:
            ordered = list_legacy_frame_keys(args.input_dir, args.ndisplay_config_path)
            fs, fe = legacy_numeric_frame_span_strings(ordered)
            main(
                args.input_dir,
                args.ndisplay_config_path,
                frame_start=fs,
                frame_end=fe,
                **kw,
            )
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        sys.exit(2)
    except ImageSetError as exc:
        print(f"Image set error: {exc}", file=sys.stderr)
        sys.exit(3)
    except Exception as exc:
        print(f"Unexpected error: {exc}", file=sys.stderr)
        sys.exit(1)
