"""Kontragent GL (buxgalteriya) ledger'i — POS "Klient Sverka" jadvali uchun.

Hujjatlar (Sales Invoice / Payment Entry) emas, aynan GL Entry asosida: shuning
uchun Journal Entry orqali qilingan to'lovlar, boshlang'ich qoldiqlar, chek
ichidagi POS to'lovlari, write-off va qaytimlar — hammasi hisobga olinadi.
Har bir hujjat (voucher) bitta qator: debet/kredit yig'indisi.

Chaqirish:
    extra.api.customer_ledger.get_party_ledger
    args: party_type (Customer|Supplier), party, company (ixtiyoriy),
          from_date / to_date (ixtiyoriy; from_date bo'lsa "opening" qator qaytadi)

Javob (summalar kontragent hisobi valyutasida):
    {
      "currency": "USD", "party_type": ..., "party": ..., "company": ...,
      "opening_balance": 0.0,
      "rows": [{"date","ts","voucher_type","type","reference","debit","credit","balance","status"}, ...],
      "final_balance": 12.34      # + = kontragent qarzdor, - = avans (Customer uchun)
    }
"""

from collections import Counter

import frappe
from frappe import _
from frappe.utils import flt, getdate

import erpnext
from erpnext.accounts.party import get_party_account_currency


@frappe.whitelist()
def get_party_ledger(party_type, party, company=None, from_date=None, to_date=None):
    if party_type not in ("Customer", "Supplier"):
        frappe.throw(_("party_type must be Customer or Supplier"))
    if not party:
        frappe.throw(_("party is required"))
    if not frappe.has_permission(party_type, "read", doc=party):
        frappe.throw(_("Not permitted"), frappe.PermissionError)

    # Customer: qarz = debet - kredit (musbat = qarzdor). Supplier: teskari.
    sign = 1 if erpnext.get_party_account_type(party_type) == "Receivable" else -1

    conditions = ["gl.party_type = %(party_type)s", "gl.party = %(party)s", "gl.is_cancelled = 0"]
    values = {"party_type": party_type, "party": party}
    if company:
        conditions.append("gl.company = %(company)s")
        values["company"] = company
    if to_date:
        conditions.append("gl.posting_date <= %(to_date)s")
        values["to_date"] = to_date

    rows = frappe.db.sql(
        f"""
        SELECT gl.posting_date, gl.voucher_type, gl.voucher_no, gl.account_currency,
               SUM(gl.debit_in_account_currency) AS debit,
               SUM(gl.credit_in_account_currency) AS credit,
               MIN(gl.creation) AS creation
        FROM `tabGL Entry` gl
        WHERE {' AND '.join(conditions)}
        GROUP BY gl.voucher_type, gl.voucher_no, gl.account_currency
        ORDER BY gl.posting_date ASC, MIN(gl.creation) ASC
        """,
        values,
        as_dict=True,
    )

    currency = None
    if rows:
        currency = Counter(r.account_currency for r in rows if r.account_currency).most_common(1)[0][0]
    if not currency:
        if not company:
            company = (
                frappe.defaults.get_user_default("Company")
                or frappe.db.get_single_value("Global Defaults", "default_company")
            )
        if company:
            currency = get_party_account_currency(party_type, party, company) or erpnext.get_company_currency(company)
    rows = [r for r in rows if r.account_currency == currency]

    opening_balance = 0.0
    if from_date:
        start = getdate(from_date)
        before = [r for r in rows if getdate(r.posting_date) < start]
        rows = [r for r in rows if getdate(r.posting_date) >= start]
        opening_balance = flt(sum(sign * (flt(r.debit) - flt(r.credit)) for r in before), 2)

    labels, statuses = _describe_vouchers(rows, party_type)

    out_rows = []
    balance = opening_balance
    for r in rows:
        debit, credit = flt(r.debit, 2), flt(r.credit, 2)
        balance = flt(balance + sign * (flt(r.debit) - flt(r.credit)), 2)
        key = (r.voucher_type, r.voucher_no)
        out_rows.append({
            "date": str(r.posting_date),
            "ts": str(r.creation or ""),
            "voucher_type": r.voucher_type,
            "type": labels.get(key, r.voucher_type),
            "reference": r.voucher_no,
            "debit": debit,
            "credit": credit,
            "balance": balance,
            "status": statuses.get(key, "Paid"),
        })

    return {
        "currency": currency,
        "party_type": party_type,
        "party": party,
        "company": company,
        "opening_balance": opening_balance,
        "rows": out_rows,
        "final_balance": balance,
    }


def _describe_vouchers(rows, party_type):
    """Har bir voucher uchun ko'rinadigan nom (type) va holat (Paid/Partial/Unpaid)."""
    by_type = {}
    for r in rows:
        by_type.setdefault(r.voucher_type, set()).add(r.voucher_no)

    labels, statuses = {}, {}

    for doctype, credit_label in (("Sales Invoice", "Credit Note"), ("Purchase Invoice", "Debit Note")):
        names = list(by_type.get(doctype, ()))
        if not names:
            continue
        for inv in frappe.get_all(
            doctype, filters={"name": ["in", names]},
            fields=[
                "name", "is_return", "grand_total", "base_grand_total", "currency",
                "party_account_currency", "outstanding_amount", "docstatus",
            ],
        ):
            key = (doctype, inv.name)
            labels[key] = credit_label if inv.is_return else doctype
            statuses[key] = _invoice_status(inv)

    names = list(by_type.get("Payment Entry", ()))
    if names:
        for pe in frappe.get_all("Payment Entry", filters={"name": ["in", names]}, fields=["name", "mode_of_payment"]):
            labels[("Payment Entry", pe.name)] = (
                f"Payment Entry ({pe.mode_of_payment})" if pe.mode_of_payment else "Payment Entry"
            )
            statuses[("Payment Entry", pe.name)] = "Paid"

    names = list(by_type.get("Journal Entry", ()))
    if names:
        for je in frappe.get_all("Journal Entry", filters={"name": ["in", names]}, fields=["name", "voucher_type"]):
            labels[("Journal Entry", je.name)] = (
                f"Journal Entry ({je.voucher_type})" if je.voucher_type and je.voucher_type != "Journal Entry"
                else "Journal Entry"
            )
        # JE qarz (Opening Entry va h.k.): Payment Entry orqali qancha yopilgani
        allocated = dict(frappe.db.sql(
            """
            SELECT per.reference_name, SUM(per.allocated_amount)
            FROM `tabPayment Entry Reference` per
            INNER JOIN `tabPayment Entry` pe ON pe.name = per.parent
            WHERE pe.docstatus = 1 AND per.reference_doctype = 'Journal Entry'
              AND per.reference_name IN %(names)s
            GROUP BY per.reference_name
            """,
            {"names": names},
        ))
        sign = 1 if erpnext.get_party_account_type(party_type) == "Receivable" else -1
        for r in rows:
            if r.voucher_type != "Journal Entry":
                continue
            debt = sign * (flt(r.debit) - flt(r.credit))
            if debt <= 0.005:
                statuses[("Journal Entry", r.voucher_no)] = "Paid"
                continue
            remaining = debt - flt(allocated.get(r.voucher_no))
            if remaining <= 0.005:
                statuses[("Journal Entry", r.voucher_no)] = "Paid"
            elif remaining < debt - 0.005:
                statuses[("Journal Entry", r.voucher_no)] = "Partial"
            else:
                statuses[("Journal Entry", r.voucher_no)] = "Unpaid"

    return labels, statuses


def _invoice_status(inv):
    if inv.docstatus == 2:
        return "Cancelled"
    # outstanding_amount — party account (Debtors) valyutasida. Chek valyutasi
    # (UZS) undan farq qilsa, jami bilan solishtirish uchun base_grand_total
    # (kompaniya/Debtors valyutasi, USD) olinadi. Aks holda 44.85 USD qoldiq
    # 533 654 UZS jamidan "kichik" bo'lib, to'liq to'lanmagan chek ham
    # "Partial" chiqardi (2026-09-08).
    outstanding = abs(flt(inv.outstanding_amount))
    if inv.get("party_account_currency") and inv.get("currency") and inv.party_account_currency != inv.currency:
        grand_total = abs(flt(inv.base_grand_total))
    else:
        grand_total = abs(flt(inv.grand_total))
    if outstanding <= 0.005:
        return "Paid"
    if grand_total > 0.005 and outstanding < grand_total - 0.005:
        return "Partial"
    return "Unpaid"
