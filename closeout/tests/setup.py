"""Test site setup, run by `bench run-tests` through the before_tests hook.

Leaves a site with the demo company and the two unclosed fiscal years the tests expect.
Safe to run again: nothing is created twice.
"""

import frappe

from closeout.tests.seed import run as seed

COMPANY = "Closeout Demo Co"
ABBR = "CDC"


def before_tests():
	frappe.clear_cache()
	if not frappe.is_setup_complete():
		_complete_setup_wizard()
	elif not frappe.db.exists("Company", COMPANY):
		create_company(COMPANY, ABBR)
	if not frappe.db.exists("Journal Entry", {"company": COMPANY}):
		seed(COMPANY)
	frappe.db.commit()  # nosemgrep


def _complete_setup_wizard():
	from frappe.desk.page.setup_wizard.setup_wizard import setup_complete

	# The first fiscal year starts in April 2024 so the seed ledger entries from 2024 have a year.
	setup_complete(
		{
			"currency": "INR",
			"full_name": "Test User",
			"company_name": COMPANY,
			"timezone": "Asia/Kolkata",
			"company_abbr": ABBR,
			"industry": "Manufacturing",
			"country": "India",
			"fy_start_date": "2024-04-01",
			"fy_end_date": "2025-03-31",
			"language": "english",
			"company_tagline": "Testing",
			"email": "test@example.com",
			"password": "test",
			"chart_of_accounts": "Standard",
		}
	)


def create_company(name, abbr):
	return frappe.get_doc(
		{
			"doctype": "Company",
			"company_name": name,
			"abbr": abbr,
			"default_currency": "INR",
			"country": "India",
			"create_chart_of_accounts_based_on": "Standard Template",
			"chart_of_accounts": "Standard",
		}
	).insert()
