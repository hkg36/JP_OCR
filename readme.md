**screen.py** the same as Cloe's. It is also built based on manga-ocr. Since Cloe has bugs that no one is maintaining, I simply made this one. The main program is screen.py. You can run test.py to check if the environment is properly configured. If there are any features you need to add, please implement them yourself. I only care about the features I use.

**comic_reader.py** is a ZIP-format comic/manga reader. Although the shortcut keys are keyboard-based, its main purpose is to work with Steam's controller/handheld simulation features (emulating keyboard and mouse input via controller).

### Tcl/Tk comic reader

[comic_reader_tk.py](comic_reader_tk.py) is a separate implementation; the Qt
reader is unchanged. Install Pillow, natsort, PyYAML, pybit7z and Send2Trash.
Python must include Tcl/Tk. pybit7z uses its bundled full `7zip.dll` (not the
ZIP/7z-only `7za.dll`) to read ZIP, RAR, 7z, CBZ and CBR archives.

```powershell
.\3.15t\Scripts\python.exe .\comic_reader_tk.py
# Optional initial archive or image:
.\3.15t\Scripts\python.exe .\comic_reader_tk.py "D:\comics\book.rar"
```

The borderless fullscreen reader shares `reader.yaml` with the original.
Right-click opens the menu; Ctrl+O or middle-click opens a file. Left-click,
Right/PageDown advance; Left/PageUp go back; Up/Down switch between naturally
sorted archives in the same directory. The wheel supports accelerated paging,
the slider seeks, and Escape exits. Opening an image recursively lists its
directory, with direct children before subdirectory images.
Delete moves the current archive, or the **entire image directory** in image
mode, to the recycle bin after confirmation; it never permanently deletes.

Archive opening, extraction, image decoding and adjacent-page preloading all
run on one background thread. Only the current page and its immediate neighbors
are cached. Tk widgets and PhotoImage objects stay on the main thread; queues
transfer decoded images, and a condition protects pending work without relying
on the GIL. New navigation replaces pending work and discards obsolete results.
Exit waits asynchronously for any active native operation before releasing the
archive. Use free-threaded wheels for Pillow and pybit7z; startup warns if a
dependency has enabled the GIL. Password-protected or damaged archives report
an error rather than prompting for a password.

Focused tests: `.\3.15t\Scripts\python.exe -m unittest discover -s tests -p test_comic_reader_tk.py`.

### Tcl/Tk screenshot tool (Windows)

[screen_tk.py](screen_tk.py) is a separate Tk implementation; [screen.py](screen.py)
remains unchanged. Run `python screen_tk.py`, or use the free-threaded environment:

```powershell
.\3.15t\Scripts\python.exe .\screen_tk.py
```

It uses Python's Tcl/Tk, Pillow, pystray, pynput, sounddevice and the existing
OCR/translation dependencies, without importing PySide6. sounddevice plays
VOICEVOX 16-bit PCM WAV audio directly from memory; it does not fall back to
Google TTS, write audio to disk, or support MP3 playback. The tray menu provides
capture, replay, settings and exit.
Alt+Q captures; Alt+W replays. Drag to select, pause for OCR/translation previews,
release to close and read the final OCR result, or right-click/Escape to cancel.
OCR copies text to the clipboard even if recognition finishes after release.
Settings continue to use `conf.yaml`, including local-only OCR model loading.

One worker handles capture, configuration/model loading, local/remote OCR,
translation, speech synthesis and playback. Each operation kind keeps only its
newest **not-yet-started** job, moved to the queue tail; the replaced future is
cancelled. Running jobs are not interrupted. Speech and replay share the audio
kind. Hotkey/tray helper threads only post events; all Tk access happens on the
main thread. Stale OCR/translation results cannot update a newer selection.
Exit cancels queued work and lets the running operation finish before resource
cleanup. Free-threaded compatibility of optional local-model dependencies must
be checked separately; configuring an OCR server avoids importing the local model.

Focused tests: `.\3.15t\Scripts\python.exe -m unittest discover -s tests -p test_screen_tk.py`.

### Local-only OCR model loading

The website backend in [ocrserver.py](ocrserver.py) uses `MangaOcrCPT("flex", local_files_only=True)`.
This loads the Hugging Face cached snapshot without checking for updates or downloading files.
If the cache is missing or incomplete, startup raises an error instead of accessing the network.
Before starting the server, populate the cache once with `MangaOcrCPT("flex")` while online,
using the same Hugging Face cache location as the server, or pass a local model directory containing
`model.safetensors`, `config.json`, and `nar_vocabulary_6817.json`.

The new `local_files_only` parameter defaults to `False`, preserving normal online loading for other callers.
Running [test.py](test.py) with `--not-network` also enables local-only loading.
