import erpnext
import frappe
from erpnext.accounts.report.financial_statements import get_cost_centers_with_children
from erpnext.accounts.report.trial_balance.trial_balance import execute as core_execute
from frappe import _
from frappe.query_builder.functions import Sum
from frappe.utils import flt, getdate

VIRTUAL_ROW = "Unclosed prior-year P&L (virtual)"


def execute(filters=None):
	filters = frappe._dict(filters or {})
	columns, data = core_execute(filters)
	data = data or []

	if filters.get("show_unclosed_fy_pl_balances"):
		# Core already carries old P&L balances in the opening, so it balances by itself.
		return (
			columns,
			data,
			_("Core option 'Show unclosed fiscal year's P&L balances' is on; no virtual row added."),
		)

	virtual = get_unclosed_pl_before(filters)
	if not virtual:
		return columns, data, None

	profit = flt(virtual)  # credit minus debit of P&L accounts before the fiscal year
	row = {
		"account": VIRTUAL_ROW,
		"account_name": VIRTUAL_ROW,
		"acc_name": VIRTUAL_ROW,
		"indent": 0,
		"has_value": True,
		"currency": filters.get("presentation_currency") or erpnext.get_company_currency(filters.company),
		"opening_debit": -profit if profit < 0 else 0.0,
		"opening_credit": profit if profit > 0 else 0.0,
		"debit": 0.0,
		"credit": 0.0,
		"closing_debit": -profit if profit < 0 else 0.0,
		"closing_credit": profit if profit > 0 else 0.0,
	}

	total = next((d for d in data if d and str(d.get("account", "")).strip("'") == "Total"), None)
	if total is None:
		data.append(row)
	else:
		data.insert(data.index(total), row)
		for key in ("opening_debit", "opening_credit", "closing_debit", "closing_credit"):
			total[key] = flt(total.get(key)) + row[key]

	msg = _(
		"Opening includes {0} of profit from earlier fiscal years that have no Period Closing Voucher, "
		"shown as one virtual equity row. Nothing is posted."
	).format(frappe.format_value(abs(profit), {"fieldtype": "Currency"}))
	return columns, data, msg


def get_unclosed_pl_before(filters):
	"""Credit minus debit of every Profit and Loss GL entry before the fiscal year starts.

	Period Closing Voucher entries are included on purpose: they offset the P&L of the years they
	closed, so what remains is exactly the profit that was never moved to equity.
	"""
	gle = frappe.qb.DocType("GL Entry")
	account = frappe.qb.DocType("Account")
	query = (
		frappe.qb.from_(gle)
		.join(account)
		.on(gle.account == account.name)
		.select(Sum(gle.credit) - Sum(gle.debit))
		.where(
			(gle.company == filters.company)
			& (gle.is_cancelled == 0)
			& (account.report_type == "Profit and Loss")
			& (gle.posting_date < getdate(filters.year_start_date))
		)
	)
	if filters.get("cost_center"):
		query = query.where(gle.cost_center.isin(get_cost_centers_with_children(filters.cost_center)))
	if filters.get("project"):
		query = query.where(gle.project.isin(filters.project))
	if filters.get("finance_book"):
		query = query.where(gle.finance_book.isin([filters.finance_book, ""]) | gle.finance_book.isnull())
	return query.run()[0][0] or 0
