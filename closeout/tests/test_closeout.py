import frappe
from frappe.tests import IntegrationTestCase

from closeout.closeout.report.close_readiness.close_readiness import execute as readiness
from closeout.closeout.report.closeout_trial_balance.closeout_trial_balance import execute as trial_balance

COMPANY = "Closeout Demo Co"


def _total(data):
	return next(d for d in data if str(d.get("account", "")).strip("'") == "Total")


class TestCloseout(IntegrationTestCase):
	"""Run on a site seeded with closeout.tests.seed.run (two unclosed fiscal years)."""

	def test_opening_balances_without_period_closing_voucher(self):
		for fy in ("2025-2026", "2026-2027"):
			_cols, data, _msg = trial_balance({"company": COMPANY, "fiscal_year": fy})
			total = _total(data)
			self.assertEqual(total["opening_debit"], total["opening_credit"], fy)
			self.assertEqual(total["closing_debit"], total["closing_credit"], fy)

	def test_balances_with_net_values(self):
		for fy in ("2025-2026", "2026-2027"):
			_cols, data, _msg = trial_balance({"company": COMPANY, "fiscal_year": fy, "show_net_values": 1})
			total = _total(data)
			self.assertEqual(total["opening_debit"], total["opening_credit"], fy)
			self.assertEqual(total["closing_debit"], total["closing_credit"], fy)

	def test_virtual_row_amount(self):
		# FY24-25 profit 70,000 + FY25-26 profit 30,000 sit before FY26-27
		_cols, data, _msg = trial_balance({"company": COMPANY, "fiscal_year": "2026-2027"})
		row = next(d for d in data if d.get("account") == "Unclosed prior-year P&L (virtual)")
		self.assertEqual(row["opening_credit"], 100000)

	def test_first_year_has_no_virtual_row(self):
		_cols, data, _msg = trial_balance({"company": COMPANY, "fiscal_year": "2024-2025"})
		self.assertFalse([d for d in data if d.get("account") == "Unclosed prior-year P&L (virtual)"])

	def test_readiness_flags_missing_period_closing_voucher(self):
		_cols, rows, *_rest = readiness({"company": COMPANY, "fiscal_year": "2026-2027"})
		pcv = next(r for r in rows if r["check"].startswith("Earlier fiscal years"))
		self.assertEqual(pcv["status"], "Fail")
		self.assertEqual(pcv["count"], 2)

	def test_readiness_flags_draft_journal_entry(self):
		je = frappe.get_doc(
			{
				"doctype": "Journal Entry",
				"company": COMPANY,
				"posting_date": "2026-06-01",
				"accounts": [
					{"account": "Cash - CDC", "debit_in_account_currency": 1},
					{"account": "Interest on Fixed Deposits - CDC", "credit_in_account_currency": 1},
				],
			}
		).insert()
		self.addCleanup(lambda: frappe.delete_doc("Journal Entry", je.name, force=True))
		_cols, rows, *_rest = readiness({"company": COMPANY, "fiscal_year": "2026-2027"})
		draft = next(r for r in rows if r["check"] == "No draft Journal Entry")
		self.assertEqual((draft["status"], draft["count"]), ("Fail", 1))
