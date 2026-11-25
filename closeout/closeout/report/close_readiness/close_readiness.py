import frappe
from frappe import _
from frappe.query_builder.functions import Count, Max, Sum
from frappe.utils import flt, formatdate, getdate

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
	rows.append(check_stock_closing_entry(filters.company, end))
	rows.extend(check_background_jobs(filters.company, end))
	rows.append(check_unposted_depreciation(filters.company, end))
	rows.extend(check_exchange_revaluation(filters.company, end))
	currency = frappe.get_cached_value("Company", filters.company, "default_currency")
	rows = [r for r in rows if r]
	for r in rows:
		r["currency"] = currency

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
		{"fieldname": "check", "label": _("Check"), "fieldtype": "Data", "width": 430, "align": "left"},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 90, "align": "left"},
		{"fieldname": "count", "label": _("Count"), "fieldtype": "Int", "width": 80},
		{
			"fieldname": "amount",
			"label": _("Amount"),
			"fieldtype": "Currency",
			"options": "currency",
			"width": 130,
		},
		{"fieldname": "hint", "label": _("What to do"), "fieldtype": "Data", "width": 820, "align": "left"},
	]


def _row(check, ok, count=0, amount=0.0, hint="", currency=None):
	return {
		"check": check,
		"status": "Pass" if ok else "Fail",
		"count": count,
		"amount": flt(amount),
		"hint": "" if ok else hint,
		"currency": currency,
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
		hint=_("No voucher for: {0}.").format(", ".join(sorted(unclosed))),
	)


def check_stock_closing_entry(company, end):
	"""Mirrors what Period Closing Voucher demands at submit, but shown up front and in one place.

	Core only asks for a company-wide, completed and fresh Stock Closing Entry when the company
	uses perpetual inventory and has stock ledger entries up to the period end.
	"""
	# erpnext is imported inside the checks, not at module level: it is a declared dependency
	# that may not be installed yet when the marketplace validates this app on its own.
	from erpnext import is_perpetual_inventory_enabled
	from erpnext.stock.doctype.stock_closing_entry.stock_closing_entry import apply_unscoped_filters

	label = _("Stock Closing Entry is completed and up to date")
	if not frappe.db.exists("DocType", "Stock Closing Entry"):
		return None
	if not is_perpetual_inventory_enabled(company) or not frappe.db.exists(
		"Stock Ledger Entry", {"company": company, "is_cancelled": 0, "posting_date": ("<=", end)}
	):
		return None

	failed = frappe.db.count("Stock Closing Entry", {"company": company, "status": "Failed", "docstatus": 1})
	entry = frappe.db.get_value(
		"Stock Closing Entry",
		apply_unscoped_filters({"company": company, "to_date": end, "docstatus": 1}),
		["name", "status", "modified"],
		as_dict=True,
	)
	if not entry:
		return _row(
			label,
			False,
			count=1,
			hint=_("Create a company-wide Stock Closing Entry with To Date {0}.").format(formatdate(end)),
		)
	if entry.status != "Completed":
		return _row(
			label,
			False,
			count=1,
			hint=_("{0} is {1}. Wait for it, or fix it if it failed.").format(entry.name, entry.status),
		)

	sle = frappe.qb.DocType("Stock Ledger Entry")
	last_change = (
		frappe.qb.from_(sle)
		.select(Max(sle.modified))
		.where((sle.company == company) & (sle.posting_date <= end))
	).run()[0][0]
	if last_change and last_change > entry.modified:
		return _row(
			label,
			False,
			count=1,
			hint=_("Stock moved after {0} was made. Regenerate it.").format(entry.name),
		)
	if failed:
		return _row(
			label,
			False,
			count=failed,
			hint=_(
				"{0} earlier Stock Closing Entries failed. Cancel them so they are not used as an opening balance."
			).format(failed),
		)
	return _row(label, True)


def check_background_jobs(company, end):
	"""Reposting and closing run in the background on v16. Anything not finished can change the numbers."""
	unfinished = (
		(
			_("No pending or failed Repost Item Valuation"),
			"Repost Item Valuation",
			{
				"company": company,
				"docstatus": 1,
				"status": ("in", ["Queued", "In Progress", "Failed"]),
				"posting_date": ("<=", end),
			},
			_("Let these finish, or open the failed ones and retry."),
		),
		(
			_("No pending or failed Repost Accounting Ledger"),
			"Repost Accounting Ledger",
			{
				"company": company,
				"docstatus": 1,
				"status": ("in", ["Queued", "In Progress", "Partially Reposted", "Failed"]),
			},
			_("Let these finish, or open the failed ones and retry."),
		),
	)
	out = []
	for label, doctype, filters, hint in unfinished:
		if not frappe.db.exists("DocType", doctype):
			continue
		count = frappe.db.count(doctype, filters)
		out.append(_row(label, count == 0, count=count, hint=hint))

	if frappe.db.exists("DocType", "Process Period Closing Voucher"):
		ppcv = frappe.qb.DocType("Process Period Closing Voucher")
		pcv = frappe.qb.DocType("Period Closing Voucher")
		count = (
			frappe.qb.from_(ppcv)
			.join(pcv)
			.on(ppcv.parent_pcv == pcv.name)
			.select(Count(ppcv.name))
			.where(
				(pcv.company == company)
				& (ppcv.docstatus == 1)
				& (ppcv.status.isin(["Queued", "Running", "Paused"]))
			)
		).run()[0][0]
		out.append(
			_row(
				_("No Period Closing Voucher still processing"),
				not count,
				count=int(count or 0),
				hint=_("A voucher is still posting in the background. Wait before reading the books."),
			)
		)
	return out


DEPRECIABLE_ASSET_STATUSES = ["Submitted", "Partially Depreciated"]


def check_unposted_depreciation(company, end):
	"""Depreciation due by year end that has no journal entry.

	Same definition of "due" as core's depreciation booking job (assets.doctype.asset.depreciation),
	but core only books it when Accounts Settings has automatic booking on and never reports what was
	left behind.
	"""
	if not frappe.db.exists("DocType", "Asset Depreciation Schedule"):
		return None
	asset = frappe.qb.DocType("Asset")
	if not frappe.db.exists("Asset", {"company": company, "docstatus": 1, "calculate_depreciation": 1}):
		return None

	ads = frappe.qb.DocType("Asset Depreciation Schedule")
	ds = frappe.qb.DocType("Depreciation Schedule")
	count, amount = (
		frappe.qb.from_(ads)
		.join(asset)
		.on(ads.asset == asset.name)
		.join(ds)
		.on(ds.parent == ads.name)
		.select(Count(ds.name), Sum(ds.depreciation_amount))
		.where(
			(asset.company == company)
			& (asset.calculate_depreciation == 1)
			& (asset.docstatus == 1)
			& (ads.docstatus == 1)
			& (asset.status.isin(DEPRECIABLE_ASSET_STATUSES))
			& (ds.schedule_date <= end)
			& (ds.journal_entry.isnull() | (ds.journal_entry == ""))
		)
	).run()[0]
	count, amount = int(count or 0), flt(amount)

	automatic = frappe.get_single_value("Accounts Settings", "book_asset_depreciation_entry_automatically")
	hint = _(
		"Book these from the asset (Make Depreciation Entry). If you book depreciation by journal entry, ignore this."
	)
	if not automatic:
		hint = _("Automatic booking is off in Accounts Settings.") + " " + hint
	return _row(
		_("Depreciation due by year end is booked"),
		count == 0,
		count=count,
		amount=amount,
		hint=hint,
	)


def _local_exchange_rate(from_currency, to_currency, on_date):
	"""Latest Currency Exchange record on or before the date. Never calls the online rate service.

	Core's get_exchange_rate falls back to an HTTP request when no local rate exists, which a report
	that anyone can refresh must not do.
	"""
	rows = frappe.get_all(
		"Currency Exchange",
		filters={"from_currency": from_currency, "to_currency": to_currency, "date": ("<=", on_date)},
		fields=["exchange_rate"],
		order_by="date desc",
		limit=1,
	)
	return flt(rows[0].exchange_rate) if rows else 0.0


def check_exchange_revaluation(company, end):
	"""Foreign currency balances still at old rates, and revaluation journals left in Draft.

	Compares the ledger itself with the rate at year end, so a revaluation booked by hand passes too.
	Which accounts to look at comes from core (Exchange Rate Revaluation); only the rate lookup differs.
	"""
	from erpnext.accounts.doctype.exchange_rate_revaluation.exchange_rate_revaluation import (
		ExchangeRateRevaluation,
	)

	if not frappe.db.exists("DocType", "Exchange Rate Revaluation"):
		return []
	out = []
	currency = frappe.get_cached_value("Company", company, "default_currency")

	balances = ExchangeRateRevaluation.get_account_balance_from_gle(
		company=company,
		posting_date=end,
		account=None,
		party_type=None,
		party=None,
		rounding_loss_allowance=0.0,
	)
	stale, missing_rate, gain_loss_total = 0, set(), 0.0
	for b in balances:
		if b.zero_balance:
			continue
		rate = _local_exchange_rate(b.account_currency, currency, end)
		if not rate:
			missing_rate.add(b.account_currency)
			continue
		diff = flt(b.balance_in_account_currency * rate - b.balance, 2)
		if diff:
			stale += 1
			gain_loss_total += diff
	if balances:
		if missing_rate:
			out.append(
				_row(
					_("Exchange rate available for every foreign currency at year end"),
					False,
					count=len(missing_rate),
					hint=_("No Currency Exchange record on or before {0} for: {1}.").format(
						formatdate(end), ", ".join(sorted(missing_rate))
					),
				)
			)
		out.append(
			_row(
				_("Foreign currency balances revalued at year end rates"),
				stale == 0,
				count=stale,
				amount=gain_loss_total,
				hint=_(
					"Still at an older rate. Create an Exchange Rate Revaluation dated {0}, or book it by journal entry."
				).format(formatdate(end)),
			)
		)

	draft_err = frappe.db.count(
		"Exchange Rate Revaluation", {"company": company, "docstatus": 0, "posting_date": ("<=", end)}
	)
	je = frappe.qb.DocType("Journal Entry")
	jea = frappe.qb.DocType("Journal Entry Account")
	draft_jvs = (
		frappe.qb.from_(je)
		.join(jea)
		.on(jea.parent == je.name)
		.select(je.name)
		.distinct()
		.where(
			(je.company == company)
			& (je.docstatus == 0)
			& (jea.reference_type == "Exchange Rate Revaluation")
			& (je.posting_date <= end)
		)
	).run()
	pending = draft_err + len(draft_jvs)
	if balances or pending or frappe.db.exists("Exchange Rate Revaluation", {"company": company}):
		out.append(
			_row(
				_("No Exchange Rate Revaluation left in Draft"),
				pending == 0,
				count=pending,
				hint=_(
					"Revaluation or its journals still in Draft, so nothing is posted. Submit them, or tick Submit ERR Journals? on the Company."
				),
			)
		)
	return out
