"""Exercise clock and simplified day/night for Fulda."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from backend.config import FULDA_OFFSET_MS, FULDA_SUN_LON, MINUTE_MS

MONTH_ABBR = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")

# 1989-09-19 08:00 Fulda wall = UTC 06:00
INITIAL_SIM_INSTANT_MS = int(datetime(1989, 9, 19, 6, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)


@dataclass
class TimebarStrings:
    main: str
    daynight: str
    sun_line: str
    is_day: bool


class ExerciseClock:
    def __init__(self, sim_ms: int | None = None) -> None:
        self.sim_ms = sim_ms if sim_ms is not None else INITIAL_SIM_INSTANT_MS

    def advance_minutes(self, minutes: int = 1) -> None:
        self.sim_ms += int(minutes) * MINUTE_MS

    @staticmethod
    def format_duration(ms: int) -> str:
        if ms < 0:
            ms = 0
        total_min = ms // MINUTE_MS
        if total_min < 60:
            return f"{total_min} min"
        hh = total_min // 60
        mm = total_min % 60
        if mm == 0:
            return f"{hh} hr"
        return f"{hh} hr {mm} min"

    def wall_parts(self) -> tuple[int, int, int, int, int]:
        wall = self.sim_ms + FULDA_OFFSET_MS
        d = datetime.fromtimestamp(wall / 1000, tz=timezone.utc)
        return d.year, d.month, d.day, d.hour, d.minute

    def format_main_line(self) -> str:
        return self.format_main_line_at(self.sim_ms)

    @staticmethod
    def format_main_line_at(sim_ms: int) -> str:
        wall = sim_ms + FULDA_OFFSET_MS
        d = datetime.fromtimestamp(wall / 1000, tz=timezone.utc)
        yy = d.year % 100
        return f"{d.hour:02d}{d.minute:02d} {d.day}{MONTH_ABBR[d.month - 1]}{yy:02d}"

    def _solar_times_approx(self, y: int, mo: int, day: int) -> tuple[int, int]:
        """Approximate sunrise/sunset UTC ms for Fulda (September typical)."""
        # Simple seasonal approximation — adequate for movement day/night flag
        d = date(y, mo, day)
        doy = d.timetuple().tm_yday
        # Fulda ~50.55°N: summer sunrise ~04:30 UTC, winter ~07:30 UTC wall-adjusted
        base_sunrise_h = 5.5 + 2.0 * abs(172 - doy) / 172
        base_sunset_h = 20.0 - 2.0 * abs(172 - doy) / 172
        noon_ref = datetime(y, mo, day, 12, 0, 0, tzinfo=timezone.utc)
        sunrise = noon_ref.replace(hour=int(base_sunrise_h), minute=int((base_sunrise_h % 1) * 60))
        sunset = noon_ref.replace(hour=int(base_sunset_h), minute=int((base_sunset_h % 1) * 60))
        return int(sunrise.timestamp() * 1000), int(sunset.timestamp() * 1000)

    def is_day(self) -> bool:
        y, mo, day, _, _ = self.wall_parts()
        rise, set_ = self._solar_times_approx(y, mo, day)
        return rise <= self.sim_ms < set_

    def timebar_strings(self) -> TimebarStrings:
        main = self.format_main_line()
        y, mo, day, _, _ = self.wall_parts()
        rise, set_ = self._solar_times_approx(y, mo, day)
        is_day = self.is_day()
        rise_h = datetime.fromtimestamp(rise / 1000, tz=timezone.utc)
        set_h = datetime.fromtimestamp(set_ / 1000, tz=timezone.utc)
        sunrise_clock = f"{rise_h.hour:02d}:{rise_h.minute:02d}"
        sunset_clock = f"{set_h.hour:02d}:{set_h.minute:02d}"
        if self.sim_ms < rise:
            label, dur = "Sunrise in", rise - self.sim_ms
        elif self.sim_ms < set_:
            label, dur = "Sunset in", set_ - self.sim_ms
        else:
            label = "Sunrise in"
            tomorrow = date(y, mo, day) + timedelta(days=1)
            rise2, _ = self._solar_times_approx(tomorrow.year, tomorrow.month, tomorrow.day)
            dur = rise2 - self.sim_ms
        total_min = max(0, dur // 60_000)
        hh, mm = total_min // 60, total_min % 60
        sun_line = f"Sunrise {sunrise_clock} · Sunset {sunset_clock} · {label} {hh:02d}:{mm:02d}"
        return TimebarStrings(main, "Day" if is_day else "Night", sun_line, is_day)
