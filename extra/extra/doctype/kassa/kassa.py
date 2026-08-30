# Copyright (c) 2025, abdulloh and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class Kassa(Document):
    def validate(self):
        self.set_default_company()
        self.set_cash_account()
        self.set_cash_account_currency()
        self.set_party_currency()
        self.set_balance()
        self.validate_party()
        self.validate_transaction_rules()
        self.validate_transfer()
        self.validate_amount()
        self.validate_currency()

    def on_submit(self):
        """Submit bo'lganda Payment Entry yoki Journal Entry yaratish"""
        if self.transaction_type in ["Приход", "Расход"]:
            if self.party_type in ["Customer", "Supplier", "Employee"]:
                self.create_payment_entry()
            elif self.party_type == "Дивиденд":
                self.create_dividend_journal_entry()
            elif self.party_type == "Расходы":
                self.create_expense_journal_entry()
        elif self.transaction_type == "Перемещения":
            self.create_transfer_payment_entry()

    def on_cancel(self):
        """Cancel bo'lganda bog'langan Payment Entry yoki Journal Entry ni cancel qilish"""
        self.cancel_linked_entries()

    def create_payment_entry(self):
        """Customer/Supplier/Employee uchun Payment Entry yaratish"""
        payment_type = "Receive" if self.transaction_type == "Приход" else "Pay"

        pe = frappe.new_doc("Payment Entry")
        pe.payment_type = payment_type
        pe.posting_date = self.date
        pe.company = self.company
        pe.mode_of_payment = self.mode_of_payment
        pe.party_type = self.party_type
        pe.party = self.party

        # Set accounts
        pe.paid_from = self.get_paid_from_account(payment_type)
        pe.paid_to = self.get_paid_to_account(payment_type)
        pe.paid_amount = flt(self.amount)
        pe.received_amount = flt(self.amount)

        # Set reference to Kassa
        pe.reference_no = self.name
        pe.reference_date = self.date
        pe.remarks = self.remarks or f"Payment for {self.name}"

        pe.flags.ignore_permissions = True
        pe.insert()
        pe.submit()

        frappe.db.set_value("Kassa", self.name, "linked_doctype", "Payment Entry", update_modified=False)
        frappe.db.set_value("Kassa", self.name, "linked_entry", pe.name, update_modified=False)

        frappe.msgprint(_("Payment Entry {0} создан").format(
            frappe.utils.get_link_to_form("Payment Entry", pe.name)
        ))

    def get_paid_from_account(self, payment_type):
        """Payment type ga qarab paid_from accountni olish"""
        if payment_type == "Receive":
            # Приход - receivable/payable account (cash account valyutasiga mos)
            return self.get_party_account_by_currency()
        else:
            # Расход - cash account
            return self.cash_account

    def get_paid_to_account(self, payment_type):
        """Payment type ga qarab paid_to accountni olish"""
        if payment_type == "Receive":
            # Приход - cash account
            return self.cash_account
        else:
            # Расход - receivable/payable account (cash account valyutasiga mos)
            return self.get_party_account_by_currency()

    def get_party_account_by_currency(self):
        """Cash account valyutasiga mos party accountni olish

        USD cash account → 1310 (receivable) yoki 2110 (payable)
        """
        cash_currency = self.cash_account_currency or frappe.get_cached_value(
            "Account", self.cash_account, "account_currency"
        )

        if self.party_type == "Customer":
            account_number = "1310"
            account = frappe.db.get_value(
                "Account",
                {"company": self.company, "account_number": account_number, "is_group": 0},
                "name"
            )
            if account:
                return account
            # Fallback: valyutaga mos receivable account
            return frappe.db.get_value(
                "Account",
                {
                    "company": self.company,
                    "account_type": "Receivable",
                    "account_currency": cash_currency,
                    "is_group": 0
                },
                "name"
            ) or frappe.get_cached_value("Company", self.company, "default_receivable_account")

        elif self.party_type == "Supplier":
            account_number = "2110"
            account = frappe.db.get_value(
                "Account",
                {"company": self.company, "account_number": account_number, "is_group": 0},
                "name"
            )
            if account:
                return account
            # Fallback: valyutaga mos payable account
            return frappe.db.get_value(
                "Account",
                {
                    "company": self.company,
                    "account_type": "Payable",
                    "account_currency": cash_currency,
                    "is_group": 0
                },
                "name"
            ) or frappe.get_cached_value("Company", self.company, "default_payable_account")

        elif self.party_type == "Employee":
            # Employee uchun payroll payable account (2120)
            account_number = "2120"
            account = frappe.db.get_value(
                "Account",
                {"company": self.company, "account_number": account_number, "is_group": 0},
                "name"
            )
            if account:
                return account
            # Fallback: generic payable account
            return frappe.db.get_value(
                "Account",
                {
                    "company": self.company,
                    "account_type": "Payable",
                    "account_currency": cash_currency,
                    "is_group": 0
                },
                "name"
            )

    def validate_transaction_rules(self):
        """Specific transaction type and party type combinations."""
        if self.transaction_type == "Приход" and self.party_type == "Дивиденд":
            frappe.throw(_("Тип контрагента Дивиденд разрешен только для операции Расход"))

    def append_cash_counterparty_rows(self, je, counterparty_account, counterparty_values=None):
        """Append balanced cash/counterparty rows based on transaction direction."""
        amount = flt(self.amount)
        cash_values = {"account": self.cash_account}
        counterparty_row = {"account": counterparty_account}
        counterparty_row.update(counterparty_values or {})

        if self.transaction_type == "Приход":
            cash_values.update({
                "debit_in_account_currency": amount,
                "debit": amount,
            })
            counterparty_row.update({
                "credit_in_account_currency": amount,
                "credit": amount,
            })
        else:
            cash_values.update({
                "credit_in_account_currency": amount,
                "credit": amount,
            })
            counterparty_row.update({
                "debit_in_account_currency": amount,
                "debit": amount,
            })

        je.append("accounts", cash_values)
        je.append("accounts", counterparty_row)

    def create_dividend_journal_entry(self):
        """Dividend uchun Journal Entry yaratish (3200 accountga)"""
        if self.transaction_type == "Приход":
            frappe.throw(_("Тип контрагента Дивиденд разрешен только для операции Расход"))

        # Get dividend account (3200)
        dividend_account = frappe.db.get_value("Account",
            {"company": self.company, "account_number": "3200", "is_group": 0}, "name")

        if not dividend_account:
            frappe.throw(_("Счет дивидендов (3200) не найден для компании {0}").format(self.company))

        je = frappe.new_doc("Journal Entry")
        je.voucher_type = "Journal Entry"
        je.posting_date = self.date
        je.company = self.company
        je.cheque_no = self.name
        je.cheque_date = self.date
        je.user_remark = self.remarks or f"Dividend journal for {self.name}"

        self.append_cash_counterparty_rows(je, dividend_account)

        je.flags.ignore_permissions = True
        je.insert()
        je.submit()

        frappe.db.set_value("Kassa", self.name, "linked_doctype", "Journal Entry", update_modified=False)
        frappe.db.set_value("Kassa", self.name, "linked_entry", je.name, update_modified=False)

        frappe.msgprint(_("Journal Entry {0} для дивидендов создан").format(
            frappe.utils.get_link_to_form("Journal Entry", je.name)
        ))

    def create_expense_journal_entry(self):
        """Расходы uchun Journal Entry yaratish"""
        if not self.expense_account:
            frappe.throw(_("Пожалуйста, выберите счет расходов"))

        # Expense Cost Center dan cost_center olish
        cost_center = frappe.db.get_value(
            "Expense Cost Center",
            {"expense_account": self.expense_account},
            "cost_center"
        )

        je = frappe.new_doc("Journal Entry")
        je.voucher_type = "Journal Entry"
        je.posting_date = self.date
        je.company = self.company
        je.cheque_no = self.name
        je.cheque_date = self.date
        je.user_remark = self.remarks or f"Expense journal for {self.name}"

        self.append_cash_counterparty_rows(je, self.expense_account, {
            "cost_center": cost_center,
        })

        je.flags.ignore_permissions = True
        je.insert()
        je.submit()

        frappe.db.set_value("Kassa", self.name, "linked_doctype", "Journal Entry", update_modified=False)
        frappe.db.set_value("Kassa", self.name, "linked_entry", je.name, update_modified=False)

        frappe.msgprint(_("Journal Entry {0} для расходов создан").format(
            frappe.utils.get_link_to_form("Journal Entry", je.name)
        ))

    def create_transfer_payment_entry(self):
        """Перемещения uchun Internal Transfer Payment Entry yaratish"""
        pe = frappe.new_doc("Payment Entry")
        pe.payment_type = "Internal Transfer"
        pe.posting_date = self.date
        pe.company = self.company
        pe.mode_of_payment = self.mode_of_payment

        # Set accounts - from and to
        pe.paid_from = self.cash_account
        pe.paid_to = self.cash_account_to
        pe.paid_amount = flt(self.amount)
        pe.received_amount = flt(self.amount)

        # Set reference to Kassa
        pe.reference_no = self.name
        pe.reference_date = self.date
        pe.remarks = self.remarks or f"Transfer from {self.name}"

        pe.flags.ignore_permissions = True
        pe.insert()
        pe.submit()

        frappe.db.set_value("Kassa", self.name, "linked_doctype", "Payment Entry", update_modified=False)
        frappe.db.set_value("Kassa", self.name, "linked_entry", pe.name, update_modified=False)

        frappe.msgprint(_("Payment Entry {0} для перемещения создан").format(
            frappe.utils.get_link_to_form("Payment Entry", pe.name)
        ))

    def cancel_linked_entries(self):
        """Bog'langan Payment Entry va Journal Entrylarni cancel qilish"""
        # Cancel Payment Entries
        payment_entries = frappe.get_all("Payment Entry",
            filters={"reference_no": self.name, "docstatus": 1},
            pluck="name")

        for pe_name in payment_entries:
            pe = frappe.get_doc("Payment Entry", pe_name)
            pe.flags.ignore_permissions = True
            pe.cancel()
            frappe.msgprint(_("Payment Entry {0} отменен").format(pe_name))

        # Cancel Journal Entries (linked via cheque_no)
        journal_entries = frappe.get_all("Journal Entry",
            filters={"cheque_no": self.name, "docstatus": 1},
            pluck="name")

        for je_name in journal_entries:
            je_doc = frappe.get_doc("Journal Entry", je_name)
            je_doc.flags.ignore_permissions = True
            je_doc.cancel()
            frappe.msgprint(_("Journal Entry {0} отменен").format(je_name))

    def set_default_company(self):
        """Set default company for Перемещения if not set"""
        if self.transaction_type == "Перемещения" and not self.company:
            default_company = frappe.db.get_single_value("Global Defaults", "default_company")
            if default_company:
                self.company = default_company
            else:
                frappe.throw(_("Пожалуйста, установите компанию по умолчанию в настройках"))

    def set_cash_account(self):
        """Mode of Payment dan cash accountni olish"""
        if self.mode_of_payment and self.company:
            cash_account = get_cash_account(self.mode_of_payment, self.company)
            if cash_account:
                self.cash_account = cash_account

        # Set cash_account_to for transfer
        if self.mode_of_payment_to and self.company:
            cash_account_to = get_cash_account(self.mode_of_payment_to, self.company)
            if cash_account_to:
                self.cash_account_to = cash_account_to

    def set_cash_account_currency(self):
        """Cash account valyutasini olish"""
        if self.cash_account:
            self.cash_account_currency = frappe.get_cached_value("Account", self.cash_account, "account_currency")

    def set_party_currency(self):
        """Party default valyutasini olish"""
        if self.party and self.party_type in ["Customer", "Supplier"] and self.company:
            self.party_currency = get_party_currency(self.party_type, self.party, self.company)

    def set_balance(self):
        """Cash account balansini olish"""
        if self.cash_account:
            self.balance = get_account_balance(self.cash_account, self.company)

        # Set balance_to for transfer
        if self.cash_account_to:
            self.balance_to = get_account_balance(self.cash_account_to, self.company)

    def validate_party(self):
        """Party validatsiyasi"""
        if self.transaction_type in ["Приход", "Расход"]:
            if not self.party_type:
                frappe.throw(_("Пожалуйста, выберите тип контрагента"))

            if self.party_type == "Расходы":
                if not self.expense_account:
                    frappe.throw(_("Пожалуйста, выберите счет расходов"))
                self.party = None
            elif self.party_type == "Дивиденд":
                self.party = None
                self.expense_account = None
            else:
                if not self.party:
                    frappe.throw(_("Пожалуйста, выберите контрагента"))
                self.expense_account = None

    def validate_transfer(self):
        """Transfer validatsiyasi"""
        if self.transaction_type == "Перемещения":
            if not self.mode_of_payment_to:
                frappe.throw(_("Пожалуйста, выберите способ оплаты (куда)"))

            if self.mode_of_payment == self.mode_of_payment_to:
                frappe.throw(_("Способ оплаты источника и назначения должны отличаться"))

    def validate_amount(self):
        """Summa validatsiyasi"""
        if flt(self.amount) <= 0:
            frappe.throw(_("Сумма должна быть больше нуля"))

        # Rasxod uchun balansni tekshirish
        if self.transaction_type == "Расход" and flt(self.amount) > flt(self.balance):
            frappe.msgprint(
                _("Внимание: Сумма расхода ({0}) превышает остаток кассы ({1})").format(
                    frappe.format_value(self.amount, {"fieldtype": "Currency"}),
                    frappe.format_value(self.balance, {"fieldtype": "Currency"})
                ),
                indicator="orange",
                alert=True
            )

    def validate_currency(self):
        """Cash account va Party valyutasi mos kelishini tekshirish"""
        if self.transaction_type not in ["Приход", "Расход"]:
            return

        if self.party_type not in ["Customer", "Supplier"]:
            return

        if not self.cash_account_currency or not self.party_currency:
            return

        if self.cash_account_currency != self.party_currency:
            frappe.throw(
                _("Валюта кассы ({0}) не совпадает с валютой контрагента ({1}). Выберите соответствующий способ оплаты.").format(
                    self.cash_account_currency, self.party_currency
                )
            )


@frappe.whitelist()
def get_cash_account(mode_of_payment, company):
    """Mode of Payment uchun cash accountni olish"""
    if not mode_of_payment or not company:
        return None

    account = frappe.db.get_value(
        "Mode of Payment Account",
        {"parent": mode_of_payment, "company": company},
        "default_account"
    )
    return account


@frappe.whitelist()
def get_cash_account_with_currency(mode_of_payment, company):
    """Mode of Payment uchun cash account va currency olish"""
    if not mode_of_payment or not company:
        return {"account": None, "currency": None}

    account = frappe.db.get_value(
        "Mode of Payment Account",
        {"parent": mode_of_payment, "company": company},
        "default_account"
    )

    if account:
        currency = frappe.get_cached_value("Account", account, "account_currency")
        return {"account": account, "currency": currency}

    return {"account": None, "currency": None}


@frappe.whitelist()
def get_party_currency(party_type, party, company):
    """Party uchun default currency olish"""
    if not party_type or not party or not company:
        return None

    currency = None

    # Get default currency from Party Account or Party itself
    if party_type == "Customer":
        # Check Party Account first - get account and then its currency
        account = frappe.db.get_value(
            "Party Account",
            {"parenttype": "Customer", "parent": party, "company": company},
            "account"
        )
        if account:
            currency = frappe.get_cached_value("Account", account, "account_currency")
        if not currency:
            # Get from Customer default currency
            currency = frappe.get_cached_value("Customer", party, "default_currency")
        if not currency:
            # Get company default currency
            currency = frappe.get_cached_value("Company", company, "default_currency")
    elif party_type == "Supplier":
        # Check Party Account first - get account and then its currency
        account = frappe.db.get_value(
            "Party Account",
            {"parenttype": "Supplier", "parent": party, "company": company},
            "account"
        )
        if account:
            currency = frappe.get_cached_value("Account", account, "account_currency")
        if not currency:
            # Get from Supplier default currency
            currency = frappe.get_cached_value("Supplier", party, "default_currency")
        if not currency:
            # Get company default currency
            currency = frappe.get_cached_value("Company", company, "default_currency")
    else:
        currency = frappe.get_cached_value("Company", company, "default_currency")

    return currency


@frappe.whitelist()
def get_account_balance(account, company):
    """Account balansini account currency da olish"""
    if not account:
        return 0

    # Get balance in account currency (debit_in_account_currency - credit_in_account_currency)
    balance = frappe.db.sql("""
        SELECT SUM(debit_in_account_currency) - SUM(credit_in_account_currency) as balance
        FROM `tabGL Entry`
        WHERE account = %s
        AND company = %s
        AND is_cancelled = 0
    """, (account, company), as_dict=True)

    if balance and balance[0].balance:
        return flt(balance[0].balance)
    return 0


@frappe.whitelist()
def get_expense_accounts(doctype, txt, searchfield, start, page_len, filters):
    """Expense accountlarni olish"""
    company = filters.get("company")

    return frappe.db.sql("""
        SELECT name, account_name
        FROM `tabAccount`
        WHERE company = %(company)s
        AND root_type = 'Expense'
        AND is_group = 0
        AND (name LIKE %(txt)s OR account_name LIKE %(txt)s)
        ORDER BY name
        LIMIT %(start)s, %(page_len)s
    """, {
        "company": company,
        "txt": f"%{txt}%",
        "start": start,
        "page_len": page_len
    })

