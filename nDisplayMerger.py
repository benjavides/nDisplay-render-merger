import json
import os
import sys
from PIL import Image

def read_ndisplay_config(file_path):
    with open(file_path, 'r') as file:
        config_data = json.load(file)
    
    viewports = config_data['nDisplay']['cluster']['nodes']['Node_0']['viewports']
    window = config_data['nDisplay']['cluster']['nodes']['Node_0']['window']
    
    return viewports, window

def find_images(input_dir, viewports):
    images = {}
    supported_formats = [".jpeg", ".jpg", ".png"]
    for file_name in os.listdir(input_dir):
        file_ext = os.path.splitext(file_name)[-1].lower()
        if file_ext in supported_formats:
            for viewport_name in viewports:
                if f".{viewport_name}." in file_name:
                    level_sequence_name, viewport, frame_number, _ = file_name.split(".")
                    if frame_number not in images:
                        images[frame_number] = {}
                    images[frame_number][viewport] = os.path.join(input_dir, file_name)
                    break
    return images

def composite_images(input_dir, viewports, images, update_progressbar=None, start_time=None):
    output_dir = os.path.join(input_dir, "merged")
    os.makedirs(output_dir, exist_ok=True)
    
    for idx, (frame_number, image_files) in enumerate(images.items()):
        output_image = Image.new("RGB", (viewports['window']['w'], viewports['window']['h']))
        level_sequence_name = None
        for viewport_name, file_path in image_files.items():
            viewport_img = Image.open(file_path)
            x = viewports[viewport_name]['region']['x']
            y = viewports[viewport_name]['region']['y']
            
            if not level_sequence_name:
                level_sequence_name = file_path.split(".")[0].split(os.path.sep)[-1]
            
            output_image.paste(viewport_img, (x, y))
        
        image_path = os.path.join(output_dir, f"{level_sequence_name}.{frame_number}.jpeg")
        output_image.save(image_path)
        
        if update_progressbar:
            update_progressbar(idx + 1, len(images), start_time)

def main(input_dir, ndisplay_config_path, update_progressbar=None, start_time=None):
    print(input_dir, ndisplay_config_path)
    viewports, window = read_ndisplay_config(ndisplay_config_path)
    viewports['window'] = window
    images = find_images(input_dir, viewports)
    if update_progressbar:
        composite_images(input_dir, viewports, images, update_progressbar, start_time)
    else:
        composite_images(input_dir, viewports, images)

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python composite_ndisplay_images.py <input_dir> <ndisplay_config_path>")
        sys.exit(1)

    input_dir = sys.argv[1]
    ndisplay_config_path = sys.argv[2]

    # input_dir = "./MovieRenders"
    # ndisplay_config_path = "./nDisplayConfig.ndisplay"
    
    main(input_dir, ndisplay_config_path)