-- store_sales.sql: the same 12 rows as store_sales.xlsx (sheet Sales), for SQLite.
-- Evidex does not upload .sql files. Run this to check the app's calculated answers:
--   sqlite3 :memory: < store_sales.sql      (or open it in DB Browser for SQLite)

DROP TABLE IF EXISTS store_sales;
CREATE TABLE store_sales ("Store" TEXT, "City" TEXT, "Month" TEXT, "Revenue" INTEGER, "Orders" INTEGER);
INSERT INTO store_sales VALUES
  ('KR-S01', 'Mumbai', '2026-09', 845000, 412),
  ('KR-S01', 'Mumbai', '2026-10', 910500, 455),
  ('KR-S02', 'Pune', '2026-09', 402300, 230),
  ('KR-S02', 'Pune', '2026-10', 455800, 251),
  ('KR-S03', 'Chennai', '2026-09', 612400, 318),
  ('KR-S03', 'Chennai', '2026-10', 598900, 309),
  ('KR-S04', 'Bengaluru', '2026-09', 733100, 366),
  ('KR-S04', 'Bengaluru', '2026-10', 801250, 392),
  ('KR-S05', 'Delhi', '2026-09', 958700, 470),
  ('KR-S05', 'Delhi', '2026-10', 1012300, 498),
  ('KR-S06', 'Jaipur', '2026-09', 288600, 175),
  ('KR-S06', 'Jaipur', '2026-10', 301450, 181);

-- Check queries. Ask the question in Evidex after uploading store_sales.xlsx;
-- the answer (and the SQL it shows) should match the expected result.

-- Question: What is the total Revenue for the City Delhi?
-- Expected: [(1971000,)]
SELECT SUM("Revenue") AS total_revenue FROM store_sales WHERE "City" = 'Delhi';

-- Question: How many Orders were placed in total in Month 2026-10?
-- Expected: [(2086,)]
SELECT SUM("Orders") AS total_orders FROM store_sales WHERE "Month" = '2026-10';

-- Question: Which City had the highest total Revenue?
-- Expected: [('Delhi', 1971000)]
SELECT "City", SUM("Revenue") AS total_revenue FROM store_sales GROUP BY "City" ORDER BY total_revenue DESC LIMIT 1;

-- Question: What is the average Revenue per row for the City Pune?
-- Expected: [(429050.0,)]
SELECT AVG("Revenue") AS average_revenue FROM store_sales WHERE "City" = 'Pune';
