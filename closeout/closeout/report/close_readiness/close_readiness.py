import frappe
from frappe import _
from frappe.query_builder.functions import Count, Sum
from frappe.utils import flt, getdate

DRAFT_DOCTYPES = (
	("Sales Invoice", "posting_date"),
	("Purchase Invoice", "posting_date"),
	("Journal Entry", "posting_date"),
	("Payment Entry", "posting_date"),
	("Delivery Note", "posting_date"),
	("Purchase Receipt", "posting_date"),
	("Stock Entry", "posting_date"),
)


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not (filters.company and filters.fiscal_year):
		frappe.throw(_("Company and Fiscal Year are required"))

	fy = frappe.get_cached_value(
		"Fiscal Year", filters.fiscal_year, ["year_start_date", "year_end_date"], as_dict=True
	)
	if not fy:
		frappe.throw(_("Fiscal Year {0} does not exist").format(filters.fiscal_year))
	start, end = getdate(fy.year_start_date), getdate(fy.year_end_date)

	rows = []
	rows.append(check_gl_balanced(filters.company, start, end))
	rows.extend(check_drafts(filters.company, start, end))
	rows.append(check_unreconciled_bank(filters.company, start, end))
	rows.append(check_unallocated_payments(filters.company, start, end))
	rows.append(check_unclosed_prior_years(filters.company, start))
	rows = [r for r in rows if r]

	failed = sum(1 for r in rows if r["status"] == "Fail")
	summary = [
		{"value": len(rows) - failed, "label": _("Passed"), "datatype": "Int", "indicator": "Green"},
		{
			"value": failed,
			"label": _("Needs attention"),
			"datatype": "Int",
			"indicator": "Red" if failed else "Green",
		},
	]
	return get_columns(), rows, None, None, summary


def get_columns():
	return [
		{"fieldname": "check", "label": _("Check"), "fieldtype": "Data", "width": 320},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 90},
		{"fieldname": "count", "label": _("Count"), "fieldtype": "Int", "width": 90},
		{"fieldname": "amount", "label": _("Amount"), "fieldtype": "Float", "width": 130},
		{"fieldname": "hint", "label": _("What to do"), "fieldtype": "Data", "width": 420},
		{"fieldname": "doctype", "label": _("Open list of"), "fieldtype": "Data", "width": 140, "hidden": 1},
	]


def _row(check, ok, count=0, amount=0.0, hint="", doctype=None):
	return {
		"check": check,
		"status": "Pass" if ok else "Fail",
		"count": count,
		"amount": flt(amount),
		"hint": "" if ok else hint,
		"doctype": doctype,
	}


def check_gl_balanced(company, start, end):
	gle = frappe.qb.DocType("GL Entry")
	diff = (
		frappe.qb.from_(gle)
		.select(Sum(gle.debit) - Sum(gle.credit))
		.where((gle.company == company) & (gle.is_cancelled == 0) & (gle.posting_date <= end))
	).run()[0][0]
	diff = flt(diff, 2)
	return _row(
		_("GL debits equal credits up to year end"),
		diff == 0,
		amount=diff,
		hint=_("Debits and credits differ. Run the General Ledger and Trial Balance to find the voucher."),
	)


def check_drafts(company, start, end):
	out = []
	for doctype, date_field in DRAFT_DOCTYPES:
		meta = frappe.get_meta(doctype)
		if not meta.has_field("company"):
			continue
		count = frappe.db.count(
			doctype,
			{"docstatus": 0, "company": company, date_field: ("between", [start, end])},
		)
		out.append(
			_row(
				_("No draft {0}").format(_(doctype)),
				count == 0,
				count=count,
				hint=_("Submit or delete these drafts before closing."),
				doctype=doctype,
			)
		)
	return out


def check_unreconciled_bank(company, start, end):
	if not frappe.db.exists("DocType", "Bank Transaction"):
		return None
	count = frappe.db.count(
		"Bank Transaction",
		{
			"docstatus": 1,
			"company": company,
			"status": ("in", ["Pending", "Unreconciled"]),
			"date": ("between", [start, end]),
		},
	)
	return _row(
		_("All bank transactions reconciled"),
		count == 0,
		count=count,
		hint=_("Reconcile these in the Bank Reconciliation Tool."),
		doctype="Bank Transaction",
	)


def check_unallocated_payments(company, start, end):
	pe = frappe.qb.DocType("Payment Entry")
	res = (
		frappe.qb.from_(pe)
		.select(Count(pe.name), Sum(pe.unallocated_amount))
		.where(
			(pe.docstatus == 1)
			& (pe.company == company)
			& (pe.unallocated_amount > 0)
			& (pe.posting_date.between(start, end))
		)
	).run()[0]
	count, amount = int(res[0] or 0), flt(res[1])
	return _row(
		_("No unallocated payments"),
		count == 0,
		count=count,
		amount=amount,
		hint=_("Allocate these payments to invoices, or confirm they are real advances."),
		doctype="Payment Entry",
	)


def check_unclosed_prior_years(company, start):
	fiscal_years = frappe.get_all(
		"Fiscal Year", filters={"year_end_date": ("<", start)}, fields=["name", "year_end_date"]
	)
	closed = set(
		frappe.get_all(
			"Period Closing Voucher",
			filters={"docstatus": 1, "company": company},
			pluck="period_end_date",
		)
	)
	closed = {getdate(d) for d in closed}
	unclosed = [fy.name for fy in fiscal_years if getdate(fy.year_end_date) not in closed]
	return _row(
		_("Earlier fiscal years have a Period Closing Voucher"),
		not unclosed,
		count=len(unclosed),
		hint=_("No voucher for: {0}. Closeout Trial Balance shows their profit as a virtual row.").format(
			", ".join(sorted(unclosed))
		),
	)
