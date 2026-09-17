# Copyright (c) 2025, abdulloh and Contributors
# See license.txt

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from erpnext.controllers.tests.test_accounts_controller import make_customer

from extra.extra.doctype.kassa.kassa import get_exchange_rate


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


    def test_submit_routes_conversion_to_internal_transfer_creator(self):
        doc = self.make_kassa_doc(
            transaction_type="Конвертация",
            party_type=None,
            expense_account=None,
            amount=None,
        )
        doc.create_conversion_payment_entry = MagicMock()

        doc.on_submit()

        doc.create_conversion_payment_entry.assert_called_once_with()

    @patch("extra.extra.doctype.kassa.kassa.frappe.get_cached_value")
    def test_conversion_requires_different_uzs_usd_accounts(self, get_cached_value):
        get_cached_value.side_effect = lambda doctype, name, fieldname: {
            ("Account", "Cash USD", "account_currency"): "USD",
            ("Account", "Bank USD", "account_currency"): "USD",
            ("Account", "Cash UZS", "account_currency"): "UZS",
        }.get((doctype, name, fieldname))

        valid_doc = self.make_kassa_doc(
            transaction_type="Конвертация",
            party_type=None,
            expense_account=None,
            amount=None,
            mode_of_payment="USD Cash",
            cash_account="Cash USD",
            mode_of_payment_to="UZS Cash",
            cash_account_to="Cash UZS",
            exchange_rate=12500,
            debit_amount=100,
            credit_amount=1250000,
        )
        valid_doc.validate_conversion()

        invalid_doc = self.make_kassa_doc(
            transaction_type="Конвертация",
            party_type=None,
            expense_account=None,
            amount=None,
            mode_of_payment="USD Cash",
            cash_account="Cash USD",
            mode_of_payment_to="USD Bank",
            cash_account_to="Bank USD",
            exchange_rate=12500,
            debit_amount=100,
            credit_amount=100,
        )
        self.assertRaises(frappe.ValidationError, invalid_doc.validate_conversion)

    @patch("extra.extra.doctype.kassa.kassa.frappe.get_cached_value")
    def test_conversion_uses_entered_rate_for_company_currency(self, get_cached_value):
        get_cached_value.side_effect = lambda doctype, name, fieldname: {
            ("Company", self.company, "default_currency"): "UZS",
        }.get((doctype, name, fieldname))

        doc = self.make_kassa_doc(
            transaction_type="Конвертация",
            exchange_rate=12500,
        )

        self.assertEqual(doc.get_company_exchange_rate("UZS"), 1)
        self.assertEqual(doc.get_company_exchange_rate("USD"), 12500)

    @patch("extra.extra.doctype.kassa.kassa.frappe.db.get_value")
    def test_reverse_exchange_rate_keeps_small_rate_precision(self, get_value):
        get_value.side_effect = [None, 12190]

        self.assertEqual(
            get_exchange_rate("UZS", "USD", "2026-09-17"),
            0.000082034,
        )

    def test_conversion_payment_entry_uses_source_and_target_amounts(self):
        fake_payment_entry = SimpleNamespace(
            flags=SimpleNamespace(ignore_permissions=False),
            name="ACC-PAY-TEST-0001",
            insert=MagicMock(),
            submit=MagicMock(),
        )
        doc = self.make_kassa_doc(
            transaction_type="Конвертация",
            party_type=None,
            expense_account=None,
            amount=None,
            mode_of_payment="USD Cash",
            cash_account="Cash USD",
            mode_of_payment_to="UZS Cash",
            cash_account_to="Cash UZS",
            exchange_rate=12500,
            debit_amount=100,
            credit_amount=1250000,
        )
        doc.name = "KASSA-TEST-0001"
        doc.get_company_exchange_rate = MagicMock(
            side_effect=lambda currency: 12500 if currency == "USD" else 1
        )

        with (
            patch(
                "extra.extra.doctype.kassa.kassa.frappe.get_cached_value",
                side_effect=["USD", "UZS"],
            ),
            patch(
                "extra.extra.doctype.kassa.kassa.frappe.new_doc",
                return_value=fake_payment_entry,
            ),
            patch("extra.extra.doctype.kassa.kassa.frappe.db.set_value"),
            patch("extra.extra.doctype.kassa.kassa.frappe.msgprint"),
            patch(
                "extra.extra.doctype.kassa.kassa.frappe.utils.get_link_to_form",
                return_value="PAYMENT-LINK",
            ),
        ):
            doc.create_conversion_payment_entry()

        self.assertEqual(fake_payment_entry.payment_type, "Internal Transfer")
        self.assertEqual(fake_payment_entry.paid_from, "Cash USD")
        self.assertEqual(fake_payment_entry.paid_to, "Cash UZS")
        self.assertEqual(fake_payment_entry.paid_amount, 100)
        self.assertEqual(fake_payment_entry.received_amount, 1250000)
        self.assertEqual(fake_payment_entry.source_exchange_rate, 12500)
        self.assertEqual(fake_payment_entry.target_exchange_rate, 1)
        fake_payment_entry.insert.assert_called_once_with()
        fake_payment_entry.submit.assert_called_once_with()
