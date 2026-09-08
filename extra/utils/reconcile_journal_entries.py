"""Reference'siz Journal Entry to'lovlarini invoice'larga bog'lash (Payment Reconciliation).

Muammo: tashqi tizimdan (masalan "Biznes") import qilinganda mijoz to'lovlari
Journal Entry (Cash Entry) bo'lib kirgan, lekin Sales Invoice'ga bog'lanmagan.
GL (buxgalteriya) to'g'ri, ammo invoice `outstanding_amount` kamaymagan, shuning
uchun POS va boshqa hujjat-asosli ekranlar mijozni hali ham qarzdor deb ko'rsatadi.

Bu skript ERPNext'ning o'z Payment Reconciliation mantiqidan foydalanadi (UI'dagi
"Payment Reconciliation" bilan bir xil natija): har bir kontragent uchun
reference'siz JE to'lovlarini sana tartibida (FIFO) ochiq invoice'larga va JE
qarzlariga (Opening Entry) bog'laydi. GL summalari o'zgarmaydi — faqat JE
qatoriga reference yoziladi va invoice outstanding yangilanadi.

Hech narsa qattiq yozilmagan (kompaniya, hisob, valyuta, sayt) — istalgan
saytda ishlaydi. Serverda ham, lokalda ham bir xil buyruq:

    # 1) Avval sinov (hech narsa saqlanmaydi):
    bench --site SAYT execute extra.utils.reconcile_journal_entries.run --kwargs "{'dry_run': 1}"

    # 2) Haqiqiy bog'lash (avval backup oling!):
    bench --site SAYT backup
    bench --site SAYT execute extra.utils.reconcile_journal_entries.run --kwargs "{'dry_run': 0}"

    # Qo'shimcha parametrlar:
    #   company='Extra'                 faqat shu kompaniya
    #   party_type='Supplier'           yetkazib beruvchilar (default: Customer)
    #   party='Mijoz nomi'              faqat bitta kontragent
    #   from_date='2026-01-01', to_date='2026-12-31'   JE/invoice sanasi oralig'i
    #   include_payment_entries=1       taqsimlanmagan Payment Entry'larni ham bog'lash
    #   verbose=0                       faqat yakuniy hisobot

Natija: har bir kontragent bo'yicha nechta JE to'lovi qancha summaga bog'langani,
qolgan ochiq qarz va bog'lanmay qolgan to'lov, xatolar ro'yxati.
"""

import frappe
from frappe.utils import cint, flt

import erpnext

PAYMENT_TYPES_JOURNAL_ONLY = ("Journal Entry",)
PAYMENT_TYPES_WITH_PE = ("Journal Entry", "Payment Entry")


def run(
    company=None,
    party_type="Customer",
    party=None,
    dry_run=1,
    from_date=None,
    to_date=None,
    include_payment_entries=0,
    verbose=1,
):
    dry_run = cint(dry_run)
    verbose = cint(verbose)
    include_payment_entries = cint(include_payment_entries)

    if party_type not in ("Customer", "Supplier"):
        frappe.throw("party_type faqat 'Customer' yoki 'Supplier' bo'lishi mumkin")

    # ERPNext reconcile() "Successfully Reconciled" msgprint qiladi — konsolda shovqin bo'lmasin
    frappe.flags.mute_messages = True

    targets = find_targets(company, party_type, party, from_date, to_date)
    mode = "DRY RUN (hech narsa saqlanmaydi)" if dry_run else "REAL RUN (o'zgarishlar saqlanadi)"
    _log(f"\n=== Journal Entry reconciliation — {mode} ===")
    _log(f"party_type={party_type} company={company or 'ALL'} party={party or 'ALL'} "
         f"include_payment_entries={include_payment_entries}")
    _log(f"Reference'siz JE to'lovi bor kontragentlar: {len(targets)}")

    totals = {
        "parties": len(targets),
        "parties_reconciled": 0,
        "parties_nothing_to_do": 0,
        "parties_failed": 0,
        "allocations": 0,
        "allocated_amount": 0.0,
        "remaining_outstanding": 0.0,
        "remaining_unallocated_payments": 0.0,
        "failures": [],
        "dry_run": dry_run,
    }

    for idx, target in enumerate(targets, start=1):
        label = f"[{idx}/{len(targets)}] {target.party} ({target.company} / {target.account})"
        try:
            result = reconcile_party(target, party_type, from_date, to_date, include_payment_entries)
        except Exception as exc:
            frappe.db.rollback()
            frappe.clear_messages()
            totals["parties_failed"] += 1
            totals["failures"].append({"party": target.party, "company": target.company, "error": str(exc)})
            _log(f"{label}: XATO — {exc}")
            continue

        if dry_run:
            frappe.db.rollback()
        else:
            frappe.db.commit()
        frappe.clear_messages()

        if result["allocations"]:
            totals["parties_reconciled"] += 1
        else:
            totals["parties_nothing_to_do"] += 1
        totals["allocations"] += result["allocations"]
        totals["allocated_amount"] += result["allocated_amount"]
        totals["remaining_outstanding"] += result["remaining_outstanding"]
        totals["remaining_unallocated_payments"] += result["remaining_unallocated_payments"]

        if verbose:
            _log(
                f"{label}: to'lov {result['payments']} ta / qarz {result['invoices']} ta -> "
                f"bog'landi {result['allocations']} ta, {_fmt(result['allocated_amount'])} {result['currency']}; "
                f"qoldi: qarz {_fmt(result['remaining_outstanding'])}, "
                f"bog'lanmagan to'lov {_fmt(result['remaining_unallocated_payments'])}"
            )

    for key in ("allocated_amount", "remaining_outstanding", "remaining_unallocated_payments"):
        totals[key] = flt(totals[key], 2)

    _log("\n=== YAKUN ===")
    _log(f"Kontragentlar: {totals['parties']}  bog'landi: {totals['parties_reconciled']}  "
         f"ish yo'q: {totals['parties_nothing_to_do']}  xato: {totals['parties_failed']}")
    _log(f"Bog'lanishlar: {totals['allocations']} ta, summa: {_fmt(totals['allocated_amount'])}")
    _log(f"Qolgan ochiq qarz: {_fmt(totals['remaining_outstanding'])}   "
         f"bog'lanmay qolgan to'lov: {_fmt(totals['remaining_unallocated_payments'])}")
    if totals["failures"]:
        _log("Xatolar:")
        for f in totals["failures"]:
            _log(f"  - {f['party']}: {f['error']}")
    if dry_run:
        _log("DRY RUN: hech narsa saqlanmadi. Haqiqiy bog'lash uchun dry_run=0 bilan yurgizing.")

    return totals


def find_targets(company, party_type, party, from_date, to_date):
    """Reference'siz JE to'lovi (Receivable: kredit > debet) bor kontragent/hisob juftliklari."""
    account_type = erpnext.get_party_account_type(party_type)
    if account_type == "Receivable":
        amount_expr = "jea.credit_in_account_currency - jea.debit_in_account_currency"
    else:
        amount_expr = "jea.debit_in_account_currency - jea.credit_in_account_currency"

    conditions = [
        "je.docstatus = 1",
        "jea.party_type = %(party_type)s",
        "IFNULL(jea.party, '') != ''",
        "acc.account_type = %(account_type)s",
        "(IFNULL(jea.reference_type, '') = '' OR jea.reference_type IN ('Sales Order', 'Purchase Order'))",
    ]
    values = {"party_type": party_type, "account_type": account_type}
    if company:
        conditions.append("je.company = %(company)s")
        values["company"] = company
    if party:
        conditions.append("jea.party = %(party)s")
        values["party"] = party
    if from_date:
        conditions.append("je.posting_date >= %(from_date)s")
        values["from_date"] = from_date
    if to_date:
        conditions.append("je.posting_date <= %(to_date)s")
        values["to_date"] = to_date

    # Faqat TO'LOV qatorlari (kredit > debet) yig'iladi. Qarz qatorlari (masalan
    # Opening Entry debeti) bilan netto hisoblansa, to'lovi qarziga teng mijoz
    # (netto 0) ro'yxatdan tushib qolar edi.
    return frappe.db.sql(
        f"""
        SELECT je.company, jea.party, jea.account,
               SUM(CASE WHEN ({amount_expr}) > 0 THEN 1 ELSE 0 END) AS rows_count,
               SUM(CASE WHEN ({amount_expr}) > 0 THEN ({amount_expr}) ELSE 0 END) AS amount
        FROM `tabJournal Entry Account` jea
        INNER JOIN `tabJournal Entry` je ON je.name = jea.parent
        INNER JOIN `tabAccount` acc ON acc.name = jea.account
        WHERE {' AND '.join(conditions)}
        GROUP BY je.company, jea.party, jea.account
        HAVING amount > 0.005
        ORDER BY je.company, jea.party, jea.account
        """,
        values,
        as_dict=True,
    )


def reconcile_party(target, party_type, from_date, to_date, include_payment_entries):
    """Bitta kontragent uchun ERPNext Payment Reconciliation'ni dasturiy yurgizadi."""
    pr = frappe.get_doc("Payment Reconciliation")
    pr.company = target.company
    pr.party_type = party_type
    pr.party = target.party
    pr.receivable_payable_account = target.account
    pr.default_advance_account = get_default_advance_account(target.company, party_type)
    pr.invoice_limit = 0   # 0 = cheklovsiz
    pr.payment_limit = 0
    pr.from_invoice_date = from_date
    pr.to_invoice_date = to_date
    pr.from_payment_date = from_date
    pr.to_payment_date = to_date

    pr.get_unreconciled_entries()

    allowed = PAYMENT_TYPES_WITH_PE if include_payment_entries else PAYMENT_TYPES_JOURNAL_ONLY
    payments = [row.as_dict() for row in pr.payments if row.reference_type in allowed]
    invoices = [row.as_dict() for row in pr.invoices]
    currency = (payments[0].get("currency") if payments else None) or (
        invoices[0].get("currency") if invoices else None
    ) or ""

    result = {
        "party": target.party,
        "currency": currency,
        "payments": len(payments),
        "invoices": len(invoices),
        "allocations": 0,
        "allocated_amount": 0.0,
        "remaining_outstanding": flt(sum(flt(i.get("outstanding_amount")) for i in invoices), 2),
        "remaining_unallocated_payments": flt(sum(flt(p.get("amount")) for p in payments), 2),
    }
    if not payments or not invoices:
        return result

    # allocate_entries() lug'atlarni o'zgartiradi — nusxa beramiz
    pr.allocate_entries(frappe._dict({
        "payments": [frappe._dict(p) for p in payments],
        "invoices": [frappe._dict(i) for i in invoices],
    }))
    allocations = [row for row in pr.allocation if flt(row.allocated_amount)]
    if not allocations:
        return result

    pr.reconcile()   # validate_allocation + reconcile_against_document (+ qayta yuklash)

    allocated = flt(sum(flt(row.allocated_amount) for row in allocations), 2)
    result["allocations"] = len(allocations)
    result["allocated_amount"] = allocated
    # reconcile() oxirida get_unreconciled_entries() qayta chaqiriladi — yangi holat
    result["remaining_outstanding"] = flt(sum(flt(i.outstanding_amount) for i in pr.invoices), 2)
    result["remaining_unallocated_payments"] = flt(
        sum(flt(p.amount) for p in pr.payments if p.reference_type in allowed), 2
    )
    return result


def get_default_advance_account(company, party_type):
    """Kompaniya avanslarni alohida hisobda yuritsa — o'sha hisob (UI bilan bir xil)."""
    if not frappe.get_cached_value("Company", company, "book_advance_payments_in_separate_party_account"):
        return None
    field = "default_advance_received_account" if party_type == "Customer" else "default_advance_paid_account"
    return frappe.get_cached_value("Company", company, field)


def _fmt(value):
    return f"{flt(value):,.2f}"


def _log(message):
    print(message)


# ---------------------------------------------------------------------------
# Ixtiyoriy 2-qadam: alohida avans hisobidagi JE kreditlarini asosiy hisobga ko'chirish
# ---------------------------------------------------------------------------

def move_advance_account_credits(company=None, party_type="Customer", party=None, dry_run=1, verbose=1, _commit=True):
    """Kompaniyaning alohida avans hisobidagi (masalan "Klient Avans") reference'siz
    JE kreditlarini kontragentning asosiy hisobiga (Debtors) ko'chiradi.

    Nima uchun: ERPNext Payment Entry avansini invoice'ga bog'laganda avans hisobidan
    Debtors'ga GL ko'chirmasini o'zi qiladi, Journal Entry uchun esa qilmaydi.
    Shuning uchun avans hisobida turgan JE krediti (masalan importdagi Opening Entry
    avansi) POS'da ishlatilmaydi. Bu funksiya har bir shunday qator uchun ko'chirish
    JE'sini yaratadi (Dr avans hisobi / Cr Debtors, ikkalasi kontragent bilan) va
    asl kredit qatorini shu JE'ga bog'laydi. Shundan keyin avans oddiy JE avansi
    bo'lib POS'da ko'rinadi va chekka qo'llanadi. GL bo'yicha: avans hisobi (majburiyat)
    kamayadi, Debtors kamayadi — kontragentning umumiy qoldig'i o'zgarmaydi.

        bench --site SAYT execute extra.utils.reconcile_journal_entries.move_advance_account_credits --kwargs "{'dry_run': 1}"
        bench --site SAYT execute extra.utils.reconcile_journal_entries.move_advance_account_credits --kwargs "{'dry_run': 0}"
    """
    from erpnext.accounts.party import get_party_account
    from erpnext.accounts.utils import get_account_currency, reconcile_against_document

    dry_run = cint(dry_run)
    verbose = cint(verbose)
    frappe.flags.mute_messages = True
    account_type = erpnext.get_party_account_type(party_type)
    receivable = account_type == "Receivable"
    amount_expr = (
        "jea.credit_in_account_currency - jea.debit_in_account_currency" if receivable
        else "jea.debit_in_account_currency - jea.credit_in_account_currency"
    )
    advance_field = "default_advance_received_account" if party_type == "Customer" else "default_advance_paid_account"

    companies = [company] if company else [
        c.name for c in frappe.get_all("Company", filters={"book_advance_payments_in_separate_party_account": 1})
    ]
    totals = {"rows": 0, "moved": 0, "amount": 0.0, "failed": 0, "failures": [], "dry_run": dry_run}
    mode = "DRY RUN (hech narsa saqlanmaydi)" if dry_run else "REAL RUN"
    _log(f"\n=== Avans hisobidagi JE kreditlarini ko'chirish — {mode} ===")

    for comp in companies:
        advance_account = frappe.get_cached_value("Company", comp, advance_field)
        if not advance_account:
            _log(f"{comp}: avans hisobi ({advance_field}) sozlanmagan — o'tkazib yuborildi")
            continue
        conditions = [
            "je.docstatus = 1", "je.company = %(company)s", "jea.account = %(advance_account)s",
            "jea.party_type = %(party_type)s", "IFNULL(jea.party, '') != ''",
            "IFNULL(jea.reference_type, '') = ''", "IFNULL(jea.reference_name, '') = ''",
            f"({amount_expr}) > 0.005",
        ]
        values = {"company": comp, "advance_account": advance_account, "party_type": party_type}
        if party:
            conditions.append("jea.party = %(party)s")
            values["party"] = party
        rows = frappe.db.sql(
            f"""
            SELECT je.name AS parent, je.posting_date, jea.name AS reference_row, jea.party,
                   jea.account_currency, jea.exchange_rate, jea.is_advance, jea.cost_center,
                   ({amount_expr}) AS amount
            FROM `tabJournal Entry Account` jea
            INNER JOIN `tabJournal Entry` je ON je.name = jea.parent
            WHERE {' AND '.join(conditions)}
            ORDER BY jea.party, je.posting_date, jea.idx
            """,
            values, as_dict=True,
        )
        _log(f"{comp}: {advance_account} hisobida {len(rows)} ta reference'siz {party_type} krediti")
        totals["rows"] += len(rows)
        company_currency = erpnext.get_company_currency(comp)

        for row in rows:
            label = f"  {row.party}: {row.parent} {_fmt(row.amount)} {row.account_currency}"
            try:
                party_account = get_party_account(party_type, row.party, comp)
                if not party_account or party_account == advance_account:
                    raise frappe.ValidationError(f"{party_type} asosiy hisobi topilmadi")
                if get_account_currency(party_account) != row.account_currency:
                    raise frappe.ValidationError(
                        f"valyuta mos emas: {party_account} != {row.account_currency}"
                    )
                rate = flt(row.exchange_rate) or 1
                amount = flt(row.amount)
                debit_row = {"account": advance_account, "party_type": party_type, "party": row.party,
                             "exchange_rate": rate, "cost_center": row.cost_center}
                credit_row = {"account": party_account, "party_type": party_type, "party": row.party,
                              "exchange_rate": rate, "cost_center": row.cost_center}
                if receivable:
                    debit_row["debit_in_account_currency"] = amount
                    credit_row["credit_in_account_currency"] = amount
                else:
                    debit_row["credit_in_account_currency"] = amount
                    credit_row["debit_in_account_currency"] = amount
                je = frappe.get_doc({
                    "doctype": "Journal Entry", "voucher_type": "Journal Entry", "company": comp,
                    "posting_date": row.posting_date,
                    "multi_currency": 1 if row.account_currency != company_currency else 0,
                    "user_remark": f"Avans hisobidan asosiy hisobga ko'chirish: {row.parent} ({row.reference_row})",
                    "accounts": [debit_row, credit_row],
                })
                je.flags.ignore_permissions = True
                je.insert()
                je.submit()
                # Asl avans qatorini ko'chirish JE'siga bog'laymiz (JE -> JE), shunda u
                # "ochiq avans" bo'lib qolmaydi.
                reconcile_against_document([frappe._dict({
                    "voucher_type": "Journal Entry", "voucher_no": row.parent,
                    "voucher_detail_no": row.reference_row,
                    "against_voucher_type": "Journal Entry", "against_voucher": je.name,
                    "account": advance_account, "party_type": party_type, "party": row.party,
                    "dr_or_cr": "credit_in_account_currency" if receivable else "debit_in_account_currency",
                    "unreconciled_amount": amount, "unadjusted_amount": amount, "allocated_amount": amount,
                    "exchange_rate": rate, "is_advance": row.is_advance,
                    "difference_amount": 0, "difference_account": None, "cost_center": row.cost_center,
                })])
            except Exception as exc:
                frappe.db.rollback()
                frappe.clear_messages()
                totals["failed"] += 1
                totals["failures"].append({"party": row.party, "journal_entry": row.parent, "error": str(exc)})
                _log(f"{label}: XATO — {exc}")
                continue

            totals["moved"] += 1
            totals["amount"] += amount
            if verbose:
                _log(f"{label} -> {je.name} ({party_account})")
            if dry_run:
                frappe.db.rollback()
            elif _commit:
                frappe.db.commit()
            frappe.clear_messages()

    totals["amount"] = flt(totals["amount"], 2)
    _log(f"\n=== YAKUN === qatorlar: {totals['rows']}  ko'chirildi: {totals['moved']}  "
         f"summa: {_fmt(totals['amount'])}  xato: {totals['failed']}")
    for f in totals["failures"]:
        _log(f"  - {f['party']} / {f['journal_entry']}: {f['error']}")
    if dry_run:
        _log("DRY RUN: hech narsa saqlanmadi.")
    return totals
