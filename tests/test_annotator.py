"""Tests for annotator.py.

Run from the project folder:
    %USERPROFILE%\\.venvs\\annotator\\Scripts\\python.exe -m unittest discover -s tests -v

Playback tests use demo/sample.mp4 (30 s test clip) and are skipped if it is missing.
"""
import os
import re
import string
import sys
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEvent, QPointF, QSettings, Qt          # noqa: E402
from PySide6.QtGui import QMouseEvent                              # noqa: E402
from PySide6.QtMultimedia import QMediaPlayer                      # noqa: E402
from PySide6.QtTest import QTest                                   # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox            # noqa: E402

import annotator as A                                              # noqa: E402

app = QApplication.instance() or QApplication([])
SAMPLE_VIDEO = ROOT / "demo" / "sample.mp4"
PLAYING = QMediaPlayer.PlayingState


def body(path):
    """A saved transcript without its "#%" metadata lines."""
    return "".join(line for line in Path(path).read_text(encoding="utf-8").splitlines(True)
                   if not line.startswith("#%"))


def meta(path):
    return [line for line in Path(path).read_text(encoding="utf-8").splitlines() if line.startswith("#%")]


def wait_until(pred, timeout=5000):
    end = time.monotonic() + timeout / 1000
    while time.monotonic() < end:
        if pred():
            return True
        QTest.qWait(20)
    return pred()


class WindowTestCase(unittest.TestCase):
    """Each test gets a fresh window with its own throw-away settings file."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmp.name)
        self.settings_path = str(self.tmpdir / "settings.ini")
        self.windows = []
        self.w = self.make_window()

    def tearDown(self):
        for w in self.windows:
            w.player.stop()
            w.dirty = False
            w.close()
            w.deleteLater()
        QTest.qWait(10)
        self.tmp.cleanup()

    def make_window(self, lang="en"):
        settings = QSettings(self.settings_path, QSettings.IniFormat)
        if lang and not settings.contains("language"):
            settings.setValue("language", lang)
        w = A.Annotator(settings)
        w.audio.setMuted(True)
        w.resize(1300, 800)
        w.show()
        self.windows.append(w)
        return w

    def activate(self, w):
        """Keyboard focus only works in the active window."""
        w.activateWindow()
        w.raise_()
        self.assertTrue(QTest.qWaitForWindowActive(w))
        w.release_focus()

    def fake_video(self, duration=60_000):
        """Pretend a video is loaded and record seeks instead of performing them."""
        self.seeks = []
        self.w.player.setPosition = self.seeks.append
        self.w._media_ready = True
        self.w.on_duration(duration)
        self.seeks.clear()


# ----------------------------------------------------------------- file format

class FormatTests(unittest.TestCase):
    def test_parse_says_does_and_raw_lines(self):
        persons, entries = A.parse_transcript(
            "Notes header\n[00:00:01.500] Anna: Hello\n[00:00:04.000] Ben *nods*\n\n")
        self.assertEqual([p.name for p in persons], ["Anna", "Ben"])
        self.assertEqual([(e.kind, e.time_ms, e.text) for e in entries], [
            ("raw", None, "Notes header"), ("says", 1500, "Hello"), ("does", 4000, "nods"), ("raw", None, "")])

    def test_fractional_and_long_timestamps(self):
        _, entries = A.parse_transcript("[00:00:06.2] A: x\n[12:34:56] B: y")
        self.assertEqual(entries[0].time_ms, 6200)
        self.assertEqual(entries[1].time_ms, ((12 * 60 + 34) * 60 + 56) * 1000)

    def test_existing_user_style_round_trips(self):
        text = "[00:11:30.277] F: 2\n[00:11:33.106] E: der er forskellige\n"
        persons, entries = A.parse_transcript(text)
        self.assertEqual([p.name for p in persons], ["F", "E"])
        self.assertEqual(A.serialize_transcript(entries), text)

    def test_empty_texts_round_trip(self):
        p = A.Person("Anna", "#000")
        entries = [A.Entry(1000, p, "says", ""), A.Entry(2000, p, "does", "")]
        text = A.serialize_transcript(entries)
        self.assertEqual(text, "[00:00:01.000] Anna:\n[00:00:02.000] Anna **\n")
        _, back = A.parse_transcript(text)
        self.assertEqual([(e.kind, e.text) for e in back], [("says", ""), ("does", "")])

    def test_end_times_round_trip_and_plain_lines_are_unchanged(self):
        text = ("[00:00:01.000-00:00:02.480] E: hej\n"
                "[00:00:03.000] F: uden slut\n"
                "[00:00:04.000-00:00:09.040] F *nikker*\n")
        _, entries = A.parse_transcript(text)
        self.assertEqual([(e.time_ms, e.end_ms) for e in entries],
                         [(1000, 2480), (3000, None), (4000, 9040)])
        self.assertEqual(A.serialize_transcript(entries), text)

    def test_end_before_start_in_file_is_clamped(self):
        _, entries = A.parse_transcript("[00:00:05.000-00:00:01.000] E: x\n")
        self.assertEqual(entries[0].end_ms, 5000)

    def test_export_has_no_end_times_and_ranged_files_are_protected(self):
        _, entries = A.parse_transcript("[00:00:01.000-00:00:02.000] E: hej\n")
        self.assertEqual(A.export_transcript(entries), 'E: "hej"\n')
        self.assertTrue(A.STAMPED_LINE_RE.search("x\n[00:00:01.000-00:00:02.000] E: hej\n"))

    def test_export_without_timestamps(self):
        _, entries = A.parse_transcript(
            "Header\n"
            "[00:06:28.000] Begge *Afventer computer*\n"
            "[00:06:30.542] C: I just have a hard time trusting people\n"
            "[00:06:33.499] E *kigger på opgaven *\n"
            "[00:06:59.384] F:  Den ændre....\n"
            "[00:07:00.000] F: \"Allerede i citat\"\n"
            "[00:07:01.000] F *(allerede i parentes)*\n"
            "\n")
        self.assertEqual(A.export_transcript(entries),
                         "Header\n"
                         "Begge: (Afventer computer)\n"
                         "C: \"I just have a hard time trusting people\"\n"
                         "E: (kigger på opgaven)\n"
                         "F: \"Den ændre....\"\n"
                         "F: \"Allerede i citat\"\n"
                         "F: (allerede i parentes)\n"
                         "\n")

    def test_fmt_ms(self):
        self.assertEqual(A.fmt_ms(3_723_045), "01:02:03.045")
        self.assertEqual(A.fmt_ms(3_723_045, millis=False), "01:02:03")
        self.assertEqual(A.fmt_ms(-5), "00:00:00.000")


# ----------------------------------------------------------------- translations

class TranslationTests(unittest.TestCase):
    def test_every_string_has_both_languages_with_same_placeholders(self):
        fields = lambda s: {f for _, f, _, _ in string.Formatter().parse(s) if f}
        for key, (en, da) in A.T.items():
            with self.subTest(key=key):
                self.assertTrue(en.strip() and da.strip())
                self.assertEqual(fields(en), fields(da))

    def test_every_key_used_in_the_code_exists(self):
        src = (ROOT / "annotator.py").read_text(encoding="utf-8")
        used = set(re.findall(
            r'"((?:label|menu|btn|cb|tip|group|ph|entry|ctx|dlg|filter|status|help)\.[a-z_0-9]+)"', src))
        used |= {f"ctx.to_{k}" for k in ("says", "does")}   # built with an f-string
        self.assertTrue(used)
        self.assertEqual(used - set(A.T), set())

    def test_default_actions_exist_for_every_language(self):
        self.assertEqual(set(A.DEFAULT_ACTIONS), set(A.LANGUAGES))


# ----------------------------------------------------------------- UI without video

class LanguageTests(WindowTestCase):
    def test_switching_updates_every_visible_text(self):
        w = self.w
        anna = w.persons[0]
        w.add_entry(1000, anna, "says", "hej")
        does_row = w.add_entry(2000, anna, "does", "")
        self.assertEqual(w.person_rows[0].says_btn.text(), "Says (1)")
        self.assertEqual(w.menuBar().actions()[0].text(), "&File")

        w.set_language("da")
        self.assertEqual(w.person_rows[0].says_btn.text(), "Siger (1)")
        self.assertEqual(w.person_rows[0].does_btn.text(), "Gør (Shift+1)")
        self.assertEqual(w.menuBar().actions()[0].text(), "&Filer")
        self.assertEqual(w.play_btn.text(), "Afspil")
        self.assertFalse(w.play_btn.icon().isNull())
        self.assertEqual(does_row.label.text(), "Person 1 gør:")
        self.assertEqual(does_row.edit.placeholderText(), "Hvad gør Person 1?")
        self.assertIn("Unavngivet transskription", w.windowTitle())
        self.assertTrue(w.lang_actions["da"].isChecked())

        w.set_language("en")
        self.assertEqual(does_row.label.text(), "Person 1 does:")
        self.assertEqual(w.menuBar().actions()[0].text(), "&File")

    def test_language_is_remembered(self):
        self.w.set_language("da")
        w2 = self.make_window(lang=None)
        self.assertEqual(A.LANG, "da")
        self.assertEqual(w2.person_rows[0].says_btn.text(), "Siger (1)")

    def test_qt_dialog_buttons_are_translated(self):
        self.w.set_language("da")
        self.assertEqual(QApplication.translate("QPlatformTheme", "&Yes"), "&Ja")
        self.w.set_language("en")
        self.assertEqual(QApplication.translate("QPlatformTheme", "&Yes"), "&Yes")

    def test_action_menu_follows_the_language_and_is_kept_in_the_file(self):
        w = self.w
        self.assertEqual(w.action_list, A.DEFAULT_ACTIONS["en"])
        w.set_language("da")
        self.assertEqual(w.action_list, A.DEFAULT_ACTIONS["da"])

        def fake_exec(dlg):
            self.assertEqual(set(dlg.edits), {"da", "en"})            # side by side
            dlg.edits["da"].setPlainText("vinker\n\n peger ")
            dlg.edits["en"].setPlainText("waves\n\npoints")
            return A.QDialog.Accepted

        with mock.patch.object(A.ActionsDialog, "exec", fake_exec):
            w.edit_actions()
        self.assertEqual(w.action_list, ["vinker", "peger"])
        w.set_language("en")
        self.assertEqual(w.action_list, ["waves", "points"])
        path = self.tmpdir / "a.txt"
        w.transcript_path = str(path)
        self.assertTrue(w.save())
        self.assertIn("#% actions da: vinker |  | peger", meta(path))
        self.assertIn("#% actions en: waves |  | points", meta(path))
        w2 = self.make_window()
        w2.open_transcript(str(path))
        self.assertEqual(w2.actions_for("en"), ["waves", "", "points"])

    def test_picking_an_action_fills_in_every_language(self):
        w = self.w
        w.set_language("da")
        p = w.persons[0]
        row = w.add_action_entry(1000, p, A.DEFAULT_ACTIONS["da"].index("nikker"))
        self.assertEqual(row.entry.text, "nikker")
        self.assertEqual(A.text_in(row.entry, "en", "da"), "nods")
        self.assertEqual(A.translation_status(row.entry, "en", "da"), "done")


class TranscriptTests(WindowTestCase):
    def test_new_transcript_has_two_people(self):
        self.assertEqual([p.name for p in self.w.persons], ["Person 1", "Person 2"])
        self.assertEqual(self.w.add_person().name, "Person 3")

    def test_entries_are_kept_in_time_order(self):
        w = self.w
        p = w.persons[0]
        for t in (5000, 1000, 3000):
            w.add_entry(t, p, "says", str(t))
        self.assertEqual([e.time_ms for e in w.entries], [1000, 3000, 5000])
        self.assertEqual([w.tx_layout.indexOf(r) for r in w.rows], [0, 1, 2])

    def test_rename_updates_every_line_and_strips_forbidden_characters(self):
        w = self.w
        p1, p2 = w.persons
        rows = [w.add_entry(1000, p1, "says", "a"), w.add_entry(2000, p2, "says", "b"),
                w.add_entry(3000, p1, "does", "c")]
        w.rename_person(p1, "An:na*")
        self.assertEqual([r.label.text() for r in rows], ["Anna:", "Person 2:", "Anna does:"])

    def test_remove_person_removes_their_lines(self):
        w = self.w
        p1, p2 = w.persons
        w.add_entry(1000, p1, "says", "a")
        w.add_entry(2000, p2, "says", "b")
        with mock.patch.object(A.QMessageBox, "question", return_value=QMessageBox.Yes):
            w.remove_person(p1)
        self.assertEqual([p.name for p in w.persons], ["Person 2"])
        self.assertEqual([e.text for e in w.entries], ["b"])
        self.assertEqual(w.person_rows[0].says_btn.text(), "Says (1)")

    def test_open_edit_save_reopen(self):
        path = self.tmpdir / "t.txt"
        path.write_text("Header\n[00:00:01.000] E: hi\n[00:00:04.000] F *nods*\n", encoding="utf-8")
        w = self.w
        w.open_transcript(str(path))
        e, f = w.persons
        w.player.position = lambda: 2500
        w.annotate(f, "says")
        QTest.keyClicks(w.rows[2].edit, "hej")
        w.rename_person(e, "Emma")
        self.assertTrue(w.save())
        self.assertEqual(body(path),
                         "Header\n[00:00:01.000] Emma: hi\n[00:00:02.500] F: hej\n[00:00:04.000] F *nods*\n")
        self.assertEqual(meta(path)[:2], ["#% transcript: 1", "#% languages: da, en"])   # old file: Danish
        w.open_transcript(str(path))
        self.assertEqual([p.name for p in w.persons], ["Emma", "F"])

    def test_autosave_after_changes(self):
        path = self.tmpdir / "auto.txt"
        path.write_text("", encoding="utf-8")
        w = self.w
        w.open_transcript(str(path))
        w.add_entry(1000, w.persons[0], "says", "saved by itself")
        self.assertTrue(wait_until(lambda: "saved by itself" in path.read_text(encoding="utf-8"), 3000))
        self.assertFalse(w.dirty)


class ExportTests(WindowTestCase):
    SOURCE = "[00:00:01.000] E: hej\n[00:00:02.000] F *nikker*\n"

    def open_source(self):
        path = self.tmpdir / "work.txt"
        path.write_text(self.SOURCE, encoding="utf-8")
        self.w.open_transcript(str(path))
        return path

    def test_exports_to_a_separate_file_and_leaves_transcript_alone(self):
        work = self.open_source()
        before = work.read_bytes()
        out = self.tmpdir / "work_formatted.txt"
        self.assertTrue(self.w.export_formatted(str(out)))
        self.assertEqual(out.read_text(encoding="utf-8"), 'E: "hej"\nF: (nikker)\n')
        self.assertEqual(work.read_bytes(), before)
        self.assertFalse(self.w.dirty)

    def test_suggested_name_is_next_to_the_transcript(self):
        work = self.open_source()
        dlg = A.ExportDialog(self.w, "text")
        path = Path(dlg.values()["path"])
        self.assertEqual(path.parent, work.parent)
        # an old file is Danish, so the language switch (and the menus) are Danish
        self.assertEqual(path.name, "work_formateret_00.00-00.02.txt")
        self.w.set_language("en")
        self.assertEqual(Path(A.ExportDialog(self.w, "text").values()["path"]).name,
                         "work_formatted_00.00-00.02.txt")

    def test_export_without_a_path_opens_the_dialog(self):
        self.open_source()
        with mock.patch.object(A.ExportDialog, "exec", return_value=A.QDialog.Rejected) as ex:
            self.assertFalse(self.w.export_formatted())
        ex.assert_called_once()

    def test_refuses_to_overwrite_the_open_transcript(self):
        work = self.open_source()
        before = work.read_bytes()
        with mock.patch.object(A.QMessageBox, "warning") as warn:
            self.assertFalse(self.w.export_formatted(str(work)))
        warn.assert_called_once()
        self.assertEqual(work.read_bytes(), before)

    def test_refuses_to_overwrite_any_timestamped_file(self):
        self.open_source()
        other = self.tmpdir / "other_transcript.txt"
        other.write_bytes(b"Notes\r\n[00:01:00.000] X: keep me\r\n")
        before = other.read_bytes()
        with mock.patch.object(A.QMessageBox, "warning") as warn:
            self.assertFalse(self.w.export_formatted(str(other)))
        warn.assert_called_once()
        self.assertEqual(other.read_bytes(), before)

    def test_replaces_an_earlier_export(self):
        self.open_source()
        out = self.tmpdir / "out.txt"
        out.write_text('E: "old"\n', encoding="utf-8")
        self.assertTrue(self.w.export_formatted(str(out)))
        self.assertEqual(out.read_text(encoding="utf-8"), 'E: "hej"\nF: (nikker)\n')

    @unittest.skipUnless((ROOT / "EogF1.txt").exists(), "user's transcript not present")
    def test_users_transcript_is_untouched_and_every_line_exported(self):
        src = ROOT / "EogF1.txt"
        before = src.read_bytes()
        self.w.open_transcript(str(src))     # read only: nothing is changed, so nothing is saved
        out = self.tmpdir / "EogF1_export.txt"
        self.assertTrue(self.w.export_formatted(str(out)))
        self.w.dirty = False
        self.w.transcript_path = None
        self.assertEqual(src.read_bytes(), before)
        src_lines = before.decode("utf-8").splitlines()
        out_lines = out.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(out_lines), len(src_lines))
        for s, o in zip(src_lines, out_lines):
            m = A.LINE_RE.match(s)
            self.assertIsNotNone(m, s)
            if m["colon"]:
                self.assertEqual(o, f'{m["name"].strip()}: "{m["said"].strip()}"')
            else:
                self.assertEqual(o, f'{m["name"].strip()}: ({m["did"].strip()})')
            self.assertNotRegex(o, r"\d{2}:\d{2}:\d{2}")


class EndTimeAndNudgeTests(WindowTestCase):
    def setUp(self):
        super().setUp()
        self.fake_video()          # 60 s, 25 fps -> 40 ms frames
        self.w.frame_ms = 40.0
        self.pos = 0
        self.w.player.position = lambda: self.pos

    def test_nudge_buttons_move_start_one_frame_and_show_it(self):
        w = self.w
        row = w.add_entry(10_000, w.persons[0], "says", "x")
        row.start_next.click()
        self.assertEqual(row.entry.time_ms, 10_040)
        self.assertEqual(row.stamp.text(), "00:10")                 # whole seconds shown
        self.assertTrue(row.stamp.toolTip().startswith("00:10.040"))  # exact time on hover
        self.assertEqual(self.seeks[-1], 10_040)
        row.start_prev.click()
        row.start_prev.click()
        self.assertEqual(row.entry.time_ms, 9_960)
        self.assertIs(w.selected_row, row)

    def test_nudging_start_keeps_time_order(self):
        w = self.w
        a = w.add_entry(1000, w.persons[0], "says", "a")
        b = w.add_entry(1040, w.persons[1], "says", "b")
        a.start_next.click()
        a.start_next.click()
        self.assertEqual(w.rows, [b, a])
        self.assertEqual([w.tx_layout.indexOf(r) for r in w.rows], [0, 1])

    def test_nudge_never_goes_below_zero_or_past_the_video(self):
        w = self.w
        row = w.add_entry(20, w.persons[0], "says", "x")
        w.nudge(row, "start", -1)
        self.assertEqual(row.entry.time_ms, 0)
        row2 = w.add_entry(59_990, w.persons[0], "says", "y")
        w.nudge(row2, "start", 1)
        self.assertEqual(row2.entry.time_ms, 60_000)

    def test_end_controls_appear_once_an_end_is_set(self):
        w = self.w
        row = w.add_entry(1000, w.persons[0], "says", "x")
        self.assertFalse(row.end_btn.isHidden())
        self.assertTrue(all(wd.isHidden() for wd in row.end_widgets))
        self.pos = 2500
        row.end_btn.click()
        self.assertEqual(row.entry.end_ms, 2500)
        self.assertTrue(row.end_btn.isHidden())
        self.assertFalse(any(wd.isHidden() for wd in row.end_widgets))
        self.assertEqual(row.end_stamp.text(), "00:02")
        self.assertTrue(row.end_stamp.toolTip().startswith("00:02.500"))
        w.clear_end(row)
        self.assertIsNone(row.entry.end_ms)
        self.assertFalse(row.end_btn.isHidden())

    def test_end_nudges_and_never_before_start(self):
        w = self.w
        row = w.add_entry(1000, w.persons[0], "says", "x")
        self.pos = 1040
        w.set_end(row)
        prev_end, next_end = row.end_widgets[1], row.end_widgets[3]
        next_end.click()
        self.assertEqual(row.entry.end_ms, 1080)
        self.assertEqual(self.seeks[-1], 1080)
        for _ in range(5):
            prev_end.click()
        self.assertEqual(row.entry.end_ms, 1000)

    def test_moving_start_past_end_pushes_end_along(self):
        w = self.w
        row = w.add_entry(1000, w.persons[0], "says", "x")
        self.pos = 1040
        w.set_end(row)
        w.nudge(row, "start", 3)
        self.assertEqual((row.entry.time_ms, row.entry.end_ms), (1120, 1120))

    def test_e_key_ends_the_selected_line(self):
        w = self.w
        self.activate(w)
        QTest.keyClick(w.centralWidget(), Qt.Key_1)        # new line is selected
        row = w.rows[0]
        QTest.keyClick(row.edit, Qt.Key_Escape)
        self.pos = 4321
        QTest.keyClick(w.centralWidget(), Qt.Key_E)
        self.assertEqual(row.entry.end_ms, 4321)

    def test_e_typed_in_a_line_is_text(self):
        w = self.w
        self.activate(w)
        QTest.keyClick(w.centralWidget(), Qt.Key_1)
        QTest.keyClicks(w.rows[0].edit, "hej")
        self.assertEqual(w.rows[0].entry.text, "hej")
        self.assertIsNone(w.rows[0].entry.end_ms)

    def test_end_before_start_is_refused(self):
        w = self.w
        row = w.add_entry(5000, w.persons[0], "says", "x")
        self.pos = 4000
        w.set_end(row)
        self.assertIsNone(row.entry.end_ms)
        self.assertEqual(w.statusBar().currentMessage(), A.tr("status.end_before_start"))

    def test_without_selection_e_asks_to_select(self):
        w = self.w
        w.set_end()
        self.assertEqual(w.statusBar().currentMessage(), A.tr("status.select_first"))

    def test_clicking_or_typing_in_a_row_selects_it(self):
        w = self.w
        a = w.add_entry(1000, w.persons[0], "says", "a")
        b = w.add_entry(2000, w.persons[1], "says", "b")
        self.assertIs(w.selected_row, b)
        QTest.mouseClick(a, Qt.LeftButton, pos=A.QPoint(1, a.height() // 2))   # the row's margin
        self.assertIs(w.selected_row, a)
        self.activate(w)
        b.edit.setFocus()
        self.assertIs(w.selected_row, b)

    def test_end_time_is_saved_and_reopened(self):
        path = self.tmpdir / "t.txt"
        path.write_text("[00:00:01.000] E: hej\n", encoding="utf-8")
        w = self.w
        w.open_transcript(str(path))
        self.pos = 1960
        w.set_end(w.rows[0])
        self.assertTrue(w.save())
        self.assertEqual(body(path), "[00:00:01.000-00:00:01.960] E: hej\n")
        w.open_transcript(str(path))
        self.assertEqual(w.rows[0].entry.end_ms, 1960)
        self.assertEqual(w.rows[0].end_stamp.text(), "00:01")
        self.assertTrue(w.rows[0].end_stamp.toolTip().startswith("00:01.960"))


class TrackViewTests(WindowTestCase):
    def setUp(self):
        super().setUp()
        self.fake_video(10_000)
        self.t = self.w.tracks

    def test_two_tracks_per_person(self):
        w, t = self.w, self.t
        self.assertEqual(t.tracks(), [(w.persons[0], "says"), (w.persons[0], "does"),
                                      (w.persons[1], "says"), (w.persons[1], "does")])
        h2 = t.height()
        w.add_person()
        self.assertEqual(len(t.tracks()), 6)
        self.assertEqual(t.height(), t.height_for(6))
        self.assertGreater(t.height(), h2)

    def test_bars_span_start_to_end_and_markers_without_end(self):
        w, t = self.w, self.t
        p1, p2 = w.persons
        span = w.add_entry(2000, p1, "says", "hej")
        span.entry.end_ms = 4000
        mark = w.add_entry(5000, p2, "does", "nikker")
        bars = {e: (bar, t.tracks().index((e.person, e.kind))) for e, bar, _ in t.bars()}
        bar, track = bars[span.entry]
        self.assertEqual(track, 0)
        self.assertAlmostEqual(bar.left(), t.x_of(2000))
        self.assertAlmostEqual(bar.right(), t.x_of(4000))
        bar, track = bars[mark.entry]
        self.assertEqual(track, 3)
        self.assertAlmostEqual(bar.center().x(), t.x_of(5000))
        self.assertEqual(bar.width(), t.MARKER_W)

    def test_tracks_cover_the_interval(self):
        w, t = self.w, self.t
        w.set_interval(2000, 6000)
        self.assertEqual(t.view_range(), (2000, 6000))
        self.assertAlmostEqual(t.x_of(2000), t.LABEL_W)
        self.assertEqual(t.ms_of(t.width()), 6000)

    def test_clicking_a_bar_selects_its_line_and_seeks(self):
        w, t = self.w, self.t
        row = w.add_entry(3000, w.persons[1], "says", "x")
        row.entry.end_ms = 6000
        w.add_entry(8000, w.persons[0], "says", "other")     # now selected
        self.seeks.clear()
        bar = next(b for e, b, _ in t.bars() if e is row.entry)
        QTest.mouseClick(t, Qt.LeftButton, pos=bar.center().toPoint())
        self.assertIs(w.selected_row, row)
        self.assertEqual(self.seeks, [3000])

    def test_clicking_empty_track_seeks(self):
        w, t = self.w, self.t
        x = round(t.x_of(7000))
        QTest.mouseClick(t, Qt.LeftButton, pos=A.QPoint(x, int(t.track_rect(0).center().y())))
        self.assertAlmostEqual(self.seeks[-1], 7000, delta=40)

    def test_text_on_tracks_toggle_is_in_view_menu_and_remembered(self):
        w = self.w
        self.assertFalse(w.tracks.show_text)
        view_menu = w.menuBar().actions()[1].menu()
        self.assertEqual(w.menuBar().actions()[1].text(), "&View")
        self.assertIn(w.track_text_action, view_menu.actions())
        w.track_text_action.trigger()
        self.assertTrue(w.tracks.show_text)
        w2 = self.make_window()
        self.assertTrue(w2.tracks.show_text)
        self.assertTrue(w2.track_text_action.isChecked())

    def test_painting_with_and_without_text(self):
        w = self.w
        r = w.add_entry(1000, w.persons[0], "says", "en lang sætning der skal forkortes")
        r.entry.end_ms = 1500
        w.add_entry(2000, w.persons[1], "does", "nikker")
        for on in (False, True):
            w.set_track_text(on)
            self.assertFalse(w.tracks.grab().isNull())

    def test_clicking_a_name_folds_that_persons_tracks_into_one(self):
        w, t = self.w, self.t
        p1, p2 = w.persons
        says = w.add_entry(1000, p1, "says", "a")
        does = w.add_entry(2000, p1, "does", "b")
        h = w.tracks_scroll.height()
        QTest.mouseClick(t, Qt.LeftButton, pos=A.QPoint(10, int(t.track_rect(0).center().y())))
        self.assertEqual(t.tracks(), [(p1, None), (p2, "says"), (p2, "does")])
        self.assertLess(w.tracks_scroll.height(), h)
        bars = {e: bar for e, bar, _ in t.bars()}
        row = t.track_rect(0)
        self.assertAlmostEqual(bars[says.entry].top(), row.top())                      # upper half
        self.assertAlmostEqual(bars[does.entry].top(), row.top() + row.height() / 2)   # lower half
        self.assertFalse(t.grab().isNull())
        QTest.mouseClick(t, Qt.LeftButton, pos=A.QPoint(10, int(t.track_rect(0).center().y())))
        self.assertEqual(len(t.tracks()), 4)
        self.assertEqual(w.tracks_scroll.height(), h)

    def test_track_labels_follow_language(self):
        self.w.set_language("da")
        self.assertEqual(A.tr("label.track_does", name="E"), "E · gør")


class HourFormatTests(WindowTestCase):
    def test_fmt_ms_without_hours(self):
        self.assertEqual(A.fmt_ms(72_345, hours=False), "01:12.345")
        self.assertEqual(A.fmt_ms(72_345, millis=False, hours=False), "01:12")
        self.assertEqual(A.fmt_ms(3_723_045, hours=False), "01:02:03.045")   # never hides real hours

    def test_short_video_shows_minutes_and_seconds(self):
        self.fake_video(59 * 60_000)
        w = self.w
        row = w.add_entry(72_345, w.persons[0], "says", "x")
        self.assertEqual(row.stamp.text(), "01:12")
        self.assertTrue(row.stamp.toolTip().startswith("01:12.345\n"))
        self.assertEqual(w.start_edit.displayFormat(), "mm:ss.zzz")

    def test_video_of_an_hour_or_more_shows_hours_everywhere(self):
        self.fake_video(2 * 3_600_000)
        w = self.w
        row = w.add_entry(72_345, w.persons[0], "says", "x")
        row.entry.end_ms = 80_000
        row.refresh()
        self.assertEqual(row.stamp.text(), "00:01:12")
        self.assertEqual(row.end_stamp.text(), "00:01:20")
        self.assertTrue(row.stamp.toolTip().startswith("00:01:12.345\n"))
        self.assertEqual(w.start_edit.displayFormat(), "HH:mm:ss.zzz")
        self.assertEqual(w.fmt(5_600), "00:00:05.600")

    def test_changing_to_a_short_video_drops_the_hours_again(self):
        self.fake_video(2 * 3_600_000)
        w = self.w
        row = w.add_entry(10_000, w.persons[0], "says", "x")
        self.assertEqual(row.stamp.text(), "00:00:10")
        w.on_duration(30_000)
        self.assertEqual(row.stamp.text(), "00:10")

    def test_without_video_the_transcript_decides(self):
        path = self.tmpdir / "long.txt"
        path.write_text("[00:00:10.000] E: a\n[01:02:03.000] E: b\n", encoding="utf-8")
        w = self.w
        w.open_transcript(str(path))
        self.assertEqual(w.rows[0].stamp.text(), "00:00:10")
        path.write_text("[00:00:10.000] E: a\n", encoding="utf-8")
        w.open_transcript(str(path))
        self.assertEqual(w.rows[0].stamp.text(), "00:10")

    def test_file_always_keeps_full_timestamps(self):
        path = self.tmpdir / "t.txt"
        path.write_text("", encoding="utf-8")
        self.fake_video(60_000)
        w = self.w
        w.open_transcript(str(path))
        w.add_entry(72_345, w.persons[0], "says", "x")
        self.assertTrue(w.save())
        self.assertEqual(body(path), "[00:01:12.345] Person 1: x\n")


class NowMarkerTests(WindowTestCase):
    def setUp(self):
        super().setUp()
        self.fake_video()
        self.state = QMediaPlayer.PausedState
        self.w.player.playbackState = lambda: self.state
        self.m = self.w.now_markers

    def add(self, *times):
        rows = [self.w.add_entry(t, self.w.persons[0], "says", str(t)) for t in times]
        # let the list lay itself out
        def laid_out():   # every row at full height, none overlapping
            return all(r.height() >= r.minimumSizeHint().height() for r in rows) and all(
                b.geometry().top() > a.geometry().bottom() for a, b in zip(rows, rows[1:]))
        self.assertTrue(wait_until(laid_out))
        QTest.qWait(20)
        return rows

    def test_marks_lines_within_one_second_while_playing(self):
        w = self.w
        r1, r2, r3, r4, r5 = self.add(1000, 4500, 5200, 5900, 8000)
        self.state = PLAYING
        w.update_now_markers(5000)
        top, bottom, inside, _ = self.m.span
        self.assertEqual(inside, [r2, r3, r4])
        self.assertEqual(top, r2.geometry().top())
        self.assertEqual(bottom, r4.geometry().bottom())
        self.assertFalse(self.m.grab().isNull())

    def test_a_line_whose_span_covers_now_is_marked(self):
        w = self.w
        r1, r2 = self.add(1000, 9000)
        r1.entry.end_ms = 4500
        self.state = PLAYING
        w.update_now_markers(5000)
        self.assertEqual(self.m.span[2], [r1])

    def test_one_line_between_rows_when_nothing_is_near(self):
        w = self.w
        r1, r2 = self.add(1000, 9000)
        self.state = PLAYING
        w.update_now_markers(5000)
        top, bottom, inside, _ = self.m.span
        self.assertEqual(inside, [])
        self.assertEqual(top, bottom)
        self.assertTrue(r1.geometry().bottom() <= top <= r2.geometry().top())
        self.assertFalse(self.m.grab().isNull())

    def test_hidden_when_not_playing(self):
        w = self.w
        self.add(5000)
        self.state = PLAYING
        w.update_now_markers(5000)
        self.assertIsNotNone(self.m.span)
        self.state = QMediaPlayer.PausedState
        w.on_state(self.state)
        self.assertIsNone(self.m.span)

    def test_follows_the_playhead_down_the_list(self):
        w = self.w
        rows = self.add(*range(1000, 61_000, 1000))
        bar = w.tx_scroll.verticalScrollBar()
        bar.setValue(0)
        w._user_scrolled_at = -1e9          # the setValue above counts as scrolling by hand
        self.state = PLAYING
        w.update_now_markers(58_000)
        self.assertGreater(bar.value(), 0)
        viewport_top = bar.value()
        self.assertTrue(viewport_top <= rows[57].geometry().top()
                        <= viewport_top + w.tx_scroll.viewport().height())

    def test_scrolling_by_hand_pauses_following_for_a_moment(self):
        w = self.w
        self.add(*range(1000, 61_000, 1000))
        bar = w.tx_scroll.verticalScrollBar()
        bar.setValue(200)
        bar.setValue(0)                     # you scroll to the top yourself
        self.state = PLAYING
        w.update_now_markers(58_000)
        self.assertEqual(bar.value(), 0)
        self.assertIsNotNone(self.m.span)   # still marked, just not scrolled to
        w._user_scrolled_at -= A.Annotator.FOLLOW_PAUSE_S + 0.1
        w.update_now_markers(58_000)
        self.assertGreater(bar.value(), 0)

    def test_does_not_scroll_while_typing(self):
        w = self.w
        rows = self.add(*range(1000, 61_000, 1000))
        bar = w.tx_scroll.verticalScrollBar()
        bar.setValue(0)
        w._user_scrolled_at = -1e9
        self.activate(w)
        rows[0].edit.setFocus()
        self.state = PLAYING
        w.update_now_markers(58_000)
        self.assertEqual(bar.value(), 0)


class RangeAndSubtitleFormatTests(unittest.TestCase):
    def setUp(self):
        self.e = A.Person("E", "#1f77b4", 4)
        self.f = A.Person("F", "#d62728", 9)
        self.entries = [
            A.Entry(None, None, "raw", "Header"),
            A.Entry(1000, self.e, "says", "hej", 3000),
            A.Entry(None, None, "raw", "note between"),
            A.Entry(2500, self.f, "does", "nikker"),
            A.Entry(9000, self.e, "says", "senere"),
        ]

    def test_entries_in_range(self):
        texts = lambda es: [x.text for x in es]
        self.assertEqual(texts(A.entries_in_range(self.entries)), [x.text for x in self.entries])
        self.assertEqual(texts(A.entries_in_range(self.entries, 1000, 2500)), ["hej", "note between", "nikker"])
        self.assertEqual(texts(A.entries_in_range(self.entries, 2000, 9000)), ["nikker", "senere"])
        self.assertEqual(A.entries_in_range(self.entries, 20_000, 30_000), [])

    def test_text_export_with_range(self):
        self.assertEqual(A.export_transcript(self.entries, 2000, 10_000), 'F: (nikker)\nE: "senere"\n')

    def test_overlay_images_follow_what_is_showing(self):
        tmp = Path(tempfile.mkdtemp())
        name = A.build_overlay(self.entries, 2000, 6000, 320, 180, tmp)
        rows = (tmp / name).read_text(encoding="utf-8").splitlines()
        self.assertEqual(rows[0], "ffconcat version 1.0")
        files = [r.split("'")[1] for r in rows if r.startswith("file")]
        durations = [float(r.split()[1]) for r in rows if r.startswith("duration")]
        # E 1.0-3.0 s, F 2.5-4.5 s (no end: 2 s) -> clip 2-6 s: [E] [E,F] [F] [nothing]
        self.assertEqual(durations, [0.5, 0.5, 1.5, 1.5])
        self.assertEqual(len(set(files[:4])), 4)
        self.assertEqual(files[-1], files[-2])          # concat needs the last file twice
        from PySide6.QtGui import QImage
        empty, busy = QImage(str(tmp / files[3])), QImage(str(tmp / files[1]))
        self.assertEqual((busy.width(), busy.height()), (320, 180))
        alpha = lambda img: sum(img.pixelColor(x, y).alpha() > 0
                                for x in range(0, img.width(), 4) for y in range(0, img.height(), 4))
        self.assertEqual(alpha(empty), 0)
        self.assertGreater(alpha(busy), 20)
        shutil.rmtree(tmp, ignore_errors=True)

    def test_video_export_arguments(self):
        args = A.video_export_args("in.mp4", "out.mp4", 1500, 4000, "text.ffconcat")
        self.assertEqual(args[args.index("-ss") + 1], "1.500")
        self.assertEqual(args[args.index("-t") + 1], "2.500")
        self.assertEqual(args[args.index("-f") + 1], "concat")
        self.assertIn("text.ffconcat", args)
        self.assertIn("overlay", args[args.index("-filter_complex") + 1])
        self.assertNotIn("-filter_complex", A.video_export_args("in.mp4", "out.mp4", 0, 1000))
        self.assertEqual(args[-1], "out.mp4")

    def test_subtitle_text_in_the_language_shown(self):
        e = A.Entry(0, self.e, "says", "hej", translations={"en": ["hi", "hej", False]})
        d = A.Entry(0, self.f, "does", "nikker")
        h = A.Entry(0, self.e, "says", "haha", translations={"en": ["", "haha", True]})
        self.assertEqual(A.subtitle_text(e, "da", "da"), "E: hej")
        self.assertEqual(A.subtitle_text(e, "en", "da"), "E: hi")
        self.assertEqual(A.subtitle_text(d, "en", "da"), "F: (nikker)")   # untranslated: original
        self.assertEqual(A.subtitle_text(h, "en", "da"), "E: haha")       # same in both

    def test_text_export_with_another_language_underneath(self):
        e = A.Entry(0, self.e, "says", "hej", translations={"en": ["hi", "hej", False]})
        d = A.Entry(1, self.f, "does", "nikker", translations={"en": ["nods", "nikker", False]})
        u = A.Entry(2, self.e, "says", "ikke oversat")
        self.assertEqual(A.export_transcript([e, d, u], lang="da", below="en", orig="da"),
                         'E: "hej"\n"hi"\nF: (nikker)\n(nods)\nE: "ikke oversat"\n')
        self.assertEqual(A.export_transcript([e, d, u], lang="en", orig="da"),
                         'E: "hi"\nF: (nods)\nE: "ikke oversat"\n')
        self.assertEqual(A.export_transcript([e, d, u], lang="en", below="da", orig="da"),
                         'E: "hi"\n"hej"\nF: (nods)\n(nikker)\nE: "ikke oversat"\n"ikke oversat"\n')


class LayoutNoOverlapTests(unittest.TestCase):
    R = A.QRectF(0, 0, 1280, 720)

    def check(self, lines):
        placed = A.layout_subtitles(lines, self.R, 32)
        rects = [p[0] for p in placed]
        for i, a in enumerate(rects):
            self.assertTrue(self.R.adjusted(-1, -1, 1, 1).contains(a), a)
            for b in rects[i + 1:]:
                self.assertFalse(a.intersects(b), (a, b))
        return placed

    def test_two_points_close_together_do_not_overlap(self):
        lines = [("E: Godt. Den næste", "#1f77b4", False, (0.30, 0.40)),
                 ("F: (dækker munden med venstre hånd)", "#d62728", True, (0.33, 0.42))]
        placed = self.check(lines)
        e = placed[0][0]
        self.assertAlmostEqual(e.center().x(), 0.30 * 1280, delta=1)   # the first stays on its point
        self.assertAlmostEqual(e.top(), 0.40 * 720, delta=1)

    def test_point_near_an_edge_stays_inside(self):
        placed = self.check([("E: " + "lang tekst " * 8, "#000", False, (0.99, 0.99))])
        self.assertLessEqual(placed[0][0].right(), 1280)
        self.assertLessEqual(placed[0][0].bottom(), 720)

    def test_presets_and_points_mixed_never_overlap(self):
        # The video shows at most MAX_SUBTITLE_LINES lines at once; try many mixes of origins.
        import random
        rnd = random.Random(7)
        pool = A.ORIGINS + [(0.5, 0.5), (0.52, 0.55), (0.1, 0.9), (0.9, 0.1), (0.3, 0.4), (0.32, 0.42)]
        for _ in range(60):
            lines = [(f"P{i}: {'ord ' * rnd.randint(2, 14)}", "#123456", rnd.random() < 0.5, rnd.choice(pool))
                     for i in range(A.MAX_SUBTITLE_LINES)]
            with self.subTest(lines=[l[3] for l in lines]):
                self.check(lines)

    def test_slides_sideways_when_up_and_down_are_full(self):
        # a tall stack of three in the left column; a fourth one aimed at it must go beside it
        lines = [(f"L{i}: " + "ord " * 12, "#000", False, 4) for i in range(3)] + \
                [("X: " + "ord " * 12, "#000", False, (0.2, 0.3))]
        placed = self.check(lines)
        left_col = max(p[0].right() for p in placed[:3])
        self.assertGreaterEqual(placed[3][0].left(), left_col)

    def test_one_point_stacks_downwards_oldest_first(self):
        placed = self.check([("E: første", "#000", False, (0.5, 0.2)), ("E: anden", "#000", False, (0.5, 0.2))])
        self.assertLess(placed[0][0].bottom(), placed[1][0].top())


class DocumentFormatTests(unittest.TestCase):
    TEXT = (
        "#% transcript: 1\n"
        "#% languages: da, en\n"
        "#% actions da: griner | nikker\n"
        "#% actions en: laughs | nods\n"
        "#% person E: #1f77b4 | 0.25 0.3\n"
        "#% person F: #d62728 | 4\n"
        "#% person Nobody yet: #2ca02c | 2\n"
        "#% future-thing: kept as it is\n"
        "Header\n"
        "[00:00:01.000] E: Altså den synes jeg også kan\n"
        "\ten: Well, I think that one works too\n"
        "[00:00:02.000-00:00:03.000] F *nikker*\n"
        "\ten: nods\n"
        "[00:00:03.500] E: Haha\n"
        "\ten =\n"
        "[00:00:04.000] E: Ja ja\n"
        "\ten!: Yes\n"
        "[00:00:05.000] F: ikke oversat\n")

    def test_everything_round_trips(self):
        doc = A.parse_document(self.TEXT)
        self.assertTrue(doc.has_meta)
        self.assertEqual(doc.languages, ["da", "en"])
        self.assertEqual(doc.actions, {"da": ["griner", "nikker"], "en": ["laughs", "nods"]})
        self.assertEqual([(p.name, p.color, p.origin) for p in doc.persons],
                         [("E", "#1f77b4", (0.25, 0.3)), ("F", "#d62728", 4), ("Nobody yet", "#2ca02c", 2)])
        self.assertEqual(doc.extra, ["#% future-thing: kept as it is"])
        self.assertEqual(A.serialize_document(doc), self.TEXT)

    def test_statuses_from_the_file(self):
        doc = A.parse_document(self.TEXT)
        st = [A.translation_status(e, "en", "da") for e in doc.entries]
        self.assertEqual(st, ["none", "done", "done", "same", "outdated", "missing"])
        self.assertEqual([A.text_in(e, "en", "da") for e in doc.entries[1:]],
                         ["Well, I think that one works too", "nods", "Haha", "Yes", ""])

    def test_changing_the_original_makes_translations_out_of_date_but_not_same(self):
        doc = A.parse_document(self.TEXT)
        said, haha = doc.entries[1], doc.entries[3]
        said.text += " godt"
        haha.text = "Hahaha"
        self.assertEqual(A.translation_status(said, "en", "da"), "outdated")
        self.assertEqual(A.translation_status(haha, "en", "da"), "same")
        self.assertEqual(A.text_in(haha, "en", "da"), "Hahaha")
        out = A.serialize_document(doc)
        self.assertIn("\ten!: Well, I think that one works too", out)
        self.assertIn("[00:00:03.500] E: Hahaha\n\ten =\n", out)

    def test_old_files_without_metadata_are_danish(self):
        doc = A.parse_document("[00:00:01.000] E: hej\n")
        self.assertFalse(doc.has_meta)
        self.assertEqual(doc.languages, ["da", "en"])
        self.assertEqual(A.parse_document("[00:00:01.000] E: hi\n", default_lang="en").languages, ["en", "da"])

    def test_a_language_used_on_lines_is_added(self):
        doc = A.parse_document("#% languages: da\n[00:00:01.000] E: hej\n\tde: hallo\n")
        self.assertEqual(doc.languages, ["da", "de"])

    def test_the_users_transcript_keeps_every_line(self):
        src = ROOT / "EogF1.txt"
        if not src.exists():
            self.skipTest("user's transcript not present")
        raw = src.read_bytes().decode("utf-8")              # read only
        doc = A.parse_document(raw)
        lines = [line for line in A.serialize_document(doc).splitlines() if not line.startswith("#%")]
        self.assertEqual(lines, raw.splitlines())            # metadata added, nothing else changed


class OriginTests(WindowTestCase):
    def test_selector_beside_name_only_when_text_on_video_is_on(self):
        w = self.w
        row = w.person_rows[0]
        lay = row.layout()
        self.assertEqual(lay.indexOf(row.origin_combo), lay.indexOf(row.name_edit) + 1)
        self.assertTrue(row.origin_combo.isHidden())
        w.set_video_text(True)
        self.assertFalse(row.origin_combo.isHidden())
        w.add_person()
        self.assertFalse(w.person_rows[2].origin_combo.isHidden())   # new people get one too
        w.set_video_text(False)
        self.assertTrue(row.origin_combo.isHidden())

    def test_choosing_an_origin_is_saved_in_the_transcript(self):
        w = self.w
        w.set_video_text(True)
        p = w.persons[0]
        w.rename_person(p, "E")
        row = w.person_rows[0]
        row.origin_combo.setCurrentIndex(A.ORIGINS.index(4))
        self.assertEqual(p.origin, 4)
        self.assertEqual(row.origin_combo.currentText(), "← Left")
        path = self.tmpdir / "t.txt"
        w.transcript_path = str(path)
        self.assertTrue(w.save())
        self.assertIn("#% person E: #1f77b4 | 4", meta(path))
        w2 = self.make_window()
        w2.open_transcript(str(path))
        self.assertEqual([q.origin for q in w2.persons], [4, 2])
        self.assertEqual(w2.person_rows[0].origin_combo.currentIndex(), A.ORIGINS.index(4))

    def test_origin_names_follow_language(self):
        w = self.w
        w.set_language("da")
        self.assertEqual(w.person_rows[0].origin_combo.itemText(A.ORIGINS.index(9)), "↗ Øverst t.h.")

    def test_text_is_placed_at_each_persons_origin(self):
        self.fake_video()
        w = self.w
        e, f = w.persons
        w.set_origin(e, 4)      # left
        w.set_origin(f, 9)      # top right
        w.player.position = lambda: 1500
        w.add_entry(1000, e, "says", "venstre")
        w.add_entry(1000, f, "does", "øverst til højre")
        w.add_entry(1200, e, "says", "også venstre")
        r = A.QRectF(0, 0, 1280, 720)
        placed = {text: rect for rect, text, *_ in w.video.layout_lines(r)}
        left1, left2 = placed["Person 1: venstre"], placed["Person 1: også venstre"]
        top_right = placed["Person 2: (øverst til højre)"]
        self.assertLess(left1.right(), r.width() / 2)
        self.assertLess(abs(left1.left() - r.height() * 0.04), 1)
        self.assertLess(left1.bottom(), left2.top())               # stacked, oldest first
        mid = (left1.top() + left2.bottom()) / 2
        self.assertLess(abs(mid - r.center().y()), 2)              # centred vertically
        self.assertGreater(top_right.left(), r.width() / 2)
        self.assertLess(abs(top_right.right() - (r.width() - r.height() * 0.04)), 1)
        self.assertLess(top_right.top(), r.height() * 0.1)


class PickPointTests(WindowTestCase):
    def setUp(self):
        super().setUp()
        self.w.set_video_text(True)
        QTest.qWait(20)

    def test_choosing_point_then_clicking_the_video_sets_the_origin(self):
        w = self.w
        p = w.persons[0]
        w.rename_person(p, "E")
        row = w.person_rows[0]
        row.origin_combo.setCurrentIndex(A.POINT_INDEX)
        self.assertIs(w.video.pick_person, p)
        r = w.video.video_rect()
        click = A.QPoint(int(r.left() + r.width() * 0.25), int(r.top() + r.height() * 0.6))
        QTest.mouseClick(w.video, Qt.LeftButton, pos=click)
        self.assertIsNone(w.video.pick_person)
        x, y = p.origin
        self.assertAlmostEqual(x, 0.25, delta=0.01)
        self.assertAlmostEqual(y, 0.6, delta=0.01)
        self.assertEqual(row.origin_combo.currentIndex(), A.POINT_INDEX)
        self.assertEqual(row.origin_combo.currentText(), "⌖ 25 %, 60 %")
        path = self.tmpdir / "t.txt"
        w.transcript_path = str(path)
        self.assertTrue(w.save())
        self.assertTrue(any(re.match(r"#% person E: #1f77b4 \| 0\.25\d* 0\.(6|59\d*)$", m) for m in meta(path)))
        w2 = self.make_window()
        w2.open_transcript(str(path))
        self.assertAlmostEqual(w2.persons[0].origin[0], 0.25, delta=0.01)

    def test_escape_cancels_and_the_selector_goes_back(self):
        w = self.w
        row = w.person_rows[0]
        row.origin_combo.setCurrentIndex(A.ORIGINS.index(4))
        self.activate(w)
        row.origin_combo.setCurrentIndex(A.POINT_INDEX)
        self.assertIsNotNone(w.video.pick_person)
        QTest.keyClick(w.centralWidget(), Qt.Key_Escape)
        self.assertIsNone(w.video.pick_person)
        self.assertEqual(w.persons[0].origin, 4)
        self.assertEqual(row.origin_combo.currentIndex(), A.ORIGINS.index(4))

    def test_picking_turns_text_on_video_on(self):
        w = self.w
        w.set_video_text(False)
        w.start_pick(w.persons[1])
        self.assertTrue(w.video.show_text)
        self.assertFalse(w.video.grab().isNull())      # draws the hint and markers

    def test_text_at_a_point_on_screen(self):
        self.fake_video()
        w = self.w
        e = w.persons[0]
        w.set_origin(e, (0.7, 0.3))
        w.player.position = lambda: 1500
        w.add_entry(1000, e, "says", "her")
        r = A.QRectF(0, 0, 1000, 500)
        rect = w.video.layout_lines(r)[0][0]
        self.assertAlmostEqual(rect.center().x(), 700, delta=1)
        self.assertAlmostEqual(rect.top(), 150, delta=1)


class LanguageSwitchTests(WindowTestCase):
    def setUp(self):
        super().setUp()
        self.path = self.tmpdir / "EogF1.txt"
        self.path.write_text("[00:00:01.000] E: Godt. Den næste\n\ten: Good. The next one\n"
                             "[00:00:02.000] F *nikker*\n", encoding="utf-8")
        self.w.set_language("da")
        self.w.open_transcript(str(self.path))

    def test_opening_keeps_the_language_you_use_when_the_file_has_it(self):
        w2 = self.make_window()
        w2.set_language("en")
        w2.open_transcript(str(self.path))
        self.assertEqual(w2.lang, "en")                 # it has English text
        w3 = self.make_window()
        w3.set_language("en")
        bare = self.tmpdir / "bare.txt"
        bare.write_text("[00:00:01.000] E: hej\n", encoding="utf-8")
        w3.open_transcript(str(bare))
        self.assertEqual(w3.lang, "da")                 # no English yet: show it in Danish

    def test_switching_shows_the_other_version_and_the_menus_follow(self):
        w = self.w
        self.assertEqual(w.lang, "da")
        self.assertEqual(w.menuBar().actions()[0].text(), "&Filer")
        self.assertEqual(w.rows[0].edit.text(), "Godt. Den næste")
        w.set_language("en")
        self.assertEqual(w.menuBar().actions()[0].text(), "&File")
        self.assertEqual(w.rows[0].edit.text(), "Good. The next one")
        self.assertEqual(w.rows[1].edit.text(), "")                     # not translated yet...
        self.assertEqual(w.rows[1].edit.placeholderText(), "nikker")    # ...the original as a hint
        self.assertTrue(w.lang_actions["en"].isChecked())

    def test_language_menu_lists_the_transcripts_languages_and_can_add_one(self):
        w = self.w
        menu = w.lang_menu
        names = [a.text() for a in menu.actions() if not a.isSeparator() and not a.menu()]
        self.assertEqual(names, ["Dansk", "English"])
        w.set_language("de")
        self.assertEqual(w.languages, ["da", "en", "de"])
        self.assertTrue(w.dirty)
        self.assertEqual(w.menuBar().actions()[0].text(), "&Filer")     # menus stay Danish

    def test_typing_in_the_other_language_writes_that_version(self):
        w = self.w
        w.set_language("en")
        QTest.keyClicks(w.rows[1].edit, "nods")
        e = w.entries[1]
        self.assertEqual(e.text, "nikker")
        self.assertEqual(A.text_in(e, "en", "da"), "nods")
        self.assertTrue(w.save())
        self.assertIn(f"[00:00:02.000] F *nikker*\n\ten: nods\n", self.path.read_text(encoding="utf-8"))

    def test_video_text_and_tracks_follow_the_language(self):
        w = self.w
        w.player.position = lambda: 1500
        self.assertEqual(w.video.lines()[0][0], "E: Godt. Den næste")
        w.set_language("en")
        self.assertEqual(w.video.lines()[0][0], "E: Good. The next one")
        self.assertFalse(hasattr(w, "mode_actions"))       # no separate "text shows" menu any more

    def test_new_transcript_is_written_in_the_language_shown(self):
        w = self.w
        w.set_language("en")
        w.new_transcript(confirm=False)
        self.assertEqual(w.languages, ["en", "da"])
        w.set_language("da")                                  # still empty: now written in Danish
        self.assertEqual(w.languages[0], "da")
        w.add_entry(1000, w.persons[0], "says", "hej")
        w.set_language("en")                                  # has lines: Danish stays the original
        self.assertEqual(w.languages, ["da", "en"])

    def test_export_follows_the_language(self):
        w = self.w
        w.set_language("en")
        out = self.tmpdir / "out.txt"
        self.assertTrue(w.export_formatted(str(out)))
        self.assertEqual(out.read_text(encoding="utf-8"), 'E: "Good. The next one"\nF: (nikker)\n')


class OldTranslationFileTests(WindowTestCase):
    def test_translations_from_the_previous_version_are_moved_into_the_transcript(self):
        path = self.tmpdir / "Carina.txt"
        path.write_text("[00:00:01.000] N *griner *fnøs *\n[00:00:02.000] A: hej\n"
                        "[00:00:03.000] A: ikke oversat\n", encoding="utf-8")
        old = self.tmpdir / "Carina.en.txt"
        old_text = ('# Dansk → English: Carina.txt\n[00:00:01.000] N *griner *fnøs *\n\t"laugh *exhale"\n'
                    '[00:00:02.000] A: hej (før)\n\t"hi"\n')
        old.write_text(old_text, encoding="utf-8")
        w = self.w
        w.open_transcript(str(path))
        e1, e2, e3 = w.entries
        self.assertEqual(A.text_in(e1, "en", "da"), "laugh *exhale")
        self.assertEqual(A.translation_status(e1, "en", "da"), "done")
        self.assertEqual(A.translation_status(e2, "en", "da"), "outdated")   # original changed since
        self.assertEqual(A.translation_status(e3, "en", "da"), "missing")
        self.assertTrue(wait_until(lambda: "laugh *exhale" in path.read_text(encoding="utf-8"), 3000))
        text = path.read_text(encoding="utf-8")
        self.assertIn("[00:00:01.000] N *griner *fnøs *\n\ten: laugh *exhale\n", text)
        self.assertIn("[00:00:02.000] A: hej\n\ten!: hi\n", text)
        self.assertEqual(old.read_text(encoding="utf-8"), old_text)          # left alone
        self.assertIn("Carina.en.txt", w.statusBar().currentMessage())
        w2 = self.make_window()                                              # nothing imported twice
        w2.open_transcript(str(path))
        self.assertFalse(w2.dirty)


class TranslationWindowTests(WindowTestCase):
    def setUp(self):
        super().setUp()
        self.path = self.tmpdir / "EogF1.txt"
        self.path.write_bytes(("[00:00:01.000] E: Altså den synes jeg også kan\r\n"
                               "[00:00:02.000] F *nikker*\r\n[00:00:03.000] E: Haha\r\n").encode("utf-8"))
        self.w.open_transcript(str(self.path))
        self.w.show_translation()
        self.t = self.w.tr_window

    def edit(self, row, text):
        self.t.table.item(row, self.t.COL_RIGHT).setText(text)

    def test_two_sides_language_shown_and_the_other(self):
        t = self.t
        self.assertTrue(t.isVisible())
        self.assertEqual(t.table.rowCount(), 3)
        self.assertEqual(t.left_label.text(), "Dansk")
        self.assertEqual(t.table.horizontalHeaderItem(t.COL_LEFT).text(), "Dansk")
        self.assertEqual(t.table.horizontalHeaderItem(t.COL_RIGHT).text(), "English")
        self.assertEqual(t.table.item(0, t.COL_LEFT).text(), "Altså den synes jeg også kan")
        self.assertFalse(t.table.item(0, t.COL_LEFT).flags() & Qt.ItemIsEditable)
        self.assertTrue(t.table.item(0, t.COL_RIGHT).flags() & Qt.ItemIsEditable)
        self.assertEqual(t.count_label.text(), "0 af 3 linjer oversat · 0 forældede")
        self.assertFalse(hasattr(t, "src_combo"))           # no "written in" chooser

    def test_translating_marks_the_line_and_is_saved_in_the_same_file(self):
        t, w = self.t, self.w
        self.edit(0, "Well, I think that one works too")
        self.assertEqual(t.table.item(0, t.COL_STATUS).text(), "✓")
        self.assertEqual(w.rows[0].badge.text(), "EN ✓")
        self.assertEqual(w.rows[1].badge.text(), "EN –")
        self.assertTrue(wait_until(lambda: "works too" in self.path.read_text(encoding="utf-8"), 3000))
        self.assertIn("[00:00:01.000] E: Altså den synes jeg også kan\n\ten: Well, I think that one works too",
                      self.path.read_text(encoding="utf-8"))
        self.assertEqual(list(self.tmpdir.glob("EogF1.*.txt")), [])          # one file only
        w2 = self.make_window()
        w2.open_transcript(str(self.path))
        self.assertEqual(A.text_in(w2.entries[0], "en", "da"), "Well, I think that one works too")
        self.assertEqual(w2.rows[0].badge.text(), "EN ✓")

    def test_same_in_both_languages_button(self):
        t, w = self.t, self.w
        same = t.table.cellWidget(2, t.COL_SAME)
        same.click()
        e = w.entries[2]
        self.assertEqual(A.translation_status(e, "en", "da"), "same")
        self.assertEqual(t.table.item(2, t.COL_STATUS).text(), "=")
        self.assertEqual(t.table.item(2, t.COL_RIGHT).text(), "Haha")
        self.assertFalse(t.table.item(2, t.COL_RIGHT).flags() & Qt.ItemIsEditable)
        self.assertEqual(w.rows[2].badge.text(), "EN =")
        self.assertEqual(t.count_label.text(), "1 af 3 linjer oversat · 0 forældede")
        self.assertTrue(w.save())
        self.assertIn("[00:00:03.000] E: Haha\n\ten =\n", self.path.read_text(encoding="utf-8"))
        w.set_language("en")
        self.assertEqual(w.rows[2].edit.text(), "Haha")
        same = t.table.cellWidget(2, t.COL_SAME)
        same.click()                                        # off again
        self.assertEqual(A.translation_status(e, "en", "da"), "missing")

    def test_same_from_the_right_click_menu(self):
        w = self.w
        w.set_same(w.entries[2], True)
        self.assertEqual(w.rows[2].badge.text(), "EN =")

    def test_changing_the_original_shows_the_translation_needs_updating(self):
        t, w = self.t, self.w
        self.edit(0, "Well, I think that one works too")
        QTest.keyClicks(w.rows[0].edit, " godt")
        self.assertEqual(w.rows[0].badge.text(), "EN !")
        t.refresh()
        self.assertEqual(t.table.item(0, t.COL_STATUS).text(), "!")
        self.assertIn("1 forældede", t.count_label.text())
        t.table.selectRow(0)
        t.mark_selected_current()
        self.assertEqual(t.table.item(0, t.COL_STATUS).text(), "✓")
        self.assertEqual(w.rows[0].badge.text(), "EN ✓")

    def test_rebuilding_the_list_never_changes_translations(self):
        t, w = self.t, self.w
        for i, text in enumerate(("Well", "nods", "Yes")):
            self.edit(i, text)
        for row in w.rows:                       # change every original: all out of date
            QTest.keyClicks(row.edit, "!")
        before = [{k: list(v) for k, v in e.translations.items()} for e in w.entries]
        with mock.patch.object(w, "set_text", wraps=w.set_text) as spy:
            t.refresh()
            t.refresh()
        spy.assert_not_called()
        self.assertEqual([{k: list(v) for k, v in e.translations.items()} for e in w.entries], before)
        self.assertEqual([t.table.item(i, t.COL_STATUS).text() for i in range(3)], ["!", "!", "!"])

    def test_only_missing_and_out_of_date(self):
        t = self.t
        self.edit(0, "Well")
        t.table.cellWidget(2, t.COL_SAME).click()
        t.todo_cb.setChecked(True)
        self.assertEqual(t.table.rowCount(), 1)

    def test_switching_language_swaps_the_sides(self):
        t, w = self.t, self.w
        self.edit(0, "Well")
        w.set_language("en")
        self.assertEqual(t.left_label.text(), "English")
        self.assertEqual(t.table.horizontalHeaderItem(t.COL_LEFT).text(), "English")
        self.assertEqual(t.table.horizontalHeaderItem(t.COL_RIGHT).text(), "Dansk")
        self.assertEqual(t.table.item(0, t.COL_LEFT).text(), "Well")
        self.assertEqual(t.table.item(0, t.COL_RIGHT).text(), "Altså den synes jeg også kan")
        self.assertEqual(t.windowTitle(), "Translation")

    def test_another_language_on_the_right(self):
        t, w = self.t, self.w
        t.dst_combo.setCurrentIndex(t.dst_combo.findData("de"))
        self.assertEqual(w.other_lang, "de")
        self.assertIn("de", w.languages)
        self.assertEqual(t.table.horizontalHeaderItem(t.COL_RIGHT).text(), "Deutsch")
        self.edit(0, "Also")
        self.assertEqual(A.text_in(w.entries[0], "de", "da"), "Also")
        self.assertTrue(w.save())
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("#% languages: da, en, de", text)
        self.assertIn("\tde: Also", text)

    def test_enter_goes_on_to_the_next_line(self):
        t = self.t
        t.show()
        t.activateWindow()
        self.assertTrue(QTest.qWaitForWindowActive(t))
        t.edit_row(0)
        editor = A.QApplication.focusWidget()
        QTest.keyClicks(editor, "Well")
        QTest.keyClick(editor, Qt.Key_Return)
        self.assertTrue(wait_until(lambda: t.table.state() == A.QAbstractItemView.EditingState
                                   and t.table.currentRow() == 1, 2000))
        self.assertEqual(A.text_in(self.w.entries[0], "en", "da"), "Well")

    def test_clicking_the_time_selects_the_line_in_the_main_window(self):
        t, w = self.t, self.w
        t._clicked(2, t.COL_TIME)
        self.assertIs(w.selected_row, w.rows[2])

    def test_badge_opens_the_window_at_that_line(self):
        t, w = self.t, self.w
        t.hide()
        w.rows[1].badge.click()
        self.assertTrue(t.isVisible())
        self.assertEqual(t.table.currentRow(), 1)

    def test_export_text_with_the_other_language(self):
        w = self.w
        self.edit(0, "Well")
        out = self.tmpdir / "out.txt"

        def fake_exec(dlg):
            self.assertTrue(dlg.tr_cb.isEnabled())
            self.assertFalse(hasattr(dlg, "mode_combo"))      # just "with text" for video
            self.assertEqual(dlg.format_combo.currentData(), "ca")
            dlg.format_combo.setCurrentIndex(dlg.format_combo.findData("simple"))
            dlg.text_radio.setChecked(True)
            dlg.start_edit.setTime(A.ms_to_qtime(0))
            dlg.end_edit.setTime(A.ms_to_qtime(2000))
            dlg.path_edit.setText(str(out))
            return A.QDialog.Accepted

        with mock.patch.object(A.ExportDialog, "exec", fake_exec):
            self.assertTrue(w.export_dialog("text"))
        self.assertEqual(out.read_text(encoding="utf-8"),
                         'E: "Altså den synes jeg også kan"\n"Well"\nF: (nikker)\n')


class ExportDialogTests(WindowTestCase):
    def test_text_only_without_a_video(self):
        w = self.w
        w.add_entry(1000, w.persons[0], "says", "a")
        d = A.ExportDialog(w, "video")
        self.assertFalse(d.video_radio.isEnabled())
        self.assertEqual(d.values()["kind"], "text")
        self.assertEqual((d.values()["start"], d.values()["end"]), (0, 1000))

    def test_defaults_to_the_interval_and_now_buttons(self):
        self.fake_video()
        w = self.w
        w.video_path = str(self.tmpdir / "clip.mp4")
        w.set_interval(5000, 20_000)
        w.player.position = lambda: 7_250
        d = A.ExportDialog(w, "video")
        v = d.values()
        self.assertEqual((v["kind"], v["start"], v["end"], v["subtitles"]), ("video", 5000, 20_000, True))
        self.assertEqual(Path(v["path"]).name, "clip_00.05-00.20.mp4")
        now_buttons = [b for b in d.findChildren(A.QPushButton) if b.text() == "Now"]
        now_buttons[1].click()
        self.assertEqual(d.values()["end"], 7_250)
        self.assertEqual(Path(d.values()["path"]).name, "clip_00.05-00.07.mp4")
        d.text_radio.setChecked(True)
        self.assertFalse(d.subs_cb.isEnabled())
        self.assertTrue(d.values()["path"].endswith(".txt"))

    def test_your_own_file_name_is_kept(self):
        self.fake_video()
        w = self.w
        w.video_path = str(self.tmpdir / "clip.mp4")
        d = A.ExportDialog(w, "video")
        QTest.keyClicks(d.path_edit, "x")
        d.end_edit.setTime(A.ms_to_qtime(9000))
        self.assertTrue(d.values()["path"].endswith("x"))

    def test_stop_before_start_is_refused(self):
        self.fake_video()
        w = self.w
        w.video_path = str(self.tmpdir / "clip.mp4")
        d = A.ExportDialog(w, "video")
        d.start_edit.setTime(A.ms_to_qtime(9000))
        d.end_edit.setTime(A.ms_to_qtime(4000))
        with mock.patch.object(A.QMessageBox, "warning") as warn:
            d.accept()
        warn.assert_called_once()
        self.assertNotEqual(d.result(), A.QDialog.Accepted)

    def test_text_export_through_the_dialog_uses_the_range(self):
        w = self.w
        p = w.persons[0]
        for t in (1000, 5000, 9000):
            w.add_entry(t, p, "says", str(t))
        out = self.tmpdir / "range.txt"

        def fake_exec(dlg):
            dlg.text_radio.setChecked(True)
            dlg.format_combo.setCurrentIndex(dlg.format_combo.findData("simple"))
            dlg.start_edit.setTime(A.ms_to_qtime(4000))
            dlg.end_edit.setTime(A.ms_to_qtime(9000))
            dlg.path_edit.setText(str(out))
            return A.QDialog.Accepted

        with mock.patch.object(A.ExportDialog, "exec", fake_exec):
            self.assertTrue(w.export_dialog())
        self.assertEqual(out.read_text(encoding="utf-8"), 'Person 1: "5000"\nPerson 1: "9000"\n')

    def test_video_export_refuses_to_overwrite_the_video(self):
        w = self.w
        w.video_path = str(self.tmpdir / "clip.mp4")
        with mock.patch.object(A.QMessageBox, "warning") as warn:
            self.assertFalse(w.export_video(w.video_path, 0, 1000))
        warn.assert_called_once()


class SectionTests(WindowTestCase):
    def test_tracks_and_interval_fold_away_and_are_remembered(self):
        w = self.w
        for section, content in ((w.tracks_section, w.tracks_scroll), (w.interval_section, w.bar)):
            with self.subTest(section=section.header.text()):
                self.assertTrue(content.isVisible())
                section.header.click()
                self.assertTrue(section.is_collapsed())
                self.assertFalse(content.isVisible())
        w2 = self.make_window()
        self.assertTrue(w2.tracks_section.is_collapsed())
        self.assertTrue(w2.interval_section.is_collapsed())
        self.assertFalse(w2.tracks_scroll.isVisible())
        w2.tracks_section.header.click()
        self.assertTrue(w2.tracks_scroll.isVisible())

    def test_section_titles_follow_language(self):
        w = self.w
        self.assertEqual(w.tracks_section.header.text(), "Tracks")
        w.set_language("da")
        self.assertEqual(w.tracks_section.header.text(), "Spor")
        self.assertEqual(w.interval_section.header.text(), "Interval")


class LayoutTests(WindowTestCase):
    def test_timestamps_show_whole_seconds_and_exact_time_on_hover(self):
        w = self.w
        row = w.add_entry(72_345, w.persons[0], "says", "x")
        self.assertEqual(row.stamp.text(), "01:12")
        self.assertTrue(row.stamp.toolTip().startswith("01:12.345\n"))
        w.set_language("da")
        self.assertEqual(row.stamp.toolTip(), "01:12.345\n" + A.tr("tip.stamp"))

    def test_right_side_toggle_hides_shows_and_is_remembered(self):
        w = self.w
        self.assertTrue(w.right_panel.isVisible())
        self.assertIsNone(w.menuBar().cornerWidget(Qt.TopRightCorner))      # no extra corner button
        w.right_action.trigger()
        self.assertFalse(w.right_panel.isVisible())
        self.assertFalse(w.right_action.isChecked())
        self.assertIn(w.right_action, w.menuBar().actions()[1].menu().actions())
        w2 = self.make_window()
        self.assertFalse(w2.right_panel.isVisible())
        w2.right_action.trigger()
        self.assertTrue(w2.right_panel.isVisible())

    def test_ctrl_b_toggles_right_side(self):
        w = self.w
        self.activate(w)
        QTest.keyClick(w, Qt.Key_B, Qt.ControlModifier)
        self.assertFalse(w.right_panel.isVisible())
        QTest.keyClick(w, Qt.Key_B, Qt.ControlModifier)
        self.assertTrue(w.right_panel.isVisible())

    def test_annotating_shows_the_right_side_again(self):
        w = self.w
        w.set_right_panel(False)
        w.annotate(w.persons[0], "says")
        self.assertTrue(w.right_panel.isVisible())

    def test_narrow_controls_drop_5s_first_then_1s(self):
        c = self.w.controls
        texts = lambda: [b.text() for b in c.findChildren(A.QPushButton) if not b.isHidden()]
        full = c.needed_width()
        without_5 = c.needed_width(set(c.drop_order[0]))
        c.fit(full)
        self.assertIn("−5s", texts())
        self.assertIn("+5s", texts())
        c.fit(full - 1)
        self.assertNotIn("−5s", texts())
        self.assertNotIn("+5s", texts())
        self.assertIn("−1s", texts())
        self.assertIn("+1s", texts())
        c.fit(without_5 - 1)
        for t in ("−5s", "+5s", "−1s", "+1s"):
            self.assertNotIn(t, texts())
        self.assertIn("◀|", texts())
        c.fit(full + 50)
        self.assertIn("−5s", texts())
        self.assertIn("−1s", texts())

    def test_wide_window_shows_every_button(self):
        w = self.w
        w.set_right_panel(False)
        w.resize(1900, 900)
        QTest.qWait(50)
        hidden = [b.text() for b in w.controls.findChildren(A.QPushButton) if b.isHidden()]
        self.assertEqual(hidden, [])

    def test_interval_buttons_wrap_to_second_line_when_narrow(self):
        r = self.w.interval_row
        r.arrange(False)
        self.assertEqual(r.lines[1].count(), 0)
        r.arrange(True)
        self.assertGreater(r.lines[1].count(), 0)
        self.assertLess(r.minimumSizeHint().width(), r.one_line_width())

    def test_left_side_can_shrink_below_the_full_button_row(self):
        w = self.w
        c = w.controls
        self.assertLess(c.minimumSizeHint().width(), c.needed_width())
        w.splitter.setSizes([10, 2000])        # as narrow as the splitter allows
        QTest.qWait(50)
        hidden = sorted(b.text() for b in c.findChildren(A.QPushButton) if b.isHidden())
        self.assertIn("−5s", hidden)
        self.assertIn("+5s", hidden)
        self.assertEqual(len(hidden) % 2, 0)    # always in pairs
        self.assertTrue(w.interval_row.wrapped)


class VolumeDropDownTests(WindowTestCase):
    def test_hover_shows_slider_that_sets_volume(self):
        w = self.w
        btn = w.mute_btn
        self.assertFalse(btn.popup.isVisible())
        QApplication.sendEvent(btn, A.QEnterEvent(QPointF(5, 5), QPointF(5, 5), btn.mapToGlobal(QPointF(5, 5))))
        self.assertTrue(btn.popup.isVisible())
        self.assertIs(w.volume_slider, btn.slider)
        w.volume_slider.setValue(35)
        self.assertAlmostEqual(w.audio.volume(), 0.35, places=2)
        self.assertEqual(btn.value_label.text(), "35%")

    def test_stays_open_over_slider_and_closes_away_from_it(self):
        btn = self.w.mute_btn
        btn.show_popup()
        btn.maybe_hide(btn.popup.geometry().center())
        self.assertTrue(btn.popup.isVisible())
        btn.maybe_hide(btn.mapToGlobal(btn.rect().center()))
        self.assertTrue(btn.popup.isVisible())
        btn.maybe_hide(btn.popup.geometry().bottomRight() + A.QPoint(300, 300))
        self.assertFalse(btn.popup.isVisible())

    def test_click_and_m_still_mute(self):
        w = self.w
        w.audio.setMuted(False)
        w.mute_btn.click()
        self.assertTrue(w.audio.isMuted())
        self.activate(w)
        QTest.keyClick(w.centralWidget(), Qt.Key_M)
        self.assertFalse(w.audio.isMuted())


class SkipOneSecondTests(WindowTestCase):
    def setUp(self):
        super().setUp()
        self.fake_video()
        self.w.player.position = lambda: 10_000

    def button(self, text):
        return next(b for b in self.w.findChildren(A.QPushButton) if b.text() == text)

    def test_buttons(self):
        self.button("−1s").click()
        self.button("+1s").click()
        self.button("−5s").click()
        self.assertEqual(self.seeks, [9_000, 11_000, 5_000])

    def test_shift_arrows_skip_one_second(self):
        w = self.w
        self.activate(w)
        QTest.keyClick(w.centralWidget(), Qt.Key_Left, Qt.ShiftModifier)
        QTest.keyClick(w.centralWidget(), Qt.Key_Right, Qt.ShiftModifier)
        QTest.keyClick(w.centralWidget(), Qt.Key_Right)
        self.assertEqual(self.seeks, [9_000, 11_000, 15_000])

    def test_tooltips_are_translated(self):
        self.assertEqual(self.button("−1s").toolTip(), "Back 1 second (Shift+←)")
        self.w.set_language("da")
        self.assertEqual(self.button("+1s").toolTip(), "1 sekund frem (Shift+→)")


class VideoTextTests(WindowTestCase):
    def setUp(self):
        super().setUp()
        self.fake_video()
        self.pos = 0
        self.w.player.position = lambda: self.pos

    def test_lines_happening_now(self):
        w = self.w
        e, f = w.persons
        a = w.add_entry(1000, e, "says", "hej")
        a.entry.end_ms = 3500
        w.add_entry(2000, f, "does", "nikker")            # no end: shown for 2 s
        w.add_entry(2500, f, "says", "")                  # nothing to show
        self.pos = 500
        self.assertEqual(w.active_entries(), [])
        self.pos = 3000
        self.assertEqual([x.text for x in w.active_entries()], ["hej", "nikker"])
        self.pos = 3501
        self.assertEqual([x.text for x in w.active_entries()], ["nikker"])
        self.pos = 2000 + A.TEXT_HOLD_MS + 1
        self.assertEqual(w.active_entries(), [])

    def test_text_style_and_colour(self):
        w = self.w
        e, f = w.persons
        w.rename_person(e, "E")
        w.rename_person(f, "F")
        w.add_entry(1000, e, "says", "Godt. Den næste")
        w.add_entry(1000, f, "does", "dækker munden")
        self.pos = 1500
        self.assertEqual(w.video.lines(), [("E: Godt. Den næste", e.color, False, 2),
                                           ("F: (dækker munden)", f.color, True, 2)])

    def test_toggle_is_in_view_menu_and_remembered(self):
        w = self.w
        view_menu = w.menuBar().actions()[1].menu()
        self.assertIn(w.video_text_action, view_menu.actions())
        self.assertFalse(w.video.show_text)
        w.video_text_action.trigger()
        self.assertTrue(w.video.show_text)
        self.assertTrue(self.make_window().video.show_text)
        w.set_language("da")
        self.assertEqual(w.video_text_action.text(), "Vis tekst på &videoen")

    def test_text_is_drawn_without_a_video_frame_too(self):
        w = self.w
        w.add_entry(0, w.persons[0], "says", "x" * 400)    # long text wraps
        w.set_video_text(False)
        plain = w.video.grab().toImage()
        w.set_video_text(True)
        self.assertNotEqual(w.video.grab().toImage(), plain)


class CATranscriptTests(unittest.TestCase):
    DOC = (
        "#% languages: da, en\n"
        "[00:00:01.000-00:00:02.000] SP03: tak for hjælpen\n\ten: thanks for the help\n"
        "[00:00:01.000] SP03 *>Sille*\n\ten =\n"
        "[00:00:01.500] SP03 *smiler*\n\ten: smiles\n"
        "[00:00:02.500-00:00:03.000] SP01: ja\n\ten: yes\n"
        "[00:00:03.500-00:00:05.000] SP03: det var super fedt\n\ten: it was super nice\n"
        "[00:00:04.200-00:00:06.000] SP02: må jeg få pulsbåndet\n"
        "\ten: may I have the heart rate monitor\n\t@volume: quiet\n"
        "[00:00:05.000] SP02 *går mod Martin*\n\ten: walks tw Martin\n"
        "[00:00:06.800] SP03 *rejser sig*\n\ten: stands up\n"
        "[00:00:07.000] SP01 *>Martin*\n\ten =\n")

    def test_like_the_example(self):
        doc = A.parse_document(self.DOC)
        out = A.export_ca(doc.entries, lang="da", below="en", orig="da", media="clip.mp4", pauses=True)
        self.assertEqual(out.splitlines(), [
            "@Media: clip.mp4",
            "@Participants: SP03, SP01, SP02",
            "@Languages: Dansk, English",
            "",
            "1. *SP03: Δtak for Δhjælpen",            # Δ where each of her actions starts
            "          thanks for the help",           # the translation underneath
            "          Δ>Sille",                       # gaze
            "          Δsmiles",                       # actions in the translation's language
            "2.        (0.5)",                         # pause between turns
            "3. *SP01: ja",
            "          yes",
            "4.        (0.5)",
            "5. *SP03: det var [super fedt",           # where SP02 starts talking over
            "          it was super nice",
            "6. *SP02:         [°må jeg få Δpulsbåndet°",   # lined up under it; said quietly
            "          °may I have the heart rate monitor°",
            "          Δwalks tw Martin",
            "7. *SP03: stands up",                     # an action on its own
            "8. *SP01: >Martin",
        ])

    def test_without_translation_or_pauses(self):
        doc = A.parse_document(self.DOC)
        out = A.export_ca(doc.entries, lang="da", orig="da")
        self.assertNotIn("(0.5)", out)
        self.assertNotIn("thanks", out)
        self.assertIn("1. *SP03: Δtak for Δhjælpen\n          Δ>Sille\n          Δsmiler\n", out)

    def test_in_english(self):
        doc = A.parse_document(self.DOC)
        out = A.export_ca(doc.entries, lang="en", orig="da")
        self.assertIn("*SP01: yes", out)
        self.assertIn("*SP03: stands up", out)

    def test_numbers_line_up_past_nine(self):
        p = A.Person("E", "#000")
        entries = [A.Entry(i * 1000, p, "says", f"linje {i}") for i in range(12)]
        rows = A.export_ca(entries, lang="da", orig="da").splitlines()[3:]
        self.assertEqual(rows[0], " 1. *E:  linje 0")
        self.assertEqual(rows[11], "12. *E:  linje 11")


class VolumeTests(WindowTestCase):
    def test_volume_options_on_spoken_lines(self):
        w = self.w
        row = w.add_entry(1000, w.persons[0], "says", "hej med dig")
        act = w.add_entry(2000, w.persons[0], "does", "nikker")
        self.assertFalse(row.vol_btn.isHidden())
        self.assertTrue(act.vol_btn.isHidden())             # only for speech
        self.assertEqual(row.vol_btn.text(), "·")
        row._fill_volume_menu()
        names = [a.text() for a in row.vol_menu.actions() if not a.isSeparator()]
        self.assertEqual(names[:4], ["Normal volume", "Quiet   °word°", "Very quiet / whispered   °°word°°",
                                     "Loud   WORD"])
        row.vol_menu.actions()[1].trigger()
        self.assertEqual(row.entry.attrs["volume"], "quiet")
        self.assertEqual(row.vol_btn.text(), "°")
        self.assertEqual(A.subtitle_text(row.entry, "da", "da"), "Person 1: °hej med dig°")
        w.set_volume(row.entry, "loud")
        self.assertEqual(A.export_line(row.entry, "da", None, "da"), 'Person 1: "HEJ MED DIG"')
        w.set_volume(row.entry, "normal")
        self.assertNotIn("volume", row.entry.attrs)

    def test_volume_is_kept_in_the_file(self):
        w = self.w
        row = w.add_entry(1000, w.persons[0], "says", "hej")
        w.set_volume(row.entry, "very_quiet")
        path = self.tmpdir / "v.txt"
        w.transcript_path = str(path)
        self.assertTrue(w.save())
        self.assertIn(f"[00:00:01.000] Person 1: hej\n\t@volume: very_quiet\n", path.read_text(encoding="utf-8"))
        w2 = self.make_window()
        w2.open_transcript(str(path))
        self.assertEqual(A.volume_of(w2.entries[0]), "very_quiet")
        self.assertEqual(w2.rows[0].vol_btn.text(), "°°")

    def test_quiet_words_and_sound_descriptions(self):
        w = self.w
        row = w.add_entry(1000, w.persons[0], "says", "det er jo fint")
        row.edit.setSelection(7, 2)                          # "jo"
        row.wrap_selection("°", "°")
        self.assertEqual(row.entry.text, "det er °jo° fint")
        row.edit.setCursorPosition(len(row.edit.text()))
        row.wrap_selection("((", "))")
        QTest.keyClicks(row.edit, "griner")
        self.assertEqual(row.entry.text, "det er °jo° fint((griner))")


class GazeTests(WindowTestCase):
    def pick(self, data=None, text=None):
        """Run the action menu, choosing the item with this data (or text)."""
        def fake_run(menu, _where):
            items = list(menu.actions())
            for a in list(items):
                if a.menu():
                    items += a.menu().actions()
            return next(a for a in items if (data is not None and a.data() == data)
                        or (text is not None and a.text() == text))
        with mock.patch.object(self.w, "run_menu", fake_run):
            self.w.show_does_menu(self.w.persons[0], self.w.person_rows[0].does_btn)

    def test_gaze_at_another_person_from_the_action_menu(self):
        w = self.w
        w.rename_person(w.persons[1], "Lotte")
        self.pick(data=">Lotte")
        row = w.rows[0]
        self.assertEqual(row.entry.text, ">Lotte")
        self.assertEqual(row.label.text(), "Person 1 looks:")
        self.assertEqual(A.translation_status(row.entry, "da", "en"), "same")    # a name: same in Danish
        self.assertEqual(A.export_line(row.entry, "en", None, "en"), "Person 1: >Lotte")

    def test_gaze_somewhere_else_is_typed(self):
        w = self.w
        self.pick(text=A.tr("menu.gaze_custom"))
        self.assertEqual(w.rows[0].entry.text, ">")
        self.assertEqual(w.rows[0].edit.cursorPosition(), 1)


class RowLayoutTests(WindowTestCase):
    def test_text_lines_up_with_and_without_end_times_and_actions_are_indented(self):
        w = self.w
        w.rename_person(w.persons[1], "Computer")
        rows = [w.add_entry(1000, w.persons[0], "says", "a"), w.add_entry(2000, w.persons[1], "says", "b"),
                w.add_entry(3000, w.persons[0], "does", "c"), w.add_entry(4000, w.persons[1], "does", "d")]
        rows[0].entry.end_ms = 1500
        rows[2].entry.end_ms = 3500
        for r in rows:
            r.refresh()
        self.assertTrue(wait_until(lambda: all(r.edit.x() > 0 for r in rows)))
        QTest.qWait(50)
        xs = [r.edit.x() for r in rows]
        self.assertEqual(xs[0], xs[1])                    # speech: with and without end time
        self.assertEqual(xs[2], xs[3])                    # actions: with and without end time
        self.assertEqual(xs[2] - xs[0], A.ACTION_INDENT)  # actions a little further in

    def test_label_column_only_as_wide_as_the_labels_in_use(self):
        w = self.w
        n, c = w.persons
        w.rename_person(n, "N")
        w.rename_person(c, "Computer")
        talk = w.add_entry(1000, n, "says", "hej")
        w.add_entry(2000, n, "does", "griner")
        w.add_entry(3000, c, "says", "I just have a hard time")
        font = A.QFont(w.font())
        font.setBold(True)
        fm = A.QFontMetrics(font)
        says_w, does_w = w.label_widths()
        # "Computer:" is the widest label in use; "Computer gør:" / "kigger:" aren't on any line
        self.assertEqual(says_w, fm.horizontalAdvance("Computer:") + 8)
        self.assertEqual(talk.label.width(), says_w)
        w.add_entry(4000, c, "does", "blinker")                  # now "Computer does:" is in use
        self.assertGreater(w.label_widths()[1], does_w)
        self.assertEqual(w.label_widths()[1] - w.label_widths()[0], A.VOL_W + A.ACTION_INDENT)
        self.assertEqual(talk.label.width(), w.label_widths()[0])  # every row follows

    def test_translation_marks_only_when_switched_on(self):
        w = self.w
        path = self.tmpdir / "t.txt"
        path.write_text(f"[00:00:01.000] E: hej\n\ten: hi\n[00:00:02.000] E: nej\n", encoding="utf-8")
        w.open_transcript(str(path))
        self.assertTrue(all(r.badge.isHidden() for r in w.rows))
        self.assertEqual(w.rows[0].badge.text(), "EN ✓")
        w.tr_marks_action.trigger()
        self.assertFalse(w.rows[0].badge.isHidden())
        self.assertEqual(w.rows[1].badge.text(), "EN –")
        self.assertTrue(self.make_window().tr_marks_on)      # remembered
        w.set_language("en")
        self.assertEqual(w.rows[1].edit.placeholderText(), "nej")   # greyed hint either way


class KeyboardTests(WindowTestCase):
    def test_digit_adds_line_and_digits_typed_in_field_are_text(self):
        w = self.w
        self.activate(w)
        QTest.keyClick(w.centralWidget(), Qt.Key_2)
        self.assertEqual(len(w.rows), 1)
        self.assertIs(w.rows[0].entry.person, w.persons[1])
        self.assertIs(QApplication.focusWidget(), w.rows[0].edit)
        QTest.keyClicks(w.rows[0].edit, "1 2 3")
        self.assertEqual(w.rows[0].entry.text, "1 2 3")
        self.assertEqual(len(w.rows), 1)
        QTest.keyClick(w.rows[0].edit, Qt.Key_Escape)
        self.assertNotIsInstance(QApplication.focusWidget(), A.QLineEdit)

    def test_shortcuts_work_right_after_start(self):
        w = self.w
        w.activateWindow()
        self.assertTrue(QTest.qWaitForWindowActive(w))
        self.assertNotIsInstance(QApplication.focusWidget(), A.TEXT_INPUTS)
        QTest.keyClick(QApplication.focusWidget() or w, Qt.Key_1)
        self.assertEqual(len(w.rows), 1)

    def test_enter_in_interval_time_field_returns_to_shortcuts(self):
        self.fake_video()
        w = self.w
        self.activate(w)
        w.start_edit.setFocus()
        # QTest can't type into a QTimeEdit offscreen, so put the text in and press a real Enter.
        w.start_edit.lineEdit().setText("00:05.000")
        QTest.keyClick(w.start_edit, Qt.Key_Return)
        self.assertEqual(w.iv_start, 5000)
        self.assertNotIsInstance(QApplication.focusWidget(), A.TEXT_INPUTS)

    def test_shift_digit_opens_does_menu(self):
        w = self.w
        calls = []
        w.show_does_menu = lambda p, anchor: calls.append(p)
        self.activate(w)
        QTest.keyClick(w.centralWidget(), Qt.Key_1, Qt.ShiftModifier)
        QTest.keyClick(w.centralWidget(), Qt.Key_At, Qt.ShiftModifier)   # Shift+2 on US layouts
        self.assertEqual(calls, [w.persons[0], w.persons[1]])

    def test_digit_beyond_people_count_does_nothing(self):
        w = self.w
        w.release_focus()
        QTest.keyClick(w.centralWidget(), Qt.Key_7)
        self.assertEqual(w.rows, [])


class IntervalTests(WindowTestCase):
    def test_full_video_by_default(self):
        self.fake_video(60_000)
        self.assertEqual((self.w.iv_start, self.w.iv_end), (0, 60_000))
        self.assertEqual((self.w.timeline.minimum(), self.w.timeline.maximum()), (0, 60_000))

    def test_timeline_range_follows_interval(self):
        self.fake_video()
        self.w.set_interval(10_000, 20_000)
        self.assertEqual((self.w.timeline.minimum(), self.w.timeline.maximum()), (10_000, 20_000))
        self.assertEqual(A.qtime_to_ms(self.w.start_edit.time()), 10_000)
        self.assertEqual(A.qtime_to_ms(self.w.end_edit.time()), 20_000)
        self.assertEqual((self.w.bar.start, self.w.bar.end, self.w.bar.duration), (10_000, 20_000, 60_000))

    def test_interval_is_clamped_to_video_and_minimum_length(self):
        self.fake_video()
        w = self.w
        w.set_interval(-5, 999_999)
        self.assertEqual((w.iv_start, w.iv_end), (0, 60_000))
        w.set_interval(30_000, 10_000, "end")          # end dragged before start
        self.assertEqual((w.iv_start, w.iv_end), (30_000, 30_000 + A.MIN_INTERVAL_MS))
        w.set_interval(30_000, 10_000, "start")        # start dragged past end
        self.assertEqual((w.iv_start, w.iv_end), (10_000 - A.MIN_INTERVAL_MS, 10_000))
        w.set_interval(60_000, 60_000, "start")
        self.assertEqual((w.iv_start, w.iv_end), (60_000 - A.MIN_INTERVAL_MS, 60_000))

    def test_every_seek_stays_inside_interval(self):
        self.fake_video()
        w = self.w
        w.set_interval(10_000, 20_000)
        w.player.position = lambda: 11_000
        self.seeks.clear()
        w.seek_rel(-5000)
        w.seek_rel(+50_000)
        w.frame_step(-100)
        w.seek(0)
        QTest.keyClick(w.centralWidget(), Qt.Key_End)
        QTest.keyClick(w.centralWidget(), Qt.Key_Home)
        self.assertEqual(self.seeks, [10_000, 20_000, 10_000, 10_000, 20_000, 10_000])

    def test_playhead_outside_new_interval_snaps_to_start(self):
        self.fake_video()
        w = self.w
        w.player.position = lambda: 50_000
        w.set_interval(10_000, 20_000)
        self.assertEqual(self.seeks, [10_000])

    def test_set_points_from_current_time(self):
        self.fake_video()
        w = self.w
        w.player.position = lambda: 12_345
        w.release_focus()
        QTest.keyClick(w.centralWidget(), Qt.Key_BracketLeft)
        w.player.position = lambda: 40_000
        QTest.keyClick(w.centralWidget(), Qt.Key_BracketRight)
        self.assertEqual((w.iv_start, w.iv_end), (12_345, 40_000))

    def test_time_edits_set_interval(self):
        self.fake_video()
        w = self.w
        w.start_edit.setTime(A.ms_to_qtime(5000))
        w.end_edit.setTime(A.ms_to_qtime(7000))
        self.assertEqual((w.iv_start, w.iv_end), (5000, 7000))

    def test_timestamps_outside_interval_are_greyed_and_not_jumped_to(self):
        self.fake_video()
        w = self.w
        inside = w.add_entry(15_000, w.persons[0], "says", "in")
        outside = w.add_entry(45_000, w.persons[0], "says", "out")
        w.set_interval(10_000, 20_000)
        self.assertFalse(inside._outside)
        self.assertTrue(outside._outside)
        self.assertEqual(outside.stamp.toolTip(), "00:45.000\nOutside the interval")
        self.seeks.clear()
        outside.stamp.click()
        self.assertEqual(self.seeks, [])
        self.assertIn("outside", w.statusBar().currentMessage())
        inside.stamp.click()
        self.assertEqual(self.seeks, [15_000])
        w.set_interval(0, 60_000)
        self.assertFalse(outside._outside)

    def test_dragging_bar_handles_sets_interval_and_previews_frame(self):
        self.fake_video()
        w = self.w
        bar = w.bar
        w.set_interval(10_000, 20_000)
        self.seeks.clear()

        def mouse(etype, x):
            pos = QPointF(x, bar.height() / 2)
            buttons = Qt.NoButton if etype == QEvent.MouseButtonRelease else Qt.LeftButton
            QApplication.sendEvent(bar, QMouseEvent(etype, pos, bar.mapToGlobal(pos),
                                                    Qt.LeftButton, buttons, Qt.NoModifier))

        mouse(QEvent.MouseButtonPress, bar.x_of(10_000))
        mouse(QEvent.MouseMove, bar.x_of(5_000))
        mouse(QEvent.MouseButtonRelease, bar.x_of(5_000))
        self.assertAlmostEqual(w.iv_start, 5_000, delta=200)
        self.assertEqual(w.iv_end, 20_000)
        self.assertAlmostEqual(self.seeks[-1], 5_000, delta=200)   # shows the frame at the handle

        mouse(QEvent.MouseButtonPress, bar.x_of(20_000))
        mouse(QEvent.MouseMove, bar.x_of(30_000))
        mouse(QEvent.MouseButtonRelease, bar.x_of(30_000))
        self.assertAlmostEqual(w.iv_end, 30_000, delta=200)

        self.seeks.clear()
        mouse(QEvent.MouseButtonPress, bar.x_of(50_000))   # click outside: nearest edge
        mouse(QEvent.MouseButtonRelease, bar.x_of(50_000))
        self.assertEqual(self.seeks, [w.iv_end])

    def test_bar_pixel_mapping_round_trips(self):
        self.fake_video(3_600_000)
        bar = self.w.bar
        for ms in (0, 1_234_567, 3_600_000):
            self.assertAlmostEqual(bar.ms_of(bar.x_of(ms)), ms, delta=3_600_000 / bar.width())


# ----------------------------------------------------------------- real playback

@unittest.skipUnless(SAMPLE_VIDEO.exists(), "demo/sample.mp4 missing")
class PlaybackTests(WindowTestCase):
    def setUp(self):
        super().setUp()
        self.load(self.w)

    def load(self, w):
        w.open_video(str(SAMPLE_VIDEO))
        self.assertTrue(wait_until(lambda: w.duration > 0), "video did not load")

    def record_shown_positions(self, ms):
        """What the user sees: the playhead on the interval bar and the timeline."""
        seen = []
        record = lambda _pos: seen.append((self.w.bar.pos, self.w.timeline.value()))
        self.w.player.positionChanged.connect(record)
        QTest.qWait(ms)
        self.w.player.positionChanged.disconnect(record)
        return seen

    def export_and_wait(self, out, start, end, subtitles=True):
        w = self.w
        with mock.patch.object(A.QMessageBox, "warning") as warn:
            self.assertTrue(w.export_video(str(out), start, end, subtitles))
            self.assertTrue(wait_until(lambda: w.export_job["ok"] is not None, 60_000), "export never finished")
        warn.assert_not_called()
        return w.export_job["ok"]

    def frame_of(self, video, at_s):
        import subprocess
        png = Path(str(video) + ".png")
        subprocess.run([A.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-ss", str(at_s),
                        "-i", str(video), "-frames:v", "1", str(png)], check=True)
        from PySide6.QtGui import QImage
        return QImage(str(png))

    def test_video_clip_export_with_burnt_in_text(self):
        import imageio_ffmpeg
        w = self.w
        e = w.persons[0]
        w.set_origin(e, 5)                                   # middle, over a busy part of the picture
        w.add_entry(1000, e, "says", "MIDTEN MIDTEN").entry.end_ms = 3000
        out = self.tmpdir / "clip.mp4"
        self.assertTrue(self.export_and_wait(out, 1000, 3000))
        self.assertTrue(out.exists())
        self.assertFalse(out.with_name("clip.part.mp4").exists())
        frames, secs = imageio_ffmpeg.count_frames_and_secs(str(out))
        self.assertAlmostEqual(secs, 2.0, delta=0.15)
        plain = self.tmpdir / "plain.mp4"
        self.assertTrue(self.export_and_wait(plain, 1000, 3000, subtitles=False))
        a, b = self.frame_of(out, 1.0), self.frame_of(plain, 1.0)
        target = A.QColor(e.color)

        def near(img):   # pixels close to the person's colour around the middle
            n = 0
            for x in range(img.width() // 4, img.width() * 3 // 4, 2):
                for y in range(img.height() * 2 // 5, img.height() * 3 // 5, 2):
                    c = img.pixelColor(x, y)
                    n += abs(c.red() - target.red()) + abs(c.green() - target.green()) \
                        + abs(c.blue() - target.blue()) < 60
            return n

        self.assertGreater(near(a), near(b) + 20)

    def test_cancelling_an_export_leaves_no_file(self):
        w = self.w
        out = self.tmpdir / "cancel.mp4"
        self.assertTrue(w.export_video(str(out), 0, 29_000, True))
        w.export_job["progress"].canceled.emit()        # as if you clicked Cancel
        self.assertTrue(wait_until(lambda: w.export_job["ok"] is not None, 20_000))
        self.assertFalse(w.export_job["ok"])
        self.assertFalse(out.exists())
        self.assertFalse(out.with_name("cancel.part.mp4").exists())

    def test_markers_follow_real_playback(self):
        w = self.w
        for t in (1000, 2000, 3000, 6000):
            w.add_entry(t, w.persons[0], "says", str(t))
        w.release_focus()
        w.seek(1500)
        self.assertTrue(wait_until(lambda: abs(w.player.position() - 1500) < 100))
        w.toggle_play()
        self.assertTrue(wait_until(lambda: w.now_markers.span is not None and w.player.position() > 2200))
        texts = [r.entry.text for r in w.now_markers.span[2]]
        self.assertIn("2000", texts)
        self.assertNotIn("6000", texts)
        w.player.pause()
        self.assertTrue(wait_until(lambda: w.now_markers.span is None))

    def test_video_frames_are_drawn_with_text_on_top(self):
        w = self.w
        w.resize(1300, 800)
        self.assertTrue(wait_until(lambda: w.video.frame.isValid()), "no video frame arrived")
        w.seek(3000)
        self.assertTrue(wait_until(lambda: abs(w.player.position() - 3000) < 100))
        QTest.qWait(200)
        e = w.add_entry(2000, w.persons[1], "says", "Hej med dig")
        e.entry.end_ms = 5000
        w.set_video_text(False)
        plain = w.video.grab().toImage()
        r = w.video.video_rect().toRect()
        centre = plain.pixelColor(r.center())
        self.assertNotEqual(centre.name(), "#000000")           # a real frame, not black
        w.set_video_text(True)
        with_text = w.video.grab().toImage()
        self.assertNotEqual(plain, with_text)
        target = A.QColor(w.persons[1].color)

        def in_person_colour(img):
            n = 0
            for x in range(r.left(), r.right(), 2):
                for y in range(int(r.bottom() - r.height() * 0.2), r.bottom(), 2):
                    c = img.pixelColor(x, y)
                    if abs(c.red() - target.red()) + abs(c.green() - target.green()) \
                            + abs(c.blue() - target.blue()) < 40:
                        n += 1
            return n

        self.assertGreater(in_person_colour(with_text), in_person_colour(plain) + 20)

    def test_loop_stays_inside_interval(self):
        w = self.w
        w.set_interval(2000, 3000)
        w.loop_cb.setChecked(True)
        w.toggle_play()
        seen = self.record_shown_positions(2600)
        self.assertEqual(w.player.playbackState(), PLAYING)
        self.assertGreater(len(seen), 10)
        bar_positions = [b for b, _ in seen]
        for bar_pos, slider_pos in seen:
            self.assertTrue(2000 - 50 <= bar_pos <= 3000 + 200, bar_pos)
            self.assertTrue(2000 <= slider_pos <= 3000, slider_pos)
        self.assertTrue(any(b < a for a, b in zip(bar_positions, bar_positions[1:])), "never looped back")
        self.assertTrue(2000 - 50 <= w.player.position() <= 3000 + 200)

    def test_first_play_after_load_starts_inside_interval(self):
        # Regression: seeks issued before the media finished loading were dropped,
        # so the first play started at 0 and could stall.
        w = self.w
        w.set_interval(1000, 5000)
        raw = []
        w.player.positionChanged.connect(raw.append)
        w.toggle_play()
        self.assertTrue(wait_until(lambda: w.player.position() > 1400, 3000), "playback stalled")
        self.assertGreaterEqual(min(raw), 1000 - 50, raw[:10])

    def test_stops_at_interval_end_then_restarts_from_start(self):
        w = self.w
        w.set_interval(1000, 1800)
        w.toggle_play()
        self.assertTrue(wait_until(lambda: w.player.playbackState() != PLAYING, 4000))
        self.assertTrue(wait_until(lambda: w.player.position() == 1800, 2000))   # parked at the end
        QTest.qWait(100)   # let the backend finish that seek, as a person pressing play would
        w.toggle_play()
        self.assertTrue(wait_until(lambda: 1000 <= w.player.position() < 1500, 2000))

    def test_interval_is_remembered_per_video(self):
        self.w.set_interval(4000, 9000)
        w2 = self.make_window()
        self.load(w2)
        self.assertEqual((w2.iv_start, w2.iv_end), (4000, 9000))
        self.assertTrue(wait_until(lambda: 4000 <= w2.player.position() <= 9000, 2000))

    def test_annotating_pauses_and_enter_resumes(self):
        w = self.w
        w.set_interval(5000, 15_000)
        w.toggle_play()
        self.assertTrue(wait_until(lambda: w.player.position() > 5300))
        self.activate(w)
        QTest.keyClick(w.centralWidget(), Qt.Key_1)
        self.assertTrue(wait_until(lambda: w.player.playbackState() != PLAYING, 1000))
        row = w.rows[0]
        self.assertTrue(5000 <= row.entry.time_ms <= 15_000)
        QTest.keyClicks(row.edit, "hello")
        QTest.keyClick(row.edit, Qt.Key_Return)
        self.assertTrue(wait_until(lambda: w.player.playbackState() == PLAYING, 1000))
        self.assertEqual(row.entry.text, "hello")

    def test_clicking_timestamp_plays_from_there(self):
        w = self.w
        row = w.add_entry(7000, w.persons[0], "says", "x")
        row.stamp.click()
        self.assertTrue(wait_until(lambda: w.player.playbackState() == PLAYING, 2000))
        self.assertTrue(wait_until(lambda: 7000 <= w.player.position() < 8000, 2000))

    def test_frame_step_moves_one_frame(self):
        w = self.w
        w.seek(10_000)
        self.assertTrue(wait_until(lambda: w.player.position() == 10_000, 2000))
        w.frame_step(1)
        self.assertTrue(wait_until(lambda: w.player.position() == 10_040, 2000))


if __name__ == "__main__":
    unittest.main()
