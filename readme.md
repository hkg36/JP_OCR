**screen.py** the same as Cloe's. It is also built based on manga-ocr. Since Cloe has bugs that no one is maintaining, I simply made this one. The main program is screen.py. You can run test.py to check if the environment is properly configured. If there are any features you need to add, please implement them yourself. I only care about the features I use.

**comic_reader.py** is a ZIP-format comic/manga reader. Although the shortcut keys are keyboard-based, its main purpose is to work with Steam's controller/handheld simulation features (emulating keyboard and mouse input via controller).

### Local-only OCR model loading

The website backend in [ocrserver.py](ocrserver.py) uses `MangaOcrCPT("flex", local_files_only=True)`.
This loads the Hugging Face cached snapshot without checking for updates or downloading files.
If the cache is missing or incomplete, startup raises an error instead of accessing the network.
Before starting the server, populate the cache once with `MangaOcrCPT("flex")` while online,
using the same Hugging Face cache location as the server, or pass a local model directory containing
`model.safetensors`, `config.json`, and `nar_vocabulary_6817.json`.

The new `local_files_only` parameter defaults to `False`, preserving normal online loading for other callers.
Running [test.py](test.py) with `--not-network` also enables local-only loading.
