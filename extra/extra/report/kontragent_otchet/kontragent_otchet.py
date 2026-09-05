import frappe
from frappe.utils import flt

TOTAL_KEYS = (
    "opening_credit_usd",
    "opening_debit_usd",
    "period_credit_usd",
    "period_debit_usd",
    "final_credit_usd",
    "final_debit_usd",
)


def execute(filters=None):
    if not filters:
        return [], []

    columns = get_columns(filters)
    data = get_data(filters)

    return columns, data


def get_columns(filters):
    columns = [
        {"label": "Контрагент тури", "fieldname": "party_type", "fieldtype": "Data", "width": 130},
        {"label": "Контрагент", "fieldname": "party", "fieldtype": "Dynamic Link", "options": "party_type", "width": 200},
        {"label": "Валюта", "fieldname": "currency", "fieldtype": "Link", "options": "Currency", "width": 80},
        {"label": "Акт Сверка", "fieldname": "akt_sverka_link", "fieldtype": "Data", "width": 120},
        {"label": "Кредит (дан олдин)", "fieldname": "opening_credit_usd", "fieldtype": "Currency", "width": 150},
        {"label": "Дебет (дан олдин)", "fieldname": "opening_debit_usd", "fieldtype": "Currency", "width": 150},
        {"label": "Кредит (давр)", "fieldname": "period_credit_usd", "fieldtype": "Currency", "width": 150},
        {"label": "Дебет (давр)", "fieldname": "period_debit_usd", "fieldtype": "Currency", "width": 150},
        {"label": "Сўнгги Кредит", "fieldname": "final_credit_usd", "fieldtype": "Currency", "width": 150},
        {"label": "Сўнгги Дебет", "fieldname": "final_debit_usd", "fieldtype": "Currency", "width": 150},
    ]

    return columns


def get_default_currency():
    """GL yozuvida valyuta bo'lmasa: kompaniya valyutasi, bo'lmasa global default"""
    company = (
        frappe.defaults.get_user_default("Company")
        or frappe.db.get_single_value("Global Defaults", "default_company")
    )
    currency = None
    if company:
        currency = frappe.get_cached_value("Company", company, "default_currency")
    return currency or frappe.db.get_single_value("Global Defaults", "default_currency") or "USD"


def get_data(filters):
    from_date = filters.get("from_date")
    to_date = filters.get("to_date")
    party_type = filters.get("party_type")
    party = filters.get("party")

    conditions = [
        "party IS NOT NULL",
        "party != ''",
        "party_type IS NOT NULL",
        "party_type != ''",
        "is_cancelled = 0",
    ]
    values = {}

    if party:
        conditions.append("party = %(party)s")
        values["party"] = party

    if party_type:
        conditions.append("party_type = %(party_type)s")
        values["party_type"] = party_type

    values["from_date"] = from_date
    values["to_date"] = to_date

    where_clause = " AND ".join(conditions)

    # Summalar *_in_account_currency ustunlaridan olinadi, shuning uchun
    # guruhlash account_currency bo'yicha ham qilinadi: har bir qator faqat
    # bitta valyutadagi yozuvlarni yig'adi va "Валюта" ustuni aynan shu valyutani ko'rsatadi.
    results = frappe.db.sql("""
        SELECT
            party_type,
            party,
            account_currency AS currency,
            IFNULL(SUM(CASE WHEN posting_date < %(from_date)s THEN credit_in_account_currency ELSE 0 END), 0) as opening_credit,
            IFNULL(SUM(CASE WHEN posting_date < %(from_date)s THEN debit_in_account_currency ELSE 0 END), 0) as opening_debit,
            IFNULL(SUM(CASE WHEN posting_date BETWEEN %(from_date)s AND %(to_date)s THEN credit_in_account_currency ELSE 0 END), 0) as period_credit,
            IFNULL(SUM(CASE WHEN posting_date BETWEEN %(from_date)s AND %(to_date)s THEN debit_in_account_currency ELSE 0 END), 0) as period_debit
        FROM `tabGL Entry`
        WHERE {where_clause}
          AND posting_date <= %(to_date)s
        GROUP BY party_type, party, account_currency
        ORDER BY party_type, party, account_currency
    """.format(where_clause=where_clause), values, as_dict=True)

    default_currency = None
    data = []
    totals_by_currency = {}

    for r in results:
        opening_net = flt(r.opening_credit - r.opening_debit, 2)
        period_credit = flt(r.period_credit, 2)
        period_debit = flt(r.period_debit, 2)
        final_net = flt(opening_net + period_credit - period_debit, 2)

        currency = r.currency
        if not currency:
            if default_currency is None:
                default_currency = get_default_currency()
            currency = default_currency

        row = {
            "party_type": r.party_type,
            "party": r.party,
            "currency": currency,
            "akt_sverka_link": "Акт Сверка",
            "opening_credit_usd": opening_net if opening_net > 0 else 0,
            "opening_debit_usd": abs(opening_net) if opening_net < 0 else 0,
            "period_credit_usd": period_credit,
            "period_debit_usd": period_debit,
            "final_credit_usd": final_net if final_net > 0 else 0,
            "final_debit_usd": abs(final_net) if final_net < 0 else 0,
        }
        data.append(row)

        totals = totals_by_currency.setdefault(currency, {key: 0 for key in TOTAL_KEYS})
        for key in TOTAL_KEYS:
            totals[key] += row.get(key, 0)

    # ЖАМИ — har bir valyuta uchun alohida (turli valyutalar qo'shilmaydi)
    for currency, totals in totals_by_currency.items():
        total_row = {
            "party_type": "",
            "party": "ЖАМИ",
            "currency": currency,
            "akt_sverka_link": "",
            "is_total_row": True,
        }
        total_row.update(totals)
        data.append(total_row)

    return data
