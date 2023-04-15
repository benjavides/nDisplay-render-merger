"""pyinstaller hook file.
You need to use this hook-file if you are packaging a project using tkinterdnd2.
Just put hook-tkinterdnd2.py in the same directory where you call pyinstaller and type:
    pyinstaller myproject/myproject.py --additional-hooks-dir=.
"""

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

hiddenimports = collect_submodules('tkinterdnd2')
datas = collect_data_files('tkinterdnd2')