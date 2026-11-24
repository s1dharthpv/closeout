import frappe
from frappe.tests import IntegrationTestCase

from closeout.closeout.report.close_readiness.close_readiness import execute as readiness
from closeout.closeout.report.closeout_trial_balance.closeout_trial_balance import execute as trial_balance

COMPANY = "Closeout Demo Co"
PCV_FILTERS = {
	"company": COMPANY,
	"with_period_closing_entry_for_opening": 1,
	"with_period_closing_entry_for_current_period": 1,
}


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


class TestPartialPeriodClosing(IntegrationTestCase):
	"""FY 2024-25 is closed with a real Period Closing Voucher, later years are not.

	A voucher cannot be posted for a year while an earlier year with postings is unclosed,
	so the only possible partial state is: oldest years closed, newest years open.
	"""

	def setUp(self):
		settings = frappe.get_single("Accounts Settings")
		previous = settings.use_legacy_controller_for_pcv
		# the default controller posts in the background, the legacy one posts at once
		frappe.db.set_single_value("Accounts Settings", "use_legacy_controller_for_pcv", 1)
		self.addCleanup(
			frappe.db.set_single_value, "Accounts Settings", "use_legacy_controller_for_pcv", previous
		)

		retained = frappe.db.get_value(
			"Account",
			{"company": COMPANY, "root_type": "Equity", "is_group": 0, "account_name": "Retained Earnings"},
		)
		self.pcv = frappe.get_doc(
			{
				"doctype": "Period Closing Voucher",
				"company": COMPANY,
				"transaction_date": "2025-03-31",
				"fiscal_year": "2024-2025",
				"period_start_date": "2024-04-01",
				"period_end_date": "2025-03-31",
				"closing_account_head": retained,
				"remarks": "closeout test",
			}
		).insert()
		self.pcv.submit()
		self.addCleanup(self._remove_pcv)

	def _remove_pcv(self):
		pcv = frappe.get_doc("Period Closing Voucher", self.pcv.name)
		if pcv.docstatus == 1:
			pcv.cancel()
		frappe.delete_doc("Period Closing Voucher", pcv.name, force=True)

	def _run(self, fiscal_year):
		_cols, data, _msg = trial_balance({**PCV_FILTERS, "fiscal_year": fiscal_year})
		return data

	def test_voucher_posted(self):
		self.assertEqual(
			frappe.db.get_value("Period Closing Voucher", self.pcv.name, "gle_processing_status"), "Completed"
		)

	def test_year_after_closed_year_needs_no_virtual_row(self):
		# FY24-25 profit (70,000) now sits in Retained Earnings, nothing is left unclosed before FY25-26
		data = self._run("2025-2026")
		self.assertFalse([d for d in data if d.get("account") == "Unclosed prior-year P&L (virtual)"])
		total = _total(data)
		self.assertEqual(total["opening_debit"], total["opening_credit"])
		self.assertEqual(total["closing_debit"], total["closing_credit"])

	def test_virtual_row_only_counts_unclosed_years(self):
		# before FY26-27: FY24-25 is closed, only FY25-26 profit (30,000) is unclosed
		data = self._run("2026-2027")
		row = next(d for d in data if d.get("account") == "Unclosed prior-year P&L (virtual)")
		self.assertEqual(row["opening_credit"], 30000)
		total = _total(data)
		self.assertEqual(total["opening_debit"], total["opening_credit"])
		self.assertEqual(total["closing_debit"], total["closing_credit"])

	def test_readiness_counts_only_the_unclosed_year(self):
		_cols, rows, *_rest = readiness({"company": COMPANY, "fiscal_year": "2026-2027"})
		check = next(r for r in rows if r["check"].startswith("Earlier fiscal years"))
		self.assertEqual((check["status"], check["count"]), ("Fail", 1))
