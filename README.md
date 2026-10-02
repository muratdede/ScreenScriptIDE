# ScreenScript

Visual automation for Android. Write short Python scripts that look at the screen, find images or text,
and tap, swipe and type, just like a person would. ScreenScript includes its own editor with a live
device screen, an image capture tool, a debugger and a "clicker" mode that lets you use the device
directly from the editor.

It works with Android emulators (BlueStacks, LDPlayer, …) and real devices over ADB. Capturing happens
inside Android, so it keeps working while the emulator window is minimized or hidden.

```python
# Wait for the "Play" button, tap it, then confirm the dialog
wait("play.png", timeout=10)
click("play.png")

if exists("dialog/ok.png", timeout=3):
    click("dialog/ok.png")

coins = Region(900, 20, 220, 60).text()
log("Coins: %s", coins)
```

---

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Script API](#script-api)
- [Configuration](#configuration)
- [The editor](#the-editor)
- [Keyboard shortcuts](#keyboard-shortcuts)
- [Troubleshooting](#troubleshooting)

---

## Features

- **Image search**: find an image on the screen even when it is slightly scaled, with an adjustable
  similarity threshold.
- **Text recognition (OCR)**: read numbers and text from the screen, find and tap text.
- **Touch input**: tap, long press, swipe, drag and drop, type text, press Android keys.
- **Fast live capture**: about 30–35 FPS at 1080p, also while the emulator is minimized.
- **Editor**: code completion, parameter hints, go to definition, find and replace (also across all
  files), multi-cursor editing, problem highlighting.
- **Debugger**: breakpoints, step over / into / out, variables, call stack, expression evaluation.
- **Screen panel**: live device view, select an area and save it as an image, insert coordinates into
  your code, test how well an image matches before running a script.
- **Clicker mode**: control the device from the editor with your mouse and keyboard.
- **Telegram notifications**: send messages and screenshots from your scripts; ask for a screenshot
  from your phone with `/capture`.
- **English and Turkish** interface.

---

## Requirements

| | |
|---|---|
| Operating system | Windows 10/11. Developed and tested on Windows; emulator auto-detection and Windows OCR are Windows only. |
| Python | 3.13 (tested). Recent 3.x versions are expected to work. |
| Android | An emulator or device with **ADB enabled**. Fast capture (`stream`) needs Android 5.0+; older versions use the slower `screencap` automatically. |
| ADB | Any `adb` works. If you do not set a path, ScreenScript looks in `PATH`, Android platform-tools, LDPlayer and BlueStacks (`HD-Adb.exe`). |

**Python packages** (installed from `requirements.txt`):

| Package | Needed for |
|---|---|
| PySide6 ≥ 6.5 | The editor |
| opencv-python, numpy | Capturing, image search |
| jedi ≥ 0.19 | Code completion and go to definition (optional; a simpler completion is used without it) |
| pyflakes ≥ 3.0 | Problem highlighting (optional) |
| rapidocr + onnxruntime | Text recognition (optional, recommended) |

**Text recognition** is optional. Install one of the two engines:

- **RapidOCR** (recommended, more accurate): `pip install rapidocr onnxruntime`
- **Windows OCR** (faster, uses the OCR languages installed in Windows):
  `pip install winrt-Windows.Media.Ocr winrt-Windows.Graphics.Imaging winrt-Windows.Storage.Streams winrt-Windows.Globalization winrt-Windows.Foundation winrt-Windows.Foundation.Collections`

### Enabling ADB in your emulator

- **BlueStacks**: Settings → Advanced → *Android Debug Bridge (ADB)* → on. The address shown there
  (usually `127.0.0.1:5555`) is what you enter in ScreenScript.
- **LDPlayer**: Settings → Other settings → *ADB debugging* → *Open local connection*.
- **Real device**: enable *Developer options* → *USB debugging*, connect the cable and accept the prompt
  on the phone.

---

## Installation

```
git clone <repository-url>
cd <repository-folder>
pip install -r requirements.txt
```

Start the editor:

```
python main.pyw                  # opens the last project
python main.pyw path\to\folder   # opens a folder as a project
```

On Windows you can also double-click `main.pyw`.

---

## Quick start

1. **Connect the device.** Open **Settings** (`Ctrl+Alt+S`) → *Device → Connection* and enter the host
   and port of your emulator (for example `127.0.0.1` and `5555`). Leave *adb path* empty to find it
   automatically.
2. **Open a project.** A project is just a folder. Use *Open Project* (`Ctrl+Shift+O`) and choose any
   folder. ScreenScript creates `images/`, `main.py` and `.screenscript.json` in it if they are missing.
   An example project is in `examples/ornek_proje`.
3. **Capture the screen.** Press **Capture** in the *Screen* panel (`Ctrl+Shift+G`). The device screen
   appears live.
4. **Save an image.** Drag a rectangle around a button and press **Save image**. It is saved to `images/`
   and can be used by name in scripts.
5. **Write the script.** For example `click("button.png")`. You can also drag images from the *Images*
   panel into the code; their path is inserted for you.
6. **Run it.** `Shift+F10` runs the script, `F5` runs it in the debugger. The output appears in the
   *Console* panel.

---

## Script API

Everything below is available in every script without importing anything.

### How images are found

You refer to images by file name: `"button.png"`, `"menu/close.png"` or just `"button"` (`.png` is
added automatically). Names are looked up in this order:

1. the project folder,
2. the image folders from the project settings (default: `images/`),
3. the folder of the running script and an `images/` folder next to it,
4. folders you add with `add_image_path(...)`.

Absolute paths also work. Anywhere an image is expected you can also pass a `Pattern` (see below) or an
image you captured (`numpy` array).

### Global names

| Name | Description |
|---|---|
| `SCREEN` | The whole screen, a `Region`. `SCREEN.w`, `SCREEN.h` give the screen size. |
| `SETTINGS` | Run-time settings; you can change them while the script runs (see [SETTINGS](#settings)). |
| `ARGS` | Script arguments from the project settings, a `dict` of strings: `ARGS.get("level", "1")`. |

### Searching for images

These functions search the whole screen. The same methods exist on every `Region` and search only
inside that region.

| Function | Returns | Description |
|---|---|---|
| `find(image)` | `Match` | Waits up to the default wait time (*Wait time*, 3 s by default). Raises `FindFailed` if the image does not appear. |
| `wait(image, timeout=None)` | `Match` | Waits up to `timeout` seconds. Raises `FindFailed` if it does not appear. |
| `exists(image, timeout=None)` | `Match` or `None` | Like `wait`, but returns `None` instead of raising. `exists(image, 0)` checks once without waiting. |
| `find_all(image)` | `list[Match]` | All places where the image appears, best first. |
| `wait_vanish(image, timeout=None)` | `bool` | Waits until the image disappears. `True` if it disappeared in time. |
| `best_score(image)` | `float` | Similarity (0–1) of the best match, ignoring the threshold. Useful to choose a threshold. |

When `timeout` is not given, the project's *Wait time* is used. While waiting, the screen is checked
*Scan rate* times per second.

### Reading text (OCR)

Requires an OCR engine (see [Requirements](#requirements)).

| Function | Returns | Description |
|---|---|---|
| `read_text(scale=1.0)` | `str` | All text in the region. Parts on the same line are joined with spaces, lines with newlines. Use `scale=3` for very small text such as a single digit. |
| `read_lines(scale=1.0)` | `list[TextMatch]` | Every piece of text with its position, in reading order. |
| `exists_text(query, timeout=None)` | `TextMatch` or `None` | Searches for text, returns `None` if not found. |
| `wait_text(query, timeout=None)` | `TextMatch` | Waits for the text. Raises `TextNotFound` if it does not appear. |
| `find_text(query)` | `TextMatch` | Same as `wait_text` with the default wait time. |

`query` is either plain text (matched anywhere in a line, ignoring upper/lower case and spaces) or a
regular expression: `exists_text(re.compile(r"Level \d+"))`.

On a region the same functions are methods; `read_text` is called `text` there:

```python
gold = Region(1500, 30, 200, 50).text()        # "12,450"
level = SCREEN.region(0, 0, 400, 100).text(scale=2)
click(find_text("Continue"))
```

### Touch and keyboard

| Function | Description |
|---|---|
| `click(target=None, timeout=None)` | Taps the target. Returns the tapped `Location`. |
| `long_press(target=None, duration_ms=800, timeout=None)` | Presses and holds. |
| `drag_drop(start, end, duration_ms=400)` | Drags from `start` to `end`. |
| `swipe(dx, dy, duration_ms=300)` | Swipes from the center of the screen (or region) by `dx`, `dy` pixels. `swipe(0, -500)` scrolls content up. |
| `type_text(text, target=None)` | Types text into the focused field. If `target` is given, taps it first. |
| `key(keycode)` | Presses an Android key, e.g. `key(Key.BACK)`. |

A **target** can be:

- an image name or `Pattern`: it is searched first (waiting up to `timeout`), then its center is tapped;
- a `Match`, `TextMatch` or `Region`: its center (or the `target_offset` of the pattern) is tapped;
- a `Location(x, y)`: that exact point is tapped;
- nothing: on a region, its center is tapped.

All coordinates are **device pixels** (the size of the captured screenshot), with `(0, 0)` at the top
left.

### Other functions

| Function | Description |
|---|---|
| `capture()` | Current screen (or region) image as a `numpy` array (BGR). |
| `highlight(target=None)` | Saves a screenshot to the `debug/` folder with the region, match or all matches of an image marked. Returns the file path. |
| `push_notification(content, caption=None)` | Sends a Telegram notification (see [Notifications](#notifications)). |
| `sleep(seconds)` | Waits. |
| `log(message, *values)` | Writes to the console: `log("Found %d items", n)`. Messages are at INFO level. |
| `add_image_path(folder)` | Adds a folder to the image search folders. |

### Region

A rectangle on the screen. Searching, reading and tapping on a region only looks inside it, which is
faster and avoids matches elsewhere.

```python
top_bar = Region(0, 0, SCREEN.w, 120)
if top_bar.exists("bell.png", 0):
    top_bar.click("bell.png")
```

Create one with `Region(x, y, w, h)` or from another region:

| Member | Description |
|---|---|
| `x`, `y`, `w`, `h` | Position and size. |
| `center` | Center `Location`. |
| `region(x, y, w, h)` | A sub-region; `x`, `y` are relative to this region. |
| `grow(px)` | The region enlarged by `px` on every side. |
| `left(w)`, `right(w)`, `above(h)`, `below(h)` | A region of that width/height next to this one. |

…plus every search, text, touch and helper function listed above, as methods (`region.find(...)`,
`region.text()`, `region.click(...)`, `region.capture()`, …).

### Match

The result of an image search. A `Match` is also a `Region`, so you can search inside it or next to it.

| Member | Description |
|---|---|
| `score` | Similarity between 0 and 1. |
| `pattern` | The `Pattern` that was searched. |
| `target` | The point that `click(match)` taps (center plus `target_offset`). |

```python
card = find("card.png")
card.below(80).click("buy.png")     # the "buy" button under that card
```

### TextMatch

The result of a text search or `read_lines()`. Also a `Region` (clickable, searchable).

| Member | Description |
|---|---|
| `text()` | The text that was read. |
| `score` | Recognition confidence between 0 and 1. |

### Pattern

An image plus search options. Methods return a new pattern, so they can be chained.

| Member | Description |
|---|---|
| `Pattern("button.png")` | Pattern for an image file (or a captured `numpy` image). |
| `.similar(0.9)` | Minimum similarity for this image (overrides the default threshold). |
| `.exact()` | Same as `.similar(0.99)`. |
| `.target_offset(dx, dy)` | Tap `dx`, `dy` pixels away from the center of the match. |

```python
click(Pattern("slider.png").similar(0.85).target_offset(120, 0))
```

### Location

A point: `Location(x, y)`, with `.x`, `.y` and `.offset(dx, dy)`.

### Key

Android key codes for `key(...)`:

| Name | Key |
|---|---|
| `Key.BACK` | Back |
| `Key.HOME` | Home |
| `Key.APP_SWITCH` | Recent apps |
| `Key.ENTER` | Enter |
| `Key.DEL` | Backspace |
| `Key.TAB` | Tab |
| `Key.ESCAPE` | Escape |

Any other Android key code number can be passed too, e.g. `key(24)` for volume up.

### SETTINGS

Start values come from the project settings. Changing them affects the rest of the script run.

| Setting | Default | Description |
|---|---|---|
| `SETTINGS.min_similarity` | 0.7 | Default threshold for image search. |
| `SETTINGS.auto_wait_timeout` | 3.0 | Default wait time in seconds. |
| `SETTINGS.scan_rate` | 3.0 | How many times per second the screen is checked while waiting. |
| `SETTINGS.click_delay` | 0.0 | Extra wait after every tap, in seconds. |
| `SETTINGS.save_on_fail` | from project | Save a screenshot to `debug/` when a search fails. |
| `SETTINGS.debug_dir` | `"debug"` | Folder for `highlight()` and failed-search screenshots. |

### Errors

| Error | When |
|---|---|
| `FindFailed` | An image was not found in time (`find`, `wait`, `click("…")`, …). |
| `TextNotFound` | Text was not found in time. A kind of `FindFailed`; `.seen` lists the texts that were visible. |
| `ImageNotFound` | The image file does not exist. |
| `OcrUnavailable` | Text functions were used but no OCR engine is installed. |
| `NotificationError` | A notification could not be sent (missing settings, user not found, no network). |

```python
try:
    click("reward.png")
except FindFailed:
    log("No reward this time")
```

### Notifications

`push_notification(content, caption=None)` sends a Telegram message to the user set in
**Settings → Notifications → Telegram**. `content` can be:

- text: `push_notification("Run finished")`
- an image file: `push_notification("images/result.png")`
- the screen or a region: `push_notification(SCREEN, caption="Level complete")`
- a `Match` or a captured image: `push_notification(find("chest.png"))`

### Full example

```python
"""Collect daily rewards."""

max_rounds = int(ARGS.get("rounds", "5"))

for i in range(max_rounds):
    if not exists("home/rewards.png", timeout=5):
        log("Rewards button not visible, going back")
        key(Key.BACK)
        continue

    click("home/rewards.png")
    claim = exists_text("Claim", timeout=3)
    if claim:
        click(claim)
        wait_vanish("popup/loading.png", timeout=15)
    else:
        push_notification(SCREEN, caption="Nothing to claim (round %d)" % (i + 1))

    sleep(1)

log("Done")
```

---

## Configuration

There are two kinds of settings:

- **Settings** (`Ctrl+Alt+S`) belong to your computer and apply to all projects: device connection,
  Telegram, language.
- **Project Settings** (`Ctrl+Alt+Shift+S`) belong to a project and are stored in the project folder:
  thresholds, wait times, input timing, script arguments.

### Settings (all projects)

| Page | Setting | Default | Description |
|---|---|---|---|
| Appearance → Language | Language | Automatic | Interface language: automatic (system language), English or Türkçe. Takes effect after a restart. |
| Device → Connection | Host | `127.0.0.1` | Address of the emulator or device. |
| | Port | `5555` | ADB port. |
| | adb path | empty | Path to `adb`. Empty = find automatically. |
| Device → Screen Capture | Format | `stream` | `stream`: fast H.264 stream (~30–35 FPS). `raw`: one screenshot at a time (~3 FPS). `png`: compressed screenshot. If `stream` cannot start, `raw` is used automatically. |
| | Capture continuously in the background | off | While a script runs, keep capturing in the background so searches always use a fresh frame. |
| Notifications → Telegram | Bot token | empty | Token from Telegram's @BotFather. Empty = the `TELEGRAM_BOT_TOKEN` environment variable. |
| | Username | empty | `@name` (or numeric chat id) of the person who receives notifications. This person must open the bot and press **Start** once. |
| | Answer bot commands | on | While the editor is open, answer `/capture`, `/console` and `/help` sent to the bot. |
| Editor → Inspections | Warnings | all on | Which code warnings are shown in the *Problems* panel. Errors are always shown. |

**Telegram commands** (only answered for the user in the settings, while the editor is open):

| Command | Answer |
|---|---|
| `/capture` | A screenshot of the device |
| `/console` | The last 20 console lines (`/console 50` for the last 50) |
| `/help` | The command list |

### Project Settings

Stored in `.screenscript.json` in the project folder.

| Page | Setting | JSON key | Default | Description |
|---|---|---|---|---|
| General | Main script | `main_script` | `main.py` | The script that runs when no other script is selected. |
| | Image folders | `image_dirs` | `["images"]` | Folders searched for images, relative to the project. |
| Matching → Image Search | Default threshold | `similarity` | 0.7 | Minimum similarity (0–1) for an image to count as found. |
| | Wait time (s) | `timeout` | 3.0 | Default wait for `find`, `wait`, `exists`, `click("…")`. |
| | Scan rate (/s) | `scan_rate` | 3.0 | Screen checks per second while waiting. |
| | Scales | `scales` | 0.8 … 1.2 | Image sizes that are tried, so slightly larger or smaller images are still found. More scales = more tolerant but slower. |
| | Match in grayscale | `grayscale` | off | Faster, but ignores color differences. |
| Matching → Text Recognition | Engine | `ocr` | `auto` | `auto` (RapidOCR if installed, otherwise Windows), `rapid` or `windows`. |
| Running → Input | Small jitter/delay to clicks | `humanize` | off | Taps land a few pixels off center with a short random delay. |
| | Tap duration | `tap_ms` | 0 ms | 0 = instant tap. If a game misses quick taps, try 60–120 ms. |
| | Gap between inputs (min / max) | `input_gap_min_ms` / `input_gap_max_ms` | 25 / 75 ms | Random minimum pause between consecutive inputs. Some games drop taps sent too quickly one after another. 0 / 0 turns it off. |
| | Random delay per input | `input_jitter_ms` | 10 ms | Every input is delayed by a random 0…this value, so repeated actions do not have a perfectly regular rhythm. |
| Running → Script Arguments | Arguments | `script_args` | empty | `key=value` lines, available as `ARGS["key"]` in scripts. |
| Running → Debugging | Save screen when not found | `save_on_fail` | on | When a search fails, a screenshot is saved to `debug/`. |
| | Log level | `log_level` | `INFO` | Lowest level of messages shown in the console: `DEBUG`, `INFO`, `WARNING`, `ERROR`. `log(...)` writes INFO messages, so they are hidden at WARNING and ERROR. |

Example `.screenscript.json`:

```json
{
  "similarity": 0.8,
  "timeout": 5.0,
  "scan_rate": 3.0,
  "scales": [0.9, 1.0, 1.1],
  "grayscale": false,
  "humanize": false,
  "tap_ms": 80,
  "input_gap_min_ms": 25,
  "input_gap_max_ms": 75,
  "input_jitter_ms": 10,
  "save_on_fail": true,
  "log_level": "INFO",
  "ocr": "auto",
  "main_script": "main.py",
  "script_args": { "rounds": "5" },
  "image_dirs": ["images"]
}
```

### Where settings are stored

Nothing personal is written into your project folders. All application data is in one folder:

- Windows: `%LOCALAPPDATA%\ScreenScript`
- Linux / macOS: `~/.local/share/ScreenScript`

| File | Contents |
|---|---|
| `settings.json` | Settings (device, Telegram, language, inspections) |
| `ide.ini` | Window layout, recent projects, open files |
| `telegram_chats.json` | Remembered Telegram chat ids |
| `*.session` | Telegram session files |

*Settings → System → Settings Folder* shows the location and opens it. Older versions stored the device
and Telegram settings in each project; they are moved here automatically when a project is opened.

---

## The editor

### Panels

| Panel | Shortcut | What it does |
|---|---|---|
| Project | `Alt+1` | Files of the project. Multi-select with `Ctrl` / `Shift`. `F2` renames (for images it can also update the names in your code), `Delete` moves to the recycle bin, `Enter` opens. |
| Images | `Alt+2` | Thumbnails of all project images. Double-click opens the image in a tab; drag an image into the code to insert its path; right-click to test it on the screen. |
| Screen | `Alt+3` | Live device screen. See below. |
| Console | `Alt+4` | Script output. While running, the input line sends text to `input()`; while paused in the debugger, it evaluates expressions. |
| Debug | `Alt+5` | Debug controls, call stack (frames), variables with an expression box, and the breakpoint list. |
| Problems | `Alt+6` | Errors and warnings found in your scripts while you type. |

### Screen panel

- **Capture / Stop / Continue** starts and stops the live view. When stopped, the last frame stays on
  screen so you can work with it.
- **Select an area** by dragging. Then:
  - **Save image**: saves the area to `images/` for use in scripts;
  - **Region**: inserts `Region(x, y, w, h)` into the code;
  - **Location**: inserts the center as `Location(x, y)`.
- **Test an image**: choose an image, set a threshold and press **Show**. All matches are marked with
  their score, so you can pick a good threshold before running anything. If an area is selected, only
  that area is searched.
- `Ctrl` + mouse wheel zooms; the bottom line shows the pixel position and color under the mouse.

### Clicker mode

Turn on **Clicker** in the Screen panel to use the device from the editor:

| You do | The device gets |
|---|---|
| Click | Tap |
| Drag | Swipe |
| Press and hold | Long press |
| Mouse wheel | Scroll |
| Type | Text |
| `Enter`, `Backspace`, `Tab` | The same keys |
| `Esc` | Back |

The buttons next to it send **Back**, **Home** and **Recent apps**. The live view starts automatically.

### Editing

- **Completion** (`Ctrl+Space`) and **parameter info** (`Ctrl+Shift+Space`, opens by itself after `(`).
- **Hover** over a function to see its description, or over an image name to see the image.
- **Go to definition**: `Ctrl`+click, `Ctrl+B` or `F12`. On an image name, it opens the image.
- **Multi-cursor**: drag with the middle mouse button (or `Alt`+drag) to put a cursor on every line,
  then type, delete or paste on all lines at once. `Alt+Shift`+click adds or removes a single cursor.
  `Esc` returns to one cursor.
- **Find in Files** (`Ctrl+Shift+F`) and **Replace in Files** (`Ctrl+Shift+R`) search the whole
  project, with a preview of each result.

---

## Keyboard shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl+Shift+O` | Open project |
| `Ctrl+N` | New script |
| `Ctrl+S` / `Ctrl+Shift+S` | Save / save all |
| `Ctrl+W` | Close tab |
| `Ctrl+Alt+S` | Settings |
| `Ctrl+Alt+Shift+S` | Project settings |
| `Ctrl+Space` | Code completion |
| `Ctrl+Shift+Space` | Parameter info |
| `Ctrl+/` | Comment / uncomment |
| `Ctrl+G` | Go to line |
| `Ctrl+B`, `F12`, `Ctrl`+click | Go to definition |
| `Ctrl+F` / `Ctrl+R` | Find / replace in file |
| `F3` / `Shift+F3` | Next / previous match |
| `Ctrl+Shift+F` / `Ctrl+Shift+R` | Find / replace in files |
| `F8` / `Shift+F8` | Next / previous problem |
| `Shift+F10`, `Ctrl+F5` | Run |
| `F5`, `Shift+F9` | Debug / continue |
| `F6` | Pause |
| `Shift+F5`, `Ctrl+F2` | Stop |
| `F10` / `F11` / `Shift+F11` | Step over / into / out |
| `F9`, `Ctrl+F8` | Toggle breakpoint (or click left of the line number) |
| `Ctrl+F9` | Mute all breakpoints |
| `Ctrl+Shift+G` | Live screen on / off |
| `Alt+1` … `Alt+6` | Show / hide panels |
| `F2`, `Delete`, `Enter` | Rename, delete, open (in the Project panel) |

---

## Troubleshooting

**"Device not ready" / cannot connect**
Check that ADB is enabled in the emulator and that host and port in *Settings → Device → Connection*
match. For BlueStacks, the port is shown next to the ADB switch in its settings.

**An image is not found although it is visible**
Use **Show** in the Screen panel to see the actual score. If it is just below the threshold, lower the
threshold for that image with `Pattern("x.png").similar(0.6)`. If the image was saved at a different
screen size, add more scales in the project settings. With *Save screen when not found* on, the
`debug/` folder shows what the screen looked like at that moment.

**Taps are ignored by the game**
Set a *Tap duration* of 60–120 ms and keep *Gap between inputs* on.

**`OcrUnavailable`**
No text recognition engine is installed. Install RapidOCR: `pip install rapidocr onnxruntime`.

**The live view is slow (~3 FPS)**
Set *Settings → Device → Screen Capture → Format* to `stream`.

**`push_notification` fails with "has not started a conversation with the bot"**
The receiving user must open your bot in Telegram and press **Start** once. Use *Send test message* in
the settings to check.
