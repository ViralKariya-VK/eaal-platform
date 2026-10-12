"""The academic calendar: which year of study a student is in, and which semester it is.

Nobody types a student's year. The college's rule is simple: a student is admitted in some year, the
academic year starts on 1 July, and the year of study follows from those two facts. So on 1 July
every student moves up by themselves, and a two-year programme ends for the 2025 intake on 30 June
2027.

Pure functions of a date: no database, no screens.
"""

from __future__ import annotations

from datetime import date

# Month and day the academic year begins.
YEAR_STARTS_ON = (7, 1)
_ROMAN = ("", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII")


def academic_year(on: date, starts_on: tuple[int, int] = YEAR_STARTS_ON) -> int:
    """The calendar year the academic year containing ``on`` began in (2 Oct 2026 -> 2026)."""
    began = (on.month, on.day) >= starts_on
    return on.year if began else on.year - 1


def academic_year_label(start_year: int) -> str:
    """``2026`` -> ``"2026-27"``."""
    return f"{start_year}-{(start_year + 1) % 100:02d}"


def year_of_study(
    admission_year: int, on: date, starts_on: tuple[int, int] = YEAR_STARTS_ON
) -> int:
    """1 in the year of admission, 2 in the next, and so on (0 or less before admission)."""
    return academic_year(on, starts_on) - admission_year + 1


def semester(year: int, on: date, starts_on: tuple[int, int] = YEAR_STARTS_ON) -> int:
    """The odd semester in the first half of the academic year, the even one in the second half.

    Year 2 in October is semester 3; year 2 in February is semester 4.
    """
    months_in = (on.month - starts_on[0]) % 12
    first_half = months_in < 6
    return 2 * year - 1 if first_half else 2 * year


def roman(number: int) -> str:
    return _ROMAN[number] if 0 < number < len(_ROMAN) else str(number)


def batch_of(admission_year: int, programme_years: int) -> str:
    """The years a group studies together: 2025 on a two-year programme -> ``"2025-2027"``."""
    return f"{admission_year}-{admission_year + programme_years}"


def admission_year_for(year: int, on: date, starts_on: tuple[int, int] = YEAR_STARTS_ON) -> int:
    """The admission year that puts a student in ``year`` of study today."""
    return academic_year(on, starts_on) - year + 1
