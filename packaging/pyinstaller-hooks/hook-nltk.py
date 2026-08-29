"""Collect NLTK code data without ambient user-downloaded corpora."""

from PyInstaller.utils.hooks import collect_data_files

datas = collect_data_files("nltk", include_py_files=False)
hiddenimports = ["nltk.chunk.named_entity"]
