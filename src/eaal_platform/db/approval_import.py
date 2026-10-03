"""Reading a CSV or Excel file of people into rows for ``approvals.import_entries``.

The expected layout is one header row, then one person per row:

    teachers:  name, email
    students:  name, email, enrollment_no

Column names are matched loosely ("E-mail", "Full name", "Roll no" ...), the
order doesn't matter, and extra columns are ignored. A file without a header
row also works if each row contains an email address.
"""

from __future__ import annotations

import csv
import io
from typing import Any

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_ROWS = 5000

_NAME = {"name", "full name", "fullname", "student name", "teacher name", "professor name",
         "display name"}  # fmt: skip
_EMAIL = {"email", "e-mail", "email address", "email id", "mail", "university email",
          "student email", "teacher email", "professor email"}  # fmt: skip
_ENROLLMENT = {"enrollment_no", "enrollment no", "enrollment", "enrollment number",
               "enrolment_no", "enrolment no", "enrolment", "enrolment number",
               "roll no", "roll number", "roll", "student id", "id"}  # fmt: skip

TEMPLATES = {
    "student": (
        "name,email,enrollment_no\n"
        "Asha Patel,asha.patel@university.edu,BSC-101\n"
        "Ravi Kumar,ravi.kumar@university.edu,BSC-102\n"
    ),
    "professor": (
        "name,email\n"
        "Dr. Meera Shah,meera.shah@university.edu\n"
        "Dr. Arjun Rao,arjun.rao@university.edu\n"
    ),
}


class UploadError(ValueError):
    """The file can't be read; the message tells the administrator what to do."""


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))  # Excel stores 101 as 101.0
    return str(value).strip()


def _csv_rows(data: bytes) -> list[list[str]]:
    for encoding in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeError:
            continue
    else:  # pragma: no cover - cp1252 decodes almost anything
        raise UploadError("That file's text encoding isn't supported. Save it as CSV (UTF-8).")
    first_line = text.splitlines()[0] if text.strip() else ""
    delimiter = max((",", ";", "\t"), key=first_line.count)
    return [
        [_text(cell) for cell in row] for row in csv.reader(io.StringIO(text), delimiter=delimiter)
    ]


def _xlsx_rows(data: bytes) -> list[list[str]]:
    try:
        from openpyxl import load_workbook
    except ImportError:  # pragma: no cover - installed with the server extra
        raise UploadError("Excel files aren't supported here. Save the file as CSV.") from None
    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        sheet = workbook.worksheets[0]
        return [[_text(cell) for cell in row] for row in sheet.iter_rows(values_only=True)]
    except Exception as exc:
        raise UploadError("That Excel file couldn't be read. Is it a valid .xlsx file?") from exc


def read_rows(filename: str, data: bytes, role: str) -> list[dict[str, Any]]:
    """Parse an uploaded file into ``[{row, name, email, enrollment_no}, ...]``."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadError("That file is too large (the limit is 5 MB).")
    if not data:
        raise UploadError("That file is empty.")
    lower = filename.lower()
    if lower.endswith(".xlsx") or lower.endswith(".xlsm"):
        grid = _xlsx_rows(data)
    elif lower.endswith(".xls"):
        raise UploadError("Old .xls files aren't supported. Save it as .xlsx or .csv.")
    elif lower.endswith((".csv", ".txt", ".tsv")):
        grid = _csv_rows(data)
    else:
        raise UploadError("Upload a .csv or .xlsx file.")

    grid = [row for row in grid if any(cell for cell in row)]
    if not grid:
        raise UploadError("That file has no rows.")

    header = [cell.lower().strip() for cell in grid[0]]
    has_header = any(cell in _EMAIL for cell in header)
    if has_header:
        body, first_number = grid[1:], 2
        name_col = next((i for i, h in enumerate(header) if h in _NAME), None)
        email_col = next(i for i, h in enumerate(header) if h in _EMAIL)
        enr_col = next((i for i, h in enumerate(header) if h in _ENROLLMENT), None)
    elif any("@" in cell for cell in grid[0]):
        # No header row: find the email in each row; the other cells are name, then enrolment.
        body, first_number = grid, 1
        name_col = enr_col = None
        email_col = -1
    else:
        raise UploadError(
            "Couldn't find an Email column. The first row must name the columns "
            "(name, email" + (", enrollment_no" if role == "student" else "") + "). "
            "Download the template to see the layout."
        )
    if len(body) > MAX_ROWS:
        raise UploadError(f"That file has more than {MAX_ROWS} rows. Split it into smaller files.")

    rows: list[dict[str, Any]] = []
    for offset, cells in enumerate(body):
        if email_col >= 0:
            email = cells[email_col] if email_col < len(cells) else ""
            name = cells[name_col] if name_col is not None and name_col < len(cells) else ""
            enrollment = cells[enr_col] if enr_col is not None and enr_col < len(cells) else ""
        else:
            at = next((i for i, c in enumerate(cells) if "@" in c), None)
            email = cells[at] if at is not None else ""
            others = [c for i, c in enumerate(cells) if i != at and c]
            name = others[0] if others else ""
            enrollment = others[1] if len(others) > 1 else ""
        rows.append(
            {
                "row": first_number + offset,
                "name": name,
                "email": email,
                "enrollment_no": enrollment if role == "student" else "",
            }
        )
    return rows
