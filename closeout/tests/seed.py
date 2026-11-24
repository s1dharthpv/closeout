"""Seed a demo company with two unclosed fiscal years (no Period Closing Voucher)."""

import frappe
from frappe.utils import getdate


def _fy(name, start, end, company):
	if frappe.db.exists("Fiscal Year", name):
		return
	doc = frappe.get_doc(
		{
			"doctype": "Fiscal Year",
			"year": name,
			"year_start_date": start,
			"year_end_date": end,
			"companies": [{"company": company}],
		}
	)
	doc.insert(ignore_if_duplicate=True)


def _je(company, date, debit_account, credit_account, amount):
	je = frappe.get_doc(
		{
			"doctype": "Journal Entry",
			"company": company,
			"posting_date": getdate(date),
			"voucher_type": "Journal Entry",
			"accounts": [
				{"account": debit_account, "debit_in_account_currency": amount},
				{"account": credit_account, "credit_in_account_currency": amount},
			],
		}
	)
	je.insert()
	je.submit()
	return je.name


def run(company="Closeout Demo Co"):
	abbr = frappe.db.get_value("Company", company, "abbr")
	cash = frappe.db.get_value("Account", {"company": company, "account_type": "Cash", "is_group": 0})
	income = frappe.db.get_value("Account", {"company": company, "root_type": "Income", "is_group": 0})
	expense = frappe.db.get_value(
		"Account", {"company": company, "root_type": "Expense", "is_group": 0, "account_type": ""}
	) or frappe.db.get_value("Account", {"company": company, "root_type": "Expense", "is_group": 0})
	_fy("2025-2026", "2025-04-01", "2026-03-31", company)
	_fy("2026-2027", "2026-04-01", "2027-03-31", company)
	out = [
		_je(company, "2024-06-15", cash, income, 100000),
		_je(company, "2024-09-10", expense, cash, 30000),
		_je(company, "2025-07-01", cash, income, 50000),
		_je(company, "2025-08-01", expense, cash, 20000),
		_je(company, "2026-05-01", cash, income, 10000),
	]
	frappe.db.commit()
	return {"abbr": abbr, "cash": cash, "income": income, "expense": expense, "jes": out}
