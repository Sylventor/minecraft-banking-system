"""UTF-8 spreadsheet-safe exports, independent of database/application configuration."""

import csv
import io
from typing import Any

from starlette.responses import Response


def safe_csv(headers: list[str], data: list[list[Any]]) -> Response:
    def cell(value: Any) -> str:
        text = str(value) if value is not None else ""
        return (
            "'" + text
            if text.startswith(("\t", "\r", "\n")) or text.lstrip().startswith(("=", "+", "-", "@"))
            else text
        )

    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows([[cell(v) for v in row] for row in data])
    return Response(
        "\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="horizon-business.csv"', "Cache-Control": "no-store"},
    )
