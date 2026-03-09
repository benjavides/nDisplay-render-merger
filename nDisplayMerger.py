import argparse
import json
import os
import sys

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

def find_images(input_dir, viewports):
    images = {}
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

def composite_images(input_dir, viewports, images, update_progressbar=None, start_time=None):
    output_dir = os.path.join(input_dir, "merged")
    os.makedirs(output_dir, exist_ok=True)
    
    # Process frames in a deterministic order
    def _frame_sort_key(frame):
        return int(frame) if frame.isdigit() else frame

    for idx, frame_number in enumerate(sorted(images.keys(), key=_frame_sort_key)):
        image_files = images[frame_number]
        output_image = Image.new("RGB", (viewports['window']['w'], viewports['window']['h']))
        level_sequence_name = None
        for viewport_name, file_path in image_files.items():
            viewport_img = Image.open(file_path)
            x = viewports[viewport_name]['region']['x']
            y = viewports[viewport_name]['region']['y']
            
            if not level_sequence_name:
                file_name = os.path.basename(file_path)
                level_sequence_name = file_name.split(".")[0].split(os.path.sep)[-1]
            
            output_image.paste(viewport_img, (x, y))
        
        image_path = os.path.join(output_dir, f"{level_sequence_name}.{frame_number}.jpeg")
        output_image.save(image_path)
        # print(f"Saved image to {image_path}")  # Add this line
        
        if update_progressbar:
            update_progressbar(idx + 1, len(images), start_time)

def main(input_dir, ndisplay_config_path, update_progressbar=None, start_time=None):
    viewports, window = read_ndisplay_config(ndisplay_config_path)
    viewports["window"] = window
    images = find_images(input_dir, viewports)
    if update_progressbar:
        composite_images(input_dir, viewports, images, update_progressbar, start_time)
    else:
        composite_images(input_dir, viewports, images)


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
    return parser


if __name__ == "__main__":
    parser = _build_arg_parser()
    args = parser.parse_args()

    try:
        main(args.input_dir, args.ndisplay_config_path)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        sys.exit(2)
    except ImageSetError as exc:
        print(f"Image set error: {exc}", file=sys.stderr)
        sys.exit(3)
    except Exception as exc:
        print(f"Unexpected error: {exc}", file=sys.stderr)
        sys.exit(1)