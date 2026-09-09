"""Bench'siz, HTTP API orqali JE to'lovlarini invoice'larga bog'lash.

Serverga SSH/bench kirish bo'lmaganda ishlatiladi. UI'dagi Payment Reconciliation
bilan aynan bir xil qadamlar: get_unreconciled_entries -> allocate_entries -> reconcile.
Har mijoz alohida so'rov (alohida tranzaksiya), qayta yurgizish xavfsiz.

    python3 reconcile_via_api.py --url http://SERVER:8000 --user Administrator --password '...' targets
    python3 reconcile_via_api.py --url ... --user ... --password ... dry
    python3 reconcile_via_api.py --url ... --user ... --password ... real
    python3 reconcile_via_api.py --url ... --user ... --password ... verify "Mijoz nomi"

Faqat `requests` kutubxonasi kerak (bench env'da bor: ~/frappe-bench/env/bin/python).
"""
import argparse, json, sys, time
from collections import defaultdict

import requests

ap = argparse.ArgumentParser()
ap.add_argument("mode", choices=["targets", "dry", "real", "verify"])
ap.add_argument("party", nargs="?", default="Isfandiyor Megastar (D8)")
ap.add_argument("--url", required=True)
ap.add_argument("--user", default="Administrator")
ap.add_argument("--password", required=True)
ap.add_argument("--party-type", default="Customer")
ap.add_argument("--timeout", type=int, default=900)
ap.add_argument("--log", default="reconcile_api.log")
A = ap.parse_args()
BASE = A.url.rstrip("/")
LOG = open(A.log, "a")


def log(*parts):
    line = " ".join(str(p) for p in parts)
    print(line, flush=True)
    LOG.write(line + "\n"); LOG.flush()


s = requests.Session()
r = s.post(f"{BASE}/api/method/login", data={"usr": A.user, "pwd": A.password}, timeout=30)
if r.status_code != 200:
    sys.exit(f"login xato: {r.status_code} {r.text[:200]}")


def get_list(doctype, filters, fields, parent=None, order_by=None, limit=0):
    p = {"doctype": doctype, "filters": json.dumps(filters), "fields": json.dumps(fields), "limit_page_length": limit}
    if parent:
        p["parent"] = parent
    if order_by:
        p["order_by"] = order_by
    r = s.get(f"{BASE}/api/method/frappe.client.get_list", params=p, timeout=A.timeout)
    if r.status_code != 200:
        raise RuntimeError(f"get_list {doctype}: {r.status_code} {r.text[:300]}")
    return r.json()["message"]


def run_doc_method(doc, method, args=None):
    data = {"docs": json.dumps(doc), "method": method}
    if args is not None:
        data["args"] = json.dumps(args)
    r = s.post(f"{BASE}/api/method/run_doc_method", data=data, timeout=A.timeout)
    if r.status_code != 200:
        body = r.text
        try:
            j = r.json()
            body = (j.get("exception") or "") + " " + str(json.loads(j.get("_server_messages") or "[]"))[:300]
        except Exception:
            pass
        raise RuntimeError(f"{method}: HTTP {r.status_code} {body[:500]}")
    j = r.json()
    return j["docs"][0], j.get("message")


def gl_net(party):
    rows = get_list("GL Entry", [["party", "=", party], ["is_cancelled", "=", 0]],
                    ["debit_in_account_currency", "credit_in_account_currency"])
    return round(sum(r["debit_in_account_currency"] - r["credit_in_account_currency"] for r in rows), 2)


if A.mode == "verify":
    si = get_list("Sales Invoice", [["customer", "=", A.party], ["docstatus", "=", 1]],
                  ["name", "posting_date", "grand_total", "outstanding_amount", "status"], order_by="posting_date asc")
    log(f"\n== {A.party} ==")
    for x in si:
        log(f"  {x['posting_date']} {x['name']} grand {x['grand_total']} outstanding {x['outstanding_amount']} {x['status']}")
    je = get_list("Journal Entry Account", [["party", "=", A.party]],
                  ["parent", "debit_in_account_currency", "credit_in_account_currency", "reference_type", "reference_name"], parent="Journal Entry")
    for x in je:
        log(f"  JE {x['parent']} dr {x['debit_in_account_currency']} cr {x['credit_in_account_currency']} -> {x['reference_type']} {x['reference_name']}")
    log(f"  invoice outstanding jami: {round(sum(x['outstanding_amount'] for x in si), 2)} | GL (debet-kredit): {gl_net(A.party)}")
    sys.exit()

# ---------- targets ----------
receivable = "Receivable" if A.party_type == "Customer" else "Payable"
credit_field, debit_field = ("credit_in_account_currency", "debit_in_account_currency")
if receivable == "Payable":
    credit_field, debit_field = debit_field, credit_field
companies = {c["name"]: c for c in get_list(
    "Company", [], ["name", "book_advance_payments_in_separate_party_account",
                    "default_advance_received_account", "default_advance_paid_account"])}
accounts = {a["name"]: a for a in get_list("Account", [["account_type", "=", receivable]], ["name", "account_type"])}
jes = {j["name"]: j["company"] for j in get_list("Journal Entry", [["docstatus", "=", 1]], ["name", "company"])}
rows = get_list("Journal Entry Account",
                [["party_type", "=", A.party_type], ["reference_name", "is", "not set"], [credit_field, ">", 0]],
                ["parent", "party", "account", credit_field, debit_field], parent="Journal Entry")
targets = defaultdict(lambda: {"amount": 0.0, "rows": 0})
for row in rows:
    company = jes.get(row["parent"])
    if not company or row["account"] not in accounts:
        continue
    amt = float(row[credit_field]) - float(row[debit_field])
    if amt <= 0.005:
        continue
    t = targets[(company, row["party"], row["account"])]
    t["amount"] += amt; t["rows"] += 1
targets = sorted(targets.items())
log(f"\n=== {A.mode.upper()} {time.strftime('%Y-%m-%d %H:%M')} === reference'siz JE to'lovi bor {A.party_type}: {len(targets)}, "
    f"qatorlar: {sum(t['rows'] for _, t in targets)}, summa: {sum(t['amount'] for _, t in targets):,.2f}")
if A.mode == "targets":
    for (co, party, acc), t in targets[:30]:
        log(f"  {party} | {acc} | {t['rows']} ta | {t['amount']:.2f}")
    sys.exit()

# ---------- reconcile per party ----------
adv_field = "default_advance_received_account" if A.party_type == "Customer" else "default_advance_paid_account"
tot = {"parties": 0, "reconciled": 0, "nothing": 0, "failed": 0, "allocations": 0, "amount": 0.0,
       "remaining_outstanding": 0.0, "remaining_payments": 0.0, "failures": []}
for idx, ((co, party, acc), t) in enumerate(targets, 1):
    tot["parties"] += 1
    comp = companies[co]
    doc = {"doctype": "Payment Reconciliation", "name": "Payment Reconciliation", "company": co,
           "party_type": A.party_type, "party": party, "receivable_payable_account": acc,
           "default_advance_account": comp[adv_field] if comp["book_advance_payments_in_separate_party_account"] else None,
           "invoice_limit": 0, "payment_limit": 0, "payments": [], "invoices": [], "allocation": []}
    label = f"[{idx}/{len(targets)}] {party}"
    try:
        doc, _ = run_doc_method(doc, "get_unreconciled_entries")
        payments = [p for p in doc.get("payments", []) if p.get("reference_type") == "Journal Entry"]
        invoices = doc.get("invoices", [])
        if not payments or not invoices:
            tot["nothing"] += 1
            tot["remaining_outstanding"] += sum(float(i.get("outstanding_amount") or 0) for i in invoices)
            tot["remaining_payments"] += sum(float(p.get("amount") or 0) for p in payments)
            log(f"{label}: to'lov {len(payments)} / qarz {len(invoices)} -> ish yo'q")
            continue
        doc, _ = run_doc_method(doc, "allocate_entries", {"payments": payments, "invoices": invoices})
        alloc = [a for a in doc.get("allocation", []) if float(a.get("allocated_amount") or 0)]
        if not alloc:
            tot["nothing"] += 1; log(f"{label}: allocation bo'sh"); continue
        amount = sum(float(a["allocated_amount"]) for a in alloc)
        if A.mode == "real":
            doc, _ = run_doc_method(doc, "reconcile")
            rem_out = sum(float(i.get("outstanding_amount") or 0) for i in doc.get("invoices", []))
            rem_pay = sum(float(p.get("amount") or 0) for p in doc.get("payments", []) if p.get("reference_type") == "Journal Entry")
        else:
            rem_out = max(sum(float(i.get("outstanding_amount") or 0) for i in invoices) - amount, 0)
            rem_pay = max(sum(float(p.get("amount") or 0) for p in payments) - amount, 0)
        tot["reconciled"] += 1; tot["allocations"] += len(alloc); tot["amount"] += amount
        tot["remaining_outstanding"] += rem_out; tot["remaining_payments"] += rem_pay
        log(f"{label}: to'lov {len(payments)} / qarz {len(invoices)} -> bog'landi {len(alloc)} ta, {amount:,.2f}; "
            f"qoldi qarz {rem_out:,.2f}, to'lov {rem_pay:,.2f}")
    except Exception as e:
        tot["failed"] += 1; tot["failures"].append((party, str(e)[:300]))
        log(f"{label}: XATO {str(e)[:300]}")

log(f"\n=== YAKUN ({A.mode}) === mijozlar {tot['parties']} | bog'landi {tot['reconciled']} | ish yo'q {tot['nothing']} | xato {tot['failed']}")
log(f"bog'lanishlar {tot['allocations']} ta, summa {tot['amount']:,.2f} | qolgan qarz {tot['remaining_outstanding']:,.2f} | "
    f"bog'lanmagan to'lov {tot['remaining_payments']:,.2f}")
for p, e in tot["failures"]:
    log("  XATO:", p, e)
if A.mode == "dry":
    log("DRY: hech narsa saqlanmadi. Haqiqiy bog'lash: mode=real")
s.get(f"{BASE}/api/method/logout", timeout=10)
