# Copyright (c) 2026, abdulloh and contributors
# For license information, please see license.txt
"""Кунлик транзакциялар — эски дастурдаги кунлик ҳисобот кўриниши.

Ҳар бир транзакция битта йиғиладиган гуруҳ бўлиб чиқади:

    № ҲУЖЖАТ   Касса <-> Контрагент              сана, вақт, фойдаланувчи
        ВЫДАНО     — берилган товар / чиққан пул (доллар ҳисобида)
        ПОЛУЧЕНО   — олинган пул (сўм, курс, доллар)
        БАЛАНС     — олинган − берилган (манфий = қарз)

Манбалар: Sales Invoice (савдо), Payment Entry (тўлов), Journal Entry
(касса ҳаракати/перемещения), Stock Entry (товар кўчириш).

"Ҳужжат" устуни — Dynamic Link: устига босиб ҳужжат очилади, шу ердан
тузатиш ёки бекор қилиш мумкин (ҳисобот хатоларни топиш учун).

Фақат ТАСДИҚЛАНГАН (submit, docstatus = 1) ҳужжатлар кўрсатилади.

"Сўм" устуни ҳар доим СЎМда: ҳужжатнинг ўз сўм суммаси бўлса ўша, акс
ҳолда доллар суммаси курсга кўпайтирилади. Курс — Currency Exchange
рўйхатидаги ЭНГ ОХИРГИ ёзув (POS ҳам худди шу манбадан олади).

БАЛАНС ҲАҚИДА: савдо қатори учун баланс ҳужжатнинг ЖОРИЙ қолдиғидан
(outstanding_amount) олинади — мижоз кейинроқ алоҳида тўлов қилса, ўша
тўлов ўз қатори бўлиб чиқади ва бу ернинг баланси 0 га келади.
"""

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt, getdate

# Эски дастурдаги бўлим номлари (кассирлар шуларга ўрганган)
ISSUED = "ВЫДАНО"
RECEIVED = "ПОЛУЧЕНО"
BALANCE = "БАЛАНС"

# Сўм устуни ва курс учун маҳаллий валюта (дўкон сўмда ишлайди)
SOM = "UZS"

TYPE_FILTER_MAP = {
    "Савдо": "Sales Invoice",
    "Тўлов": "Payment Entry",
    "Касса/Перемещения": "Journal Entry",
    "Товар кўчириш": "Stock Entry",
}


def execute(filters=None):
    filters = frappe._dict(filters or {})
    if not filters.get("from_date") or not filters.get("to_date"):
        frappe.throw(_("Сана оралиғини кўрсатинг"))

    columns = get_columns()
    rows, transactions = get_data(filters)
    return columns, rows, None, None, get_report_summary(transactions)


def get_columns():
    """Эски дастурдаги 4 та устун.

    Сарлавҳа қаторида шу устунлар сана / вақт / фойдаланувчи учун
    ишлатилади, товар қаторида сони / нархи / жами, пул қаторида эса
    сўм суммаси / курс / доллар — худди эски жадвалдагидек.
    Шунинг учун типи Data: қийматлар серверда форматланади.
    """
    return [
        {"fieldname": "title", "label": _("Наименование"), "fieldtype": "Data", "width": 520},
        {"fieldname": "qty", "label": _("Кол-во"), "fieldtype": "Data", "width": 150, "align": "right"},
        {"fieldname": "rate", "label": _("Цена"), "fieldtype": "Data", "width": 120, "align": "right"},
        {"fieldname": "amount", "label": _("ИТОГО"), "fieldtype": "Data", "width": 150, "align": "right"},
        # Ҳужжат линки сарлавҳа матнига уланади (kunlik_tranzaksiyalar.js)
        {"fieldname": "voucher_type", "label": "Doctype", "fieldtype": "Data", "width": 0, "hidden": 1},
        {"fieldname": "voucher_no", "label": "Voucher", "fieldtype": "Data", "width": 0, "hidden": 1},
        {"fieldname": "row_type", "label": "Row", "fieldtype": "Data", "width": 0, "hidden": 1},
    ]


# ──────────────────────────────────────────────────────────────────────
#  Маълумот йиғиш
# ──────────────────────────────────────────────────────────────────────
def get_data(filters):
    types = get_selected_types(filters)

    transactions = []
    if "Sales Invoice" in types:
        transactions += get_sales_invoices(filters)
    if "Payment Entry" in types:
        transactions += get_payment_entries(filters)
    if "Journal Entry" in types:
        transactions += get_journal_entries(filters)
    if "Stock Entry" in types:
        transactions += get_stock_entries(filters)

    # БАЛАНС — эски дастурдагидек ШУ ҲУЖЖАТ ичидаги: олинган − берилган.
    # (Мижоз кейин алоҳида тўлов қилса, у ўз қатори бўлиб чиқади.)
    for txn in transactions:
        issued = sum(flt(line.get("amount")) for line in txn["issued"])
        received = sum(flt(line.get("amount")) for line in txn["received"])
        # Товар кўчириш — ички ҳаракат, қарз ҳосил қилмайди
        # Чиқаришда 2 хона — шунинг учун баланс ҳам 2 хонага келтирилади:
        # хом ҳисобдаги 1e-15 каби қолдиқлар "-0,00" бўлиб чиқмаслиги учун.
        txn["balance"] = 0.0 if txn["voucher_type"] == "Stock Entry" else (flt(received - issued, 2) or 0.0)

    if cint(filters.get("only_debt")):
        transactions = [t for t in transactions if flt(t.get("balance"), 2) < 0]

    transactions.sort(key=lambda t: (cstr(t["posting_date"]), t["sort_time"], t["voucher_no"]))

    user_names = get_user_names(transactions)
    # Курс бир марта олинади (Currency Exchange'даги энг охирги ёзув) ва
    # сўм суммаси бўлмаган қаторларни сўмга ўтказишда ишлатилади.
    latest_rate = get_latest_exchange_rate(get_company_currency())

    rows = []
    for txn in transactions:
        rows.extend(build_rows(txn, user_names, latest_rate))
    return rows, transactions


def get_selected_types(filters):
    """Фильтрда танланган турлар (бўш бўлса — ҳаммаси)."""
    selected = filters.get("transaction_type")
    if isinstance(selected, str):
        selected = [selected] if selected else []
    if not selected:
        return set(TYPE_FILTER_MAP.values())
    return {TYPE_FILTER_MAP[s] for s in selected if s in TYPE_FILTER_MAP}


def get_docstatus_list(filters):
    """Фақат ТАСДИҚЛАНГАН ҳужжатлар — черновик ва бекор қилинганлар кунлик
    ҳисобга кирмайди."""
    return [1]


def base_conditions(filters, alias, party_field=None):
    """Барча манбалар учун умумий шартлар (сана, фойдаланувчи, контрагент)."""
    conditions = [
        f"{alias}.posting_date BETWEEN %(from_date)s AND %(to_date)s",
        f"{alias}.docstatus IN %(docstatus)s",
    ]
    if filters.get("party") and party_field:
        conditions.append(f"{alias}.{party_field} = %(party)s")
    return conditions


def query_values(filters):
    return {
        "from_date": filters.get("from_date"),
        "to_date": filters.get("to_date"),
        "docstatus": get_docstatus_list(filters),
        "party": filters.get("party"),
        "pos_profile": filters.get("pos_profile"),
        "account": filters.get("account"),
    }


# ── Савдо (Sales Invoice) ─────────────────────────────────────────────
def get_sales_invoices(filters):
    conditions = base_conditions(filters, "si", "customer")
    if filters.get("pos_profile"):
        conditions.append("si.pos_profile = %(pos_profile)s")

    invoices = frappe.db.sql(
        """
        SELECT si.name, si.posting_date, si.posting_time, si.owner, si.customer,
               si.customer_name, si.pos_profile, si.currency, si.conversion_rate,
               si.grand_total, si.base_grand_total, si.outstanding_amount,
               si.docstatus, si.is_return
        FROM `tabSales Invoice` si
        WHERE {conditions}
        """.format(conditions=" AND ".join(conditions)),
        query_values(filters),
        as_dict=True,
    )
    if not invoices:
        return []

    names = [inv.name for inv in invoices]
    items = group_children(
        frappe.db.sql(
            """
            SELECT parent, item_name, item_code, qty, rate, amount
            FROM `tabSales Invoice Item`
            WHERE parent IN %(names)s ORDER BY parent, idx
            """,
            {"names": names},
            as_dict=True,
        )
    )
    payments = group_children(
        frappe.db.sql(
            """
            SELECT parent, mode_of_payment, account, amount
            FROM `tabSales Invoice Payment`
            WHERE parent IN %(names)s ORDER BY parent, idx
            """,
            {"names": names},
            as_dict=True,
        )
    )
    advances = group_children(
        frappe.db.sql(
            """
            SELECT parent, reference_name, allocated_amount
            FROM `tabSales Invoice Advance`
            WHERE parent IN %(names)s ORDER BY parent, idx
            """,
            {"names": names},
            as_dict=True,
        )
    )

    transactions = []
    for inv in invoices:
        rate = fx_rate(inv.conversion_rate)
        # Чек доллардa ёзилган бўлиши мумкин (эски тизимдан кўчирилганлар).
        # Ундай ҳолда ҳужжатдаги сумма СЎМ ЭМАС — "Сўм" устуни уни курс
        # бўйича ўзи ҳисоблайди (som_of).
        is_som = (inv.currency or "") == SOM
        # ЯХЛИТЛАМАСДАН: доллар қиймати ҳар доим чек суммасини курсга
        # кўпайтириб олинади. ERPNext'нинг base_amount'и ҳар қатор бўйича
        # алоҳида тийингача яхлитланган — шунинг учун улар йиғиндиси тўлов
        # билан 0.01 фарқ қиларди. Яхлитлаш фақат чиқаришда (fmt_num).
        cr = flt(inv.conversion_rate) or 1.0

        issued = [
            {
                "title": it.item_name or it.item_code,
                "qty": flt(it.qty),
                "rate": flt(it.rate) * cr,
                "amount": flt(it.amount) * cr,
                "uzs_amount": flt(it.amount) if is_som else 0,
                "fx_rate": rate,
            }
            for it in items.get(inv.name, [])
        ]

        received = []
        for pay in payments.get(inv.name, []):
            received.append({
                "title": pay.mode_of_payment or short_account(pay.account),
                "amount": flt(pay.amount) * cr,
                "uzs_amount": flt(pay.amount) if is_som else 0,
                "fx_rate": rate,
            })
        for adv in advances.get(inv.name, []):
            received.append({
                "title": _("Аванс") + f" ({adv.reference_name})",
                "amount": flt(adv.allocated_amount) * cr,
                "uzs_amount": flt(adv.allocated_amount) if is_som else 0,
                "fx_rate": rate,
            })

        # Қолдиқ ҳужжат валютасида (сўм) сақланади — долларга ўтказамиз
        outstanding_usd = flt(inv.outstanding_amount) * cr

        transactions.append({
            "voucher_type": "Sales Invoice",
            "voucher_no": inv.name,
            "posting_date": inv.posting_date,
            "posting_time": fmt_time(inv.posting_time),
            "sort_time": cstr(inv.posting_time),
            "owner": inv.owner,
            "docstatus": inv.docstatus,
            "kassa": kassa_of_invoice(inv, payments.get(inv.name, [])),
            "party": inv.customer_name or inv.customer,
            "issued": issued,
            "received": received,
            "total": flt(inv.grand_total) * cr,
            "total_uzs": flt(inv.grand_total) if is_som else 0,
            "fx_rate": rate,
            "outstanding": flt(outstanding_usd, 2),
            "note": _("Қайтариш") if inv.is_return else "",
        })
    return transactions


def kassa_of_invoice(inv, payment_rows):
    """Савдо қайси касса/дўкон номидан бўлганини аниқлаш."""
    for pay in payment_rows:
        if pay.account:
            return short_account(pay.account)
    return inv.pos_profile or _("Савдо")


# ── Тўлов (Payment Entry) ─────────────────────────────────────────────
def get_payment_entries(filters):
    conditions = base_conditions(filters, "pe", "party")
    if filters.get("account"):
        conditions.append("(pe.paid_from = %(account)s OR pe.paid_to = %(account)s)")

    entries = frappe.db.sql(
        """
        SELECT pe.name, pe.posting_date, pe.creation, pe.owner, pe.payment_type,
               pe.party_type, pe.party, pe.party_name, pe.paid_from, pe.paid_to,
               pe.paid_amount, pe.received_amount, pe.base_paid_amount,
               pe.base_received_amount, pe.source_exchange_rate, pe.target_exchange_rate,
               pe.paid_from_account_currency, pe.paid_to_account_currency,
               pe.mode_of_payment, pe.remarks, pe.docstatus
        FROM `tabPayment Entry` pe
        WHERE {conditions}
        """.format(conditions=" AND ".join(conditions)),
        query_values(filters),
        as_dict=True,
    )

    transactions = []
    for pe in entries:
        incoming = pe.payment_type == "Receive"
        # Кассага тушган/кассадан чиққан томон
        kassa_account = pe.paid_to if incoming else pe.paid_from
        money_currency = pe.paid_to_account_currency if incoming else pe.paid_from_account_currency
        money_amount = flt(pe.received_amount) if incoming else flt(pe.paid_amount)
        acc_rate = flt(pe.target_exchange_rate if incoming else pe.source_exchange_rate)
        # Яхлитламасдан: сумма × курс. base_* майдонлари ERPNext томонидан
        # тийингача яхлитланган — улардан фойдалансак фарқ пайдо бўларди.
        usd_amount = money_amount * acc_rate if acc_rate else flt(
            pe.base_received_amount if incoming else pe.base_paid_amount
        ) or flt(pe.paid_amount)

        line = {
            "title": pe.mode_of_payment or short_account(kassa_account),
            "amount": usd_amount,
            "uzs_amount": money_amount if money_currency == SOM else 0,
            "fx_rate": fx_rate(acc_rate) if money_currency == SOM else 0,
        }

        transactions.append({
            "voucher_type": "Payment Entry",
            "voucher_no": pe.name,
            "posting_date": pe.posting_date,
            "posting_time": fmt_time(pe.creation),
            "sort_time": cstr(pe.creation)[11:],
            "owner": pe.owner,
            "docstatus": pe.docstatus,
            "kassa": short_account(kassa_account),
            "party": pe.party_name or pe.party or short_account(pe.paid_from if incoming else pe.paid_to),
            "issued": [] if incoming else [line],
            "received": [line] if incoming else [],
            "total": usd_amount,
            "total_uzs": line["uzs_amount"],
            "fx_rate": line["fx_rate"],
            "note": clean_remark(pe.remarks),
        })
    return transactions


# ── Касса ҳаракати / перемещения (Journal Entry) ──────────────────────
def get_journal_entries(filters):
    conditions = base_conditions(filters, "je")
    values = query_values(filters)

    if filters.get("party") or filters.get("account"):
        sub = ["jea.parent = je.name"]
        if filters.get("party"):
            sub.append("jea.party = %(party)s")
        if filters.get("account"):
            sub.append("jea.account = %(account)s")
        conditions.append(
            "EXISTS (SELECT 1 FROM `tabJournal Entry Account` jea WHERE " + " AND ".join(sub) + ")"
        )

    entries = frappe.db.sql(
        """
        SELECT je.name, je.posting_date, je.creation, je.owner, je.voucher_type,
               je.user_remark, je.total_debit, je.docstatus
        FROM `tabJournal Entry` je
        WHERE {conditions}
        """.format(conditions=" AND ".join(conditions)),
        values,
        as_dict=True,
    )
    if not entries:
        return []

    rows = group_children(
        frappe.db.sql(
            """
            SELECT parent, account, account_currency, party_type, party,
                   debit, credit, debit_in_account_currency, credit_in_account_currency,
                   exchange_rate, user_remark
            FROM `tabJournal Entry Account`
            WHERE parent IN %(names)s ORDER BY parent, idx
            """,
            {"names": [e.name for e in entries]},
            as_dict=True,
        )
    )

    account_types = get_account_types(rows)

    transactions = []
    for je in entries:
        issued, received = [], []
        credit_accounts, debit_accounts = [], []
        cash_account = other_account = ""
        party = ""
        for acc in rows.get(je.name, []):
            # Сарлавҳа «Касса <-> Контрагент» бўлиши учун: пул счёти қайси,
            # қарши томон қайси — счёт туридан аниқлаймиз.
            if account_types.get(acc.account) in ("Cash", "Bank"):
                cash_account = cash_account or short_account(acc.account)
            else:
                other_account = other_account or short_account(acc.account)
            is_uzs = (acc.account_currency or "") == SOM
            # Сўм счётида доллар қиймати хом ҳисобланади (сумма × курс),
            # яхлитланган debit/credit майдонлари ишлатилмайди.
            acc_rate = flt(acc.exchange_rate)
            if flt(acc.credit):
                # Кредит = шу кассадан пул чиқди
                credit_accounts.append(short_account(acc.account))
                som_out = flt(acc.credit_in_account_currency) if is_uzs else 0
                issued.append({
                    "title": short_account(acc.account),
                    "amount": som_out * acc_rate if (is_uzs and acc_rate) else flt(acc.credit),
                    "uzs_amount": som_out,
                    "fx_rate": fx_rate(acc_rate) if is_uzs else 0,
                })
            if flt(acc.debit):
                # Дебет = шу кассага пул кирди
                debit_accounts.append(short_account(acc.account))
                som_in = flt(acc.debit_in_account_currency) if is_uzs else 0
                received.append({
                    "title": short_account(acc.account),
                    "amount": som_in * acc_rate if (is_uzs and acc_rate) else flt(acc.debit),
                    "uzs_amount": som_in,
                    "fx_rate": fx_rate(acc_rate) if is_uzs else 0,
                })
            if acc.party and not party:
                party = acc.party

        transactions.append({
            "voucher_type": "Journal Entry",
            "voucher_no": je.name,
            "posting_date": je.posting_date,
            "posting_time": fmt_time(je.creation),
            "sort_time": cstr(je.creation)[11:],
            "owner": je.owner,
            "docstatus": je.docstatus,
            "kassa": cash_account or ", ".join(credit_accounts) or je.voucher_type,
            "party": party or other_account or ", ".join(debit_accounts) or (je.user_remark or "")[:60],
            "issued": issued,
            "received": received,
            "total": flt(je.total_debit),
            "total_uzs": 0,
            "fx_rate": 0,
            "note": je.user_remark or "",
        })
    return transactions


# ── Товар кўчириш (Stock Entry) ───────────────────────────────────────
def get_stock_entries(filters):
    conditions = base_conditions(filters, "se")
    entries = frappe.db.sql(
        """
        SELECT se.name, se.posting_date, se.posting_time, se.owner, se.stock_entry_type,
               se.from_warehouse, se.to_warehouse, se.total_amount, se.docstatus, se.remarks
        FROM `tabStock Entry` se
        WHERE {conditions}
        """.format(conditions=" AND ".join(conditions)),
        query_values(filters),
        as_dict=True,
    )
    if not entries:
        return []

    items = group_children(
        frappe.db.sql(
            """
            SELECT parent, item_name, item_code, qty, basic_rate, amount,
                   s_warehouse, t_warehouse
            FROM `tabStock Entry Detail`
            WHERE parent IN %(names)s ORDER BY parent, idx
            """,
            {"names": [e.name for e in entries]},
            as_dict=True,
        )
    )

    transactions = []
    for se in entries:
        se_items = items.get(se.name, [])
        issued = [
            {
                "title": it.item_name or it.item_code,
                "qty": flt(it.qty),
                "rate": flt(it.basic_rate),
                "amount": flt(it.amount),
            }
            for it in se_items
        ]
        source = se.from_warehouse or (se_items[0].s_warehouse if se_items else "")
        target = se.to_warehouse or (se_items[0].t_warehouse if se_items else "")

        transactions.append({
            "voucher_type": "Stock Entry",
            "voucher_no": se.name,
            "posting_date": se.posting_date,
            "posting_time": fmt_time(se.posting_time),
            "sort_time": cstr(se.posting_time),
            "owner": se.owner,
            "docstatus": se.docstatus,
            "kassa": short_account(source) or se.stock_entry_type,
            "party": short_account(target) or se.stock_entry_type,
            "issued": issued,
            "received": [],
            "total": flt(se.total_amount),
            "total_uzs": 0,
            "fx_rate": 0,
            "note": se.remarks or "",
        })
    return transactions


# ── Қаторлар қуриш ────────────────────────────────────────────────────
def build_rows(txn, user_names, latest_rate=0.0):
    """Битта транзакциядан эски жадвалдаги кўринишдаги қаторлар:

        № ҲУЖЖАТ  Касса <-> Контрагент | сана   | вақт  | фойдаланувчи
        ВЫДАНО                         |        |       |
           Товар номи                  | сони   | нарх  | жами ($)
        ПОЛУЧЕНО                       |        |       |
           Тўлов тури                  | сўм    | курс  | жами ($)
        БАЛАНС                         |        |       | қолдиқ ($)
        (бўш ажратувчи қатор)
    """
    link = {"voucher_type": txn["voucher_type"], "voucher_no": txn["voucher_no"]}

    rows = [dict(
        link,
        row_type="header",
        title=f"№ {short_voucher(txn['voucher_no'])} {txn['kassa']} <-> {txn['party']}",
        qty=fmt_date(txn["posting_date"]),
        rate=txn["posting_time"],
        amount=user_names.get(txn["owner"], txn["owner"]),
    )]

    issued_rows = [detail_row(line, latest_rate) for line in txn["issued"]]
    received_rows = [detail_row(line, latest_rate) for line in txn["received"]]

    if issued_rows:
        rows.append(section_row(ISSUED))
        rows.extend(issued_rows)
    if received_rows:
        rows.append(section_row(RECEIVED))
        rows.extend(received_rows)

    rows.append({
        "row_type": "balance",
        "title": BALANCE,
        "qty": "",
        "rate": "",
        # 0 бўлса ҳам кўрсатилади — эски дастурда ҳам БАЛАНС доим тўлдирилган
        "amount": fmt_num(txn["balance"]) or "0,00",
    })
    # Эски дастурдагидек транзакциялар орасида бўш қатор
    rows.append({"row_type": "spacer", "title": "", "qty": "", "rate": "", "amount": ""})
    return rows


def section_row(title):
    return {"row_type": "section", "title": title, "qty": "", "rate": "", "amount": ""}


def detail_row(line, latest_rate=0.0):
    """Товар қатори — сони/нархи; пул қатори — сўм суммаси/курс."""
    qty = flt(line.get("qty"))
    if qty:
        return {
            "row_type": "item",
            "title": "    " + cstr(line.get("title")),
            "qty": fmt_qty(qty),
            "rate": fmt_num(line.get("rate")),
            "amount": fmt_num(line.get("amount")),
        }

    som, rate = som_of(line.get("amount"), line.get("uzs_amount"), line.get("fx_rate"), latest_rate)
    return {
        "row_type": "money",
        "title": "    " + cstr(line.get("title")),
        "qty": fmt_num(som),
        # Курс минглик ажратгичсиз — эски дастурдагидек "11900,00"
        "rate": fmt_rate(rate),
        "amount": fmt_num(line.get("amount")),
    }


def som_of(usd_amount, som_amount, doc_rate, latest_rate):
    """(сўм, курс) — ҳужжатнинг ўз сўм суммаси бўлса ўша, акс ҳолда доллар
    суммаси Currency Exchange'даги энг охирги курсга кўпайтирилади."""
    som = flt(som_amount, 2)
    rate = flt(doc_rate, 2)
    if som:
        return som, (rate or flt(latest_rate, 2))
    usd = flt(usd_amount)
    if usd and latest_rate:
        return flt(usd * latest_rate, 2), flt(latest_rate, 2)
    return 0.0, 0.0


# ── Форматлаш (эски дастурдагидек: 386 128,45) ────────────────────────
def fmt_num(value, decimals=2):
    number = flt(value)
    if not number:
        return ""
    return f"{number:,.{decimals}f}".replace(",", " ").replace(".", ",")


def fmt_rate(value):
    number = flt(value)
    if not number:
        return ""
    return f"{number:.2f}".replace(".", ",")


def fmt_qty(value):
    number = flt(value)
    if not number:
        return ""
    if number == int(number):
        return str(int(number))
    return fmt_num(number)


def fmt_date(value):
    if not value:
        return ""
    try:
        return getdate(value).strftime("%d.%m.%Y")
    except Exception:
        return cstr(value)


def get_report_summary(transactions):
    """Жадвал устидаги умумий кўрсаткичлар (доллар ҳисобида)."""
    issued = received = debt = 0.0
    for txn in transactions:
        issued += sum(flt(line.get("amount")) for line in txn["issued"])
        received += sum(flt(line.get("amount")) for line in txn["received"])
        debt += min(flt(txn.get("balance")), 0)

    return [
        {"value": len(transactions), "label": _("Транзакциялар"), "datatype": "Int", "indicator": "Blue"},
        {"value": flt(issued, 2), "label": _("ВЫДАНО"), "datatype": "Currency", "currency": "USD"},
        {"value": flt(received, 2), "label": _("ПОЛУЧЕНО"), "datatype": "Currency", "currency": "USD",
         "indicator": "Green"},
        {"value": flt(debt, 2), "label": _("БАЛАНС"), "datatype": "Currency", "currency": "USD",
         "indicator": "Red" if debt else "Green"},
    ]


def get_account_types(grouped_rows):
    """Журнал қаторларидаги счётларнинг тури (Cash/Bank аниқлаш учун)."""
    accounts = {acc.account for rows in grouped_rows.values() for acc in rows if acc.account}
    if not accounts:
        return {}
    return {
        a.name: a.account_type
        for a in frappe.get_all("Account", filters={"name": ("in", list(accounts))},
                                fields=["name", "account_type"])
    }


def group_children(rows):
    """Болалар жадвалидаги қаторларни parent бўйича гуруҳлаш."""
    grouped = {}
    for row in rows:
        grouped.setdefault(row.parent, []).append(row)
    return grouped


def get_latest_exchange_rate(from_currency, to_currency=SOM):
    """Currency Exchange рўйхатидаги ЭНГ ОХИРГИ курс (1 доллар = ... сўм).

    POS ҳам курсни фақат шу жадвалдан олади (ташқи API йўқ) — шунинг учун
    ҳисобот ва чек бир хил курсда бўлади. Тескари ёзув (UZS -> USD) бўлса
    ундан ҳам ҳисоблаб олинади.
    """
    if not from_currency or from_currency == to_currency:
        return 0.0
    row = frappe.get_all(
        "Currency Exchange",
        filters={"from_currency": from_currency, "to_currency": to_currency},
        fields=["exchange_rate"],
        order_by="date desc, creation desc",
        limit=1,
    )
    if row and flt(row[0].exchange_rate):
        return flt(row[0].exchange_rate, 2)

    inverse = frappe.get_all(
        "Currency Exchange",
        filters={"from_currency": to_currency, "to_currency": from_currency},
        fields=["exchange_rate"],
        order_by="date desc, creation desc",
        limit=1,
    )
    if inverse and flt(inverse[0].exchange_rate):
        return flt(1 / flt(inverse[0].exchange_rate), 2)
    return 0.0


def get_company_currency():
    company = (
        frappe.defaults.get_user_default("Company")
        or frappe.db.get_single_value("Global Defaults", "default_company")
    )
    return (company and frappe.get_cached_value("Company", company, "default_currency")) or "USD"


def short_voucher(name):
    """«ACC-SINV-2026-01180» -> «ACC-...-01180» (устун сиғмай қолмасин)."""
    text = cstr(name or "")
    parts = [p for p in text.split("-") if p]
    if len(parts) < 2:
        return text
    return f"{parts[0][:3]}-...-{parts[-1]}"


def fx_rate(conversion_rate):
    """conversion_rate (сўм -> доллар) дан одатий курсни (1$ = ... сўм) чиқариш."""
    rate = flt(conversion_rate)
    if not rate or rate >= 1:
        return 0.0
    return flt(1 / rate, 2)


def fmt_time(value):
    """Вақтни HH:MM кўринишида (posting_time, creation ёки timedelta)."""
    if not value:
        return ""
    text = cstr(value)
    if " " in text:  # datetime -> вақт қисми
        text = text.split(" ", 1)[1]
    return text[:5]


def clean_remark(remark):
    """ERPNext автоматик ёзадиган «Amount USD 42.02 received from ...» изоҳи
    фойдасиз — фақат қўлда ёзилган изоҳни кўрсатамиз."""
    text = cstr(remark or "").strip()
    if text.startswith("Amount ") or text.startswith("Payment of "):
        return ""
    return text


def short_account(name):
    """«D8 Dokon - E» -> «D8 Dokon» (эски дастурдаги кўриниш)."""
    if not name:
        return ""
    return cstr(name).rsplit(" - ", 1)[0]


def get_user_names(transactions):
    owners = {t["owner"] for t in transactions if t.get("owner")}
    if not owners:
        return {}
    return {
        u.name: u.full_name or u.name
        for u in frappe.get_all("User", filters={"name": ("in", list(owners))}, fields=["name", "full_name"])
    }
