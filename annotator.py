"""Video Transcript Annotator.

Video on the left, transcript (a plain .txt file) on the right. Press a
person's "Says" / "Does" button (or 1-9 / Shift+1-9) to add a timestamped
line, type what happened, press Enter to continue playback.

The timeline covers only the chosen interval; the interval bar below it
shows the whole video, the interval and the current position.

Transcript format, one line per entry (the end time is optional):
    [00:01:23.450] Anna: Hello there
    [00:01:30.000-00:01:33.120] Anna *points at the board*
Lines that don't match this format are kept untouched.

File > Export (Ctrl+E) cuts a video clip with the text burnt in, or writes the text
without timestamps to a separate file, between a start and a stop point:
    Anna: "Hello there"
    Anna: (points at the board)

Usage:  python annotator.py [video] [transcript.txt]
"""
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import (
    QEvent, QLibraryInfo, QLocale, QPoint, QPointF, QProcess, QRectF, QRegularExpression, QSettings, QSize,
    QSizeF, Qt,
    QTime, QTimer, QTranslator, QUrl, Signal,
)
from PySide6.QtGui import (
    QAction, QActionGroup, QColor, QCursor, QEnterEvent, QFont, QFontMetrics, QImage, QKeySequence, QPainter,
    QPalette, QPen,
    QRegularExpressionValidator,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaMetaData, QMediaPlayer, QVideoFrame, QVideoSink
from PySide6.QtWidgets import (
    QAbstractItemDelegate, QAbstractItemView, QAbstractSpinBox, QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLabel, QLayout,
    QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressDialog, QPushButton,
    QRadioButton, QScrollArea, QSlider, QSplitter, QStyle, QStyleOptionSlider, QTextEdit, QTimeEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QToolButton, QToolTip, QVBoxLayout, QWidget,
)

PALETTE = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e",
           "#17becf", "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22"]
DEFAULT_ACTIONS = {
    "en": ["laughs", "nods", "shakes head", "points", "gestures",
           "looks away", "writes", "stands up", "sits down"],
    "da": ["griner", "nikker", "ryster på hovedet", "peger", "gestikulerer",
           "kigger væk", "skriver", "rejser sig", "sætter sig"],
}
SPEEDS = ["0.5x", "0.75x", "1x", "1.25x", "1.5x", "2x"]
TEXT_INPUTS = (QLineEdit, QAbstractSpinBox, QTextEdit, QPlainTextEdit)
MIN_INTERVAL_MS = 500
TEXT_HOLD_MS = 2000      # on-video text for a line without an end time stays this long

LINE_RE = re.compile(
    r"^\[(?P<h>\d+):(?P<m>\d{2}):(?P<s>\d{2})(?:\.(?P<ms>\d{1,3}))?"
    r"(?:-(?P<eh>\d+):(?P<em>\d{2}):(?P<es>\d{2})(?:\.(?P<ems>\d{1,3}))?)?\]\s+"
    r"(?P<name>[^:*\[\]]+?)(?:(?P<colon>:)\s?(?P<said>.*)|\s+\*(?P<did>.*)\*)\s*$"
)
NAME_FORBIDDEN = re.compile(r"[:*\[\]|]")

STAMP_CSS = ("QPushButton{color:#2a6fdb;border:none;text-decoration:underline;"
             "font-family:Consolas,monospace;padding:0 2px;}"
             "QPushButton:hover{color:#0b3d91;}")
STAMP_CSS_OUTSIDE = STAMP_CSS.replace("#2a6fdb", "#9a9a9a").replace("#0b3d91", "#6a6a6a")


# ----------------------------------------------------------------- translations

LANG = "en"
LANGUAGES = {"en": "English", "da": "Dansk"}

T = {   # key: (English, Danish)
    "label.app": ("Video Transcript Annotator", "Videotransskribering"),
    "menu.file": ("&File", "&Filer"),
    "menu.open_video": ("Open &video…", "Åbn &video…"),
    "menu.open_transcript": ("Open &transcript…", "Åbn &transskription…"),
    "menu.new_transcript": ("&New transcript", "&Ny transskription"),
    "menu.save": ("&Save", "&Gem"),
    "menu.save_as": ("Save &as…", "Gem &som…"),
    "menu.export": ("&Export (video clip or text)…", "&Eksportér (videoklip eller tekst)…"),
    "menu.quit": ("&Quit", "&Afslut"),
    "menu.view": ("&View", "&Vis"),
    "menu.track_text": ("Show &text on the tracks", "Vis &tekst på sporene"),
    "menu.language": ("&Language", "&Sprog"),
    "menu.help": ("&Help", "&Hjælp"),
    "menu.shortcuts": ("Keyboard &shortcuts", "&Tastaturgenveje"),
    "menu.custom_action": ("Custom… (type it)", "Andet… (skriv selv)"),
    "btn.play": ("Play", "Afspil"),
    "btn.pause": ("Pause", "Pause"),
    "btn.says": ("Says", "Siger"),
    "btn.does": ("Does", "Gør"),
    "btn.add_person": ("＋ Add person", "＋ Tilføj person"),
    "btn.edit_actions": ("Edit action list…", "Rediger handlingsliste…"),
    "btn.set_start": ("Set start ([)", "Sæt start ([)"),
    "btn.set_end": ("Set end (])", "Sæt slut (])"),
    "btn.whole_video": ("Whole video", "Hele videoen"),
    "cb.loop": ("Loop", "Gentag"),
    "tip.back5": ("Back 5 seconds (←)", "5 sekunder tilbage (←)"),
    "tip.prev_frame": ("Previous frame (,)", "Forrige billede (,)"),
    "tip.play": ("Play / pause (Space)", "Afspil / pause (mellemrum)"),
    "tip.next_frame": ("Next frame (.)", "Næste billede (.)"),
    "tip.fwd5": ("Forward 5 seconds (→)", "5 sekunder frem (→)"),
    "tip.back1": ("Back 1 second (Shift+←)", "1 sekund tilbage (Shift+←)"),
    "tip.fwd1": ("Forward 1 second (Shift+→)", "1 sekund frem (Shift+→)"),
    "tip.collapse": ("Click to show or hide", "Klik for at vise eller skjule"),
    "group.tracks": ("Tracks", "Spor"),
    "label.track_both": ("{name} · says + does", "{name} · siger + gør"),
    "menu.video_text": ("Show text on the &video", "Vis tekst på &videoen"),
    "tip.origin": ("Where this person's text appears on the video",
                   "Hvor personens tekst vises på videoen"),
    "label.origin_1": ("Bottom left", "Nederst t.v."),
    "label.origin_2": ("Bottom", "Nederst"),
    "label.origin_3": ("Bottom right", "Nederst t.h."),
    "label.origin_4": ("Left", "Venstre"),
    "label.origin_5": ("Middle", "Midten"),
    "label.origin_6": ("Right", "Højre"),
    "label.origin_7": ("Top left", "Øverst t.v."),
    "label.origin_8": ("Top", "Øverst"),
    "label.origin_9": ("Top right", "Øverst t.h."),
    "label.origin_point": ("Point on the video…", "Punkt på videoen…"),
    "menu.add_language": ("&Add language", "&Tilføj sprog"),
    "menu.translation": ("&Translation…", "&Oversættelse…"),
    "label.tr_title": ("Translation", "Oversættelse"),
    "label.tr_to": ("Translate into", "Oversæt til"),
    "label.tr_time": ("Time", "Tid"),
    "label.tr_person": ("Person", "Person"),
    "label.tr_count": ("{done} of {total} lines translated · {outdated} out of date",
                       "{done} af {total} linjer oversat · {outdated} forældede"),
    "cb.tr_todo": ("Only missing and out of date", "Kun manglende og forældede"),
    "btn.tr_current": ("Mark as up to date", "Markér som opdateret"),
    "tip.tr_current": ("For the selected lines: the original changed, but the translation still fits",
                       "For de valgte linjer: originalen er ændret, men oversættelsen passer stadig"),
    "tip.tr_done": ("Translated", "Oversat"),
    "tip.tr_missing": ("Not translated yet", "Ikke oversat endnu"),
    "tip.tr_outdated": ("The original has changed since it was translated",
                        "Originalen er ændret, siden den blev oversat"),
    "tip.tr_was": ("Translated from: {text}", "Oversat fra: {text}"),
    "cb.tr_export": ("Add {lang} under each line", "Tilføj {lang} under hver linje"),
    "tip.tr_same": ("The same in both languages", "Ens på begge sprog"),
    "tip.same_btn": ("The same in both languages (haha, names, ...)",
                     "Ens på begge sprog (haha, navne, ...)"),
    "ctx.same": ("Same in both languages", "Ens på begge sprog"),
    "status.pick": ("Click on the video where {name}'s text should go  (Esc cancels)",
                    "Klik på videoen, hvor {name}s tekst skal stå  (Esc fortryder)"),
    "menu.right_panel": ("Show the &right side (people and transcript)",
                         "Vis &højre side (personer og transskription)"),
    "tip.mute": ("Mute (M)", "Slå lyd fra (M)"),
    "tip.timeline": ("The interval. Click or drag to move within it.",
                     "Intervallet. Klik eller træk for at flytte rundt i det."),
    "tip.interval_bar": ("The whole video. Drag the handles to change the interval; "
                         "the line shows where you are.",
                         "Hele videoen. Træk i håndtagene for at ændre intervallet; "
                         "stregen viser, hvor du er."),
    "tip.set_start": ("Start the interval at the current video time",
                      "Start intervallet ved den nuværende videotid"),
    "tip.set_end": ("End the interval at the current video time",
                    "Slut intervallet ved den nuværende videotid"),
    "tip.whole_video": ("Reset the interval to the whole video",
                        "Nulstil intervallet til hele videoen"),
    "tip.remove_person": ("Remove person", "Fjern person"),
    "tip.stamp": ("Click to play from here · right-click for options",
                  "Klik for at afspille herfra · højreklik for flere valg"),
    "tip.stamp_outside": ("Outside the interval", "Uden for intervallet"),
    "tip.delete_line": ("Delete line", "Slet linje"),
    "tip.nudge_prev": ("One frame earlier", "Ét billede tidligere"),
    "tip.nudge_next": ("One frame later", "Ét billede senere"),
    "tip.add_end": ("Set the end time to the current video time (E)",
                    "Sæt sluttiden til den nuværende videotid (E)"),
    "tip.end_stamp": ("End time · click to show this frame · right-click for options",
                      "Sluttid · klik for at vise billedet · højreklik for flere valg"),
    "tip.tracks": ("What each person says and does in the interval. Click a bar to select its line; "
                   "click a name to fold that person's tracks into one.",
                   "Hvad hver person siger og gør i intervallet. Klik på en bjælke for at vælge linjen; "
                   "klik på et navn for at folde personens spor sammen til ét."),
    "label.track_says": ("{name} · says", "{name} · siger"),
    "label.track_does": ("{name} · does", "{name} · gør"),
    "label.speed": ("Speed", "Hastighed"),
    "label.unnamed": ("(unnamed)", "(unavngivet)"),
    "label.untitled": ("Untitled transcript", "Unavngivet transskription"),
    "group.interval": ("Interval", "Interval"),
    "group.people": ("People", "Personer"),
    "ph.name": ("Name", "Navn"),
    "ph.says": ("What does {name} say?", "Hvad siger {name}?"),
    "ph.does": ("What does {name} do?", "Hvad gør {name}?"),
    "ph.blank": ("(blank line)", "(tom linje)"),
    "entry.says": ("{name}:", "{name}:"),
    "entry.does": ("{name} does:", "{name} gør:"),
    "entry.looks": ("{name} looks:", "{name} kigger:"),
    "menu.gaze": ("Gaze  >", "Blik  >"),
    "menu.gaze_custom": ("Somewhere else… (type it)", "Et andet sted… (skriv selv)"),
    "menu.tr_marks": ("Show translation &status on the lines", "Vis oversættelses&status på linjerne"),
    "tip.volume": ("Volume: {volume}  (click for options)", "Lydstyrke: {volume}  (klik for valg)"),
    "label.volume_normal": ("normal", "normal"),
    "label.volume_quiet": ("quiet °…°", "stille °…°"),
    "label.volume_very_quiet": ("very quiet °°…°°", "meget stille °°…°°"),
    "label.volume_loud": ("loud (CAPITALS)", "højt (VERSALER)"),
    "menu.volume_normal": ("Normal volume", "Normal lydstyrke"),
    "menu.volume_quiet": ("Quiet   °word°", "Stille   °ord°"),
    "menu.volume_very_quiet": ("Very quiet / whispered   °°word°°", "Meget stille / hvisket   °°ord°°"),
    "menu.volume_loud": ("Loud   WORD", "Højt   ORD"),
    "menu.quiet_words": ("Only the selected words quiet   °…°", "Kun de markerede ord stille   °…°"),
    "menu.sound": ("Describe a sound   ((…))", "Beskriv en lyd   ((…))"),
    "label.format_ca": ("CA transcript: numbered, translation and actions underneath",
                        "CA-transskription: nummereret, oversættelse og handlinger nedenunder"),
    "label.format_simple": ('Simple:  F: "text"  /  F: (action)', 'Enkel:  F: "tekst"  /  F: (handling)'),
    "cb.pauses": ("Show pauses between turns, e.g. (0.8)  (needs end times)",
                  "Vis pauser mellem replikker, fx (0.8)  (kræver sluttider)"),
    "ctx.retime": ("Set to current video time", "Sæt til nuværende videotid"),
    "ctx.change_person": ("Change person", "Skift person"),
    "ctx.set_end": ("Set end to current video time (E)", "Sæt slut til nuværende videotid (E)"),
    "ctx.clear_end": ("Remove end time", "Fjern sluttid"),
    "ctx.to_does": ("Change to 'does'", "Skift til 'gør'"),
    "ctx.to_says": ("Change to 'says'", "Skift til 'siger'"),
    "dlg.actions_title": ("Action list", "Handlingsliste"),
    "dlg.actions_label": ("One action per line, in the same order in each language "
                          "(picking one fills in the other languages too):",
                          "Én handling pr. linje, i samme rækkefølge på hvert sprog "
                          "(vælger du én, udfyldes de andre sprog også):"),
    "dlg.remove_title": ("Remove person", "Fjern person"),
    "dlg.remove_text": ("Remove {name} and their {n} line(s) from the transcript?",
                        "Fjern {name} og deres {n} linje(r) fra transskriptionen?"),
    "dlg.unsaved_title": ("Unsaved transcript", "Ikke-gemt transskription"),
    "dlg.unsaved_text": ("Save the current transcript first?",
                         "Vil du gemme den nuværende transskription først?"),
    "dlg.open_video": ("Open video", "Åbn video"),
    "dlg.open_transcript": ("Open transcript", "Åbn transskription"),
    "dlg.save_transcript": ("Save transcript", "Gem transskription"),
    "dlg.default_name": ("transcript.txt", "transskription.txt"),
    "dlg.export_title": ("Export", "Eksportér"),
    "dlg.file_problem": ("File problem", "Filproblem"),
    "dlg.save_failed": ("Could not save {name}:\n{err}", "Kunne ikke gemme {name}:\n{err}"),
    "dlg.export_suffix": ("_formatted", "_formateret"),
    "dlg.export_refused": ("Not exported", "Ikke eksporteret"),
    "dlg.export_same": ("That is the transcript or video you are working with, so it will not be "
                        "overwritten. Choose another file name.",
                        "Det er den transskription eller video, du arbejder med, så den bliver ikke "
                        "overskrevet. Vælg et andet filnavn."),
    "dlg.export_replace": ("{name} already exists. Replace it?", "{name} findes allerede. Vil du erstatte den?"),
    "dlg.export_range_bad": ("The stop point must be after the start point.",
                             "Stoppunktet skal ligge efter startpunktet."),
    "dlg.export_no_video": ("Open a video first to export a clip.",
                            "Åbn en video først for at eksportere et klip."),
    "dlg.export_no_ffmpeg": ("ffmpeg was not found, so video clips can't be exported. "
                             "Install it with:  pip install imageio-ffmpeg",
                             "ffmpeg blev ikke fundet, så videoklip kan ikke eksporteres. "
                             "Installér det med:  pip install imageio-ffmpeg"),
    "dlg.export_progress": ("Exporting {name}…", "Eksporterer {name}…"),
    "dlg.export_failed": ("The export failed:\n{err}", "Eksporten mislykkedes:\n{err}"),
    "label.export_what": ("Export", "Eksportér"),
    "label.export_video": ("Video clip with the text (.mp4)", "Videoklip med teksten (.mp4)"),
    "label.export_text": ("Text without timestamps (.txt)", "Tekst uden tidsstempler (.txt)"),
    "label.export_start": ("Start point", "Startpunkt"),
    "label.export_stop": ("Stop point", "Stoppunkt"),
    "label.export_file": ("File", "Fil"),
    "cb.subtitles": ("With text", "Med tekst"),
    "btn.now": ("Now", "Nu"),
    "tip.now": ("Use the current video time", "Brug den nuværende videotid"),
    "btn.browse": ("Browse…", "Gennemse…"),
    "btn.export": ("Export", "Eksportér"),
    "btn.cancel": ("Cancel", "Annullér"),
    "filter.mp4": ("MP4 video", "MP4-video"),
    "dlg.export_protected": (
        "{name} contains timestamped lines, so it looks like a transcript you work in. "
        "It will not be overwritten. Choose another file name.",
        "{name} indeholder linjer med tidsstempler, så den ligner en transskription, du arbejder i. "
        "Den bliver ikke overskrevet. Vælg et andet filnavn."),
    "filter.videos": ("Videos", "Videoer"),
    "filter.text": ("Text files", "Tekstfiler"),
    "filter.all": ("All files", "Alle filer"),
    "status.start": ("Open a video (Ctrl+O) and a transcript (Ctrl+T) to start.  F1 = shortcuts",
                     "Åbn en video (Ctrl+O) og en transskription (Ctrl+T) for at begynde.  F1 = genveje"),
    "status.video": ("Video: {name}", "Video: {name}"),
    "status.video_error": ("Video error: {msg}", "Videofejl: {msg}"),
    "status.outside": ("That timestamp is outside the interval.",
                       "Det tidsstempel ligger uden for intervallet."),
    "status.loaded": ("Loaded {n} line(s), {p} person(s).", "Indlæste {n} linje(r), {p} person(er)."),
    "status.exported": ("Exported to {name}", "Eksporteret til {name}"),
    "status.imported": ("Moved {n} translation(s) from {files} into the transcript. "
                        "That old file is no longer needed once this is saved.",
                        "Flyttede {n} oversættelse(r) fra {files} ind i transskriptionen. "
                        "Den gamle fil er ikke længere nødvendig, når dette er gemt."),
    "status.select_first": ("Select a line first (click it).", "Vælg en linje først (klik på den)."),
    "status.end_before_start": ("The end can't be before the start.",
                                "Slutningen kan ikke ligge før starten."),
    "help.title": ("Keyboard shortcuts", "Tastaturgenveje"),
    "help.text": (
        "1 – 9\tPerson N says\n"
        "Shift+1 – 9\tPerson N does (action menu, with Gaze >)\n"
        "E\tEnd the selected line at the current time\n"
        "Enter\tFinish typing, resume playback\n"
        "Esc\tLeave the text field\n\n"
        "Space\tPlay / pause\n"
        "← / →\tBack / forward 5 seconds\n"
        "Shift+← / →\tBack / forward 1 second\n"
        ", / .\tPrevious / next frame\n"
        "Home / End\tGo to interval start / end\n"
        "[ / ]\tSet interval start / end to now\n"
        "M\tMute\n\n"
        "Ctrl+O\tOpen video\n"
        "Ctrl+T\tOpen transcript\n"
        "Ctrl+N\tNew transcript\n"
        "Ctrl+S\tSave\n"
        "Ctrl+E\tExport a video clip with the text, or the text (start and stop point)\n"
        "Ctrl+L\tTranslation window (written text on the left, translation on the right)\n\n"
        "The timeline covers only the interval. Drag the handles on the interval bar "
        "(the whole video) to change it; 'Whole video' resets it.\n"
        "Click a timestamp to play from there; ‹ and › beside it move it one frame. "
        "Right-click a line to re-time it, set or remove its end, change person, or delete it.\n"
        "View > Show text on the tracks writes each line's text on its bar.\n"
        "View > Show text on the video shows what is being said and done right now, in each "
        "person's colour (a line without an end time stays 2 seconds).\n"
        "Click Tracks or Interval to fold them away; click a name on the tracks to fold that "
        "person's two tracks into one.\n"
        "The Language menu switches which version of the transcript you see and type "
        "(and the menus, for Danish and English). Everything is saved in the one .txt file.\n"
        "The small button in front of a spoken line sets its volume (°quiet°, LOUD) and can mark "
        "single words quiet or add a ((sound)). View > Show translation status marks which lines "
        "still need translating.",
        "1 – 9\tPerson N siger\n"
        "Shift+1 – 9\tPerson N gør (handlingsmenu, med Blik >)\n"
        "E\tAfslut den valgte linje ved den nuværende tid\n"
        "Enter\tAfslut teksten, fortsæt afspilning\n"
        "Esc\tForlad tekstfeltet\n\n"
        "Mellemrum\tAfspil / pause\n"
        "← / →\t5 sekunder tilbage / frem\n"
        "Shift+← / →\t1 sekund tilbage / frem\n"
        ", / .\tForrige / næste billede\n"
        "Home / End\tGå til intervallets start / slut\n"
        "[ / ]\tSæt intervallets start / slut til nu\n"
        "M\tSlå lyd fra\n\n"
        "Ctrl+O\tÅbn video\n"
        "Ctrl+T\tÅbn transskription\n"
        "Ctrl+N\tNy transskription\n"
        "Ctrl+S\tGem\n"
        "Ctrl+E\tEksportér et videoklip med teksten, eller teksten (start- og stoppunkt)\n"
        "Ctrl+L\tOversættelsesvindue (skrevet tekst til venstre, oversættelse til højre)\n\n"
        "Tidslinjen dækker kun intervallet. Træk i håndtagene på intervalbjælken "
        "(hele videoen) for at ændre det; 'Hele videoen' nulstiller det.\n"
        "Klik på et tidsstempel for at afspille derfra; ‹ og › ved siden af flytter det ét billede. "
        "Højreklik på en linje for at ændre tiden, sætte eller fjerne sluttiden, skifte person "
        "eller slette den.\n"
        "Vis > Vis tekst på sporene skriver hver linjes tekst på dens bjælke.\n"
        "Vis > Vis tekst på videoen viser, hvad der bliver sagt og gjort lige nu, i hver "
        "persons farve (en linje uden sluttid bliver stående i 2 sekunder).\n"
        "Klik på Spor eller Interval for at folde dem sammen; klik på et navn på sporene for at "
        "folde personens to spor sammen til ét.\n"
        "Sprog-menuen skifter, hvilken udgave af transskriptionen du ser og skriver i "
        "(og menuerne, for dansk og engelsk). Alt gemmes i den ene .txt-fil.\n"
        "Den lille knap foran en replik sætter lydstyrken (°stille°, HØJT) og kan gøre enkelte ord "
        "stille eller tilføje en ((lyd)). Vis > Vis oversættelsesstatus viser, hvilke linjer der "
        "mangler at blive oversat."),
}


def tr(key, **kw):
    en, da = T[key]
    text = da if LANG == "da" else en
    return text.format(**kw) if kw else text


# ----------------------------------------------------------------- model

@dataclass(eq=False)
class Person:
    name: str
    color: str
    # Where this person's text goes on the video: a preset, numpad style (2 = bottom centre),
    # or a point (x, y) on the picture as fractions of its width and height.
    origin: int | tuple = 2


# Preset origins in the order the selector lists them (numpad numbering); the selector's
# last item picks a point on the video instead.
ORIGINS = [2, 8, 4, 6, 1, 3, 7, 9, 5]
ORIGIN_ARROWS = {1: "↙", 2: "↓", 3: "↘", 4: "←", 5: "·", 6: "→", 7: "↖", 8: "↑", 9: "↗"}
POINT_INDEX = len(ORIGINS)      # index of "point on the video" in the selector


def origin_index(origin):
    """The selector index for an origin."""
    return ORIGINS.index(origin) if isinstance(origin, int) else POINT_INDEX


def valid_origin(o):
    """A preset 1-9 or an (x, y) point with both inside 0..1; anything else -> None."""
    if isinstance(o, int) and not isinstance(o, bool):
        return o if o in ORIGIN_ARROWS else None
    try:
        x, y = (float(v) for v in o)
    except (TypeError, ValueError):
        return None
    return (x, y) if 0 <= x <= 1 and 0 <= y <= 1 else None


# Languages offered for translation (code: name in that language).
TRANSLATION_LANGS = {"da": "Dansk", "en": "English", "de": "Deutsch", "sv": "Svenska", "nb": "Norsk",
                     "fr": "Français", "es": "Español", "it": "Italiano", "nl": "Nederlands", "pl": "Polski"}


@dataclass(eq=False)
class Entry:
    time_ms: int | None      # None for raw (unparsed) lines
    person: Person | None
    kind: str                # "says", "does" or "raw"
    text: str                # in the language the transcript is written in (its first language)
    end_ms: int | None = None   # optional end time (never before time_ms)
    # Other languages by code: [text, the original it was made from, same-as-original flag].
    # When the original changes later, the translation is out of date (unless it is "same").
    translations: dict = field(default_factory=dict)
    # Other per-line settings, e.g. {"volume": "quiet"} (kept in the file as "@volume: quiet").
    attrs: dict = field(default_factory=dict)


# How loudly something is said, written in CA (Jefferson) notation.
VOLUMES = ["normal", "quiet", "very_quiet", "loud"]
VOLUME_SYMBOL = {"normal": "", "quiet": "°", "very_quiet": "°°", "loud": "A"}


def apply_volume(text, volume):
    """°quiet°, °°very quiet°°, LOUD (capitals) - as in conversation analysis."""
    if not text:
        return text
    if volume == "quiet":
        return f"°{text}°"
    if volume == "very_quiet":
        return f"°°{text}°°"
    if volume == "loud":
        return text.upper()
    return text


def volume_of(e):
    v = e.attrs.get("volume", "normal")
    return v if v in VOLUMES else "normal"


def is_gaze(text):
    """An action written as  >Name  is where someone looks (CA notation)."""
    return text.strip().startswith(">")


def text_in(e, lang, orig):
    """A line's text in a language ('' when it has none yet). orig = the written-in language."""
    if lang is None or lang == orig:
        return e.text
    t = e.translations.get(lang)
    if not t:
        return ""
    return e.text if t[2] else t[0]


def shown_text(e, lang, orig):
    """The text to show for a language: its own, or the original when it has none yet."""
    return text_in(e, lang, orig).strip() or e.text.strip()


def translation_status(e, lang, orig):
    """For a language other than the written-in one: 'none' (nothing to translate), 'missing',
    'done', 'same' (same in both languages) or 'outdated' (the original changed since)."""
    if e.kind == "raw" or e.time_ms is None or lang == orig:
        return "none"
    t = e.translations.get(lang)
    has_orig = bool(e.text.strip())
    if t and t[2]:
        return "same" if has_orig else "none"
    has_tr = bool(t and t[0].strip())
    if not has_orig and not has_tr:
        return "none"
    if not (has_orig and has_tr):
        return "missing"
    return "done" if t[1] == e.text else "outdated"


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def fmt_ms(ms, millis=True, hours=True):
    """HH:MM:SS(.mmm); with hours=False MM:SS(.mmm), unless the time is an hour or more."""
    ms = max(0, int(ms))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, rem = divmod(rem, 1000)
    text = f"{h:02d}:{m:02d}:{s:02d}" if hours or h else f"{m:02d}:{s:02d}"
    return f"{text}.{rem:03d}" if millis else text


def ms_to_qtime(ms):
    return QTime(0, 0).addMSecs(int(ms))


def qtime_to_ms(t):
    return QTime(0, 0).msecsTo(t)


VOL_W = 24            # the volume button in front of spoken lines
ACTION_INDENT = 22    # actions start this much further right than speech


def label_key(e):
    if e.kind == "says":
        return "entry.says"
    return "entry.looks" if is_gaze(e.text) else "entry.does"


def display_name(person):
    return person.name or tr("label.unnamed")


def entry_to_line(e):
    if e.kind == "raw":
        return e.text
    end = f"-{fmt_ms(e.end_ms)}" if e.end_ms is not None else ""
    stamp = f"[{fmt_ms(e.time_ms)}{end}]"
    name = e.person.name or "Unnamed"
    if e.kind == "says":
        return f"{stamp} {name}: {e.text}".rstrip()
    return f"{stamp} {name} *{e.text}*"


# ----------------------------------------------------------------- the transcript file
#
# One plain text file holds everything. Metadata lines start with "#%":
#     #% transcript: 1
#     #% languages: da, en                    <- the first is the language it is written in
#     #% actions da: griner | nikker | ...    <- the action menu, same order in each language
#     #% actions en: laughs | nods | ...
#     #% person E: #1f77b4 | 0.25 0.3         <- colour | text position (preset 1-9 or x y)
# then one line per entry, each followed by its other languages:
#     [00:06:32.543] F: Altså den synes jeg også kan
#     	en: Well, I think that one works too
#     	en =                                  <- same in both languages (haha, names...)
#     	en!: ...                              <- out of date: the original changed since
#     	@volume: quiet                        <- said quietly (°...°); also very_quiet, loud
# Files without metadata (older ones) are read as written in Danish.

META_RE = re.compile(r"^#%\s*(?P<key>[\w-]+)(?:\s+(?P<arg>[^:]*?))?\s*:\s?(?P<value>.*)$")
ATTR_RE = re.compile(r"^\s+@(?P<key>[\w-]+):\s?(?P<value>.*)$")
OTHER_LANG_RE = re.compile(r"^\s+(?P<lang>[a-z]{2,3})(?P<old>!)?(?:\s*(?P<same>=)\s*|:\s?(?P<text>.*))$")
FILE_VERSION = "1"


@dataclass
class Document:
    persons: list
    entries: list
    languages: list                       # first = the language it is written in
    actions: dict                         # language -> action names (aligned between languages)
    extra: list = field(default_factory=list)   # metadata lines this version doesn't know (kept)
    has_meta: bool = False


def fmt_origin(o):
    return f"{o[0]:g} {o[1]:g}" if isinstance(o, tuple) else str(o)


def parse_origin(text):
    parts = text.split()
    if len(parts) == 1 and parts[0].isdigit():
        return valid_origin(int(parts[0]))
    return valid_origin(parts) if len(parts) == 2 else None


def parse_document(text, default_lang="da"):
    """Read a transcript file (with or without metadata)."""
    persons, entries, actions, extra = {}, [], {}, []
    languages, has_meta = None, False

    def person(name):
        if name not in persons:
            taken = {p.color for p in persons.values()}
            color = next((c for c in PALETTE if c not in taken), PALETTE[len(persons) % len(PALETTE)])
            persons[name] = Person(name, color)
        return persons[name]

    for line in text.splitlines():
        m = META_RE.match(line) if line.startswith("#%") else None
        if m:
            has_meta = True
            key, arg, value = m["key"].lower(), (m["arg"] or "").strip(), m["value"].strip()
            if key == "languages":
                languages = [c.strip() for c in value.split(",") if c.strip()]
            elif key == "actions" and arg:
                names = [a.strip() for a in value.split("|")]
                while names and not names[-1]:
                    names.pop()
                actions[arg] = names
            elif key == "person" and arg:
                parts = [x.strip() for x in value.split("|")]
                p = person(arg)
                if parts and QColor(parts[0]).isValid():
                    p.color = parts[0]
                if len(parts) > 1 and parse_origin(parts[1]) is not None:
                    p.origin = parse_origin(parts[1])
            elif key != "transcript":
                extra.append(line)
            continue
        a = ATTR_RE.match(line)
        if a and entries and entries[-1].kind != "raw":
            entries[-1].attrs[a["key"]] = a["value"].strip()
            continue
        o = OTHER_LANG_RE.match(line)
        if o and entries and entries[-1].kind != "raw":
            e = entries[-1]
            if o["same"]:
                e.translations[o["lang"]] = ["", e.text, True]
            else:   # "!" = out of date: remember a source that can't match the original
                e.translations[o["lang"]] = [o["text"], None if o["old"] else e.text, False]
            continue
        m = LINE_RE.match(line)
        if not m:
            entries.append(Entry(None, None, "raw", line))
            continue
        ms = _match_ms(m, "")
        end = max(ms, _match_ms(m, "e")) if m["eh"] else None
        if m["colon"]:
            entries.append(Entry(ms, person(m["name"].strip()), "says", m["said"], end))
        else:
            entries.append(Entry(ms, person(m["name"].strip()), "does", m["did"], end))

    if not languages:
        languages = [default_lang, "en" if default_lang != "en" else "da"]
    for e in entries:                     # languages used on lines but not listed
        for lang in e.translations:
            if lang not in languages:
                languages.append(lang)
    return Document(list(persons.values()), entries, languages, actions, extra, has_meta)


def parse_transcript(text):
    """(persons, entries) of a transcript."""
    doc = parse_document(text)
    return doc.persons, doc.entries


def other_lang_lines(e, languages):
    rows = [f"\t@{k}: {v}" for k, v in sorted(e.attrs.items()) if v and not (k == "volume" and v == "normal")]
    for lang in list(languages[1:]) + sorted(set(e.translations) - set(languages)):
        t = e.translations.get(lang)
        if not t or lang == languages[0]:
            continue
        if t[2]:
            rows.append(f"\t{lang} =")
        elif t[0].strip():
            rows.append(f"\t{lang}{'' if t[1] == e.text else '!'}: {t[0]}")
    return rows


def serialize_document(doc):
    """The whole file: metadata, then the lines with their other languages."""
    rows = [f"#% transcript: {FILE_VERSION}", f"#% languages: {', '.join(doc.languages)}"]
    for lang in doc.languages:
        names = [a.replace("|", "/").strip() for a in doc.actions.get(lang, [])]
        while names and not names[-1]:
            names.pop()
        if names:
            rows.append(f"#% actions {lang}: {' | '.join(names)}")
    for p in doc.persons:
        rows.append(f"#% person {p.name or 'Unnamed'}: {p.color} | {fmt_origin(p.origin)}")
    rows += doc.extra
    for e in doc.entries:
        rows.append(entry_to_line(e))
        if e.kind != "raw":
            rows += other_lang_lines(e, doc.languages)
    return "\n".join(rows) + "\n"


def _match_ms(m, prefix):
    ms = int(m[prefix + "h"]) * 3_600_000 + int(m[prefix + "m"]) * 60_000 + int(m[prefix + "s"]) * 1000
    if m[prefix + "ms"]:
        ms += int(m[prefix + "ms"].ljust(3, "0"))
    return ms


def serialize_transcript(entries, languages=("da",)):
    """Just the lines (no metadata), each with its other languages."""
    out = []
    for e in entries:
        out.append(entry_to_line(e))
        if e.kind != "raw":
            out += other_lang_lines(e, list(languages))
    return "".join(line + "\n" for line in out)


STAMPED_LINE_RE = re.compile(r"^\[\d+:\d{2}:\d{2}(?:\.\d{1,3})?[\]-]", re.M)


def _wrap(text, left, right):
    """Put text in quotes/brackets unless it already is."""
    if len(text) >= 2 and text[0] in left and text[-1] in right:
        return text
    return f"{left[0]}{text}{right[0]}"


def export_line(e, lang=None, below=None, orig=None):
    """One entry in the export style:  F: "says this"  /  F: (does this), in lang (the original
    when it has no text in lang). With below, that language follows on its own line:
    "the translation"  /  (translation)."""
    if e.kind == "raw":
        return e.text
    name = e.person.name or "Unnamed"
    says = e.kind == "says"
    if says:
        wrap = lambda t: _wrap(apply_volume(t, volume_of(e)), '"“', '"”')
    else:
        wrap = lambda t: t if is_gaze(t) else _wrap(t, "(", ")")
    line = f"{name}: {wrap(shown_text(e, lang, orig))}"
    extra = text_in(e, below, orig).strip() if below and below != lang else ""
    return f"{line}\n{wrap(extra)}" if extra else line


def entries_in_range(entries, start=None, end=None):
    """The entries that start within [start, end] (all when no range is given), in order.
    Lines without a time (notes, blank lines) are kept when they sit between kept lines."""
    if start is None and end is None:
        return list(entries)
    lo = -1 if start is None else start
    hi = float("inf") if end is None else end
    keep = [i for i, e in enumerate(entries) if e.time_ms is not None and lo <= e.time_ms <= hi]
    if not keep:
        return []
    first, last = keep[0], keep[-1]
    return [e for i, e in enumerate(entries[first:last + 1], first)
            if e.time_ms is None or lo <= e.time_ms <= hi]


def export_transcript(entries, start=None, end=None, lang=None, below=None, orig=None):
    """The transcript without timestamps, in the same order. Raw lines are copied as they are.
    With start/end only the lines starting in that range are exported; lang picks the
    language, below adds another language under each line."""
    return "".join(export_line(e, lang, below, orig) + "\n"
                   for e in entries_in_range(entries, start, end))


def subtitle_text(e, lang=None, orig=None):
    """How a line reads on the video:  E: what she says  /  F: (what he does), in lang
    (the original when it has no text in lang yet)."""
    name = e.person.name or tr("label.unnamed")
    text = shown_text(e, lang, orig)
    if e.kind == "says":
        return f"{name}: {apply_volume(text, volume_of(e))}"
    return f"{name}: {text}" if is_gaze(text) else f"{name}: {_wrap(text, '(', ')')}"


def subtitle_span(e):
    """(start, end) a line is shown on the video; without an end time it stays TEXT_HOLD_MS."""
    return e.time_ms, (e.end_ms if e.end_ms is not None else e.time_ms + TEXT_HOLD_MS)


def active_at(entries, pos):
    """The lines happening at pos, in time order (a line without an end lasts TEXT_HOLD_MS)."""
    out = []
    for e in entries:
        if e.kind == "raw" or e.time_ms is None or e.person is None or not (
                e.text.strip() or any(t[0].strip() for t in e.translations.values())):
            continue
        a, b = subtitle_span(e)
        if a <= pos <= b:
            out.append(e)
    return out


MAX_SUBTITLE_LINES = 6


def subtitle_lines(entries, pos, lang=None, orig=None):
    """[(text, colour, italic, origin)] on the video at pos, oldest first."""
    return [(subtitle_text(e, lang, orig), e.person.color, e.kind != "says", e.person.origin)
            for e in active_at(entries, pos)[-MAX_SUBTITLE_LINES:]]


def layout_subtitles(lines, r, px, family=""):
    """Where each line's box goes inside the picture rect r, as
    [(rect, text, colour, italic, align, pad)]. Lines are grouped by origin and stacked there,
    oldest first. A preset origin sits at that edge/corner; a point (x, y) puts the stack's top
    centre on that spot. Blocks are then moved up or down until no two boxes overlap."""
    font = QFont(family) if family else QFont()
    font.setPixelSize(px)
    margin = r.height() * 0.04
    gap = px * 0.18
    groups = {}
    for line in lines:
        groups.setdefault(line[3], []).append(line)

    def make_block(origin, group, narrow=1.0):
        """The stacked boxes for one origin at their wanted spot, and which way to move them
        when they collide (away from their own edge). narrow < 1 wraps the text narrower."""
        point = not isinstance(origin, int)
        col = 1 if point else (origin - 1) % 3          # 0 left, 1 centre, 2 right
        row = None if point else (origin - 1) // 3      # 0 bottom, 1 middle, 2 top
        align = (Qt.AlignLeft, Qt.AlignHCenter, Qt.AlignRight)[col]
        max_w = r.width() * (0.92 if col == 1 and not point else 0.45) * narrow
        boxes = []
        for text, color, italic, _o in group:
            font.setItalic(italic)
            font.setBold(not italic)
            pad = px * 0.3
            bb = QFontMetrics(font).boundingRect(QRectF(0, 0, max_w, 10_000).toRect(),
                                                 Qt.TextWordWrap | align, text)
            boxes.append([QSizeF(bb.width() + 2 * pad, bb.height() + 2 * pad), text, color, italic, pad])
        total = lambda: sum(bx[0].height() for bx in boxes) + gap * (len(boxes) - 1)
        while len(boxes) > 1 and total() > r.height() - 2 * margin:
            boxes.pop(0)                                 # no room: drop the oldest
        if point:
            y = clamp(r.top() + origin[1] * r.height(), r.top(), r.bottom() - total())
            cx = r.left() + origin[0] * r.width()
        elif row == 0:
            y = r.bottom() - margin - total()
        elif row == 2:
            y = r.top() + margin
        else:
            y = r.center().y() - total() / 2
        placed = []
        for size, text, color, italic, pad in boxes:
            if point:
                x = clamp(cx - size.width() / 2, r.left(), r.right() - size.width())
            elif col == 0:
                x = r.left() + margin
            elif col == 2:
                x = r.right() - margin - size.width()
            else:
                x = r.center().x() - size.width() / 2
            placed.append([QRectF(QPointF(x, y), size), text, color, italic, align, pad])
            y += size.height() + gap
        return placed, (-1 if row == 0 else 1)

    taken, out = [], []
    step = max(4.0, px * 0.8)

    def find_move(placed, prefer):
        """The nearest move that puts the block on free space: ((dx, dy), overlap area)."""
        rects = [bx[0] for bx in placed]
        top, bottom = min(rc.top() for rc in rects), max(rc.bottom() for rc in rects)
        left, right = min(rc.left() for rc in rects), max(rc.right() for rc in rects)

        def overlap(dx, dy):
            if top + dy < r.top() - 0.5 or bottom + dy > r.bottom() + 0.5 \
                    or left + dx < r.left() - 0.5 or right + dx > r.right() + 0.5:
                return float("inf")
            area = 0.0
            for rc in rects:
                moved = rc.translated(dx, dy).adjusted(-gap / 2, -gap / 2, gap / 2, gap / 2)
                for t in taken:
                    hit = moved.intersected(t)
                    area += max(0.0, hit.width()) * max(0.0, hit.height())
            return area

        cost = overlap(0.0, 0.0)
        if cost == 0:
            return (0.0, 0.0), 0.0
        # Candidates: steps up/down, the picture's sides and middle, and right next to every
        # box already placed (beside, above, below). The nearest free one wins, preferring
        # to move away from the block's own edge.
        w_, h_ = right - left, bottom - top
        xs = {0.0, r.left() + margin - left, r.right() - margin - right, r.center().x() - (left + right) / 2,
              r.left() - left, r.right() - right}
        ys = {0.0, r.top() + margin - top, r.bottom() - margin - bottom, r.top() - top, r.bottom() - bottom}
        for k in range(1, int(r.height() / step) + 2):
            ys |= {prefer * k * step, -prefer * k * step}
        for t in taken:
            xs |= {t.right() + gap - left, t.left() - gap - w_ - left}
            ys |= {t.bottom() + gap - top, t.top() - gap - h_ - top}
        best, best_cost = (0.0, 0.0), cost
        for dx, dy in sorted(((dx, dy) for dx in xs for dy in ys),
                             key=lambda m: (abs(m[0]) + abs(m[1]), 0 if m[1] * prefer >= 0 else 1)):
            c = overlap(dx, dy)
            if c == 0:
                return (dx, dy), 0.0
            if c < best_cost:
                best, best_cost = (dx, dy), c
        return best, best_cost

    for origin, group in groups.items():
        choice = None
        for narrow in (1.0, 0.6, 0.4):        # no free spot: try the text wrapped narrower
            placed, prefer = make_block(origin, group, narrow)
            move, cost = find_move(placed, prefer)
            if choice is None or cost < choice[2]:
                choice = (placed, move, cost)
            if cost == 0:
                break
        placed, move, _cost = choice          # (if nothing is free at all: the least overlap)
        for bx in placed:
            bx[0] = bx[0].translated(*move)
            taken.append(bx[0])
            out.append(tuple(bx))
    return out


def paint_subtitles(p, placed, px, family=""):
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    font = QFont(family) if family else QFont()
    font.setPixelSize(px)
    for rect, text, color, italic, align, pad in placed:
        font.setItalic(italic)
        font.setBold(not italic)
        p.setFont(font)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 215))
        p.drawRoundedRect(rect, pad, pad)
        p.setPen(QColor(color))
        p.drawText(rect.adjusted(pad, pad, -pad, -pad), Qt.TextWordWrap | align, text)


def subtitle_px(height, screen=True):
    """Text size for a picture this tall (on screen kept between 12 and 40 px)."""
    return int(clamp(height / 22, 12, 40)) if screen else max(12, round(height / 22))


def render_subtitles(lines, width, height):
    """A transparent width x height image with the lines drawn exactly as on screen."""
    img = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    if lines:
        r = QRectF(0, 0, width, height)
        px = subtitle_px(height, screen=False)
        p = QPainter(img)
        paint_subtitles(p, layout_subtitles(lines, r, px), px)
        p.end()
    return img


def build_overlay(entries, start, end, width, height, folder, lang=None, orig=None):
    """Draw the text for the clip [start, end] as transparent PNGs in folder - one per stretch
    of time in which the same lines are showing - plus an ffconcat list that plays them for
    the right durations. Returns the list's file name."""
    cuts = {start, end}
    for e in entries:
        if e.kind != "raw" and e.time_ms is not None and e.person is not None:
            for t in subtitle_span(e):
                if start < t < end:
                    cuts.add(t)
    cuts = sorted(cuts)
    files, rows = {}, ["ffconcat version 1.0"]
    last = None
    for a, b in zip(cuts, cuts[1:]):
        lines = subtitle_lines(entries, (a + b) / 2, lang, orig)
        key = tuple(lines)
        if key not in files:
            files[key] = f"text_{len(files):04d}.png"
            render_subtitles(lines, width, height).save(str(Path(folder) / files[key]))
        rows += [f"file '{files[key]}'", f"duration {(b - a) / 1000:.3f}"]
        last = files[key]
    rows.append(f"file '{last}'")            # the concat demuxer needs the last file twice
    (Path(folder) / "text.ffconcat").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return "text.ffconcat"


LEGACY_TRANSLATED_RE = re.compile(r'^\s+"(?P<text>.*)"\s*$')


def import_legacy_translations(entries, transcript_path):
    """The previous version kept each translation in its own file next to the transcript
    (<name>.<lang>.txt: the original line, then the translation in quotes on the next line).
    Read those into the lines that don't have that language yet. Returns [(lang, file, count)]."""
    found = []
    p = Path(transcript_path)
    for lang in TRANSLATION_LANGS:
        path = p.with_name(f"{p.stem}.{lang}.txt")
        if not path.exists() or same_file(path, p):
            continue
        pool = {}
        for e in entries:
            if e.kind != "raw" and e.time_ms is not None and e.person is not None:
                pool.setdefault((e.time_ms, e.kind, e.person.name), []).append(e)
        count, pending = 0, None
        for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            m = LINE_RE.match(line)
            if m:
                kind = "says" if m["colon"] else "does"
                pending = ((_match_ms(m, ""), kind, m["name"].strip()), m["said"] if m["colon"] else m["did"])
                continue
            t = LEGACY_TRANSLATED_RE.match(line)
            if t and pending and pool.get(pending[0]):
                e = pool[pending[0]].pop(0)
                if lang not in e.translations and t["text"].strip():
                    e.translations[lang] = [t["text"], pending[1], False]
                    count += 1
            pending = None
        if count:
            found.append((lang, path.name, count))
    return found


def write_atomically(path, text):
    """Write a UTF-8 file via a temporary file, so a failed write never leaves half a file."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp~")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


CA_MIN_PAUSE_MS = 200     # gaps between turns at least this long are written as (0.8)


def _word_index(text, frac):
    """Where a word starts nearest to frac (0..1) of the way through text."""
    starts = [i for i, ch in enumerate(text) if not ch.isspace() and (i == 0 or text[i - 1].isspace())]
    if not starts:
        return 0
    target = clamp(frac, 0, 1) * len(text)
    return min(starts, key=lambda i: abs(i - target))


def _insert_marks(text, marks):
    """Insert marks [(index, mark, tag)] (in the given order at equal indexes). Returns the new
    text and {tag: where that mark ended up}."""
    out, where, shift = text, {}, 0
    for idx, mark, tag in sorted(marks, key=lambda m: m[0]):
        pos = idx + shift
        out = out[:pos] + mark + out[pos:]
        if tag is not None:
            where[tag] = pos
        shift += len(mark)
    return out, where


def export_ca(entries, start=None, end=None, lang=None, below=None, orig=None, media=None, pauses=False):
    """A transcript in the style used in conversation analysis (CA):

         1. *E:  Δtak for hjælpen          <- what is said, numbered, °volume° as marked
                 thanks for the help       <- the translation (below), if wanted
                 Δ>F                       <- what E does while saying it (Δ = where it starts)
         2. *F:  det var [super fedt
         3. *E:          [må jeg få den    <- [ = where someone starts talking over
         4.      (0.8)                     <- a pause between turns (needs end times)
         5. *F:  rejser sig                <- an action on its own

    Actions are written in the translation's language when there is one (as in the example
    this follows), else in lang; gaze is written >Name."""
    sel = entries_in_range(entries, start, end)
    timed = [e for e in sel if e.kind != "raw" and e.person is not None and e.time_ms is not None]
    names = []
    for e in timed:
        name = e.person.name or "Unnamed"
        if name not in names:
            names.append(name)
    act_lang = below or lang

    def action_text(e):
        return text_in(e, act_lang, orig).strip() or shown_text(e, lang, orig)

    def end_of(e):
        return e.end_ms if e.end_ms is not None else e.time_ms

    talk = [e for e in timed if e.kind == "says" and shown_text(e, lang, orig)]
    # actions done during the same person's talk go under that line
    attached = {}
    for a in timed:
        if a.kind == "does":
            during = [u for u in talk if u.person is a.person and u.time_ms <= a.time_ms <= end_of(u)]
            if during:
                attached.setdefault(id(max(during, key=lambda u: u.time_ms)), []).append(a)
    hidden = {id(a) for acts in attached.values() for a in acts}
    # overlaps: someone starts while the previous line (by someone else) is still going
    overlap_of, prev = {}, None
    for u in talk:
        if prev is not None and prev.person is not u.person and prev.end_ms is not None \
                and prev.time_ms <= u.time_ms < prev.end_ms:
            overlap_of[id(u)] = prev
        prev = u

    units, last_talk = [], None
    for e in sel:
        if e.kind == "raw":
            if e.text.strip():
                units.append(("com", e))
        elif id(e) in hidden or e.person is None or e.time_ms is None:
            continue
        elif e.kind == "says":
            if not shown_text(e, lang, orig):
                continue
            if pauses and last_talk is not None and last_talk.end_ms is not None \
                    and e.time_ms - last_talk.end_ms >= CA_MIN_PAUSE_MS:
                units.append(("pause", e.time_ms - last_talk.end_ms))
            units.append(("talk", e))
            last_talk = e
        elif action_text(e):
            units.append(("act", e))

    count = sum(kind != "com" for kind, _ in units)
    num_w = len(str(max(count, 1)))
    spk_w = max([len(n) + 3 for n in names] + [5])          # "*Name:" plus a space
    indent = " " * (num_w + 2 + spk_w)
    rows = []
    if media:
        rows.append(f"@Media: {media}")
    if names:
        rows.append(f"@Participants: {', '.join(names)}")
    langs = [lang] + ([below] if below and below != lang else [])
    rows.append(f"@Languages: {', '.join(TRANSLATION_LANGS.get(c, c) for c in langs if c)}")
    rows.append("")

    rendered, n = {}, 0
    for kind, item in units:
        if kind == "com":
            rows.append(f"%com: {item.text.strip()}")
            continue
        n += 1
        prefix = f"{n:>{num_w}}. "
        if kind == "pause":
            rows.append(f"{prefix}{' ' * spk_w}({item / 1000:.1f})")
            continue
        speaker = f"*{item.person.name or 'Unnamed'}:".ljust(spk_w)
        if kind == "act":
            rows.append(f"{prefix}{speaker}{action_text(item)}")
            continue
        u = item
        text = apply_volume(shown_text(u, lang, orig), volume_of(u))
        dur = (u.end_ms - u.time_ms) if u.end_ms is not None else 0
        marks = []
        for b in talk:                          # someone else starts talking during this line
            if overlap_of.get(id(b)) is u:
                marks.append((_word_index(text, (b.time_ms - u.time_ms) / dur if dur else 0), "[", id(b)))
        for a in attached.get(id(u), []):
            idx = _word_index(text, (a.time_ms - u.time_ms) / dur if dur else 0)
            if not any(m[0] == idx and m[1] == "Δ" for m in marks):
                marks.append((idx, "Δ", None))
        text, where = _insert_marks(text, marks)
        rendered.update(where)
        if id(u) in overlap_of:                 # line the [ up under the other speaker's [
            col = rendered.get(id(u))
            if col is not None:
                text = " " * col + "[" + text
        rows.append(f"{prefix}{speaker}{text}")
        if below and below != lang:
            translated = text_in(u, below, orig).strip()
            if translated:
                rows.append(indent + apply_volume(translated, volume_of(u)))
        for a in attached.get(id(u), []):
            rows.append(indent + "Δ" + action_text(a))
    return "\n".join(rows) + "\n"


def same_file(a, b):
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


def ffmpeg_exe():
    """The ffmpeg program bundled with imageio-ffmpeg, or one on PATH; None if neither."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:   # not installed or no binary for this platform
        from shutil import which
        return which("ffmpeg")


def video_export_args(video, out, start, end, overlay_list=None):
    """ffmpeg arguments for cutting [start, end] out of video (re-encoded so the cut is exact).
    With overlay_list (an ffconcat list of transparent PNGs in ffmpeg's working folder, see
    build_overlay) the text is laid over the picture."""
    args = ["-hide_banner", "-y", "-nostats", "-progress", "pipe:1",
            "-ss", f"{start / 1000:.3f}", "-t", f"{(end - start) / 1000:.3f}", "-i", str(video)]
    if overlay_list:
        args += ["-f", "concat", "-safe", "0", "-i", overlay_list, "-filter_complex",
                 "[1:v]format=rgba[t];[0:v][t]overlay=0:0:eof_action=pass:format=auto,format=yuv420p[v]",
                 "-map", "[v]"]
    else:
        args += ["-map", "0:v:0", "-pix_fmt", "yuv420p"]
    args += ["-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "-t", f"{(end - start) / 1000:.3f}",
             str(out)]
    return args


# ----------------------------------------------------------------- widgets

class JumpSlider(QSlider):
    """Slider that jumps straight to the clicked position instead of paging."""

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton and self.maximum() > self.minimum():
            opt = QStyleOptionSlider()
            self.initStyleOption(opt)
            groove = self.style().subControlRect(QStyle.CC_Slider, opt, QStyle.SC_SliderGroove, self)
            handle = self.style().subControlRect(QStyle.CC_Slider, opt, QStyle.SC_SliderHandle, self)
            if not handle.contains(ev.position().toPoint()):
                span = max(1, groove.width() - handle.width())
                x = int(ev.position().x() - groove.x() - handle.width() / 2)
                self.setValue(QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), x, span))
                self.sliderMoved.emit(self.value())
        super().mousePressEvent(ev)


class IntervalBar(QWidget):
    """The whole video: the interval as a range with two draggable handles,
    and a line marking the current position."""
    intervalEdited = Signal(int, int, str)   # start, end, which handle moved
    seekRequested = Signal(int)
    MARGIN = 6     # room for the handles at both ends
    GRAB = 8       # how close (px) counts as grabbing a handle

    def __init__(self):
        super().__init__(focusPolicy=Qt.NoFocus, mouseTracking=True)
        self.setFixedHeight(24)
        self.duration = self.start = self.end = self.pos = 0
        self._drag = None

    def set_interval(self, duration, start, end):
        self.duration, self.start, self.end = duration, start, end
        self.update()

    def set_pos(self, pos):
        self.pos = pos
        self.update()

    def x_of(self, ms):
        width = self.width() - 2 * self.MARGIN
        return self.MARGIN + (width * ms / self.duration if self.duration else 0)

    def ms_of(self, x):
        width = max(1, self.width() - 2 * self.MARGIN)
        return round(clamp((x - self.MARGIN) / width, 0.0, 1.0) * self.duration)

    def handle_at(self, x):
        ds, de = abs(x - self.x_of(self.start)), abs(x - self.x_of(self.end))
        if min(ds, de) > self.GRAB:
            return None
        if ds == de:   # handles on top of each other: pick by side
            return "start" if x < self.x_of(self.start) else "end"
        return "start" if ds < de else "end"

    def mousePressEvent(self, ev):
        if self.duration and ev.button() == Qt.LeftButton:
            self._drag = self.handle_at(ev.position().x()) or "seek"
            self.mouseMoveEvent(ev)

    def mouseMoveEvent(self, ev):
        x = ev.position().x()
        if self._drag is None:
            self.setCursor(Qt.SizeHorCursor if self.duration and self.handle_at(x) else Qt.ArrowCursor)
            return
        ms = self.ms_of(x)
        if self._drag == "start":
            self.intervalEdited.emit(ms, self.end, "start")
        elif self._drag == "end":
            self.intervalEdited.emit(self.start, ms, "end")
        else:
            self.seekRequested.emit(ms)

    def mouseReleaseEvent(self, _ev):
        self._drag = None

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal, h = self.palette(), self.height()
        track = QRectF(self.MARGIN, h / 2 - 4, self.width() - 2 * self.MARGIN, 8)
        p.setPen(Qt.NoPen)
        p.setBrush(pal.color(QPalette.Mid))
        p.drawRoundedRect(track, 4, 4)
        if not self.duration:
            return
        accent = pal.color(QPalette.Highlight)
        xs, xe = self.x_of(self.start), self.x_of(self.end)
        p.setBrush(accent)
        p.drawRoundedRect(QRectF(xs, track.top(), xe - xs, track.height()), 4, 4)
        p.setBrush(accent.darker(140))
        for x in (xs, xe):
            p.drawRoundedRect(QRectF(x - 3, 2, 6, h - 4), 2, 2)
        p.setPen(QPen(pal.color(QPalette.WindowText), 2))
        xp = self.x_of(self.pos)
        p.drawLine(QPointF(xp, 1), QPointF(xp, h - 1))


class VideoView(QWidget):
    """Draws the video frames itself, so the lines that are happening right now
    can be drawn on top in each person's colour."""
    MAX_LINES = 6

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.show_text = False
        self.pick_person = None      # choosing this person's text point
        self._layouts = {}           # recent text layouts
        self.frame = QVideoFrame()
        self.sink = QVideoSink(self)
        self.sink.videoFrameChanged.connect(self._new_frame)
        self.setMinimumSize(320, 180)
        self.setAttribute(Qt.WA_OpaquePaintEvent)

    def _new_frame(self, frame):
        self.frame = frame
        self.update()

    def video_rect(self):
        size = self.frame.size() if self.frame.isValid() else None
        if not size or size.isEmpty():
            return QRectF(self.rect())
        s = QSizeF(size).scaled(QSizeF(self.size()), Qt.KeepAspectRatio)
        return QRectF((self.width() - s.width()) / 2, (self.height() - s.height()) / 2, s.width(), s.height())

    def lines(self):
        """[(text, colour, italic, origin)] for the lines happening now, oldest first."""
        w = self.win
        return subtitle_lines(w.entries, w.player.position(), w.lang, w.orig)

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.black)
        r = self.video_rect()
        if self.frame.isValid():
            self.frame.paint(p, r, QVideoFrame.PaintOptions())
        if self.show_text:
            self.paint_lines(p, r)
        if self.pick_person is not None:
            self.paint_pick(p, r)

    def layout_lines(self, r):
        """[(rect, text, colour, italic, align, pad)] - where each line's box goes (no overlaps).
        The same lines stay up for many frames, so the last few layouts are remembered."""
        px = subtitle_px(r.height())
        key = (tuple(self.lines()), r.x(), r.y(), r.width(), r.height(), px)
        placed = self._layouts.get(key)
        if placed is None:
            if len(self._layouts) > 32:
                self._layouts.clear()
            placed = self._layouts[key] = layout_subtitles(list(key[0]), r, px)
        return placed

    def paint_lines(self, p, r):
        paint_subtitles(p, self.layout_lines(r), subtitle_px(r.height()))

    # --- choosing a point on the video for a person's text
    def start_pick(self, person):
        self.pick_person = person
        self.setCursor(Qt.CrossCursor)
        self.update()

    def stop_pick(self):
        self.pick_person = None
        self.unsetCursor()
        self.update()

    def point_at(self, pos):
        """(x, y) as fractions of the picture for a widget position, kept inside the picture."""
        r = self.video_rect()
        return (round(clamp((pos.x() - r.left()) / max(1, r.width()), 0, 1), 4),
                round(clamp((pos.y() - r.top()) / max(1, r.height()), 0, 1), 4))

    def mousePressEvent(self, ev):
        if self.pick_person is not None and ev.button() == Qt.LeftButton:
            person = self.pick_person
            self.stop_pick()
            self.win.statusBar().clearMessage()
            self.win.set_origin(person, self.point_at(ev.position()))
            return
        super().mousePressEvent(ev)

    def paint_pick(self, p, r):
        """While picking: a hint at the top and a cross on every point already chosen."""
        p.setRenderHint(QPainter.Antialiasing)
        for person in self.win.persons:
            if isinstance(person.origin, tuple):
                x, y = r.left() + person.origin[0] * r.width(), r.top() + person.origin[1] * r.height()
                p.setPen(QPen(QColor(person.color), 3))
                p.drawLine(QPointF(x - 9, y), QPointF(x + 9, y))
                p.drawLine(QPointF(x, y - 9), QPointF(x, y + 9))
        font = QFont(p.font())
        font.setPixelSize(15)
        font.setBold(True)
        p.setFont(font)
        hint = tr("status.pick", name=display_name(self.pick_person))
        box = QRectF(r.left() + 8, r.top() + 8, r.width() - 16, 30)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 170))
        p.drawRoundedRect(box, 6, 6)
        p.setPen(QColor(self.pick_person.color).lighter(160))
        p.drawText(box, Qt.AlignCenter, hint)


class ControlsBar(QWidget):
    """The playback buttons. When there isn't room, whole groups of buttons are
    hidden in drop_order (first the ±5 s pair, then the ±1 s pair) instead of
    squashing everything."""

    def __init__(self):
        super().__init__()
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(6)
        self.lay.setSizeConstraint(QLayout.SetNoConstraint)
        self.drop_order = []

    def _widgets(self):
        return [w for w in (self.lay.itemAt(i).widget() for i in range(self.lay.count())) if w]

    def needed_width(self, dropped=()):
        shown = [w for w in self._widgets() if w not in dropped]
        return sum(w.sizeHint().width() for w in shown) + self.lay.spacing() * max(0, len(shown) - 1)

    def minimumSizeHint(self):
        everything = {w for group in self.drop_order for w in group}
        return QSize(self.needed_width(everything), super().minimumSizeHint().height())

    def groups_to_drop(self, width):
        dropped = set()
        for group in self.drop_order:
            if self.needed_width(dropped) <= width:
                break
            dropped |= set(group)
        return dropped

    def fit(self, width=None):
        width = self.width() if width is None else width
        if getattr(self, "_fitting", False):
            return
        self._fitting = True          # showing a button can resize us again; finish this pass first
        try:
            dropped = self.groups_to_drop(width)
            for group in self.drop_order:
                for w in group:
                    w.setVisible(w not in dropped)
        finally:
            self._fitting = False

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self.fit()


class WrapRow(QWidget):
    """Two groups of widgets on one line; the second group moves to its own line
    when there isn't room (its last widget is pushed to the right)."""
    SPACING = 6

    def __init__(self, first, second):
        super().__init__()
        self.first, self.second = first, second
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        v.setSizeConstraint(QLayout.SetNoConstraint)
        self.lines = [QHBoxLayout(), QHBoxLayout()]
        for line in self.lines:
            line.setSpacing(self.SPACING)
            v.addLayout(line)
        self.wrapped = None
        self.arrange(False)

    def _width(self, widgets):
        return sum(w.sizeHint().width() for w in widgets) + self.SPACING * max(0, len(widgets) - 1)

    def one_line_width(self):
        return self._width(self.first + self.second)

    def minimumSizeHint(self):
        return QSize(max(self._width(self.first), self._width(self.second)),
                     super().minimumSizeHint().height())

    def arrange(self, wrapped):
        if wrapped == self.wrapped:
            return
        self.wrapped = wrapped
        for line in self.lines:
            while line.count():
                line.takeAt(0)
        top, bottom = self.lines
        for w in self.first:
            top.addWidget(w)
        target = bottom if wrapped else top
        for w in self.second[:-1]:
            target.addWidget(w)
        target.addStretch(1)
        target.addWidget(self.second[-1])
        if wrapped:
            top.addStretch(1)
        self.updateGeometry()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self.arrange(self.width() < self.one_line_width())


class VolumeButton(QToolButton):
    """Mute button (click) with a volume slider that drops down while hovering."""
    HIDE_DELAY = 300

    def __init__(self):
        super().__init__(checkable=True, focusPolicy=Qt.NoFocus, autoRaise=False)
        self.setFixedWidth(36)
        self.popup = QFrame(self, Qt.ToolTip | Qt.FramelessWindowHint)
        self.popup.setFrameShape(QFrame.StyledPanel)
        self.popup.setAttribute(Qt.WA_ShowWithoutActivating)
        pl = QVBoxLayout(self.popup)
        pl.setContentsMargins(4, 6, 4, 6)
        self.slider = QSlider(Qt.Vertical, focusPolicy=Qt.NoFocus, minimum=0, maximum=100)
        self.slider.setFixedHeight(110)
        self.value_label = QLabel(alignment=Qt.AlignCenter)
        self.slider.valueChanged.connect(lambda v: self.value_label.setText(f"{v}%"))
        pl.addWidget(self.slider, 0, Qt.AlignHCenter)
        pl.addWidget(self.value_label)
        self.hide_timer = QTimer(self, singleShot=True, interval=self.HIDE_DELAY)
        self.hide_timer.timeout.connect(self.maybe_hide)
        self.popup.installEventFilter(self)

    def show_popup(self):
        self.hide_timer.stop()
        self.popup.adjustSize()
        x = self.width() / 2 - self.popup.width() / 2
        self.popup.move(self.mapToGlobal(QPoint(int(x), self.height())))
        self.popup.show()

    def maybe_hide(self, pos=None):
        pos = QCursor.pos() if pos is None else pos
        over_button = self.rect().contains(self.mapFromGlobal(pos))
        if not over_button and not self.popup.geometry().contains(pos):
            self.popup.hide()

    def enterEvent(self, ev):
        self.show_popup()
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        self.hide_timer.start()
        super().leaveEvent(ev)

    def eventFilter(self, obj, ev):
        if obj is self.popup:
            if ev.type() == QEvent.Enter:
                self.hide_timer.stop()
            elif ev.type() == QEvent.Leave:
                self.hide_timer.start()
        return False

    def hideEvent(self, ev):
        self.popup.hide()
        super().hideEvent(ev)


class NowMarkers(QWidget):
    """Transparent layer over the transcript list. While the video plays it draws a
    line at 1 s before and 1 s after the current position and tints the lines in
    between, so you can follow along on the right side."""
    COLOR = QColor("#ff7f0e")
    WINDOW_MS = 1000

    def __init__(self, win, parent):
        super().__init__(parent)
        self.win = win
        self.span = None          # (top y, bottom y, rows inside, pos) or None
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        parent.installEventFilter(self)
        self.setGeometry(parent.rect())

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Resize:
            self.setGeometry(obj.rect())
        return False

    def compute(self, pos):
        lo, hi = pos - self.WINDOW_MS, pos + self.WINDOW_MS
        rows = [r for r in self.win.rows if r.entry.time_ms is not None
                and r.entry.time_ms <= hi and (r.entry.end_ms or r.entry.time_ms) >= lo]
        if rows:
            return (min(r.geometry().top() for r in rows), max(r.geometry().bottom() for r in rows),
                    rows, pos)
        # Nothing within a second: one line where the current time falls between lines.
        later = next((r for r in self.win.rows if r.entry.time_ms is not None and r.entry.time_ms > pos), None)
        timed = [r for r in self.win.rows if r.entry.time_ms is not None]
        if later is not None:
            y = later.geometry().top() - 1
        elif timed:
            y = timed[-1].geometry().bottom() + 1
        else:
            y = 2
        return (y, y, [], pos)

    def set_pos(self, pos):
        """pos=None hides the markers. Returns the span shown."""
        span = None if pos is None else self.compute(pos)
        old = self.span
        self.span = span
        if (old is None) != (span is None) or (span and old and old[:3] != span[:3]) \
                or (span and old and span[3] // 1000 != old[3] // 1000):   # labels show seconds
            self.raise_()
            self.update()
        return span

    def paintEvent(self, _ev):
        if not self.span:
            return
        top, bottom, rows, pos = self.span
        p = QPainter(self)
        tint = QColor(self.COLOR)
        tint.setAlpha(40)
        for r in rows:
            p.fillRect(r.geometry(), tint)
        p.setPen(QPen(self.COLOR, 2))
        font = p.font()
        font.setPixelSize(10)
        font.setBold(True)
        p.setFont(font)
        fm = p.fontMetrics()
        marks = [(top - 1, "−1s " + self.win.fmt(max(0, pos - self.WINDOW_MS), millis=False)),
                 (bottom + 1, "+1s " + self.win.fmt(pos + self.WINDOW_MS, millis=False))]
        if not rows:   # one line, labelled with both
            marks = [(top, f"−1s / +1s  {self.win.fmt(pos, millis=False)}")]
        for y, label in marks:
            p.setPen(QPen(self.COLOR, 2))
            p.drawLine(0, y, self.width(), y)
            # A small tag centred on the line, left of the rows' ✕ buttons.
            w = fm.horizontalAdvance(label) + 8
            box = QRectF(self.width() - w - 34, y - fm.height() / 2, w, fm.height())
            if box.top() < 0:
                box.moveTop(0)
            p.fillRect(box, self.COLOR)
            p.setPen(QColor("white"))
            p.drawText(box, Qt.AlignCenter, label)


class ExportDialog(QDialog):
    """What to export (a video clip with the text burnt in, or the text without timestamps),
    the start and stop point (the interval by default) and the file."""

    def __init__(self, win, kind="video"):
        super().__init__(win)
        self.win = win
        self.setWindowTitle(tr("dlg.export_title"))
        form = QFormLayout(self)

        self.video_radio = QRadioButton(tr("label.export_video"))
        self.text_radio = QRadioButton(tr("label.export_text"))
        self.kind_group = QButtonGroup(self)
        for b in (self.video_radio, self.text_radio):
            self.kind_group.addButton(b)
        self.subs_cb = QCheckBox(tr("cb.subtitles"), checked=True)
        other = win.other_lang
        has_other = other != win.lang and any(text_in(e, other, win.orig).strip() for e in win.entries)
        self.tr_cb = QCheckBox(tr("cb.tr_export", lang=TRANSLATION_LANGS.get(other, other)),
                               checked=has_other)
        self.tr_cb.setEnabled(has_other)
        self.format_combo = QComboBox()
        self.format_combo.addItem(tr("label.format_ca"), "ca")
        self.format_combo.addItem(tr("label.format_simple"), "simple")
        self.format_combo.setCurrentIndex(max(0, self.format_combo.findData(
            win.settings.value("export_format", "ca"))))
        self.pauses_cb = QCheckBox(tr("cb.pauses"), checked=win.settings.value("export_pauses", True, type=bool))
        self.format_combo.currentIndexChanged.connect(lambda _i: self._kind_changed())
        indent = lambda w: (lambda row: (row.addSpacing(24), row.addWidget(w, 1), row)[2])(QHBoxLayout())
        what = QVBoxLayout()
        what.addWidget(self.video_radio)
        what.addLayout(indent(self.subs_cb))
        what.addWidget(self.text_radio)
        what.addLayout(indent(self.format_combo))
        what.addLayout(indent(self.tr_cb))
        what.addLayout(indent(self.pauses_cb))
        self._has_other = has_other
        form.addRow(tr("label.export_what"), what)
        if not win.video_path:
            self.video_radio.setEnabled(False)
            self.subs_cb.setEnabled(False)
            kind = "text"

        fmt = "HH:mm:ss.zzz" if win.hours else "mm:ss.zzz"
        if win.duration > 0:
            start, end, top = win.iv_start, win.iv_end, win.duration
        else:
            last = max((max(e.time_ms, e.end_ms or 0) for e in win.entries if e.time_ms is not None), default=0)
            start, end, top = 0, last, max(last, 24 * 3_600_000 - 1)
        self.start_edit = QTimeEdit(displayFormat=fmt, maximumTime=ms_to_qtime(top), time=ms_to_qtime(start))
        self.end_edit = QTimeEdit(displayFormat=fmt, maximumTime=ms_to_qtime(top), time=ms_to_qtime(end))
        for label_key, edit in (("label.export_start", self.start_edit), ("label.export_stop", self.end_edit)):
            row = QHBoxLayout()
            row.addWidget(edit, 1)
            now = QPushButton(tr("btn.now"), toolTip=tr("tip.now"))
            now.clicked.connect(lambda _=False, e=edit: e.setTime(ms_to_qtime(win.player.position())))
            row.addWidget(now)
            form.addRow(tr(label_key), row)
            edit.timeChanged.connect(self._suggest)

        self.path_edit = QLineEdit(minimumWidth=380)
        self.path_edit.textEdited.connect(lambda _t: setattr(self, "_auto_path", False))
        browse = QPushButton(tr("btn.browse"))
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.path_edit, 1)
        row.addWidget(browse)
        form.addRow(tr("label.export_file"), row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText(tr("btn.export"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        self._auto_path = True
        (self.video_radio if kind == "video" else self.text_radio).setChecked(True)
        self.kind_group.buttonToggled.connect(lambda *_: self._kind_changed())
        self._kind_changed()

    def kind(self):
        return "video" if self.video_radio.isChecked() else "text"

    def _kind_changed(self):
        video = self.kind() == "video"
        self.subs_cb.setEnabled(video and self.win.video_path is not None)
        self.tr_cb.setEnabled(not video and self._has_other)
        self.format_combo.setEnabled(not video)
        self.pauses_cb.setEnabled(not video and self.format_combo.currentData() == "ca")
        self._suggest()

    def _suggest(self, *_):
        """Propose a file name next to the video / transcript until you type your own."""
        if not self._auto_path:
            return
        start, end = qtime_to_ms(self.start_edit.time()), qtime_to_ms(self.end_edit.time())
        span = f"{fmt_ms(start, False, self.win.hours)}-{fmt_ms(end, False, self.win.hours)}".replace(":", ".")
        if self.kind() == "video":
            base = Path(self.win.video_path)
            name = f"{base.stem}_{span}.mp4"
        else:
            base = Path(self.win.transcript_path or self.win.video_path or tr("dlg.default_name"))
            name = f"{base.stem}{tr('dlg.export_suffix')}_{span}.txt"
        self.path_edit.setText(str(base.with_name(name)))

    def _browse(self):
        video = self.kind() == "video"
        filt = (f"{tr('filter.mp4')} (*.mp4)" if video else f"{tr('filter.text')} (*.txt)") + \
            f";;{tr('filter.all')} (*)"
        path, _ = QFileDialog.getSaveFileName(self, tr("dlg.export_title"), self.path_edit.text(), filt)
        if path:
            self.path_edit.setText(path)
            self._auto_path = False

    def values(self):
        return {"kind": self.kind(), "subtitles": self.subs_cb.isChecked(),
                "translation": self.tr_cb.isChecked(), "format": self.format_combo.currentData(),
                "pauses": self.pauses_cb.isChecked(),
                "start": qtime_to_ms(self.start_edit.time()), "end": qtime_to_ms(self.end_edit.time()),
                "path": self.path_edit.text().strip()}

    def accept(self):
        v = self.values()
        if v["end"] <= v["start"]:
            QMessageBox.warning(self, tr("dlg.export_refused"), tr("dlg.export_range_bad"))
            return
        if not v["path"]:
            return
        target = Path(v["path"])
        if target.exists() and self._auto_path and QMessageBox.question(
                self, tr("dlg.export_title"), tr("dlg.export_replace", name=target.name)) != QMessageBox.Yes:
            return      # (the Browse dialog already asked when you picked the file there)
        super().accept()


class TranslationWindow(QWidget):
    """A second window: the transcript in the language shown (the Language menu) on the left,
    and in another language on the right, where you type. Each line shows whether it is
    translated (✓), the same in both languages (=), still missing (–) or out of date because
    the original changed (!). The "=" button marks a line as the same in both languages."""
    COL_TIME, COL_PERSON, COL_LEFT, COL_RIGHT, COL_SAME, COL_STATUS = range(6)
    STATUS = {"done": ("✓", "#2ca02c"), "same": ("=", "#2ca02c"), "missing": ("–", "#9a9a9a"),
              "outdated": ("!", "#ff7f0e")}

    def __init__(self, win):
        super().__init__(win, Qt.Window)
        self.win = win
        self.row_entries = []
        self.resize(1050, 700)
        v = QVBoxLayout(self)

        top = QHBoxLayout()
        self.left_label = QLabel()
        f = self.left_label.font()
        f.setBold(True)
        self.left_label.setFont(f)
        self.to_label = QLabel()
        self.dst_combo = QComboBox()
        self.dst_combo.currentIndexChanged.connect(
            lambda _i: win.set_other_lang(self.dst_combo.currentData()) if self.dst_combo.currentData() else None)
        for w in (self.left_label, QLabel("↔"), self.to_label, self.dst_combo):
            top.addWidget(w)
        top.addStretch(1)
        self.todo_cb = QCheckBox()
        self.todo_cb.toggled.connect(lambda _on: self.refresh())
        self.mark_btn = QPushButton()
        self.mark_btn.clicked.connect(self.mark_selected_current)
        top.addWidget(self.todo_cb)
        top.addWidget(self.mark_btn)
        v.addLayout(top)

        self.count_label = QLabel()
        v.addWidget(self.count_label)
        self.table = QTableWidget(0, 6)
        self.table.verticalHeader().hide()
        self.table.setWordWrap(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
                                   | QAbstractItemView.AnyKeyPressed | QAbstractItemView.SelectedClicked)
        head = self.table.horizontalHeader()
        for col, mode in ((self.COL_TIME, QHeaderView.ResizeToContents),
                          (self.COL_PERSON, QHeaderView.ResizeToContents),
                          (self.COL_LEFT, QHeaderView.Stretch), (self.COL_RIGHT, QHeaderView.Stretch),
                          (self.COL_SAME, QHeaderView.ResizeToContents),
                          (self.COL_STATUS, QHeaderView.ResizeToContents)):
            head.setSectionResizeMode(col, mode)
        self.table.itemChanged.connect(self._edited)
        self.table.cellClicked.connect(self._clicked)
        # Enter in a translation goes on to the next line.
        self.table.itemDelegate().closeEditor.connect(self._editor_closed)
        v.addWidget(self.table, 1)

        self.refresh_timer = QTimer(self, singleShot=True, interval=250)
        self.refresh_timer.timeout.connect(self.refresh)
        self.retranslate()
        self.refresh()

    def retranslate(self):
        w = self.win
        self.setWindowTitle(tr("label.tr_title"))
        self.left_label.setText(TRANSLATION_LANGS.get(w.lang, w.lang))
        self.to_label.setText(tr("label.tr_to"))
        self.dst_combo.blockSignals(True)
        self.dst_combo.clear()
        for code, name in TRANSLATION_LANGS.items():
            if code != w.lang:
                self.dst_combo.addItem(name, code)
        self.dst_combo.setCurrentIndex(self.dst_combo.findData(w.other_lang))
        self.dst_combo.blockSignals(False)
        self.todo_cb.setText(tr("cb.tr_todo"))
        self.mark_btn.setText(tr("btn.tr_current"))
        self.mark_btn.setToolTip(tr("tip.tr_current"))
        self._headers()
        self.update_counts()

    def _headers(self):
        w = self.win
        self.table.setHorizontalHeaderLabels([
            tr("label.tr_time"), tr("label.tr_person"), TRANSLATION_LANGS.get(w.lang, w.lang),
            TRANSLATION_LANGS.get(w.other_lang, w.other_lang), "=", ""])
        self.table.horizontalHeaderItem(self.COL_SAME).setToolTip(tr("tip.same_btn"))

    def schedule_refresh(self):
        self.refresh_timer.start()

    def refresh(self):
        """Rebuild the list (keeps the selected line and scroll position)."""
        if self.table.state() == QAbstractItemView.EditingState:
            self.refresh_timer.start()      # don't pull the rug from under the typing
            return
        w = self.win
        current = self.current_entry()
        scroll = self.table.verticalScrollBar().value()
        entries = [e for e in w.entries if translation_status(e, w.pair_lang, w.orig) != "none"]
        if self.todo_cb.isChecked():
            entries = [e for e in entries if translation_status(e, w.pair_lang, w.orig) in ("missing", "outdated")]
        was_blocked = self.table.blockSignals(True)
        self.table.setRowCount(0)
        self.table.setRowCount(len(entries))
        self.row_entries = entries
        for i, e in enumerate(entries):
            time_item = QTableWidgetItem(w.fmt(e.time_ms, millis=False))
            person_item = QTableWidgetItem(display_name(e.person))
            person_item.setForeground(QColor(e.person.color))
            f = person_item.font()
            f.setBold(True)
            person_item.setFont(f)
            left = QTableWidgetItem()
            right = QTableWidgetItem()
            status = QTableWidgetItem()
            for item in (time_item, person_item, left, status):
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            status.setTextAlignment(Qt.AlignCenter)
            for col, item in ((self.COL_TIME, time_item), (self.COL_PERSON, person_item),
                              (self.COL_LEFT, left), (self.COL_RIGHT, right), (self.COL_STATUS, status)):
                self.table.setItem(i, col, item)
            same = QToolButton(text="=", checkable=True, autoRaise=True, focusPolicy=Qt.NoFocus)
            same.setToolTip(tr("tip.same_btn"))
            same.setStyleSheet("QToolButton{font-weight:bold;padding:0 6px;}"
                               "QToolButton:checked{color:white;background:#2ca02c;border-radius:3px;}")
            same.toggled.connect(lambda on, e=e: self._same_toggled(e, on))
            self.table.setCellWidget(i, self.COL_SAME, same)
            self._show_row(i)
        self._headers()
        self.table.blockSignals(was_blocked)
        self.table.resizeRowsToContents()
        self.table.verticalScrollBar().setValue(scroll)
        if current in entries:
            self.table.selectRow(entries.index(current))
        self.update_counts()

    def _show_row(self, i):
        """Fill in one line's two languages, its "=" button and its status."""
        w = self.win
        e = self.row_entries[i]
        status = translation_status(e, w.pair_lang, w.orig)
        was_blocked = self.table.blockSignals(True)       # (keep it blocked during a rebuild)
        left, right = self.table.item(i, self.COL_LEFT), self.table.item(i, self.COL_RIGHT)
        left.setText(text_in(e, w.lang, w.orig))
        right.setText(text_in(e, w.other_lang, w.orig))
        italic = e.kind != "says"
        for item in (left, right):
            fi = item.font()
            fi.setItalic(italic)
            item.setFont(fi)
        # "Same in both languages": the translation follows the original, so it isn't typed.
        same_locked = status == "same" and w.other_lang == w.pair_lang
        flags = right.flags()
        right.setFlags(flags & ~Qt.ItemIsEditable if same_locked else flags | Qt.ItemIsEditable)
        right.setForeground(QColor("#2ca02c") if same_locked else self.palette().color(QPalette.Text))
        right.setBackground(QColor(255, 127, 14, 45) if status == "outdated" else QColor(0, 0, 0, 0))
        t = e.translations.get(w.pair_lang)
        right.setToolTip(tr("tip.tr_was", text=t[1]) if status == "outdated" and t and t[1] else "")
        symbol, color = self.STATUS.get(status, ("", "#000"))
        item = self.table.item(i, self.COL_STATUS)
        item.setText(symbol)
        item.setForeground(QColor(color))
        f = item.font()
        f.setBold(True)
        item.setFont(f)
        item.setToolTip(tr(f"tip.tr_{status}") if status in self.STATUS else "")
        same = self.table.cellWidget(i, self.COL_SAME)
        if same is not None:
            same.blockSignals(True)
            same.setChecked(status == "same")
            same.blockSignals(False)
        self.table.blockSignals(was_blocked)

    def update_counts(self):
        w = self.win
        statuses = [translation_status(e, w.pair_lang, w.orig) for e in w.entries]
        total = sum(st != "none" for st in statuses)
        self.count_label.setText(tr("label.tr_count", done=statuses.count("done") + statuses.count("same"),
                                    total=total, outdated=statuses.count("outdated")))

    def current_entry(self):
        row = self.table.currentRow()
        return self.row_entries[row] if 0 <= row < len(self.row_entries) else None

    def row_of(self, entry):
        return self.row_entries.index(entry) if entry in self.row_entries else -1

    def _edited(self, item):
        if item.column() != self.COL_RIGHT:
            return
        e = self.row_entries[item.row()]
        self.win.set_text(e, item.text(), self.win.other_lang)
        self._show_row(item.row())
        self.update_counts()

    def _same_toggled(self, entry, on):
        self.win.set_same(entry, on)
        row = self.row_of(entry)
        if row >= 0:
            self._show_row(row)
        self.update_counts()

    def _clicked(self, row, col):
        if col in (self.COL_TIME, self.COL_PERSON) and 0 <= row < len(self.row_entries):
            e = self.row_entries[row]
            self.win.select_entry(e)
            self.win.seek(e.time_ms)

    def _editor_closed(self, _editor, hint):
        if hint == QAbstractItemDelegate.SubmitModelCache:      # Enter
            row = self.table.currentRow() + 1
            if row < self.table.rowCount():
                QTimer.singleShot(0, lambda: self.edit_row(row))

    def edit_row(self, row):
        if 0 <= row < self.table.rowCount():
            self.table.setCurrentCell(row, self.COL_RIGHT)
            item = self.table.item(row, self.COL_RIGHT)
            if item.flags() & Qt.ItemIsEditable:
                self.table.editItem(item)

    def focus_entry(self, entry):
        if entry not in self.row_entries and self.todo_cb.isChecked():
            self.todo_cb.setChecked(False)
        row = self.row_of(entry)
        if row >= 0:
            self.table.scrollToItem(self.table.item(row, self.COL_RIGHT))
            self.edit_row(row)

    def mark_selected_current(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        for row in rows:
            self.win.mark_translation_current(self.row_entries[row])
            self._show_row(row)
        self.update_counts()

    def closeEvent(self, ev):
        if self.table.state() == QAbstractItemView.EditingState:
            self.table.setCurrentItem(None)     # commit what is being typed
        super().closeEvent(ev)


class ActionsDialog(QDialog):
    """The action menu side by side in each of the transcript's languages: one action per
    line, in the same order, so picking "nikker" also fills in "nods"."""

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle(tr("dlg.actions_title"))
        v = QVBoxLayout(self)
        v.addWidget(QLabel(tr("dlg.actions_label")))
        row = QHBoxLayout()
        self.edits = {}
        for lang in win.languages:
            col = QVBoxLayout()
            title = QLabel(TRANSLATION_LANGS.get(lang, lang))
            f = title.font()
            f.setBold(True)
            title.setFont(f)
            edit = QPlainTextEdit("\n".join(win.actions_for(lang)))
            edit.setMinimumSize(200, 260)
            col.addWidget(title)
            col.addWidget(edit)
            row.addLayout(col)
            self.edits[lang] = edit
        v.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)

    def values(self):
        """{language: actions}, keeping empty lines in between so the languages stay lined up."""
        out = {}
        for lang, edit in self.edits.items():
            names = [line.strip().replace("|", "/") for line in edit.toPlainText().splitlines()]
            while names and not names[-1]:
                names.pop()
            out[lang] = names
        return out


class Section(QWidget):
    """A titled section; click the title to fold the content away (remembered)."""

    def __init__(self, content, settings, key):
        super().__init__()
        self.content, self.settings, self.key = content, settings, key
        self.header = QToolButton(checkable=True, autoRaise=True, focusPolicy=Qt.NoFocus,
                                  toolButtonStyle=Qt.ToolButtonTextBesideIcon)
        self.header.setStyleSheet("QToolButton{font-weight:bold;border:none;padding:1px 2px;}")
        self.header.setArrowType(Qt.DownArrow)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        lay.addWidget(self.header)
        lay.addWidget(content)
        self.header.toggled.connect(self.set_collapsed)
        self.header.setChecked(settings.value(key, False, type=bool))

    def set_collapsed(self, collapsed):
        self.header.setChecked(collapsed)
        self.content.setVisible(not collapsed)
        self.header.setArrowType(Qt.RightArrow if collapsed else Qt.DownArrow)
        self.settings.setValue(self.key, bool(collapsed))

    def is_collapsed(self):
        return self.header.isChecked()


class TrackView(QWidget):
    """Two tracks per person (says / does) over the interval. A line with an end
    time is a bar from start to end; one without is a short marker at its start."""
    LABEL_W = 110
    TRACK_H = 18
    GAP = 3
    PAD = 4
    MARKER_W = 6

    def __init__(self, win):
        super().__init__(focusPolicy=Qt.NoFocus)
        self.win = win
        self.show_text = False
        self.folded = set()      # persons whose two tracks are shown as one
        self.setMinimumHeight(self.height_for(1))

    # --- geometry
    def height_for(self, n_tracks):
        return 2 * self.PAD + n_tracks * (self.TRACK_H + self.GAP) - (self.GAP if n_tracks else 0)

    def refresh_size(self):
        self.setFixedHeight(self.height_for(len(self.tracks())))

    def tracks(self):
        """[(person, kind)]; kind is None for a folded person's single track."""
        out = []
        for p in self.win.persons:
            out += [(p, None)] if p in self.folded else [(p, "says"), (p, "does")]
        return out

    def toggle_fold(self, person):
        self.folded ^= {person}
        self.win._fit_tracks()

    def track_at(self, y):
        i = int((y - self.PAD) // (self.TRACK_H + self.GAP))
        tracks = self.tracks()
        return i if 0 <= i < len(tracks) and y >= self.PAD else None

    def view_range(self):
        w = self.win
        if w.duration > 0:
            return w.iv_start, max(w.iv_end, w.iv_start + 1)
        last = max((max(e.time_ms, e.end_ms or 0) for e in w.entries if e.time_ms is not None), default=0)
        return 0, last + 2000

    def x_of(self, ms):
        a, b = self.view_range()
        width = max(1, self.width() - self.LABEL_W - self.PAD)
        return self.LABEL_W + (ms - a) / (b - a) * width

    def ms_of(self, x):
        a, b = self.view_range()
        width = max(1, self.width() - self.LABEL_W - self.PAD)
        return round(a + clamp((x - self.LABEL_W) / width, 0.0, 1.0) * (b - a))

    def track_rect(self, i):
        y = self.PAD + i * (self.TRACK_H + self.GAP)
        return QRectF(self.LABEL_W, y, self.width() - self.LABEL_W - self.PAD, self.TRACK_H)

    def bars(self):
        """[(entry, bar rect, text room rect)] in drawing order."""
        index = {}
        for i, (p, kind) in enumerate(self.tracks()):
            for k in ((kind,) if kind else ("says", "does")):
                index[(id(p), k)] = (i, kind is None)
        per_track = {}
        for e in self.win.entries:
            if e.time_ms is not None and e.person is not None and (id(e.person), e.kind) in index:
                per_track.setdefault((*index[(id(e.person), e.kind)], e.kind), []).append(e)
        out = []
        for (i, folded, kind), entries in per_track.items():
            r = self.track_rect(i)
            if folded:   # says in the upper half, does in the lower half
                half = r.height() / 2
                r = QRectF(r.left(), r.top() + (0 if kind == "says" else half), r.width(), half)
            entries.sort(key=lambda e: e.time_ms)
            for j, e in enumerate(entries):
                x1 = self.x_of(e.time_ms)
                if e.end_ms is not None:
                    x2 = max(self.x_of(e.end_ms), x1 + 2)
                    bar = QRectF(x1, r.top(), x2 - x1, r.height())
                    room = bar
                else:
                    bar = QRectF(x1 - self.MARKER_W / 2, r.top(), self.MARKER_W, r.height())
                    nxt = self.x_of(entries[j + 1].time_ms) if j + 1 < len(entries) else r.right()
                    room = QRectF(bar.right() + 2, r.top(), max(0, nxt - bar.right() - 4), r.height())
                out.append((e, bar, room))
        return out

    def entry_at(self, pos):
        for e, bar, _room in reversed(self.bars()):
            if bar.adjusted(-2, 0, 2, 0).contains(pos):
                return e
        return None

    # --- mouse
    def mousePressEvent(self, ev):
        if ev.button() != Qt.LeftButton:
            return
        if ev.position().x() < self.LABEL_W:
            i = self.track_at(ev.position().y())
            if i is not None:
                self.toggle_fold(self.tracks()[i][0])
            return
        e = self.entry_at(ev.position())
        if e is not None:
            self.win.select_entry(e)
            self.win.seek(e.time_ms)
        elif ev.position().x() >= self.LABEL_W:
            self.win.seek(self.ms_of(ev.position().x()))

    def event(self, ev):
        if ev.type() == QEvent.ToolTip:
            e = self.entry_at(QPointF(ev.pos()))
            if e is not None:
                fmt = self.win.fmt
                times = fmt(e.time_ms) + (f" – {fmt(e.end_ms)}" if e.end_ms is not None else "")
                label = tr(label_key(e), name=display_name(e.person))
                QToolTip.showText(ev.globalPos(), f"{times}\n{label} {shown_text(e, self.win.lang, self.win.orig)}",
                                  self)
                return True
        return super().event(ev)

    # --- painting
    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = self.palette()
        p.fillRect(self.rect(), pal.color(QPalette.Base))
        fm = p.fontMetrics()
        for i, (person, kind) in enumerate(self.tracks()):
            r = self.track_rect(i)
            p.fillRect(r, pal.color(QPalette.AlternateBase if kind == "says" else QPalette.Window))
            if kind is None:
                label = "▸ " + tr("label.track_both", name=display_name(person))
            else:
                label = ("▾ " if kind == "says" else "    ") + tr(f"label.track_{kind}", name=display_name(person))
            p.setPen(QColor(person.color))
            p.drawText(QRectF(4, r.top(), self.LABEL_W - 8, r.height()), Qt.AlignVCenter | Qt.AlignLeft,
                       fm.elidedText(label, Qt.ElideRight, self.LABEL_W - 8))
        area = QRectF(self.LABEL_W, 0, self.width() - self.LABEL_W, self.height())
        p.setClipRect(area)
        selected = self.win.selected_row.entry if self.win.selected_row else None
        text_color = pal.color(QPalette.Text)
        for e, bar, room in self.bars():
            color = QColor(e.person.color)
            fill = QColor(color)
            fill.setAlpha(230 if e.kind == "says" else 90)
            p.setPen(QPen(color, 1))
            p.setBrush(fill)
            p.drawRoundedRect(bar, 3, 3)
            if e is selected:
                p.setPen(QPen(pal.color(QPalette.WindowText), 2))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(bar.adjusted(-1, -1, 1, 1), 3, 3)
            text = shown_text(e, self.win.lang, self.win.orig)
            if self.show_text and text and room.width() > 12:
                inside = room is bar
                p.setPen(QColor("white") if inside and e.kind == "says" else text_color)
                text_rect = room.adjusted(3, 0, -3, 0) if inside else room
                p.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft,
                           fm.elidedText(text, Qt.ElideRight, int(text_rect.width())))
        p.setClipping(False)
        if self.win.duration > 0:
            x = self.x_of(self.win.player.position())
            if self.LABEL_W <= x <= self.width():
                p.setPen(QPen(QColor("#d62728"), 2))
                p.drawLine(QPointF(x, 0), QPointF(x, self.height()))


class PersonRow(QWidget):
    def __init__(self, win, person):
        super().__init__()
        self.person = person
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        self.swatch = QLabel()
        self.swatch.setFixedSize(14, 14)
        self.name_edit = QLineEdit(person.name)
        self.name_edit.setValidator(QRegularExpressionValidator(QRegularExpression(r"[^:*\[\]|]*")))
        self.name_edit.textEdited.connect(lambda t: win.rename_person(person, t))
        self.name_edit.returnPressed.connect(win.release_focus)
        # Where this person's text appears on the video (only shown while text on video is on).
        self.origin_combo = QComboBox(focusPolicy=Qt.NoFocus)
        self.origin_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        for o in ORIGINS:
            self.origin_combo.addItem("", o)
        self.origin_combo.addItem("", "point")          # last item: pick a point on the video
        self.origin_combo.setCurrentIndex(origin_index(person.origin))
        self.origin_combo.currentIndexChanged.connect(self._origin_chosen)
        self.origin_combo.activated.connect(          # choosing the point item again re-picks
            lambda i: win.start_pick(person) if i == POINT_INDEX and win.video.pick_person is None else None)
        self.origin_combo.setVisible(win.video.show_text)
        self.win = win
        self.says_btn = QPushButton()
        self.says_btn.clicked.connect(lambda: win.annotate(person, "says"))
        self.does_btn = QPushButton()
        self.does_btn.clicked.connect(lambda: win.show_does_menu(person, self.does_btn))
        self.remove_btn = QToolButton(text="✕", autoRaise=True)
        self.remove_btn.clicked.connect(lambda: win.remove_person(person))

        lay.addWidget(self.swatch)
        lay.addWidget(self.name_edit, 1)
        lay.addWidget(self.origin_combo)
        lay.addWidget(self.says_btn)
        lay.addWidget(self.does_btn)
        lay.addWidget(self.remove_btn)

    def refresh(self, index):
        color = self.person.color
        self.swatch.setStyleSheet(f"background:{color};border-radius:3px;")
        btn_css = (f"QPushButton{{border:2px solid {color};border-radius:4px;padding:3px 10px;}}"
                   f"QPushButton:hover{{background:{color};color:white;}}")
        n = index + 1
        self.says_btn.setText(f"{tr('btn.says')} ({n})" if n <= 9 else tr("btn.says"))
        self.does_btn.setText(f"{tr('btn.does')} (Shift+{n})" if n <= 9 else tr("btn.does"))
        self.says_btn.setStyleSheet(btn_css)
        self.does_btn.setStyleSheet(btn_css)
        self.name_edit.setPlaceholderText(tr("ph.name"))
        self.remove_btn.setToolTip(tr("tip.remove_person"))
        self.refresh_origin()

    def refresh_origin(self):
        combo = self.origin_combo
        for i, o in enumerate(ORIGINS):
            combo.setItemText(i, f"{ORIGIN_ARROWS[o]} {tr(f'label.origin_{o}')}")
        o = self.person.origin
        if isinstance(o, tuple):
            combo.setItemText(POINT_INDEX, f"⌖ {round(o[0] * 100)} %, {round(o[1] * 100)} %")
        else:
            combo.setItemText(POINT_INDEX, f"⌖ {tr('label.origin_point')}")
        combo.blockSignals(True)
        combo.setCurrentIndex(origin_index(o))
        combo.blockSignals(False)
        combo.setToolTip(tr("tip.origin"))

    def _origin_chosen(self, i):
        if i == POINT_INDEX:
            self.win.start_pick(self.person)
        elif i >= 0:
            self.win.set_origin(self.person, ORIGINS[i])


class EntryRow(QFrame):
    def __init__(self, win, entry):
        super().__init__(objectName="entryRow")
        self.win, self.entry = win, entry
        self.resume_on_enter = False
        self._outside = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 1, 2, 1)
        lay.setSpacing(6)

        self.stamp = self.label = self.end_stamp = self.badge = self.vol_btn = self.end_box = None
        self.end_widgets = []
        if entry.kind != "raw":
            lay.setSpacing(0)
            self.stamp = QPushButton(cursor=QCursor(Qt.PointingHandCursor), focusPolicy=Qt.NoFocus)
            self.stamp.clicked.connect(lambda: win.jump_to(self.entry.time_ms))
            self.start_prev = self._nudge_button("‹", "start", -1)
            self.start_next = self._nudge_button("›", "start", 1)
            for w in (self.start_prev, self.stamp, self.start_next):
                lay.addWidget(w)

            # The end time sits in a slot of fixed width, so the text starts in the same
            # place whether or not the line has an end time.
            self.end_box = QWidget()
            eb = QHBoxLayout(self.end_box)
            eb.setContentsMargins(0, 0, 0, 0)
            eb.setSpacing(0)
            self.end_btn = QToolButton(text="⇥", autoRaise=True, focusPolicy=Qt.NoFocus)
            self.end_btn.clicked.connect(lambda: win.set_end(self))
            self.end_stamp = QPushButton(cursor=QCursor(Qt.PointingHandCursor), focusPolicy=Qt.NoFocus)
            self.end_stamp.setStyleSheet(STAMP_CSS)
            self.end_stamp.clicked.connect(lambda: win.show_end(self))
            dash = QLabel("–")
            self.end_widgets = [dash, self._nudge_button("‹", "end", -1), self.end_stamp,
                                self._nudge_button("›", "end", 1)]
            eb.addWidget(self.end_btn)
            for w in self.end_widgets:
                eb.addWidget(w)
            eb.addStretch(1)
            lay.addWidget(self.end_box)
            lay.addSpacing(6)
            self.label = QLabel()
            lay.addWidget(self.label)
            self.badge = QToolButton(autoRaise=True, focusPolicy=Qt.NoFocus)
            self.badge.setFixedWidth(46)
            self.badge.clicked.connect(lambda: win.show_translation(self.entry))
            self.badge.hide()
            lay.addWidget(self.badge)
            # Spoken lines: how loudly it is said. Actions get the same room plus an indent.
            self.vol_btn = QToolButton(autoRaise=True, focusPolicy=Qt.NoFocus,
                                       popupMode=QToolButton.InstantPopup)
            self.vol_btn.setFixedWidth(VOL_W)
            self.vol_menu = QMenu(self.vol_btn)
            self.vol_menu.aboutToShow.connect(self._fill_volume_menu)
            self.vol_btn.setMenu(self.vol_menu)
            lay.addWidget(self.vol_btn)
            self.indent = QWidget()
            lay.addWidget(self.indent)

        self.edit = QLineEdit(text_in(entry, win.lang, win.orig))
        if entry.kind == "raw":
            self.edit.setStyleSheet("color:#888;")
        self.edit.textEdited.connect(self._edited)
        self.edit.returnPressed.connect(lambda: win.finish_entry(self))
        lay.addWidget(self.edit, 1)

        self.del_btn = QToolButton(text="✕", autoRaise=True, focusPolicy=Qt.NoFocus)
        self.del_btn.clicked.connect(lambda: win.delete_entry(self))
        lay.addWidget(self.del_btn)

        for w in (self, self.stamp, self.label, self.end_stamp):
            if w is not None:
                w.setContextMenuPolicy(Qt.CustomContextMenu)
                w.customContextMenuRequested.connect(self._menu)
        self.refresh()

    def _nudge_button(self, text, which, n):
        b = QToolButton(text=text, autoRaise=True, focusPolicy=Qt.NoFocus)
        b.setStyleSheet("QToolButton{font-weight:bold;font-size:15px;padding:0 3px;color:#2a6fdb;}")
        b.setAutoRepeat(True)          # hold to keep stepping
        b.setAutoRepeatDelay(400)
        b.setAutoRepeatInterval(90)
        b.clicked.connect(lambda: self.win.nudge(self, which, n))
        b.setProperty("tip", "tip.nudge_prev" if n < 0 else "tip.nudge_next")
        return b

    def mousePressEvent(self, ev):
        self.win.select_row(self)
        super().mousePressEvent(ev)

    def set_selected(self, selected):
        self.setStyleSheet("QFrame#entryRow{background:rgba(42,111,219,45);border-radius:3px;}"
                           if selected else "")

    def refresh(self):
        self.del_btn.setToolTip(tr("tip.delete_line"))
        if self.entry.kind == "raw":
            self.edit.setPlaceholderText(tr("ph.blank"))
            return
        p, says = self.entry.person, self.entry.kind == "says"
        name = display_name(p)
        # Whole seconds on screen; the exact time (milliseconds) in the tooltip.
        fmt = self.win.fmt
        self.stamp.setText(fmt(self.entry.time_ms, millis=False))
        has_end = self.entry.end_ms is not None
        self.end_btn.setVisible(not has_end)
        for w in self.end_widgets:
            w.setVisible(has_end)
        if has_end:
            self.end_stamp.setText(fmt(self.entry.end_ms, millis=False))
            self.end_stamp.setToolTip(f"{fmt(self.entry.end_ms)}\n{tr('tip.end_stamp')}")
        self.end_btn.setToolTip(tr("tip.add_end"))
        for b in self.findChildren(QToolButton):
            if b.property("tip"):
                b.setToolTip(tr(b.property("tip")))
        self.label.setText(tr(label_key(self.entry), name=name))
        self.label.setStyleSheet(f"color:{p.color};font-weight:bold;")
        self.refresh_text()
        self.apply_widths()
        self.refresh_volume()
        font = self.edit.font()
        font.setItalic(not says)
        self.edit.setFont(font)
        self._outside = None
        self.update_interval_state()
        self.refresh_badge()

    def apply_widths(self):
        """Line up with the other rows: the same end-time slot and label column everywhere;
        actions indented a little further than speech."""
        if self.end_box is None:
            return
        stamp_w = self.stamp.sizeHint().width()
        self.end_box.setFixedWidth(sum(w.sizeHint().width() for w in self.end_widgets
                                       if w is not self.end_stamp) + stamp_w)
        says_w, does_w = self.win.label_widths()
        says = self.entry.kind == "says"
        # Speech: label + volume button; actions: a wider label, so their text starts
        # ACTION_INDENT further right than speech.
        self.label.setFixedWidth(says_w if says else does_w)
        self.label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)    # the name sits by its text
        self.label.setContentsMargins(0, 0, 6, 0)
        self.vol_btn.setVisible(says)
        self.indent.hide()

    def refresh_volume(self):
        if self.vol_btn is None or self.entry.kind != "says":
            return
        v = volume_of(self.entry)
        symbol = VOLUME_SYMBOL[v]
        self.vol_btn.setText(symbol or "·")
        color = "#9a9a9a" if v == "normal" else ("#d62728" if v == "loud" else "#2a6fdb")
        self.vol_btn.setStyleSheet(f"QToolButton{{color:{color};font-weight:bold;font-size:13px;"
                                   f"padding:0;}}QToolButton::menu-indicator{{image:none;}}")
        self.vol_btn.setToolTip(tr("tip.volume", volume=tr(f"label.volume_{v}")))

    def _fill_volume_menu(self):
        """The volume options, plus marking just some words."""
        m = self.vol_menu
        m.clear()
        current = volume_of(self.entry)
        for v in VOLUMES:
            act = m.addAction(tr(f"menu.volume_{v}"))
            act.setCheckable(True)
            act.setChecked(v == current)
            act.triggered.connect(lambda _=False, v=v: self.win.set_volume(self.entry, v))
        m.addSeparator()
        quiet = m.addAction(tr("menu.quiet_words"))
        quiet.setEnabled(self.edit.hasSelectedText())
        quiet.triggered.connect(lambda: self.wrap_selection("°", "°"))
        m.addAction(tr("menu.sound")).triggered.connect(lambda: self.wrap_selection("((", "))"))

    def wrap_selection(self, left, right):
        """Put CA marks around the selected words (or at the cursor, ready to type)."""
        e = self.edit
        if e.hasSelectedText():
            start, text = e.selectionStart(), e.selectedText()
            e.setText(e.text()[:start] + left + text + right + e.text()[start + len(text):])
            e.setCursorPosition(start + len(left) + len(text) + len(right))
        else:
            pos = e.cursorPosition()
            e.setText(e.text()[:pos] + left + right + e.text()[pos:])
            e.setCursorPosition(pos + len(left))
        self.win.set_text(self.entry, e.text())
        e.setFocus()

    def refresh_text(self):
        """Show the line in the language shown; an empty translation shows the original
        greyed out as a hint."""
        if self.entry.kind == "raw":
            return
        w, e = self.win, self.entry
        wanted = text_in(e, w.lang, w.orig)
        if self.edit.text() != wanted:
            self.edit.setText(wanted)
        if w.lang != w.orig and e.text.strip():
            self.edit.setPlaceholderText(e.text)
        else:
            self.edit.setPlaceholderText(tr("ph.says" if e.kind == "says" else "ph.does",
                                            name=display_name(e.person)))

    def refresh_badge(self):
        """A small mark for the translation: ✓ translated, = same, ! out of date, – missing."""
        if self.badge is None:
            return
        lang = self.win.pair_lang
        status = translation_status(self.entry, lang, self.win.orig)
        if status == "none":
            self.badge.hide()
            return
        symbol, color = TranslationWindow.STATUS[status]
        self.badge.setText(f"{lang.upper()} {symbol}")
        self.badge.setStyleSheet(f"QToolButton{{color:{color};font-weight:bold;font-size:10px;padding:0 2px;}}")
        text = text_in(self.entry, lang, self.win.orig)
        self.badge.setToolTip(f"{tr(f'tip.tr_{status}')}\n{text}".strip())
        self.badge.setVisible(self.win.tr_marks_on)     # View > Show translation status

    def update_interval_state(self):
        """Grey out the timestamp when it lies outside the interval."""
        if self.stamp is None:
            return
        outside = not self.win.in_interval(self.entry.time_ms)
        if outside != self._outside:
            self._outside = outside
            self.stamp.setStyleSheet(STAMP_CSS_OUTSIDE if outside else STAMP_CSS)
            self.stamp.setToolTip(
                f"{self.win.fmt(self.entry.time_ms)}\n{tr('tip.stamp_outside' if outside else 'tip.stamp')}")

    def _edited(self, text):
        self.win.set_text(self.entry, text)    # in the language shown

    def _menu(self, _pos):
        menu = QMenu(self)
        if self.entry.kind != "raw":
            menu.addAction(tr("ctx.retime")).triggered.connect(lambda: self.win.retime_entry(self))
            menu.addAction(tr("ctx.set_end")).triggered.connect(lambda: self.win.set_end(self))
            same = menu.addAction(tr("ctx.same"))
            same.setCheckable(True)
            same.setChecked(translation_status(self.entry, self.win.pair_lang, self.win.orig) == "same")
            same.triggered.connect(lambda on: self.win.set_same(self.entry, on))
            if self.entry.end_ms is not None:
                menu.addAction(tr("ctx.clear_end")).triggered.connect(lambda: self.win.clear_end(self))
            sub = menu.addMenu(tr("ctx.change_person"))
            for p in self.win.persons:
                sub.addAction(display_name(p)).triggered.connect(
                    lambda _=False, p=p: self.win.change_person(self, p))
            other = "does" if self.entry.kind == "says" else "says"
            menu.addAction(tr(f"ctx.to_{other}")).triggered.connect(
                lambda: self.win.set_kind(self, other))
            menu.addSeparator()
        menu.addAction(tr("tip.delete_line")).triggered.connect(lambda: self.win.delete_entry(self))
        menu.exec(QCursor.pos())


# ----------------------------------------------------------------- main window

class Annotator(QMainWindow):
    def __init__(self, settings=None):
        super().__init__()
        global LANG
        self.settings = settings or QSettings("VideoAnnotator", "VideoAnnotator")
        LANG = self.settings.value("language") or ("da" if QLocale.system().name().startswith("da") else "en")
        self.qt_translator = QTranslator(self)
        self._i18n = []      # (setter, key) pairs re-applied on language change

        self.persons: list[Person] = []
        self.person_rows: list[PersonRow] = []
        self.entries: list[Entry] = []
        self.rows: list[EntryRow] = []
        self.transcript_path: str | None = None
        self.dirty = False
        self.frame_ms = 40.0
        self.duration = 0
        self.iv_start = self.iv_end = 0
        self.video_key = None
        self._media_ready = False
        self.selected_row = None
        self.video_path = None
        self.export_job = None
        self.hours = False    # show the hour part of times (only for material of an hour or more)
        # Languages of the open transcript (the first is the one it is written in), the one
        # shown and typed in now (the Language menu), and the other side of the translation window.
        self.lang = LANG
        self.languages = [LANG, "en" if LANG != "en" else "da"]
        self.other_lang = self.languages[1]
        self.actions = {}            # language -> the action menu (kept in the transcript file)
        self.extra_meta = []         # metadata lines from a newer version, kept as they are
        self.tr_window = None
        self.tr_marks_on = self.settings.value("tr_marks", False, type=bool)   # View > translation status
        self._label_widths = None    # cached widths of the label column (see label_widths)

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.video = VideoView(self)
        self.video.show_text = self.settings.value("video_text", False, type=bool)
        self.player.setVideoSink(self.video.sink)

        self.save_timer = QTimer(self, singleShot=True, interval=800)
        self.save_timer.timeout.connect(self.save)

        self._build_ui()
        self._build_menus()

        self.player.positionChanged.connect(self.on_position)
        self.player.durationChanged.connect(self.on_duration)
        self.player.mediaStatusChanged.connect(self.on_media_status)
        self.player.playbackStateChanged.connect(self.on_state)
        self.player.metaDataChanged.connect(self.on_metadata)
        self.player.errorOccurred.connect(
            lambda _e, msg: self.statusBar().showMessage(tr("status.video_error", msg=msg)))

        QApplication.instance().installEventFilter(self)
        self.new_transcript(confirm=False)
        self.set_language(LANG)
        self.statusBar().showMessage(tr("status.start"))
        self.release_focus()   # so shortcuts work right away instead of typing into a field

    @property
    def orig(self):
        """The language the transcript is written in."""
        return self.languages[0]

    @property
    def pair_lang(self):
        """Of the two languages in the translation window, the one that is a translation."""
        return self.lang if self.lang != self.orig else self.other_lang

    def actions_for(self, lang):
        """The action menu in a language (from the transcript, else the built-in one)."""
        return self.actions.get(lang) or DEFAULT_ACTIONS.get(lang) or []

    @property
    def action_list(self):
        return [a for a in self.actions_for(self.lang) if a]

    def _tr(self, setter, key):
        """Set a translated text now and again whenever the language changes."""
        self._i18n.append((setter, key))
        setter(tr(key))

    # ------------------------------------------------------------ UI

    def _build_ui(self):
        splitter = QSplitter(Qt.Horizontal)
        splitter.setFocusPolicy(Qt.ClickFocus)
        self.setCentralWidget(splitter)

        # --- left: video, timeline, controls, interval
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.addWidget(self.video, 1)

        timeline_row = QHBoxLayout()
        self.timeline = JumpSlider(Qt.Horizontal, focusPolicy=Qt.NoFocus)
        self.timeline.sliderMoved.connect(self.seek)
        self._tr(self.timeline.setToolTip, "tip.timeline")
        self.time_label = QLabel(fmt_ms(0, hours=False))
        self.time_label.setStyleSheet("font-family:Consolas,monospace;font-weight:bold;")
        timeline_row.addWidget(self.timeline, 1)
        timeline_row.addWidget(self.time_label)
        lv.addLayout(timeline_row)

        self.controls = ControlsBar()
        ctl = self.controls.lay

        def button(text, tip_key, slot):
            b = QPushButton(text, focusPolicy=Qt.NoFocus)
            self._tr(b.setToolTip, tip_key)
            b.clicked.connect(slot)
            ctl.addWidget(b)
            return b

        back5 = button("−5s", "tip.back5", lambda: self.seek_rel(-5000))
        back1 = button("−1s", "tip.back1", lambda: self.seek_rel(-1000))
        button("◀|", "tip.prev_frame", lambda: self.frame_step(-1))
        self.play_btn = button("", "tip.play", self.toggle_play)
        self.play_btn.setMinimumWidth(90)
        button("|▶", "tip.next_frame", lambda: self.frame_step(1))
        fwd1 = button("+1s", "tip.fwd1", lambda: self.seek_rel(1000))
        fwd5 = button("+5s", "tip.fwd5", lambda: self.seek_rel(5000))
        self.controls.drop_order = [[back5, fwd5], [back1, fwd1]]   # first to go when narrow
        ctl.addStretch(1)
        speed_label = QLabel()
        self._tr(speed_label.setText, "label.speed")
        ctl.addWidget(speed_label)
        self.speed_combo = QComboBox(focusPolicy=Qt.NoFocus)
        self.speed_combo.addItems(SPEEDS)
        self.speed_combo.setCurrentText("1x")
        self.speed_combo.currentTextChanged.connect(lambda t: self.player.setPlaybackRate(float(t[:-1])))
        ctl.addWidget(self.speed_combo)
        self.mute_btn = VolumeButton()
        self.mute_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaVolume))
        self._tr(self.mute_btn.setToolTip, "tip.mute")
        self.mute_btn.toggled.connect(self.set_muted)
        ctl.addWidget(self.mute_btn)
        self.volume_slider = self.mute_btn.slider    # lives in the drop-down shown on hover
        self.volume_slider.valueChanged.connect(lambda v: self.audio.setVolume(v / 100))
        self.volume_slider.setValue(80)
        lv.addWidget(self.controls)

        self.tracks = TrackView(self)
        self._tr(self.tracks.setToolTip, "tip.tracks")
        self.tracks.show_text = self.settings.value("track_text", False, type=bool)
        self.tracks_scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.StyledPanel)
        self.tracks_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.tracks_scroll.setWidget(self.tracks)
        self.tracks_section = Section(self.tracks_scroll, self.settings, "collapsed/tracks")
        self._tr(self.tracks_section.header.setText, "group.tracks")
        self._tr(self.tracks_section.header.setToolTip, "tip.collapse")
        lv.addWidget(self.tracks_section)

        box = QWidget()
        bv = QVBoxLayout(box)
        bv.setContentsMargins(0, 0, 0, 0)
        self.bar = IntervalBar()
        self._tr(self.bar.setToolTip, "tip.interval_bar")
        self.bar.intervalEdited.connect(lambda s, e, which: self.set_interval(s, e, which, preview=True))
        self.bar.seekRequested.connect(self.seek)
        bv.addWidget(self.bar)
        first, second = [], []
        self.start_edit = QTimeEdit(displayFormat="mm:ss.zzz", keyboardTracking=False)
        self.end_edit = QTimeEdit(displayFormat="mm:ss.zzz", keyboardTracking=False)
        self.start_edit.timeChanged.connect(lambda t: self.set_interval(qtime_to_ms(t), self.iv_end, "start"))
        self.end_edit.timeChanged.connect(lambda t: self.set_interval(self.iv_start, qtime_to_ms(t), "end"))
        for edit in (self.start_edit, self.end_edit):
            edit.lineEdit().returnPressed.connect(self.release_focus)
        first += [self.start_edit, QLabel("–"), self.end_edit]
        for text_key, tip_key, slot in [
            ("btn.set_start", "tip.set_start", lambda: self.set_interval_point("start")),
            ("btn.set_end", "tip.set_end", lambda: self.set_interval_point("end")),
            ("btn.whole_video", "tip.whole_video", lambda: self.set_interval(0, self.duration)),
        ]:
            b = QPushButton(focusPolicy=Qt.NoFocus)
            self._tr(b.setText, text_key)
            self._tr(b.setToolTip, tip_key)
            b.clicked.connect(slot)
            second.append(b)
        self.loop_cb = QCheckBox(focusPolicy=Qt.NoFocus)
        self._tr(self.loop_cb.setText, "cb.loop")
        second.append(self.loop_cb)
        self.interval_row = WrapRow(first, second)   # buttons wrap to a 2nd line when narrow
        bv.addWidget(self.interval_row)
        self.interval_section = Section(box, self.settings, "collapsed/interval")
        self._tr(self.interval_section.header.setText, "group.interval")
        self._tr(self.interval_section.header.setToolTip, "tip.collapse")
        lv.addWidget(self.interval_section)
        splitter.addWidget(left)

        # --- right: people + transcript
        right = QWidget()
        rv = QVBoxLayout(right)
        self.file_label = QLabel()
        self.file_label.setStyleSheet("font-weight:bold;font-size:14px;")
        rv.addWidget(self.file_label)

        people_box = QGroupBox()
        self._tr(people_box.setTitle, "group.people")
        pv = QVBoxLayout(people_box)
        people_inner = QWidget()
        self.people_layout = QVBoxLayout(people_inner)
        self.people_layout.setContentsMargins(0, 0, 0, 0)
        self.people_layout.addStretch(1)
        self.people_scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.NoFrame)
        self.people_scroll.setWidget(people_inner)
        pv.addWidget(self.people_scroll)
        prow = QHBoxLayout()
        add_btn = QPushButton(focusPolicy=Qt.NoFocus)
        self._tr(add_btn.setText, "btn.add_person")
        add_btn.clicked.connect(lambda: self.add_person())
        edit_actions_btn = QPushButton(focusPolicy=Qt.NoFocus)
        self._tr(edit_actions_btn.setText, "btn.edit_actions")
        edit_actions_btn.clicked.connect(self.edit_actions)
        prow.addWidget(add_btn)
        prow.addStretch(1)
        prow.addWidget(edit_actions_btn)
        pv.addLayout(prow)
        rv.addWidget(people_box)

        tx_inner = QWidget()
        self.tx_layout = QVBoxLayout(tx_inner)
        self.tx_layout.setContentsMargins(4, 4, 4, 4)
        self.tx_layout.setSpacing(1)
        self.tx_layout.addStretch(1)
        self.tx_scroll = QScrollArea(widgetResizable=True)
        self.tx_scroll.setWidget(tx_inner)
        self.now_markers = NowMarkers(self, tx_inner)
        self._auto_scrolling = False
        self._user_scrolled_at = -1e9
        self.tx_scroll.verticalScrollBar().valueChanged.connect(self._transcript_scrolled)
        rv.addWidget(self.tx_scroll, 1)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([780, 560])
        splitter.setChildrenCollapsible(False)
        self.splitter, self.right_panel = splitter, right

    def _build_menus(self):
        def menu(key):
            m = self.menuBar().addMenu("")
            self._tr(m.setTitle, key)
            return m

        def action(m, key, shortcut, slot):
            act = QAction(self, shortcut=QKeySequence(shortcut))
            self._tr(act.setText, key)
            act.triggered.connect(lambda _=False: slot())
            m.addAction(act)

        file_menu = menu("menu.file")
        action(file_menu, "menu.open_video", "Ctrl+O", self.open_video)
        action(file_menu, "menu.open_transcript", "Ctrl+T", self.open_transcript)
        action(file_menu, "menu.new_transcript", "Ctrl+N", self.new_transcript)
        action(file_menu, "menu.save", "Ctrl+S", self.save)
        action(file_menu, "menu.save_as", "Ctrl+Shift+S", self.save_as)
        action(file_menu, "menu.export", "Ctrl+E", self.export_dialog)
        file_menu.addSeparator()
        action(file_menu, "menu.quit", "Ctrl+Q", self.close)

        view_menu = menu("menu.view")
        self.track_text_action = QAction(self, checkable=True, checked=self.tracks.show_text)
        self._tr(self.track_text_action.setText, "menu.track_text")
        self.track_text_action.toggled.connect(self.set_track_text)
        view_menu.addAction(self.track_text_action)
        self.video_text_action = QAction(self, checkable=True, checked=self.video.show_text)
        self._tr(self.video_text_action.setText, "menu.video_text")
        self.video_text_action.toggled.connect(self.set_video_text)
        view_menu.addAction(self.video_text_action)
        view_menu.addSeparator()
        self.tr_marks_action = QAction(self, checkable=True, checked=self.tr_marks_on)
        self._tr(self.tr_marks_action.setText, "menu.tr_marks")
        self.tr_marks_action.toggled.connect(self.set_tr_marks)
        view_menu.addAction(self.tr_marks_action)
        self.translation_action = QAction(self, shortcut=QKeySequence("Ctrl+L"))
        self._tr(self.translation_action.setText, "menu.translation")
        self.translation_action.triggered.connect(lambda _=False: self.show_translation())
        view_menu.addAction(self.translation_action)
        view_menu.addSeparator()
        self.right_action = QAction(self, checkable=True, shortcut=QKeySequence("Ctrl+B"))
        self._tr(self.right_action.setText, "menu.right_panel")
        self.right_action.toggled.connect(self.set_right_panel)
        view_menu.addAction(self.right_action)
        self.right_action.setChecked(not self.settings.value("right_hidden", False, type=bool))
        self.set_right_panel(self.right_action.isChecked())

        self.lang_menu = menu("menu.language")
        self.rebuild_language_menu()

        action(menu("menu.help"), "menu.shortcuts", "F1", self.show_help)

    def rebuild_language_menu(self):
        """The transcript's languages (pick one to see and type that version), then 'Add'."""
        m = self.lang_menu
        m.clear()
        group = QActionGroup(m)
        self.lang_actions = {}
        for code in self.languages:
            act = QAction(TRANSLATION_LANGS.get(code, code), m, checkable=True, checked=code == self.lang)
            act.triggered.connect(lambda _=False, c=code: self.set_language(c))
            group.addAction(act)
            m.addAction(act)
            self.lang_actions[code] = act
        m.addSeparator()
        add = m.addMenu(tr("menu.add_language"))
        for code, name in TRANSLATION_LANGS.items():
            if code not in self.languages:
                add.addAction(name).triggered.connect(lambda _=False, c=code: self.set_language(c))
        add.setEnabled(bool(add.actions()))

    def set_language(self, lang):
        """The one language switch: show and type the transcript in this language (adding it
        to the transcript when new), and - for Danish or English - the menus too.
        A transcript without lines yet becomes written in the chosen language."""
        global LANG
        if lang not in TRANSLATION_LANGS:
            lang = "en"
        if not any(e.kind != "raw" for e in self.entries) and self.languages[0] != lang:
            if lang in self.languages:
                self.languages.remove(lang)
            self.languages.insert(0, lang)
        elif lang not in self.languages:
            self.add_language(lang)
        self.lang = lang
        if self.other_lang == lang or self.other_lang not in self.languages:
            self.other_lang = next((c for c in self.languages if c != lang), "en" if lang != "en" else "da")
        if lang in LANGUAGES:
            LANG = lang
            self.settings.setValue("language", LANG)
        app = QApplication.instance()
        app.removeTranslator(self.qt_translator)   # Qt's own dialog buttons (Yes/No/Save...)
        if LANG == "da" and self.qt_translator.load(
                "qtbase_da", QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
            app.installTranslator(self.qt_translator)
        for setter, key in self._i18n:
            setter(tr(key))
        self.rebuild_language_menu()
        self.on_state(self.player.playbackState())
        for idx, row in enumerate(self.person_rows):
            row.refresh(idx)
        self.update_tr_marks()
        self._label_widths = None            # the labels are in the new language
        for row in self.rows:
            row.refresh()
        self.set_dirty(self.dirty)
        self.tracks.update()
        self.video.update()
        if self.tr_window is not None:
            self.tr_window.retranslate()
            self.tr_window.refresh()

    def add_language(self, code):
        if code in TRANSLATION_LANGS and code not in self.languages:
            self.languages.append(code)
            self.rebuild_language_menu()
            self.changed()

    def set_track_text(self, on):
        self.tracks.show_text = bool(on)
        self.settings.setValue("track_text", bool(on))
        self.tracks.update()

    def set_right_panel(self, shown):
        """Show or hide the right side (people and transcript)."""
        shown = bool(shown)
        self.right_action.setChecked(shown)
        self.right_panel.setVisible(shown)
        self.settings.setValue("right_hidden", not shown)

    def set_video_text(self, on):
        self.video.show_text = bool(on)
        self.settings.setValue("video_text", bool(on))
        for row in self.person_rows:          # the origin selectors only matter with text on
            row.origin_combo.setVisible(bool(on))
        self.video.update()

    def show_help(self):
        QMessageBox.information(self, tr("help.title"), tr("help.text"))

    # ------------------------------------------------------------ keyboard

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.FocusIn and isinstance(obj, QLineEdit) and isinstance(obj.parent(), EntryRow) \
                and obj.window() is self:
            self.select_row(obj.parent())   # typing in a line selects it
        if ev.type() != QEvent.KeyPress or not isinstance(obj, QWidget) or obj.window() is not self:
            return super().eventFilter(obj, ev)
        key, mods = ev.key(), ev.modifiers()
        if key == Qt.Key_Escape and self.video.pick_person is not None:
            self.cancel_pick()
            return True
        if isinstance(QApplication.focusWidget(), TEXT_INPUTS) or isinstance(obj, TEXT_INPUTS):
            if key == Qt.Key_Escape:
                self.release_focus()
                return True
            return False
        if mods & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
            return False

        digit = self._digit(ev)
        if digit:
            if digit <= len(self.persons):
                p = self.persons[digit - 1]
                if mods & Qt.ShiftModifier:
                    self.show_does_menu(p, self.person_rows[digit - 1].does_btn)
                else:
                    self.annotate(p, "says")
            return True
        handlers = {
            Qt.Key_Space: self.toggle_play,
            Qt.Key_Left: lambda: self.seek_rel(-1000 if mods & Qt.ShiftModifier else -5000),
            Qt.Key_Right: lambda: self.seek_rel(1000 if mods & Qt.ShiftModifier else 5000),
            Qt.Key_Comma: lambda: self.frame_step(-1),
            Qt.Key_Less: lambda: self.frame_step(-1),
            Qt.Key_Period: lambda: self.frame_step(1),
            Qt.Key_Greater: lambda: self.frame_step(1),
            Qt.Key_Home: lambda: self.seek(self.iv_start),
            Qt.Key_End: lambda: self.seek(self.iv_end),
            Qt.Key_M: self.mute_btn.toggle,
            Qt.Key_E: lambda: self.set_end(),
            Qt.Key_BracketLeft: lambda: self.set_interval_point("start"),
            Qt.Key_BracketRight: lambda: self.set_interval_point("end"),
        }
        if key in handlers:
            handlers[key]()
            return True
        return False

    @staticmethod
    def _digit(ev):
        nv = ev.nativeVirtualKey()
        if sys.platform == "win32" and 0x31 <= nv <= 0x39:   # top-row digit regardless of Shift
            return nv - 0x30
        if Qt.Key_1 <= ev.key() <= Qt.Key_9:
            return ev.key() - Qt.Key_0
        shifted = "!@#$%^&*("
        if ev.text() and ev.text() in shifted:
            return shifted.index(ev.text()) + 1
        return 0

    def release_focus(self):
        self.centralWidget().setFocus()

    # ------------------------------------------------------------ playback

    def open_video(self, path=None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(
                self, tr("dlg.open_video"), "",
                f"{tr('filter.videos')} (*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.wmv *.mpg *.mpeg);;"
                f"{tr('filter.all')} (*)")
        if not path:
            return
        resolved = str(Path(path).resolve())
        self.video_path = resolved
        self._media_ready = False
        self.duration = 0
        self.set_interval(0, 0)
        self.video_key = hashlib.sha1(resolved.lower().encode()).hexdigest()[:16]
        self.player.setSource(QUrl.fromLocalFile(resolved))
        self.player.pause()   # show the first frame
        self.statusBar().showMessage(tr("status.video", name=Path(path).name))

    def _interval_key(self):
        return f"intervals/{self.video_key}"

    def seek(self, ms):
        """Move the playhead, never leaving the interval."""
        if self.duration > 0:
            self.player.setPosition(int(clamp(ms, self.iv_start, self.iv_end)))

    def play(self):
        if self._media_ready:
            self.player.play()

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
            return
        if self.duration > 0 and self.player.position() >= self.iv_end - 30:
            self.seek(self.iv_start)   # at the end: start over
        self.play()

    def seek_rel(self, delta):
        self.seek(self.player.position() + delta)

    def frame_step(self, n):
        self.player.pause()
        self.seek(round(self.player.position() + n * self.frame_ms))

    def set_muted(self, muted):
        self.audio.setMuted(muted)
        self.mute_btn.setIcon(self.style().standardIcon(
            QStyle.SP_MediaVolumeMuted if muted else QStyle.SP_MediaVolume))

    def in_interval(self, ms):
        return self.duration <= 0 or self.iv_start <= ms <= self.iv_end

    def set_interval(self, start, end, moved="end", preview=False):
        """Set the interval (clamped to the video). With preview, show the frame
        at the handle being moved."""
        d = self.duration
        if d <= 0:
            start = end = 0
        else:
            start, end = clamp(int(start), 0, d), clamp(int(end), 0, d)
            min_len = min(MIN_INTERVAL_MS, d)
            if end - start < min_len:
                if moved == "start":
                    start = end - min_len
                else:
                    end = start + min_len
                if start < 0:
                    start, end = 0, min_len
                if end > d:
                    start, end = d - min_len, d
        self.iv_start, self.iv_end = start, end

        for edit, ms in ((self.start_edit, start), (self.end_edit, end)):
            edit.blockSignals(True)
            edit.setTime(ms_to_qtime(ms))
            edit.blockSignals(False)
        self.timeline.setRange(start, end)
        self.bar.set_interval(d, start, end)
        self.tracks.update()
        for row in self.rows:
            row.update_interval_state()
        if d > 0 and self.video_key:
            self.settings.setValue(self._interval_key(), [start, end])

        if preview:
            self.player.pause()
            self.seek(start if moved == "start" else end)
        elif d > 0 and not start <= self.player.position() <= end:
            self.seek(start)

    def set_interval_point(self, which):
        pos = self.player.position()
        if which == "start":
            self.set_interval(pos, self.iv_end, "start")
        else:
            self.set_interval(self.iv_start, pos, "end")

    def on_position(self, pos):
        if self.duration > 0 and not self.iv_start - 250 <= pos <= self.iv_end + 250:
            # Outside the interval (e.g. the backend briefly reports 0 on the first
            # play after loading): snap back without showing it.
            self.seek(pos)
            return
        if not self.timeline.isSliderDown():
            self.timeline.setValue(pos)
        self.time_label.setText(self.fmt(pos))
        self.bar.set_pos(pos)
        self.tracks.update()
        if self.video.show_text:
            self.video.update()
        self.update_now_markers(pos)
        if self.duration > 0 and pos >= self.iv_end and self.player.playbackState() == QMediaPlayer.PlayingState:
            if self.loop_cb.isChecked():
                self.player.setPosition(self.iv_start)
            else:
                self.player.pause()
                self.player.setPosition(self.iv_end)

    def on_media_status(self, status):
        # Seeking or playing before the first frame is buffered makes the FFmpeg
        # backend drop the seek or stall, so the video only counts as ready here.
        if status == QMediaPlayer.BufferedMedia and not self._media_ready:
            self._media_ready = True
            self.on_duration(self.player.duration())

    def on_duration(self, dur):
        if not self._media_ready:
            return   # applied by on_media_status once the video is ready
        self.duration = dur
        self.update_time_format()
        for edit in (self.start_edit, self.end_edit):
            edit.blockSignals(True)
            edit.setMaximumTime(ms_to_qtime(dur))
            edit.blockSignals(False)
        start, end = 0, dur
        saved = self.settings.value(self._interval_key()) if dur > 0 and self.video_key else None
        if saved:
            try:
                start, end = (int(v) for v in (saved.split(",") if isinstance(saved, str) else saved))
            except (TypeError, ValueError):
                start, end = 0, dur
        self.set_interval(start, end)

    def on_state(self, state):
        playing = state == QMediaPlayer.PlayingState
        self.play_btn.setText(tr("btn.pause" if playing else "btn.play"))
        self.play_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPause if playing else QStyle.SP_MediaPlay))
        self.update_now_markers()

    FOLLOW_PAUSE_S = 3.0     # after you scroll the transcript yourself, don't follow for this long

    def _transcript_scrolled(self, _value):
        if not self._auto_scrolling:
            self._user_scrolled_at = time.monotonic()

    def update_now_markers(self, pos=None):
        """While playing: mark ±1 s around the playhead in the transcript and keep it in view
        (not while you are typing, or just after you scrolled the list yourself)."""
        if self.player.playbackState() != QMediaPlayer.PlayingState:
            self.now_markers.set_pos(None)
            return
        span = self.now_markers.set_pos(self.player.position() if pos is None else pos)
        typing = isinstance(QApplication.focusWidget(), TEXT_INPUTS)
        recently_scrolled = time.monotonic() - self._user_scrolled_at < self.FOLLOW_PAUSE_S
        if span and not typing and not recently_scrolled:
            top, bottom = span[0], span[1]
            self._auto_scrolling = True
            try:
                self.tx_scroll.ensureVisible(0, (top + bottom) // 2, 0, (bottom - top) // 2 + 24)
            finally:
                self._auto_scrolling = False

    def on_metadata(self):
        fps = self.player.metaData().value(QMediaMetaData.Key.VideoFrameRate)
        try:
            if fps and float(fps) > 0:
                self.frame_ms = 1000.0 / float(fps)
        except (TypeError, ValueError):
            pass

    def jump_to(self, ms):
        if not self.in_interval(ms):
            self.statusBar().showMessage(tr("status.outside"), 4000)
            return
        self.seek(ms)
        self.play()

    # ------------------------------------------------------------ people

    def add_person(self, name=None, color=None):
        if name is None:
            used = {p.name for p in self.persons}
            n = len(self.persons) + 1
            while f"Person {n}" in used:
                n += 1
            name = f"Person {n}"
        if color is None:
            taken = {p.color for p in self.persons}
            color = next((c for c in PALETTE if c not in taken), PALETTE[len(self.persons) % len(PALETTE)])
        person = Person(name, color)
        self._add_person_row(person)
        return person

    def _saved_origins(self):
        try:
            data = json.loads(self.settings.value("origins", "{}") or "{}")
            out = {}
            for k, v in data.items():
                o = valid_origin(v)
                if o is not None:
                    out[str(k)] = o
            return out
        except (TypeError, ValueError, AttributeError):
            return {}

    def set_origin(self, person, origin):
        """Choose where a person's text appears on the video: a preset 1-9 or a point (x, y)
        on the picture (fractions of its size). Saved in the transcript."""
        origin = valid_origin(origin)
        if origin is None:
            return
        person.origin = origin
        if person in self.persons:
            self.person_rows[self.persons.index(person)].refresh_origin()
        self.changed()

    def start_pick(self, person):
        """Let the next click on the video set where this person's text goes (Esc cancels)."""
        if not self.video.show_text:
            self.set_video_text(True)
        self.video.start_pick(person)
        self.statusBar().showMessage(tr("status.pick", name=display_name(person)))

    def cancel_pick(self):
        person = self.video.pick_person
        self.video.stop_pick()
        self.statusBar().clearMessage()
        if person in self.persons:
            self.person_rows[self.persons.index(person)].refresh_origin()   # back to what it was

    def _add_person_row(self, person, saved_origin=True):
        if saved_origin:     # older transcripts: positions chosen before they were kept in the file
            person.origin = self._saved_origins().get(person.name, person.origin)
        self.persons.append(person)
        row = PersonRow(self, person)
        self.person_rows.append(row)
        self.people_layout.insertWidget(self.people_layout.count() - 1, row)
        row.refresh(len(self.persons) - 1)
        self._fit_people()
        self.refresh_widths()

    def _fit_people(self):
        """Grow the people list with its rows, scrolling after ~5 people."""
        row_h = self.person_rows[0].sizeHint().height() + self.people_layout.spacing() if self.person_rows else 30
        self.people_scroll.setFixedHeight(min(len(self.person_rows), 5) * row_h + 4)
        self._fit_tracks()

    def _fit_tracks(self):
        """The track viewer grows with its tracks, scrolling after 8."""
        self.tracks.refresh_size()
        tracks_h = self.tracks.height_for(min(len(self.tracks.tracks()), 8))
        self.tracks_scroll.setFixedHeight(tracks_h + 2 * self.tracks_scroll.frameWidth())
        self.tracks.update()

    def rename_person(self, person, text):
        person.name = NAME_FORBIDDEN.sub("", text).strip()
        for row in self.rows:
            if row.entry.person is person:
                row.refresh()
        self.refresh_widths()
        self.changed()

    def remove_person(self, person):
        lines = [r for r in self.rows if r.entry.person is person]
        if lines:
            answer = QMessageBox.question(
                self, tr("dlg.remove_title"),
                tr("dlg.remove_text", name=display_name(person), n=len(lines)))
            if answer != QMessageBox.Yes:
                return
            for r in lines:
                if r is self.selected_row:
                    self.selected_row = None
                self._remove_row(r)
                r.deleteLater()
        i = self.persons.index(person)
        self.persons.pop(i)
        self.person_rows.pop(i).deleteLater()
        for idx, row in enumerate(self.person_rows):
            row.refresh(idx)
        self._fit_people()
        self.changed()

    def edit_actions(self):
        """Edit the action menu, side by side in each language (kept in the transcript)."""
        dlg = ActionsDialog(self)
        if dlg.exec() == QDialog.Accepted:
            self.actions = dlg.values()
            self.changed()

    def add_action_entry(self, time_ms, person, index):
        """An action picked from the menu, filled in in every language that has it."""
        def name(lang):
            names = self.actions_for(lang)
            return names[index].strip() if index < len(names) else ""
        original = name(self.orig) or name(self.lang)
        row = self.add_entry(time_ms, person, "does", original)
        for lang in self.languages[1:]:
            if name(lang):
                row.entry.translations[lang] = [name(lang), original, False]
        row.refresh()
        self.translations_changed(row.entry)
        return row

    # ------------------------------------------------------------ annotating

    def annotate(self, person, kind):
        was_playing = self.player.playbackState() == QMediaPlayer.PlayingState
        self.player.pause()
        self.set_right_panel(True)    # you need to see the line to type in it
        row = self.add_entry(self.player.position(), person, kind, "")
        row.resume_on_enter = was_playing
        row.edit.setFocus()

    def run_menu(self, menu, where):
        """Show a popup menu and return the chosen item (separate so tests can stand in)."""
        return menu.exec(where)

    def show_does_menu(self, person, anchor):
        was_playing = self.player.playbackState() == QMediaPlayer.PlayingState
        t = self.player.position()
        self.player.pause()
        menu = QMenu(self)
        for i, a in enumerate(self.actions_for(self.lang)):
            if a:
                menu.addAction(a).setData(i)
        menu.addSeparator()
        gaze_menu = menu.addMenu(tr("menu.gaze"))
        for other in self.persons:
            if other is not person and other.name:
                gaze_menu.addAction(f">{other.name}").setData(f">{other.name}")
        gaze_custom = gaze_menu.addAction(tr("menu.gaze_custom"))
        custom = menu.addAction(tr("menu.custom_action"))
        edit = menu.addAction(tr("btn.edit_actions"))
        where = anchor.mapToGlobal(QPoint(0, anchor.height())) if anchor.isVisible() else QCursor.pos()
        chosen = self.run_menu(menu, where)
        if chosen is None or chosen is edit:
            if chosen is edit:
                self.edit_actions()
            if was_playing:
                self.play()
            return
        if chosen is custom or chosen is gaze_custom:
            self.set_right_panel(True)
            row = self.add_entry(t, person, "does", ">" if chosen is gaze_custom else "")
            row.resume_on_enter = was_playing
            row.edit.setFocus()
            row.edit.end(False)
        elif isinstance(chosen.data(), str):          # gaze at a person: same in every language
            row = self.add_entry(t, person, "does", chosen.data())
            for lang in self.languages[1:]:
                self.set_same(row.entry, True, lang)
            row.refresh()
            if was_playing:
                self.play()
        else:
            self.add_action_entry(t, person, chosen.data())
            if was_playing:
                self.play()

    def finish_entry(self, row):
        self.release_focus()
        if row.resume_on_enter:
            row.resume_on_enter = False
            self.play()

    def add_entry(self, time_ms, person, kind, text):
        entry = Entry(int(time_ms), person, kind, text)
        row = EntryRow(self, entry)
        self._place(row)
        self.select_row(row)
        self.changed()
        QTimer.singleShot(0, lambda: self.tx_scroll.ensureWidgetVisible(row))
        return row

    def _place(self, row):
        """Insert row (and its entry) at its time-sorted position."""
        t = row.entry.time_ms
        idx = len(self.entries)
        if t is not None:
            for i, e in enumerate(self.entries):
                if e.time_ms is not None and e.time_ms > t:
                    idx = i
                    break
        self.entries.insert(idx, row.entry)
        self.rows.insert(idx, row)
        self.tx_layout.insertWidget(idx, row)

    def _remove_row(self, row):
        i = self.rows.index(row)
        self.rows.pop(i)
        self.entries.pop(i)
        self.tx_layout.removeWidget(row)
        return i

    def delete_entry(self, row):
        if row is self.selected_row:
            self.selected_row = None
        self._remove_row(row)
        row.deleteLater()
        self.changed()

    def _set_start(self, row, ms):
        """Move a line's start (keeping time order); its end never ends up before it."""
        self._remove_row(row)
        e = row.entry
        e.time_ms = int(ms)
        if e.end_ms is not None and e.end_ms < e.time_ms:
            e.end_ms = e.time_ms
        self._place(row)
        row.refresh()
        self.changed()

    def retime_entry(self, row):
        self._set_start(row, self.player.position())

    def nudge(self, row, which, n):
        """Move the start or end of a line by n frames and show that frame."""
        e = row.entry
        current = e.time_ms if which == "start" else e.end_ms
        if current is None:
            return
        ms = max(0, round(current + n * self.frame_ms))
        if self.duration > 0:
            ms = min(ms, self.duration)
        if which == "start":
            self._set_start(row, ms)
        else:
            e.end_ms = max(ms, e.time_ms)
            ms = e.end_ms
            row.refresh()
            self.changed()
        self.select_row(row)
        self.player.pause()
        self.seek(ms)

    def set_end(self, row=None):
        """End a line (the selected one by default) at the current video time."""
        row = row or self.selected_row
        if row is None or row.entry.kind == "raw":
            self.statusBar().showMessage(tr("status.select_first"), 4000)
            return
        pos = self.player.position()
        if pos < row.entry.time_ms:
            self.statusBar().showMessage(tr("status.end_before_start"), 4000)
            return
        row.entry.end_ms = int(pos)
        row.refresh()
        self.select_row(row)
        self.changed()

    def clear_end(self, row):
        row.entry.end_ms = None
        row.refresh()
        self.changed()

    def show_end(self, row):
        """Show the frame at a line's end time (paused)."""
        self.select_row(row)
        if not self.in_interval(row.entry.end_ms):
            self.statusBar().showMessage(tr("status.outside"), 4000)
            return
        self.player.pause()
        self.seek(row.entry.end_ms)

    def select_row(self, row):
        if row is self.selected_row:
            return
        if self.selected_row is not None:
            self.selected_row.set_selected(False)
        self.selected_row = row
        if row is not None:
            row.set_selected(True)
        self.tracks.update()

    def select_entry(self, entry):
        row = next((r for r in self.rows if r.entry is entry), None)
        if row is not None:
            self.select_row(row)
            self.tx_scroll.ensureWidgetVisible(row)

    def fmt(self, ms, millis=True):
        """A time as shown on screen: MM:SS, or HH:MM:SS when the video is an hour or longer."""
        return fmt_ms(ms, millis, self.hours)

    def update_time_format(self):
        """Decide whether times need the hour part and redisplay them if that changed."""
        if self.duration > 0:
            hours = self.duration >= 3_600_000
        else:   # no video: go by the transcript
            hours = any(max(e.time_ms or 0, e.end_ms or 0) >= 3_600_000 for e in self.entries)
        if hours == self.hours:
            return
        self.hours = hours
        fmt = "HH:mm:ss.zzz" if hours else "mm:ss.zzz"
        for edit in (self.start_edit, self.end_edit):
            edit.blockSignals(True)
            edit.setDisplayFormat(fmt)
            edit.blockSignals(False)
        self.time_label.setText(self.fmt(self.player.position()))
        for row in self.rows:
            row.refresh()

    def changed(self):
        """Something in the transcript changed: autosave and redraw tracks, video text and
        the translation window."""
        self.mark_dirty()
        self.refresh_widths()
        self.tracks.update()
        self.video.update()
        if self.tr_window is not None:
            self.tr_window.schedule_refresh()

    # ------------------------------------------------------------ translation

    def update_tr_marks(self):
        """(The translation marks are switched on and off in the View menu.)"""
        return False

    def set_tr_marks(self, on):
        """Show each line's translation status (✓ = – !) - which lines still need translating."""
        self.tr_marks_on = bool(on)
        self.settings.setValue("tr_marks", self.tr_marks_on)
        self.tr_marks_action.setChecked(self.tr_marks_on)
        for row in self.rows:
            row.refresh_badge()

    def label_widths(self):
        """(label width on speech lines, label width on action lines). Only the labels that
        are actually on the lines count, so the column is no wider than needed; speech text
        starts in one place, action text ACTION_INDENT further right."""
        if self._label_widths is None:
            font = QFont(self.font())
            font.setBold(True)
            fm = QFontMetrics(font)
            says, does = [], []
            for e in self.entries:
                if e.kind in ("says", "does") and e.person is not None:
                    w = fm.horizontalAdvance(tr(label_key(e), name=display_name(e.person)))
                    (says if e.kind == "says" else does).append(w)
            gap = 8
            speech_x = max(max(says, default=24) + gap + VOL_W, max(does, default=0) + gap - ACTION_INDENT)
            self._label_widths = (speech_x - VOL_W, speech_x + ACTION_INDENT)
        return self._label_widths

    def refresh_widths(self):
        """Re-fit the label column (names, kinds or language changed)."""
        old, self._label_widths = self._label_widths, None
        if self.label_widths() != old:
            for row in self.rows:
                row.apply_widths()

    def set_volume(self, entry, volume):
        """How loudly a line is said: normal, quiet (°...°), very_quiet (°°...°°) or loud."""
        if volume not in VOLUMES:
            return
        if volume == "normal":
            entry.attrs.pop("volume", None)
        else:
            entry.attrs["volume"] = volume
        for row in self.rows:
            if row.entry is entry:
                row.refresh_volume()
        self.changed()

    def set_text(self, entry, text, lang=None):
        """Type a line's text in a language (default: the one shown). In the language the
        transcript is written in this changes the original - its translations then show as
        out of date; in any other language it is that language's version."""
        lang = lang or self.lang
        if lang == self.orig:
            entry.text = text
        elif text.strip():
            entry.translations[lang] = [text, entry.text, False]
        else:
            entry.translations.pop(lang, None)
        self.translations_changed(entry)

    def set_translation(self, entry, text, lang=None):
        self.set_text(entry, text, lang or self.pair_lang)

    def set_same(self, entry, same=True, lang=None):
        """The line is the same in both languages (haha, names, ...): the translation
        simply follows the original."""
        lang = lang or self.pair_lang
        if lang == self.orig:
            return
        if same:
            entry.translations[lang] = ["", entry.text, True]
        elif entry.translations.get(lang, ("", "", False))[2]:
            entry.translations.pop(lang, None)
        self.translations_changed(entry)

    def mark_translation_current(self, entry, lang=None):
        """The original changed but the translation still fits: no longer out of date."""
        t = entry.translations.get(lang or self.pair_lang)
        if t and not t[2]:
            t[1] = entry.text
            self.translations_changed(entry)

    def translations_changed(self, entry=None):
        self.changed()
        everything = self.update_tr_marks()
        for row in self.rows:
            if everything or entry is None or row.entry is entry:
                row.refresh_text()
                row.refresh_badge()

    def set_other_lang(self, code):
        """The other side of the translation window."""
        if code not in TRANSLATION_LANGS or code == self.lang or code == self.other_lang:
            return
        self.add_language(code)
        self.other_lang = code
        self.update_tr_marks()
        for row in self.rows:
            row.refresh_badge()
        if self.tr_window is not None:
            self.tr_window.retranslate()
            self.tr_window.refresh()

    def show_translation(self, entry=None):
        """Open the translation window (optionally at one line)."""
        if self.tr_window is None:
            self.tr_window = TranslationWindow(self)
            self.update_tr_marks()
            for row in self.rows:
                row.refresh_badge()
        self.tr_window.show()
        self.tr_window.raise_()
        self.tr_window.activateWindow()
        if entry is not None:
            self.tr_window.focus_entry(entry)

    def active_entries(self, pos=None):
        """Lines happening at pos (default: now), in time order. A line without an
        end time counts as happening for TEXT_HOLD_MS after its start."""
        return active_at(self.entries, self.player.position() if pos is None else pos)

    def change_person(self, row, person):
        row.entry.person = person
        row.refresh()
        self.changed()

    def set_kind(self, row, kind):
        row.entry.kind = kind
        row.refresh()
        self.changed()

    # ------------------------------------------------------------ files

    def _clear(self):
        for row in self.rows:
            row.deleteLater()
        for row in self.person_rows:
            row.deleteLater()
        self.rows, self.entries, self.person_rows, self.persons = [], [], [], []
        self.selected_row = None

    def load_model(self, persons, entries, saved_origins=True):
        self._clear()
        for p in persons:
            self._add_person_row(p, saved_origins)
        for e in entries:
            row = EntryRow(self, e)
            self.entries.append(e)
            self.rows.append(row)
            self.tx_layout.insertWidget(self.tx_layout.count() - 1, row)
        while len(self.persons) < 2:
            self.add_person()
        self.update_time_format()
        self._label_widths = None
        self.refresh_widths()
        self.tracks.update()

    def new_transcript(self, confirm=True):
        """An empty transcript, written in the language shown now (switch language first
        to start in another one)."""
        if confirm and not self.confirm_discard():
            return
        self.languages = [self.lang, "en" if self.lang != "en" else "da"]
        self.actions, self.extra_meta = {}, []
        self.load_model([], [])
        self.transcript_path = None
        self.set_language(self.lang)
        self.set_dirty(False)

    def open_transcript(self, path=None):
        if not self.confirm_discard():
            return
        if not path:
            path, _ = QFileDialog.getOpenFileName(
                self, tr("dlg.open_transcript"), "",
                f"{tr('filter.text')} (*.txt);;{tr('filter.all')} (*)")
        if not path:
            return
        doc = parse_document(Path(path).read_text(encoding="utf-8-sig", errors="replace"))
        self.languages, self.actions, self.extra_meta = doc.languages, doc.actions, doc.extra
        self.load_model(doc.persons, doc.entries, saved_origins=not doc.has_meta)
        self.transcript_path = path
        imported = import_legacy_translations(self.entries, path)
        for lang, _name, _n in imported:
            if lang not in self.languages:
                self.languages.append(lang)
        # Keep showing the language you use, if this transcript has it; else its own language.
        lang = self.lang
        if lang not in self.languages or (lang != self.orig and not any(
                text_in(e, lang, self.orig).strip() for e in self.entries)):
            lang = self.orig
        self.set_language(lang)
        self.set_dirty(False)
        self.statusBar().showMessage(tr("status.loaded", n=len(self.entries), p=len(self.persons)), 4000)
        if imported:     # save them into the transcript itself (the old files are left alone)
            self.changed()
            self.statusBar().showMessage(tr("status.imported", n=sum(n for _l, _f, n in imported),
                                            files=", ".join(f for _l, f, _n in imported)), 15000)

    def document(self):
        return Document(self.persons, self.entries, self.languages,
                        {lang: self.actions_for(lang) for lang in self.languages}, self.extra_meta)

    def save(self):
        """Everything goes into the one transcript file: metadata, lines and translations."""
        if not self.transcript_path:
            return self.save_as()
        self.save_timer.stop()
        try:
            write_atomically(self.transcript_path, serialize_document(self.document()))
        except OSError as err:
            QMessageBox.warning(self, tr("dlg.file_problem"),
                                tr("dlg.save_failed", name=Path(self.transcript_path).name, err=err))
            return False
        self.set_dirty(False)
        return True

    def save_as(self):
        path, _ = QFileDialog.getSaveFileName(
            self, tr("dlg.save_transcript"), tr("dlg.default_name"),
            f"{tr('filter.text')} (*.txt);;{tr('filter.all')} (*)")
        if not path:
            return False
        self.transcript_path = path
        return self.save()

    def export_formatted(self, path=None, start=None, end=None, below=None, fmt="simple", pauses=False):
        """Write the transcript without timestamps, in the language shown, to a separate file
        (only the lines that start between start and end, when given; with below, another
        language under each line). Without a path the export dialog opens.

        Never overwrites the open transcript or any file with timestamped lines,
        so a transcript you work in can't be replaced by an export by mistake.
        """
        if not path:
            return self.export_dialog("text")
        target = Path(path)
        if self.transcript_path and same_file(target, self.transcript_path):
            QMessageBox.warning(self, tr("dlg.export_refused"), tr("dlg.export_same"))
            return False
        if target.exists():
            existing = target.read_bytes().decode("utf-8", errors="replace")
            if STAMPED_LINE_RE.search(existing):
                QMessageBox.warning(self, tr("dlg.export_refused"),
                                    tr("dlg.export_protected", name=target.name))
                return False
        if fmt == "ca":
            media = Path(self.video_path).name if self.video_path else None
            text = export_ca(self.entries, start, end, self.lang, below, self.orig, media, pauses)
        else:
            text = export_transcript(self.entries, start, end, self.lang, below, self.orig)
        target.write_text(text, encoding="utf-8")
        self.statusBar().showMessage(tr("status.exported", name=target.name), 6000)
        return True

    def export_dialog(self, kind=None):
        """Ask what to export (video clip or text), from where to where, and to which file."""
        kind = kind or self.settings.value("export_kind", "video" if self.video_path else "text")
        dlg = ExportDialog(self, kind)
        if dlg.exec() != QDialog.Accepted:
            return False
        v = dlg.values()
        self.settings.setValue("export_kind", v["kind"])
        if v["kind"] == "text":
            self.settings.setValue("export_format", v["format"])
            self.settings.setValue("export_pauses", v["pauses"])
            below = self.other_lang if v["translation"] else None
            return self.export_formatted(v["path"], v["start"], v["end"], below, v["format"], v["pauses"])
        return self.export_video(v["path"], v["start"], v["end"], v["subtitles"])

    def video_size(self):
        size = self.video.frame.size() if self.video.frame.isValid() else None
        if not size or size.isEmpty():
            size = self.player.metaData().value(QMediaMetaData.Key.Resolution)
        if isinstance(size, QSize) and not size.isEmpty():
            return size.width(), size.height()
        return 1920, 1080

    def export_video(self, path, start, end, subtitles=True):
        """Cut [start, end] out of the open video into path (MP4), with the text (in the
        language shown) when subtitles is true.
        Runs ffmpeg in the background with a progress dialog; returns True when it started.
        ffmpeg writes to a .part file that replaces the target only when it succeeded."""
        if not self.video_path:
            QMessageBox.warning(self, tr("dlg.export_refused"), tr("dlg.export_no_video"))
            return False
        ff = ffmpeg_exe()
        if not ff:
            QMessageBox.warning(self, tr("dlg.export_refused"), tr("dlg.export_no_ffmpeg"))
            return False
        target = Path(path)
        protected = [self.video_path] + ([self.transcript_path] if self.transcript_path else [])
        if any(same_file(target, f) for f in protected):
            QMessageBox.warning(self, tr("dlg.export_refused"), tr("dlg.export_same"))
            return False
        if end <= start:
            QMessageBox.warning(self, tr("dlg.export_refused"), tr("dlg.export_range_bad"))
            return False
        tmp = Path(tempfile.mkdtemp(prefix="annotator_export_"))
        sub = None
        if subtitles:   # the text drawn exactly as on screen, laid over the picture
            sub = build_overlay(self.entries, start, end, *self.video_size(), tmp,
                                self.lang, self.orig)
        part = target.with_name(target.stem + ".part" + (target.suffix or ".mp4"))
        proc = QProcess(self)
        proc.setProgram(ff)
        proc.setArguments(video_export_args(self.video_path, part, start, end, sub))
        proc.setWorkingDirectory(str(tmp))
        progress = QProgressDialog(tr("dlg.export_progress", name=target.name), tr("btn.cancel"),
                                   0, 1000, self)
        progress.setWindowTitle(tr("dlg.export_title"))
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        job = {"proc": proc, "target": target, "part": part, "tmp": tmp, "progress": progress,
               "cancelled": False, "log": "", "ok": None}
        self.export_job = job

        def read_progress():
            text = bytes(proc.readAllStandardOutput()).decode(errors="replace")
            for m in re.finditer(r"out_time_(?:us|ms)=(\d+)", text):
                done_ms = int(m.group(1)) / 1000
                progress.setValue(int(clamp(done_ms / max(1, end - start), 0, 1) * 1000))

        def read_log():
            job["log"] = (job["log"] + bytes(proc.readAllStandardError()).decode(errors="replace"))[-4000:]

        def cancel():
            job["cancelled"] = True
            proc.kill()

        def finished(code, _status):
            job["cancelled"] = job["cancelled"] or progress.wasCanceled()
            progress.reset()
            progress.hide()
            ok = code == 0 and not job["cancelled"] and part.exists()
            if ok:
                os.replace(part, target)
                self.statusBar().showMessage(tr("status.exported", name=target.name), 8000)
            else:
                try:
                    part.unlink(missing_ok=True)
                except OSError:
                    pass
                if not job["cancelled"]:
                    err = "\n".join(job["log"].strip().splitlines()[-6:])
                    QMessageBox.warning(self, tr("dlg.export_refused"), tr("dlg.export_failed", err=err))
            shutil.rmtree(tmp, ignore_errors=True)
            job["ok"] = ok

        proc.readyReadStandardOutput.connect(read_progress)
        proc.readyReadStandardError.connect(read_log)
        progress.canceled.connect(cancel)
        proc.finished.connect(finished)
        proc.start()
        return True

    def mark_dirty(self):
        self.set_dirty(True)
        if self.transcript_path:
            self.save_timer.start()   # autosave shortly after the last change

    def set_dirty(self, dirty):
        self.dirty = dirty
        name = Path(self.transcript_path).name if self.transcript_path else tr("label.untitled")
        mark = " *" if dirty else ""
        self.file_label.setText(f"{name}{mark}")
        self.setWindowTitle(f"{name}{mark} — {tr('label.app')}")

    def confirm_discard(self):
        if not self.dirty:
            return True
        if self.transcript_path:
            return self.save()
        answer = QMessageBox.question(
            self, tr("dlg.unsaved_title"), tr("dlg.unsaved_text"),
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if answer == QMessageBox.Save:
            return self.save_as()
        return answer == QMessageBox.Discard

    def closeEvent(self, ev):
        if self.tr_window is not None and self.tr_window.isVisible():
            self.tr_window.close()               # commits a translation being typed
        if self.confirm_discard():
            ev.accept()
        else:
            ev.ignore()


def main():
    app = QApplication(sys.argv)
    win = Annotator()
    win.resize(1400, 860)
    win.show()
    args = sys.argv[1:]
    if args:
        win.open_video(args[0])
    if len(args) > 1:
        win.open_transcript(args[1])
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
