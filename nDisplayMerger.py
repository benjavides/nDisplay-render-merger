import argparse
import json
import os
import sys
import time

from PIL import Image

from errors import ConfigError, ImageSetError


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


def _frame_sort_key(frame):
    return int(frame) if str(frame).isdigit() else frame


def find_images(input_dir, viewports):
    images = {}
    # Only support LDR formats; HDR EXR is not supported to avoid losing information.
    supported_formats = [".jpeg", ".jpg", ".png"]

    # Exclude the synthetic 'window' key we add later
    expected_viewports = [name for name in viewports.keys() if name != "window"]

    for file_name in os.listdir(input_dir):
        file_ext = os.path.splitext(file_name)[-1].lower()
        if file_ext in supported_formats:
            for viewport_name in expected_viewports:
                if f".{viewport_name}." in file_name:
                    level_sequence_name, viewport, frame_number, _ = file_name.split(".")
                    if frame_number not in images:
                        images[frame_number] = {}
                    images[frame_number][viewport] = os.path.join(input_dir, file_name)
                    break

    if not images:
        raise ImageSetError(
            f"No images found in '{input_dir}' for expected viewports: {', '.join(expected_viewports)}."
        )

    # Validate that each frame has all expected viewports
    for frame_number, image_files in images.items():
        present = set(image_files.keys())
        missing = sorted(set(expected_viewports) - present)
        if missing:
            raise ImageSetError(
                "Missing viewports for some frames. "
                f"Frame {frame_number} is missing: {', '.join(missing)}. "
                f"Expected viewports: {', '.join(expected_viewports)}."
            )

    return images


def list_legacy_frame_keys(input_dir, ndisplay_config_path):
    """Sorted frame keys for UI auto-fill (full validation via find_images)."""
    viewports, window = read_ndisplay_config(ndisplay_config_path)
    viewports["window"] = window
    images = find_images(input_dir, viewports)
    return sorted(images.keys(), key=_frame_sort_key)


def filter_legacy_frames_in_range(ordered_keys, frame_start, frame_end):
    """Inclusive numeric range on frame keys; ordered_keys must be pre-sorted."""
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
    output_dir=None,
    update_progressbar=None,
    start_time=None,
    cancel_event=None,
    pause_event=None,
    on_frame_status=None,
    frames_to_process=None,
):
    if output_dir is None or output_dir == "":
        output_dir = os.path.join(input_dir, "merged")

    os.makedirs(output_dir, exist_ok=True)

    if frames_to_process is None:
        frames_to_process = sorted(images.keys(), key=_frame_sort_key)

    total = len(frames_to_process)

    for idx, frame_number in enumerate(frames_to_process):
        if cancel_event is not None and cancel_event.is_set():
            break

        if on_frame_status is not None:
            on_frame_status(str(frame_number), idx + 1, total)

        image_files = images[frame_number]

        while True:
            if cancel_event is not None and cancel_event.is_set():
                break
            if wait_if_paused(cancel_event, pause_event):
                continue
            if cancel_event is not None and cancel_event.is_set():
                break

            output_image = Image.new("RGB", (viewports["window"]["w"], viewports["window"]["h"]))
            level_sequence_name = None
            output_ext = None
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
                    viewport_img = Image.open(file_path)
                except Exception as exc:
                    raise ImageSetError(f"Failed to open image '{file_path}': {exc}") from exc

                x = viewports[viewport_name]["region"]["x"]
                y = viewports[viewport_name]["region"]["y"]

                if level_sequence_name is None:
                    file_name = os.path.basename(file_path)
                    name_without_ext, ext = os.path.splitext(file_name)
                    level_sequence_name = name_without_ext.split(os.path.sep)[-1].split(".")[0]
                    output_ext = ext.lower()

                output_image.paste(viewport_img, (x, y))

            if cancel_event is not None and cancel_event.is_set():
                break
            if restart_frame:
                continue

            if wait_if_paused(cancel_event, pause_event):
                continue
            if cancel_event is not None and cancel_event.is_set():
                break

            if not output_ext:
                output_ext = ".jpeg"

            image_path = os.path.join(output_dir, f"{level_sequence_name}.{frame_number}{output_ext}")
            output_image.save(image_path)

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
):
    viewports, window = read_ndisplay_config(ndisplay_config_path)
    viewports["window"] = window
    images = find_images(input_dir, viewports)
    ordered = sorted(images.keys(), key=_frame_sort_key)
    if frame_start is not None and frame_end is not None:
        frames_to_process = filter_legacy_frames_in_range(ordered, frame_start, frame_end)
    else:
        frames_to_process = ordered
    composite_images(
        input_dir,
        viewports,
        images,
        output_dir,
        update_progressbar,
        start_time,
        cancel_event,
        pause_event,
        on_frame_status,
        frames_to_process,
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
    return parser


if __name__ == "__main__":
    parser = _build_arg_parser()
    args = parser.parse_args()

    try:
        fs, fe = args.frame_start, args.frame_end
        if (fs is None) ^ (fe is None):
            print("Both --frame-start and --frame-end are required for a subset export.", file=sys.stderr)
            sys.exit(4)
        if fs is not None:
            main(
                args.input_dir,
                args.ndisplay_config_path,
                output_dir=args.output_dir,
                frame_start=str(fs),
                frame_end=str(fe),
            )
        else:
            ordered = list_legacy_frame_keys(args.input_dir, args.ndisplay_config_path)
            main(
                args.input_dir,
                args.ndisplay_config_path,
                output_dir=args.output_dir,
                frame_start=str(ordered[0]),
                frame_end=str(ordered[-1]),
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
