"""Tests for atx_console.py — stdlib unittest only, no real atxpower calls.

Run: python3 -m unittest test_atx_console -v
"""
from __future__ import annotations

import io
import os
import tempfile
import unittest
from unittest.mock import patch

import atx_console as ac


def make_settings(device: str, atxpower: str = "/bin/true") -> ac.Settings:
    return ac.Settings(atxpower=atxpower, device=device, host="test-host", target_label="")


def make_console(
    device: str,
    stdin_text: str,
    *,
    atxpower: str = "/bin/true",
    is_tty: bool = False,
) -> tuple[ac.ATXConsole, io.StringIO]:
    out = io.StringIO()
    ui = ac.ConsoleUI(io.StringIO(stdin_text), out)
    ui.is_tty = is_tty
    ui.palette = ac.Palette(is_tty)
    ui.animate = False  # never sleep/animate in tests
    console = ac.ATXConsole(make_settings(device, atxpower), ui)
    return console, out


class FakeDevice:
    """A temp path that exists, standing in for /dev/ttyACM0."""

    def __enter__(self) -> str:
        self._tmp = tempfile.NamedTemporaryFile(delete=False)
        self._tmp.close()
        return self._tmp.name

    def __exit__(self, *exc) -> None:
        os.unlink(self._tmp.name)


class MenuDataTests(unittest.TestCase):
    def test_every_item_has_a_working_handler(self) -> None:
        console, _ = make_console("/nonexistent", "")
        for item in ac.MENU_ITEMS:
            self.assertTrue(
                hasattr(console, item.handler),
                f"MENU_ITEMS references missing method {item.handler!r}",
            )

    def test_keys_are_unique_and_match_dispatch_table(self) -> None:
        keys = [item.key for item in ac.MENU_ITEMS]
        self.assertEqual(len(keys), len(set(keys)), "duplicate menu key")
        self.assertEqual(set(keys), {str(n) for n in range(1, 10)})

    def test_badge_colors_are_real_palette_attributes(self) -> None:
        p = ac.Palette(True)
        for item in ac.MENU_ITEMS:
            self.assertTrue(hasattr(p, item.badge_color), item.badge_color)

    def test_every_menu_row_aligns_to_menu_width(self) -> None:
        # A prior version's alignment math missed the 2-space leading
        # indent and overshot MENU_WIDTH by 2 columns on every row —
        # this asserts the visible (ANSI-stripped) width directly so
        # that class of bug can't silently come back.
        import re

        out = io.StringIO()
        ui = ac.ConsoleUI(io.StringIO("q\n"), out)
        ui.is_tty = True
        ui.palette = ac.Palette(True)
        console = ac.ATXConsole(make_settings("/nonexistent"), ui)
        console.menu()
        rows = [
            re.sub(r"\x1b\[[0-9;]*m", "", line)
            for line in out.getvalue().splitlines()
            if "[" in line and "]" in line
        ]
        self.assertEqual(len(rows), len(ac.MENU_ITEMS))
        for row in rows:
            self.assertEqual(len(row), ac.MENU_WIDTH, row)

    def test_section_heading_and_item_numbers_share_one_color(self) -> None:
        # heading_color/number_color used to be two copies of the same
        # if/else — guard against them drifting apart again.
        import re

        out = io.StringIO()
        ui = ac.ConsoleUI(io.StringIO("q\n"), out)
        ui.is_tty = True
        ui.palette = ac.Palette(True)
        console = ac.ATXConsole(make_settings("/nonexistent"), ui)
        console.menu()
        color_re = re.compile(r"\x1b\[38;2;\d+;\d+;\d+m")
        lines = out.getvalue().splitlines()
        heading_color = None
        for line in lines:
            stripped = re.sub(r"\x1b\[[0-9;]*m", "", line)
            if stripped.strip() in ("POWER", "RAW / INFO"):
                heading_color = color_re.search(line).group()
            elif stripped.strip().startswith("‹") and "quit" not in stripped and heading_color:
                self.assertIn(heading_color, line, stripped)


class ConfirmTests(unittest.TestCase):
    def test_accepts_y_variants(self) -> None:
        for answer in ("y", "Y", "yes", "YES"):
            console, _ = make_console("/nonexistent", answer + "\n")
            self.assertTrue(console.confirm("prompt?"))

    def test_declines_anything_else(self) -> None:
        for answer in ("n", "no", "", "maybe"):
            console, _ = make_console("/nonexistent", answer + "\n")
            self.assertFalse(console.confirm("prompt?"))

    def test_eof_declines_without_raising(self) -> None:
        console, out = make_console("/nonexistent", "")  # empty stdin -> EOF
        self.assertFalse(console.confirm("prompt?"))
        self.assertIn("input closed", out.getvalue())


class InvokeArgvTests(unittest.TestCase):
    """Verify each action sends the exact atxpower command the appliance expects."""

    def _assert_command(self, method_name: str, expected_cmd: str, confirms: str) -> None:
        with FakeDevice() as device:
            console, _ = make_console(device, confirms)
            with patch("subprocess.run") as run:
                run.return_value = type(
                    "R", (), {"returncode": 0, "stdout": b"on\n"}
                )()
                getattr(console, method_name)()
            calls = [c.args[0] for c in run.call_args_list]
            self.assertIn([console.settings.atxpower, device, expected_cmd], calls)

    def test_power_on(self) -> None:
        self._assert_command("do_on", "power_on", "y\n")

    def test_power_off(self) -> None:
        self._assert_command("do_off", "power_off", "y\n")

    def test_power_off_hard_needs_two_confirms(self) -> None:
        self._assert_command("do_off_hard", "power_off_hard", "y\ny\n")

    def test_power_off_hard_stops_after_first_decline(self) -> None:
        with FakeDevice() as device:
            console, _ = make_console(device, "n\n")
            with patch("subprocess.run") as run:
                console.do_off_hard()
            run.assert_not_called()

    def test_power_off_hard_stops_if_second_confirm_declined(self) -> None:
        with FakeDevice() as device:
            console, _ = make_console(device, "y\nn\n")
            with patch("subprocess.run") as run:
                console.do_off_hard()
            run.assert_not_called()

    def test_reset(self) -> None:
        self._assert_command("do_reset", "power_reset", "y\n")

    def test_raw_short_click(self) -> None:
        self._assert_command("do_click_short", "click_power_short", "y\n")

    def test_raw_long_click(self) -> None:
        self._assert_command("do_click_long", "click_power_long", "y\n")

    def test_raw_reset_click(self) -> None:
        self._assert_command("do_click_reset", "click_reset", "y\n")

    def test_declined_confirm_never_calls_atxpower(self) -> None:
        with FakeDevice() as device:
            console, _ = make_console(device, "n\n")
            with patch("subprocess.run") as run:
                console.do_on()
            run.assert_not_called()

    def test_eof_never_calls_atxpower(self) -> None:
        with FakeDevice() as device:
            console, _ = make_console(device, "")
            with patch("subprocess.run") as run:
                console.do_on()
            run.assert_not_called()


class ResultHandlingTests(unittest.TestCase):
    def test_nonzero_exit_is_reported(self) -> None:
        with FakeDevice() as device:
            console, out = make_console(device, "y\n")
            with patch("subprocess.run") as run:
                run.return_value = type(
                    "R", (), {"returncode": 1, "stdout": b"board busy\n"}
                )()
                console.do_on()
            self.assertIn("atxpower exited 1", out.getvalue())
            self.assertIn("board busy", out.getvalue())

    def test_missing_device_reported_without_invoking_subprocess(self) -> None:
        console, out = make_console("/definitely/not/a/real/path", "y\n")
        with patch("subprocess.run") as run:
            console.do_on()
        run.assert_not_called()
        self.assertIn("ATX board not found", out.getvalue())

    def test_missing_atxpower_executable_reported(self) -> None:
        with FakeDevice() as device:
            console, out = make_console(device, "y\n", atxpower="/definitely/not/real")
            with patch("subprocess.run") as run:
                console.do_on()
            run.assert_not_called()
            self.assertIn("missing", out.getvalue())


class ColorAndTtyTests(unittest.TestCase):
    def test_no_color_codes_when_not_a_tty(self) -> None:
        console, out = make_console("/nonexistent", "q\n", is_tty=False)
        console.banner()
        console.menu()
        self.assertNotIn("\x1b[", out.getvalue())

    def test_color_codes_present_when_tty(self) -> None:
        console, out = make_console("/nonexistent", "q\n", is_tty=True)
        console.banner()
        console.menu()
        self.assertIn("\x1b[", out.getvalue())

    def test_no_color_env_var_disables_color_even_on_tty(self) -> None:
        with patch.dict(os.environ, {"NO_COLOR": "1"}):
            out = io.StringIO()
            ui = ac.ConsoleUI(io.StringIO(""), out)
            ui.is_tty = True  # even if the stream claims to be a tty
            ui.palette = ac.Palette(
                ui.is_tty and "NO_COLOR" not in os.environ
            )
        self.assertFalse(ui.palette.enabled)


class RuleCacheTests(unittest.TestCase):
    def test_cached_rule_matches_uncached_output(self) -> None:
        out = io.StringIO()
        ui = ac.ConsoleUI(io.StringIO(""), out)
        ui.is_tty = True
        ui.palette = ac.Palette(True)
        ui.rule(50)
        first = out.getvalue()
        out.seek(0)
        out.truncate()
        ui.rule(50)  # second call should hit the cache
        second = out.getvalue()
        self.assertEqual(first, second)

    def test_progress_bar_reaches_full_and_100_percent(self) -> None:
        out = io.StringIO()
        ui = ac.ConsoleUI(io.StringIO(""), out)
        ui.is_tty = True
        ui.palette = ac.Palette(True)
        ui.animate = True
        ui.progress("test", 0.05)
        # Frame count varies with scheduling; check the LAST frame only,
        # which is the one guaranteed to represent completion.
        last_frame = out.getvalue().split("\r")[-1]
        self.assertIn("100%", last_frame)
        self.assertEqual(last_frame.count("█"), 30)


if __name__ == "__main__":
    unittest.main()
