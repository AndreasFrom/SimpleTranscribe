# Video Transcript Annotator

This application was co written with claude code. And was done for simple analysis.

A desktop app for transcribing video: the video on the left, the transcript on the right.
You mark who **says** and who **does** what, with clickable timestamps, and can
translate the transcript and export clips with the text burnt in. The app is in
English and Danish.

Everything is kept in one plain `.txt` file that stays readable in any text editor.

## Features

- **Transcribing:** press `1`–`9` when a person speaks and type what they say. `Shift+1`–`9`
  picks what they do from an action menu, including **Gaze**, written `>Name`. Typing pauses
  the video and `Enter` carries on.
- **Volume:** the small button in front of a spoken line sets how it's said, in CA notation:
  `°quiet°`, `°°very quiet/whispered°°` or `LOUD`. It can also make just the selected words
  quiet, or add a description of a sound, like `((typing))`.
- **Timestamps:** click one to play from there, and use `‹` `›` to move it one frame.
  A line can also have an end time (`E`).
- **Interval:** set the part of the video you're working on; the timeline stays inside it.
- **Tracks:** a says/does timeline per person, which you can fold away.
- **Text on the video:** each person's lines in their own colour, at a corner or edge,
  or at a point you click on the video. The lines never overlap.
- **Languages:** the Language menu switches which version of the transcript you see and type in.
- **Translation window (`Ctrl+L`):** the written text and the translation side by side. It
  marks lines that are missing or out of date, and has an `=` button for lines that are
  the same in both languages ("haha", names, ...). Lines that aren't translated yet show the
  original greyed out. **View → Show translation status** marks each line (✓ = – !).
- **Export (`Ctrl+E`):** between a start and a stop point, either a video clip (`.mp4`),
  with or without the text, or the text without timestamps (`.txt`). The text can be a
  numbered **CA transcript** (see below) or a simple list.

Press `F1` in the app for all keyboard shortcuts.

## Requirements

- Windows, macOS or Linux
- [Python](https://www.python.org/downloads/) 3.10 or newer (developed with 3.11)
- About 250 MB of disk space for the Python packages:
  - [PySide6](https://pypi.org/project/PySide6/) (Qt), used for the window and video playback
  - [imageio-ffmpeg](https://pypi.org/project/imageio-ffmpeg/), which brings its own copy
    of `ffmpeg` and is only needed for exporting video clips

## Getting started

### Windows

1. Download this folder (or clone it).
2. Double-click **`setup.bat`**. It makes a `.venv` folder and installs the packages.
   This is only needed once.
3. Double-click **`run.bat`** to start the app.

You can also start it with a video and a transcript:

```bat
run.bat "my video.mp4" "my transcript.txt"
```

### macOS / Linux (or Windows by hand)

```bash
cd path/to/this/folder
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python annotator.py                # optional: python annotator.py video.mp4 transcript.txt
```

## Using it

1. **File → Open video** (`Ctrl+O`).
2. **File → Open transcript** (`Ctrl+T`), or just start typing and choose
   **File → Save** (`Ctrl+S`) to pick a file name. Once a file has a name, changes are
   saved automatically.
3. Give the people names on the right (two to start with; **＋ Add person** for more).
4. Set the **Interval** under the video to the part you're transcribing.
5. Play the video (`Space`). When someone speaks, press their number and type, then `Enter`.

To write a transcript **in English** (or another language), switch the **Language** menu
before you add the first line. A new transcript is written in the language shown.

## The transcript file

It's an ordinary UTF-8 text file. Lines starting with `#%` hold the settings that belong
to the transcript:

```
#% transcript: 1
#% languages: da, en
#% actions da: griner | nikker | ryster på hovedet
#% actions en: laughs | nods | shakes head
#% person E: #1f77b4 | 0.25 0.3
#% person F: #d62728 | 4
[00:06:32.543] F: Altså den synes jeg også kan
	en: Well, I think that one works too
[00:06:34.251-00:06:36.000] F *gestikulerer med hænderne*
	en: gestures with her hands
[00:06:35.314] E: Haha
	en =
[00:06:36.000] E: det må du godt
	@volume: quiet
	en: you may
[00:06:37.000] E *>F*
	en =
```

- **`languages`:** the first one is the language the transcript is written in.
- **`actions`:** the action menu, one list per language, in the same order. Picking
  "nikker" also fills in "nods".
- **`person`:** each person's colour, and where their text goes on the video. That's either
  a preset `1`–`9` (laid out like a phone keypad: `2` bottom, `8` top, `4` left, ...) or a
  point `x y`, given as fractions of the picture's width and height.
- **`[start-end] Name: text`:** something said. **`[start] Name *text*`:** something done.
- **Indented lines:** the other languages. `en =` means "the same as the original", and
  `en!:` marks a translation that is out of date because the original changed afterwards.
  `@volume:` is `quiet`, `very_quiet` or `loud`. An action written `>Name` is a gaze.
- **Older files** without `#%` lines open fine. They are treated as Danish, and the
  metadata lines are added the first time they're saved.
- **Old translation files** (`name.en.txt` next to `name.txt`, from earlier versions) are
  moved into the transcript automatically when it's opened. After that you can delete them.

## The CA transcript export

**File → Export → Text → CA transcript** writes a transcript in the style used in
conversation analysis:

```
@Media: example.mp4
@Participants: SP03, SP01, SP02
@Languages: Dansk, English

1. *SP03: Δtak for Δhjælpen
          thanks for the help
          Δ>Sille
          Δsmiles
2.        (0.5)
3. *SP01: ja
          yes
4.        (0.5)
5. *SP03: det var [super fedt
          it was super nice
6. *SP02:         [°må jeg få Δpulsbåndet°
          °may I have the heart rate monitor°
          Δwalks tw Martin
7. *SP03: stands up
8. *SP01: >Martin
```

- **Each turn** is numbered, with the speaker as `*Name:`.
- **The translation** goes underneath, if you tick it in the export.
- **Actions done while talking** go under the talk, marked with `Δ`. A `Δ` is also placed in
  the talk at about the word where the action starts, worked out from the times.
- **Actions on their own** get their own line, written in the translation's language when
  there is one.
- **Overlaps:** a `[` marks where someone starts talking over, lined up under the other
  speaker's `[`.
- **Pauses:** `(0.5)` between turns can be switched on in the export. They need end times.
- **Volume** is shown as `°…°`, `°°…°°` or CAPITALS.

## Running the tests

```bat
run_tests.bat
```

or, with the virtual environment active:

```bash
python -m unittest discover -s tests -v
```

The playback and export tests use the short clip in `demo/sample.mp4`.

## Project files

| File | What it is |
|---|---|
| `annotator.py` | The whole app |
| `requirements.txt` | The Python packages it needs |
| `setup.bat`, `run.bat`, `run_tests.bat` | Windows shortcuts for setting up, starting and testing |
| `tests/test_annotator.py` | The automated tests |
| `demo/sample.mp4` | A 30-second test clip used by the tests |

Your own videos and transcripts aren't needed to run the app. Leave them out if you
share the folder.

## Troubleshooting

- **"Python was not found":** install Python from [python.org](https://www.python.org/downloads/)
  and tick *Add python.exe to PATH*.
- **`pip` fails with a "path too long" error on Windows:** this happens with the Microsoft
  Store version of Python. Either use the python.org installer, or create the environment
  in a short folder; `run.bat` looks there too:
  ```bat
  python -m venv %USERPROFILE%\.venvs\annotator
  %USERPROFILE%\.venvs\annotator\Scripts\python -m pip install -r requirements.txt
  ```
- **The video doesn't play:** PySide6 plays video through its built-in FFmpeg backend,
  so most `.mp4`/`.mov`/`.mkv` files work. A very unusual codec may not.
- **Export says ffmpeg was not found:** run `pip install imageio-ffmpeg` in the environment.
