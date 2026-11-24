/* global erpnext */
frappe.query_reports["Closeout Trial Balance"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		{
			fieldname: "fiscal_year",
			label: __("Fiscal Year"),
			fieldtype: "Link",
			options: "Fiscal Year",
			default: erpnext.utils.get_fiscal_year(frappe.datetime.get_today()),
			reqd: 1,
		},
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date" },
		{
			fieldname: "cost_center",
			label: __("Cost Center"),
			fieldtype: "Link",
			options: "Cost Center",
		},
		{
			fieldname: "project",
			label: __("Project"),
			fieldtype: "MultiSelectList",
			get_data: (txt) => frappe.db.get_link_options("Project", txt),
		},
		{
			fieldname: "finance_book",
			label: __("Finance Book"),
			fieldtype: "Link",
			options: "Finance Book",
		},
		{
			fieldname: "with_period_closing_entry_for_opening",
			label: __("Period Closing Entry For Opening Balance"),
			fieldtype: "Check",
			default: 1,
		},
		{
			fieldname: "with_period_closing_entry_for_current_period",
			label: __("Period Closing Entry For Current Period"),
			fieldtype: "Check",
			default: 1,
		},
		{ fieldname: "show_zero_values", label: __("Show zero values"), fieldtype: "Check" },
		{
			fieldname: "show_unclosed_fy_pl_balances",
			label: __("Show unclosed fiscal year's P&L balances"),
			fieldtype: "Check",
		},
	],
	tree: true,
	name_field: "account",
	parent_field: "parent_account",
	initial_depth: 3,
};
