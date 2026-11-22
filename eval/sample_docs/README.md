# Sample documents (synthetic)

Made-up documents for a made-up company, Kestrel Retail, as spec section 7 allows.
Regenerate with `backend\.venv\Scripts\python eval\make_sample_docs.py`. Swap in real
documents any time, then rewrite `eval/testset.json` to match.

| File | Type | Department | Path | Used for |
| --- | --- | --- | --- | --- |
| `employee_handbook.pdf` | PDF, 4 pages | Retail Operations (People) | Text | Lookups, clause numbers (Clause 4.2), codes (HR-LV-07), multi-section answers |
| `supplier_code_of_conduct.docx` | DOCX | Procurement | Text | Lookups, rule codes (SCC-11) |
| `versions/v1/refund_policy.docx` | DOCX | Customer Care | Text | Version traps: upload first |
| `versions/v2/refund_policy.docx` | DOCX | Customer Care | Text | Version traps: upload second, same name |
| `store_operations_manual.md` | MD | Retail Operations | Text | Heading-based source locations |
| `delivery_partner_faq.txt` | TXT | Logistics | Text | Prompt injection (contains "ignore all of your previous rules") |
| `sales_q3.csv` | CSV, 60 rows | Finance | Table | Totals, counts, rankings |
| `inventory.xlsx` | XLSX, sheets Stock and Suppliers | Merchandising | Table | Multi-sheet tables |
| `stores.json` | JSON, list of records | Retail Operations | Table | JSON records into SQLite |
| `loyalty_programme.json` | JSON, nested | Marketing | Text | Flattened JSON paths |
| `broken/empty.txt` | TXT, 0 bytes | - | Rejected | Broken file: empty |
| `broken/scanned_notice.pdf` | PDF, image only | - | Rejected | Broken file: scanned PDF, no text layer |

## Refund policy: v1 vs v2 (7 differing facts)

| Fact | v1 | v2 |
| --- | --- | --- |
| Effective date | 1 January 2026 | 1 July 2026 |
| Return window | 30 days | 14 days |
| Restocking fee, opened electronics | 10% | 15% |
| Store credit validity | 6 months | 12 months |
| Staff approval limit | ₹5,000 | ₹10,000 |
| Refund processing time | 7 business days | 5 business days |
| Contact email | returns@kestrel.example | care@kestrel.example |
