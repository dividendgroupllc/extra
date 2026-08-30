# Copyright (c) 2025, abdulloh and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from erpnext.controllers.tests.test_accounts_controller import make_customer


class TestKassa(FrappeTestCase):
    """
    Integration-style tests for Kassa accounting flows.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.load_accounting_context()
        cls.customer = make_customer("_Test Kassa Customer")

    def tearDown(self):
        frappe.db.rollback()

    @classmethod
    def load_accounting_context(cls):
        mode_of_payment_row = frappe.get_all(
            "Mode of Payment Account",
            fields=["parent", "company", "default_account"],
            limit=1,
        )[0]
        cls.company = mode_of_payment_row.company
        cls.mode_of_payment = mode_of_payment_row.parent
        cls.cash_account = mode_of_payment_row.default_account
        cls.expense_account = frappe.db.get_value(
            "Account",
            {"company": cls.company, "root_type": "Expense", "is_group": 0},
            "name",
        )

    def make_kassa_doc(self, **overrides):
        data = {
            "doctype": "Kassa",
            "date": frappe.utils.nowdate(),
            "transaction_type": "Расход",
            "mode_of_payment": self.mode_of_payment,
            "company": self.company,
            "party_type": "Расходы",
            "expense_account": self.expense_account,
            "amount": 150,
            "remarks": "test",
        }
        data.update(overrides)
        return frappe.get_doc(data)

    def get_journal_accounts(self, journal_entry_name):
        accounts = frappe.get_all(
            "Journal Entry Account",
            filters={"parent": journal_entry_name},
            fields=["account", "debit_in_account_currency", "credit_in_account_currency"],
            order_by="idx asc",
        )
        return {row.account: row for row in accounts}

    def test_expense_journal_entry_for_expense_outflow(self):
        doc = self.make_kassa_doc()
        doc.insert()
        doc.submit()

        kassa = frappe.get_doc("Kassa", doc.name)
        self.assertEqual(kassa.linked_doctype, "Journal Entry")

        accounts = self.get_journal_accounts(kassa.linked_entry)
        self.assertEqual(accounts[self.cash_account].credit_in_account_currency, 150)
        self.assertEqual(accounts[self.cash_account].debit_in_account_currency, 0)
        self.assertEqual(accounts[self.expense_account].debit_in_account_currency, 150)
        self.assertEqual(accounts[self.expense_account].credit_in_account_currency, 0)

    def test_expense_journal_entry_for_expense_inflow(self):
        doc = self.make_kassa_doc(transaction_type="Приход")
        doc.insert()
        doc.submit()

        kassa = frappe.get_doc("Kassa", doc.name)
        self.assertEqual(kassa.linked_doctype, "Journal Entry")

        accounts = self.get_journal_accounts(kassa.linked_entry)
        self.assertEqual(accounts[self.cash_account].debit_in_account_currency, 150)
        self.assertEqual(accounts[self.cash_account].credit_in_account_currency, 0)
        self.assertEqual(accounts[self.expense_account].credit_in_account_currency, 150)
        self.assertEqual(accounts[self.expense_account].debit_in_account_currency, 0)

    def test_dividend_is_blocked_for_income(self):
        doc = self.make_kassa_doc(
            transaction_type="Приход",
            party_type="Дивиденд",
            expense_account=None,
        )

        self.assertRaisesRegex(
            frappe.ValidationError,
            "Дивиденд",
            doc.insert,
        )

    def test_customer_income_creates_receive_payment_entry(self):
        doc = self.make_kassa_doc(
            transaction_type="Приход",
            party_type="Customer",
            party=self.customer,
            expense_account=None,
        )

        doc.insert()
        doc.submit()

        kassa = frappe.get_doc("Kassa", doc.name)
        payment_entry = frappe.get_doc("Payment Entry", kassa.linked_entry)

        self.assertEqual(kassa.linked_doctype, "Payment Entry")
        self.assertEqual(payment_entry.payment_type, "Receive")
        self.assertEqual(payment_entry.paid_to, self.cash_account)
