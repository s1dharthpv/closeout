from unittest.mock import patch

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


class TestReportFilters(IntegrationTestCase):
	"""Cost center, project and finance book filters keep the report balanced.

	Each extra journal entry is tagged on every line, so any filtered view of the ledger balances
	by itself. All entries fall in FY 2024-25, so they are prior-year profit for FY 2025-26.
	Base prior-year profit of the demo data (untagged) is 70,000.
	"""

	BASE = 70000

	def setUp(self):
		self.cash = "Cash - CDC"
		self.income = "Interest on Fixed Deposits - CDC"
		root = frappe.db.get_all(
			"Cost Center", {"company": COMPANY, "is_group": 1}, pluck="name", order_by="lft", limit=1
		)[0]
		self.cc = frappe.get_doc(
			{
				"doctype": "Cost Center",
				"cost_center_name": "Closeout B",
				"company": COMPANY,
				"parent_cost_center": root,
			}
		).insert()
		self.project = frappe.get_doc(
			{"doctype": "Project", "project_name": "Closeout Test Project", "company": COMPANY}
		).insert()
		self.book = frappe.get_doc({"doctype": "Finance Book", "finance_book_name": "Closeout FB"}).insert()
		self.entries = []
		self._post(40000, cost_center=self.cc.name)
		self._post(20000, project=self.project.name)
		self._post(10000, finance_book=self.book.name)
		self.addCleanup(self._cleanup)

	def _post(self, amount, cost_center=None, project=None, finance_book=None):
		extra = {}
		if cost_center:
			extra["cost_center"] = cost_center
		if project:
			extra["project"] = project
		je = frappe.get_doc(
			{
				"doctype": "Journal Entry",
				"company": COMPANY,
				"posting_date": "2024-12-01",
				"finance_book": finance_book,
				"accounts": [
					{"account": self.cash, "debit_in_account_currency": amount, **extra},
					{"account": self.income, "credit_in_account_currency": amount, **extra},
				],
			}
		).insert()
		je.submit()
		self.entries.append(je.name)

	def _cleanup(self):
		for name in self.entries:
			je = frappe.get_doc("Journal Entry", name)
			if je.docstatus == 1:
				je.cancel()
			frappe.delete_doc("Journal Entry", name, force=True)
		frappe.delete_doc("Finance Book", self.book.name, force=True)
		frappe.delete_doc("Project", self.project.name, force=True)
		frappe.delete_doc("Cost Center", self.cc.name, force=True)

	def _run(self, **filters):
		base = {"company": COMPANY, "fiscal_year": "2025-2026"}
		_cols, data, _msg = trial_balance({**base, **filters})
		virtual = next((d for d in data if d.get("account") == "Unclosed prior-year P&L (virtual)"), None)
		return virtual, _total(data)

	def _assert_balanced(self, total):
		self.assertEqual(total["opening_debit"], total["opening_credit"])
		self.assertEqual(total["closing_debit"], total["closing_credit"])

	def test_cost_center_filter(self):
		virtual, total = self._run(cost_center=self.cc.name)
		self.assertEqual(virtual["opening_credit"], 40000)
		self._assert_balanced(total)

	def test_project_filter(self):
		virtual, total = self._run(project=[self.project.name])
		self.assertEqual(virtual["opening_credit"], 20000)
		self._assert_balanced(total)

	def test_finance_book_filter(self):
		# entries of the chosen book are added to the entries that have no book
		virtual, total = self._run(finance_book=self.book.name)
		self.assertEqual(virtual["opening_credit"], self.BASE + 40000 + 20000 + 10000)
		self._assert_balanced(total)

	def test_no_filter_when_a_finance_book_exists(self):
		# core leaves out entries of any book that is not selected, so the virtual row must too
		virtual, total = self._run()
		self.assertEqual(virtual["opening_credit"], self.BASE + 40000 + 20000)
		self._assert_balanced(total)

	def test_default_finance_book_entries_are_included(self):
		previous = frappe.db.get_value("Company", COMPANY, "default_finance_book")
		frappe.db.set_value("Company", COMPANY, "default_finance_book", self.book.name)
		self.addCleanup(frappe.db.set_value, "Company", COMPANY, "default_finance_book", previous)
		frappe.clear_cache()
		virtual, total = self._run(include_default_book_entries=1)
		self.assertEqual(virtual["opening_credit"], self.BASE + 40000 + 20000 + 10000)
		self._assert_balanced(total)


class TestBackgroundAndStockChecks(IntegrationTestCase):
	"""The v16 checks: background jobs and the Stock Closing Entry.

	Documents are written straight to the table, so no background job or posting is triggered.
	"""

	END = "2027-03-31"

	def _insert(self, doctype, **values):
		doc = frappe.new_doc(doctype)
		doc.update({"company": COMPANY, "docstatus": 1, **values})
		doc.name = frappe.generate_hash(length=12)
		doc.flags.name_set = True
		doc.db_insert()
		self.addCleanup(frappe.db.delete, doctype, {"name": doc.name})
		return doc

	def _rows(self):
		_cols, rows, *_rest = readiness({"company": COMPANY, "fiscal_year": "2026-2027"})
		return {r["check"]: r for r in rows}

	def test_clean_site_passes_background_checks(self):
		rows = self._rows()
		for label in (
			"No pending or failed Repost Item Valuation",
			"No pending or failed Repost Accounting Ledger",
			"No Period Closing Voucher still processing",
		):
			self.assertEqual((rows[label]["status"], rows[label]["count"]), ("Pass", 0), label)

	def test_failed_repost_item_valuation_is_flagged(self):
		self._insert("Repost Item Valuation", status="Failed", posting_date="2026-06-01")
		row = self._rows()["No pending or failed Repost Item Valuation"]
		self.assertEqual((row["status"], row["count"]), ("Fail", 1))

	def test_repost_after_year_end_is_ignored(self):
		self._insert("Repost Item Valuation", status="Queued", posting_date="2027-06-01")
		row = self._rows()["No pending or failed Repost Item Valuation"]
		self.assertEqual(row["status"], "Pass")

	def test_pending_repost_accounting_ledger_is_flagged(self):
		self._insert("Repost Accounting Ledger", status="Queued")
		row = self._rows()["No pending or failed Repost Accounting Ledger"]
		self.assertEqual((row["status"], row["count"]), ("Fail", 1))

	def test_stock_closing_check_is_skipped_without_perpetual_inventory(self):
		self.assertNotIn("Stock Closing Entry is completed and up to date", self._rows())

	def _with_stock(self):
		"""Pretend the company uses perpetual inventory and has stock ledger entries."""
		real_exists = frappe.db.exists

		def exists(doctype, *args, **kwargs):
			if doctype == "Stock Ledger Entry":
				return True
			return real_exists(doctype, *args, **kwargs)

		for target, replacement in (
			(
				"closeout.closeout.report.close_readiness.close_readiness.is_perpetual_inventory_enabled",
				lambda company: True,
			),
			("frappe.db.exists", exists),
		):
			patcher = patch(target, replacement)
			patcher.start()
			self.addCleanup(patcher.stop)

	def test_missing_stock_closing_entry_fails(self):
		self._with_stock()
		row = self._rows()["Stock Closing Entry is completed and up to date"]
		self.assertEqual(row["status"], "Fail")
		self.assertIn("To Date", row["hint"])

	def test_unfinished_stock_closing_entry_fails(self):
		self._with_stock()
		self._insert("Stock Closing Entry", status="In Progress", to_date=self.END, from_date="2026-04-01")
		row = self._rows()["Stock Closing Entry is completed and up to date"]
		self.assertEqual(row["status"], "Fail")
		self.assertIn("In Progress", row["hint"])

	def test_completed_stock_closing_entry_passes(self):
		self._with_stock()
		self._insert("Stock Closing Entry", status="Completed", to_date=self.END, from_date="2026-04-01")
		row = self._rows()["Stock Closing Entry is completed and up to date"]
		self.assertEqual(row["status"], "Pass")

	def test_stale_stock_closing_entry_fails(self):
		self._with_stock()
		self._insert(
			"Stock Closing Entry",
			status="Completed",
			to_date=self.END,
			from_date="2026-04-01",
			modified="2020-01-01 00:00:00",
		)
		self._insert("Stock Ledger Entry", posting_date="2026-06-01", is_cancelled=0)
		row = self._rows()["Stock Closing Entry is completed and up to date"]
		self.assertEqual(row["status"], "Fail")
		self.assertIn("Regenerate", row["hint"])

	def test_earlier_failed_stock_closing_entry_fails(self):
		self._with_stock()
		self._insert("Stock Closing Entry", status="Completed", to_date=self.END, from_date="2026-04-01")
		self._insert("Stock Closing Entry", status="Failed", to_date="2026-03-31", from_date="2025-04-01")
		row = self._rows()["Stock Closing Entry is completed and up to date"]
		self.assertEqual((row["status"], row["count"]), ("Fail", 1))
		self.assertIn("failed", row["hint"])
