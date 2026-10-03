"""The applications table as an Excel workbook.

Every text cell comes from email content or the LLM, so it is written as an
explicit string. Left to itself, openpyxl stores a value starting with ``=``
as a formula, which would let a hostile email run one in the user's Excel.
"""

from collections.abc import Sequence
from datetime import datetime
from io import BytesIO
from typing import Final

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from apply_agent.domain import stage_of
from apply_agent.storage.repository import ApplicationView

XLSX_MEDIA_TYPE: Final = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
HEADERS: Final = (
    "Company",
    "Role",
    "Stage",
    "Status",
    "Set by hand",
    "Latest",
    "First seen",
    "Last news",
    "Days since last news",
)
_WIDTHS: Final = (28, 36, 12, 22, 12, 70, 12, 12, 12)
_DATE_FORMAT: Final = "yyyy-mm-dd"


def _text(sheet: Worksheet, row: int, column: int, value: str) -> None:
    cell = sheet.cell(row=row, column=column, value=value)
    cell.data_type = "s"


def applications_workbook(views: Sequence[ApplicationView], now: datetime) -> bytes:
    workbook = Workbook(write_only=False)
    workbook.remove(workbook.worksheets[0])
    sheet = workbook.create_sheet("Applications")
    sheet.append(HEADERS)
    for cell in sheet[1]:
        cell.font = Font(bold=True)

    for row, view in enumerate(views, start=2):
        app = view.application
        texts = (
            app.company,
            app.role or "",
            stage_of(app.status).value,
            app.status.value.replace("_", " "),
            "yes" if app.status_overridden else "",
            view.latest_summary,
        )
        for column, value in enumerate(texts, start=1):
            _text(sheet, row, column, value)
        for column, when in ((7, app.first_seen_at), (8, app.last_activity_at)):
            cell = sheet.cell(row=row, column=column, value=when.date())
            cell.number_format = _DATE_FORMAT
        sheet.cell(row=row, column=9, value=max((now - app.last_activity_at).days, 0))

    for column, width in enumerate(_WIDTHS, start=1):
        sheet.column_dimensions[get_column_letter(column)].width = width
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
