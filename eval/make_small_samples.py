"""Small quick-test files in eval/sample_docs/small/ (made-up data, like the other samples):

- smaller.pdf          2 pages of text: a warranty and service policy, for lookup questions
- store_sales.xlsx     one sheet, 12 rows: Store, City, Month, Revenue, Orders
- store_sales.sql      the same 12 rows as a SQLite script, plus check queries with the
                       expected results, to verify the app's calculated answers.
                       (Evidex does not upload .sql files; this is a reference to run yourself.)

    backend\\.venv\\Scripts\\python eval\\make_small_samples.py
"""

from __future__ import annotations

from pathlib import Path

OUT = Path(__file__).resolve().parent / "sample_docs" / "small"

POLICY = [
    ("Warranty and Service Policy", None),
    ("1. Warranty period", [
        "Clause 1.1. Electronics sold by Kestrel Retail carry a warranty of 24 months from the date of purchase.",
        "Clause 1.2. Furniture carries a warranty of 12 months. Clothing and footwear carry no warranty.",
        "Clause 1.3. The warranty card or the original receipt is needed for every claim.",
    ]),
    ("2. Repairs", [
        "Clause 2.1. Repairs under warranty are free of charge and are completed within 10 working days.",
        "Clause 2.2. If a repair takes longer than 10 working days, the customer receives a replacement product.",
    ]),
    ("3. Service centres", [
        "Clause 3.1. Products are repaired at the service centre in Pune or the service centre in Chennai.",
        "Clause 3.2. Service centres are open from 10:00 to 18:00, Monday to Saturday.",
    ]),
    ("4. Extended warranty", [
        "Clause 4.1. Customers may buy an extended warranty of 12 more months for INR 1,499 within 30 days of "
        "purchase.",
        "Clause 4.2. The extended warranty is not available for products that cost less than INR 5,000.",
    ]),
]

ROWS = [  # Store, City, Month, Revenue, Orders
    ("KR-S01", "Mumbai", "2026-09", 845000, 412),
    ("KR-S01", "Mumbai", "2026-10", 910500, 455),
    ("KR-S02", "Pune", "2026-09", 402300, 230),
    ("KR-S02", "Pune", "2026-10", 455800, 251),
    ("KR-S03", "Chennai", "2026-09", 612400, 318),
    ("KR-S03", "Chennai", "2026-10", 598900, 309),
    ("KR-S04", "Bengaluru", "2026-09", 733100, 366),
    ("KR-S04", "Bengaluru", "2026-10", 801250, 392),
    ("KR-S05", "Delhi", "2026-09", 958700, 470),
    ("KR-S05", "Delhi", "2026-10", 1012300, 498),
    ("KR-S06", "Jaipur", "2026-09", 288600, 175),
    ("KR-S06", "Jaipur", "2026-10", 301450, 181),
]
COLUMNS = ["Store", "City", "Month", "Revenue", "Orders"]

# Check queries: question to ask in Evidex, the SQL, and the expected result (computed below).
CHECKS = [
    ("What is the total Revenue for the City Delhi?",
     "SELECT SUM(\"Revenue\") AS total_revenue FROM store_sales WHERE \"City\" = 'Delhi'"),
    ("How many Orders were placed in total in Month 2026-10?",
     "SELECT SUM(\"Orders\") AS total_orders FROM store_sales WHERE \"Month\" = '2026-10'"),
    ("Which City had the highest total Revenue?",
     "SELECT \"City\", SUM(\"Revenue\") AS total_revenue FROM store_sales GROUP BY \"City\" "
     "ORDER BY total_revenue DESC LIMIT 1"),
    ("What is the average Revenue per row for the City Pune?",
     "SELECT AVG(\"Revenue\") AS average_revenue FROM store_sales WHERE \"City\" = 'Pune'"),
]


def make_pdf(path: Path) -> None:
    from reportlab import rl_config
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

    rl_config.invariant = 1
    st = getSampleStyleSheet()
    story = []
    for i, (heading, paras) in enumerate(POLICY):
        if i == 3:
            story.append(PageBreak())  # sections 3 and 4 on page 2
        story.append(Paragraph(heading, st["Title"] if paras is None else st["Heading2"]))
        for p in paras or []:
            story += [Paragraph(p, st["BodyText"]), Spacer(1, 6)]
    SimpleDocTemplate(str(path), pagesize=A4, title="Warranty and Service Policy", author="Kestrel Retail").build(story)


def make_xlsx(path: Path) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Sales"
    ws.append(COLUMNS)
    for r in ROWS:
        ws.append(list(r))
    wb.properties.title = "Store Sales"
    wb.save(str(path))


def expected_results() -> list[tuple[str, str, list[tuple]]]:
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.executescript(sql_script(with_checks=False))
    return [(q, sql, conn.execute(sql).fetchall()) for q, sql in CHECKS]


def sql_script(with_checks: bool = True) -> str:
    lines = [
        "-- store_sales.sql: the same 12 rows as store_sales.xlsx (sheet Sales), for SQLite.",
        "-- Evidex does not upload .sql files. Run this to check the app's calculated answers:",
        "--   sqlite3 :memory: < store_sales.sql      (or open it in DB Browser for SQLite)",
        "",
        "DROP TABLE IF EXISTS store_sales;",
        'CREATE TABLE store_sales ("Store" TEXT, "City" TEXT, "Month" TEXT, "Revenue" INTEGER, "Orders" INTEGER);',
        "INSERT INTO store_sales VALUES",
    ]
    vals = [f"  ('{s}', '{c}', '{m}', {rev}, {o})" for s, c, m, rev, o in ROWS]
    lines.append(",\n".join(vals) + ";")
    if with_checks:
        lines += ["", "-- Check queries. Ask the question in Evidex after uploading store_sales.xlsx;",
                  "-- the answer (and the SQL it shows) should match the expected result."]
        for q, sql, rows in expected_results():
            lines += ["", f"-- Question: {q}", f"-- Expected: {rows}", sql + ";"]
    return "\n".join(lines) + "\n"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    make_pdf(OUT / "smaller.pdf")
    make_xlsx(OUT / "store_sales.xlsx")
    (OUT / "store_sales.sql").write_text(sql_script(), encoding="utf-8")
    for p in sorted(OUT.iterdir()):
        print(f"{p.name}  ({p.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
