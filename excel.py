"""Excel-отчёт за период: итоги, табель, по дням, оплаты, долги."""
from collections import defaultdict
from datetime import date
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from core import WEEKDAYS_SHORT, db, period_title, price

BOLD = Font(bold=True)
HEADER_FILL = PatternFill("solid", fgColor="DDEBF7")
MONEY = "# ##0"


def _header(ws, row: int, values: list):
    for col, v in enumerate(values, 1):
        c = ws.cell(row=row, column=col, value=v)
        c.font = BOLD
        c.fill = HEADER_FILL
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _widths(ws, widths):
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _bold_last_row(ws):
    for c in ws[ws.max_row]:
        c.font = BOLD


def build_excel(d1: str, d2: str) -> bytes:
    p = price()
    orders = db.period_orders(d1, d2)
    wb = Workbook()

    # --- Итоги ---
    ws = wb.active
    ws.title = "Итоги"
    ws["A1"] = f"Обеды за {period_title(d1, d2)}"
    ws["A1"].font = Font(bold=True, size=14)
    _header(ws, 3, ["№", "Сотрудник", "Дней", "Порций", "Начислено, сум"])
    stats = db.stats(d1, d2, p)
    for i, r in enumerate(stats, 1):
        ws.append([i, r["name"], r["days"], r["portions"], r["charged"] or 0])
    ws.append([None, "Итого", sum(r["days"] for r in stats), sum(r["portions"] for r in stats),
               sum(r["charged"] or 0 for r in stats)])
    _bold_last_row(ws)
    for row in ws.iter_rows(min_row=4, min_col=5, max_col=5):
        row[0].number_format = MONEY
    _widths(ws, (5, 32, 8, 9, 16))
    ws.freeze_panes = "A4"

    # --- Табель: сотрудники × дни ---
    ws = wb.create_sheet("Табель")
    days = sorted({o["day"] for o in orders})
    grid: dict[str, dict[str, int]] = defaultdict(dict)
    for o in orders:
        grid[o["name"]][o["day"]] = o["portions"]
    _header(ws, 1, ["Сотрудник"] + [f"{date.fromisoformat(d):%d.%m}\n{WEEKDAYS_SHORT[date.fromisoformat(d).weekday()]}"
                                    for d in days] + ["Итого"])
    for name in sorted(grid, key=str.lower):
        ws.append([name] + [grid[name].get(d) for d in days] + [sum(grid[name].values())])
    ws.append(["Порций за день"] + [sum(grid[n].get(d, 0) for n in grid) for d in days]
              + [sum(o["portions"] for o in orders)])
    _bold_last_row(ws)
    _widths(ws, [32] + [7] * (len(days) + 1))
    for row in ws.iter_rows(min_row=2, min_col=2):
        for c in row:
            c.alignment = Alignment(horizontal="center")
    ws.row_dimensions[1].height = 30
    ws.freeze_panes = "B2"

    # --- По дням ---
    ws = wb.create_sheet("По дням")
    _header(ws, 1, ["Дата", "День", "Порций", "Меню", "Кто обедал"])
    by_day: dict[str, list] = defaultdict(list)
    for o in orders:
        by_day[o["day"]].append(o)
    for d in days:
        rows = by_day[d]
        names = ", ".join(o["name"] + (f" ×{o['portions']}" if o["portions"] > 1 else "") for o in rows)
        dd = date.fromisoformat(d)
        ws.append([f"{dd:%d.%m.%Y}", WEEKDAYS_SHORT[dd.weekday()], sum(o["portions"] for o in rows),
                   (db.get_menu(d) or "").replace("\n", "; "), names])
    _widths(ws, (12, 6, 9, 40, 90))
    ws.freeze_panes = "A2"

    # --- Оплаты за период ---
    ws = wb.create_sheet("Оплаты")
    _header(ws, 1, ["Дата", "Сотрудник", "Сумма, сум", "Способ"])
    payments = db.period_payments(d1, d2)
    for pay in payments:
        ws.append([pay["created_at"][:16].replace("T", " "), pay["name"], pay["amount"],
                   pay["note"] or ("перевод (скриншот)" if pay["file_id"] else "")])
    ws.append([None, "Итого", sum(pay["amount"] for pay in payments)])
    _bold_last_row(ws)
    for row in ws.iter_rows(min_row=2, min_col=3, max_col=3):
        row[0].number_format = MONEY
    _widths(ws, (17, 32, 14, 22))
    ws.freeze_panes = "A2"

    # --- Долги на сегодня (за всё время) ---
    ws = wb.create_sheet("Долги")
    _header(ws, 1, ["Сотрудник", "Начислено всего", "Оплачено всего", "Долг (+) / переплата (−)"])
    balances = db.balances(p)
    for b in balances:
        ws.append([b["name"], b["charged"], b["paid"], b["debt"]])
    ws.append(["Итого", sum(b["charged"] for b in balances), sum(b["paid"] for b in balances),
               sum(b["debt"] for b in balances)])
    _bold_last_row(ws)
    for row in ws.iter_rows(min_row=2, min_col=2, max_col=4):
        for c in row:
            c.number_format = MONEY
    _widths(ws, (32, 17, 17, 24))
    ws.freeze_panes = "A2"

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
