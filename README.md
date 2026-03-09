# nDisplay Merger

**nDisplay Merger** helps you composite images rendered with nDisplay using Unreal Engine’s Movie Render Queue (UE 5.1+).

[!["Buy Me A Coffee"](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/benjavides)

When rendering nDisplay with Movie Render Queue, Unreal outputs **one image per viewport per frame** and does **not** compose the viewports according to the `Output Mapping` in the nDisplay configuration.  
nDisplay Merger takes:

1. The **folder with the rendered images**
2. The **nDisplay configuration file** (the same one that defines the Output Mapping)

and produces **one merged image per frame**, laid out exactly as defined in the nDisplay config.

![nDisplay Merger output mapping](./assets/image-20230415000442107.png)

---

### Requirements

- **Python 3.9+** (tested with 3.9/3.10)
- Unreal Engine **5.1 or later** (for Movie Render Queue with nDisplay)
- The Python dependencies in `requirements.txt`

Create and activate a virtual environment (recommended):

```bash
# from the project root
python -m venv .venv

# Windows (PowerShell)
.venv\Scripts\Activate.ps1

# Windows (cmd.exe)
.venv\Scripts\activate.bat

pip install -r requirements.txt
```

---

### How to Use (GUI – recommended)

1. **Create your nDisplay config**
   - In Unreal, set up your nDisplay configuration as usual.
     ![nDisplay config step](./assets/image-20230417145050771.png)
   - Make sure the **Output Mapping (STEP 3)** is correctly configured – this defines how the viewports will be laid out in the final image.
   - Export the configuration as an `.ndisplay` file.
     ![Export nDisplay config](./assets/image-20230417145134182.png)
   - The exported config is what nDisplay Merger will read.
   
2. **Render with Movie Render Queue (nDisplay)**
   - Use Movie Render Queue with your nDisplay setup (UE 5.1+).
     ![Movie Render Queue setup](./assets/image-20230417145432511.png)
   - The render output should be a folder containing images named per viewport and frame (e.g. `LevelSequence.Segmento0.0001.jpeg`).
   
     ![Movie Render Queue settings](./assets/image-20230417145547068.png)
   
3. **Run the nDisplay Merger app**
   - Launch the compiled executable `nDisplayMerger.exe` (see “Compile to Executable” below) or run the `ui.py` script with Python.
   - In the UI:
     - Select the **input directory**: the folder that contains the rendered images.
     - Select the **nDisplay config**: the exported `.ndisplay` file.
   - Click **“Run Compositor”**.
     ![nDisplay Merger UI](./assets/image-20230417145652092.png)
   
4. **Review the result**
   - The tool creates a `merged` subfolder inside the input directory.
   - You will get **one composed image per frame**, following the Output Mapping from the config.
     ![Merged frame result](./assets/image-20230417150432899.png)



---

### Command Line Usage

You can also run nDisplay Merger directly from the command line:

```bash
python .\nDisplayMerger.py .\Example\MovieRenders .\Example\nDisplayConfig.ndisplay
```

Where:

- `.\Example\MovieRenders` is the folder with the rendered viewport images.
- `.\Example\nDisplayConfig.ndisplay` is the exported nDisplay config file.

This will create a `merged` folder inside `.\Example\MovieRenders` with one composed image per frame.

---

### Compile to Executable (Windows)

If you want to ship a standalone executable (no Python required for end users), you can build it with PyInstaller:

```bash
python -m PyInstaller --onefile --windowed ui.py --additional-hooks-dir=. --name=nDisplayMerger --icon=assets\app.ico
```

This will generate `dist\nDisplayMerger.exe`, which you can distribute to artists/TDs. The application name is **nDisplay Merger**, and the executable file name is `nDisplayMerger.exe`.

