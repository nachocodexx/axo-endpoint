from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from axo_control_plane.domain.errors import InvalidError

# A person's settings: how the platform shows things to them (language,
# time zone, date / number formats, appearance, default ranges). Saved by
# the Control Plane, one document per person; a field they never set
# follows the platform default (``PLATFORM_DEFAULTS``, which the Platform
# Admin can change). Times are always stored in UTC and only *shown* in the
# person's time zone; billing never uses it (bills count on one fixed clock,
# UTC -- see ``application/storage_metering.py``).


@dataclass(frozen=True)
class Setting:
    name: str
    default: Any
    choices: Optional[Tuple[str, ...]] = None     # None = checked by ``_check_<name>``
    group: str = "region"
    title: str = ""
    help: str = ""

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"name": self.name, "group": self.group, "title": self.title,
                               "help": self.help, "default": self.default}
        if self.choices is not None:
            out["choices"] = list(self.choices)
        return out


DATE_FORMATS = ("MM/DD/YYYY", "DD/MM/YYYY", "YYYY-MM-DD", "MMM D, YYYY", "D MMM YYYY")
NUMBER_FORMATS = ("1,234.56", "1.234,56", "1 234,56", "1234.56")
RANGES = ("1h", "24h", "7d", "30d")

SETTINGS: Tuple[Setting, ...] = (
    Setting("language", "en", ("en",), "region", "Language",
            "The language of the pages (more are coming)."),
    Setting("time_zone", "America/Mexico_City", None, "region", "Time zone",
            "Every date and time is shown in this zone."),
    Setting("date_format", "MM/DD/YYYY", DATE_FORMATS, "region", "Date format"),
    Setting("clock", "12h", ("12h", "24h"), "region", "Clock", "2:05 PM or 14:05."),
    Setting("number_format", "1,234.56", NUMBER_FORMATS, "region", "Number format",
            "How thousands and decimals are written, money included."),
    Setting("first_day_of_week", "sunday", ("sunday", "monday"), "region", "First day of the week",
            "Where weeks start, e.g. in the \"when jobs run\" chart."),
    Setting("theme", "light", ("light", "dark", "system"), "appearance", "Theme",
            "\"System\" follows your computer's light / dark setting."),
    Setting("font_size", "normal", ("small", "normal", "large"), "appearance", "Text size"),
    Setting("density", "comfortable", ("comfortable", "compact"), "appearance", "Table spacing",
            "Compact fits more rows on the screen."),
    Setting("chart_range", "24h", RANGES, "defaults", "Chart time range",
            "The period charts open with."),
    Setting("activity_window", "24h", RANGES, "defaults", "Activity period",
            "How far back activity lists open with."),
)
BY_NAME: Dict[str, Setting] = {s.name: s for s in SETTINGS}
PLATFORM_DEFAULTS: Dict[str, Any] = {s.name: s.default for s in SETTINGS}


def is_time_zone(value: Any) -> bool:
    """An IANA time zone name this machine knows (e.g. America/Mexico_City)."""
    return isinstance(value, str) and value in _zones()


def _check_time_zone(value: Any) -> str:
    if not is_time_zone(value):
        raise InvalidError(f"{value!r} isn't a known time zone (e.g. America/Mexico_City, Europe/Madrid, UTC)",
                           code="SETTINGS_INVALID", details={"setting": "time_zone"})
    return value


_ZONES: Optional[frozenset] = None


def _zones() -> frozenset:
    global _ZONES
    if _ZONES is None:
        _ZONES = frozenset(available_timezones()) | {"UTC"}
    return _ZONES


def time_zones() -> list:
    return sorted(_zones())


def validate(changes: Mapping[str, Any]) -> Dict[str, Any]:
    """Checks a change of settings. A ``None`` value means "back to the
    default" and is kept as ``None``; unknown names are refused."""
    out: Dict[str, Any] = {}
    for name, value in changes.items():
        setting = BY_NAME.get(name)
        if setting is None:
            raise InvalidError(f"there's no setting {name!r}", code="SETTINGS_INVALID",
                               details={"setting": name, "known": sorted(BY_NAME)})
        if value is None:
            out[name] = None
        elif setting.choices is not None:
            if value not in setting.choices:
                raise InvalidError(f"{name} must be one of {', '.join(setting.choices)}", code="SETTINGS_INVALID",
                                   details={"setting": name, "choices": list(setting.choices)})
            out[name] = value
        else:
            out[name] = globals()[f"_check_{name}"](value)
    return out


def effective(own: Mapping[str, Any], defaults: Mapping[str, Any]) -> Dict[str, Any]:
    """What applies: the person's own value, else the platform's, else the built-in one."""
    return {s.name: own.get(s.name) if own.get(s.name) is not None
            else defaults.get(s.name, s.default) for s in SETTINGS}


# ── writing values the way a person's settings say ──────────────────────────
# Used for the text of notifications (the UI writes them again from their
# ``fields``, so a later change of settings shows there too).
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _zone(settings: Mapping[str, Any]):
    try:
        return ZoneInfo(settings.get("time_zone") or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def format_date(ts: float, settings: Mapping[str, Any]) -> str:
    dt = datetime.fromtimestamp(ts, tz=_zone(settings))
    fmt = settings.get("date_format") or "MM/DD/YYYY"
    d, m, y = dt.day, dt.month, dt.year
    return {
        "DD/MM/YYYY": f"{d:02d}/{m:02d}/{y}",
        "YYYY-MM-DD": f"{y}-{m:02d}-{d:02d}",
        "MMM D, YYYY": f"{_MONTHS[m - 1]} {d}, {y}",
        "D MMM YYYY": f"{d} {_MONTHS[m - 1]} {y}",
    }.get(fmt, f"{m:02d}/{d:02d}/{y}")


def format_time(ts: float, settings: Mapping[str, Any]) -> str:
    dt = datetime.fromtimestamp(ts, tz=_zone(settings))
    if settings.get("clock") == "24h":
        return f"{dt.hour:02d}:{dt.minute:02d}"
    return f"{(dt.hour % 12) or 12}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"


def format_number(value: float, settings: Mapping[str, Any], decimals: int = 2) -> str:
    text = f"{value:,.{decimals}f}"                          # 1,234.56
    fmt = settings.get("number_format") or "1,234.56"
    group, point = {"1.234,56": (".", ","), "1 234,56": (" ", ","), "1234.56": ("", ".")}.get(fmt, (",", "."))
    return text.replace(",", "\0").replace(".", point).replace("\0", group)


def format_money(cents: int, settings: Mapping[str, Any]) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}${format_number(abs(int(cents)) / 100, settings)}"


def format_field(field: Mapping[str, Any], settings: Mapping[str, Any]) -> str:
    """One placeholder of a notification: ``{"type": "date" | "datetime" |
    "money", "value": ...}``."""
    kind, value = field.get("type"), field.get("value")
    if value is None:
        return "?"
    if kind == "date":
        return format_date(float(value), settings)
    if kind == "datetime":
        return f"{format_date(float(value), settings)} {format_time(float(value), settings)}"
    if kind == "money":
        return format_money(int(value), settings)
    return str(value)


def render(template: str, fields: Mapping[str, Mapping[str, Any]], settings: Mapping[str, Any]) -> str:
    """``"{ve} renews on {renews_at}"`` with each ``{name}`` written per the settings."""
    out = template
    for name, field in fields.items():
        out = out.replace("{" + name + "}", format_field(field, settings))
    return out


def date(ts: Optional[float]) -> Dict[str, Any]:
    return {"type": "date", "value": ts}


def money(cents: int) -> Dict[str, Any]:
    return {"type": "money", "value": int(cents)}
