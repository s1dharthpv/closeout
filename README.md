# Closeout

Year-end close helper for ERPNext on Frappe v16.

Closeout adds two reports that help when books are closed without a Period Closing Voucher every year. It reads your ledger and posts nothing.

## The problem

ERPNext's Trial Balance starts Income and Expense accounts afresh each fiscal year. It assumes the previous year's profit was already moved to equity by a Period Closing Voucher. If you never posted that voucher, the opening balance does not balance: Opening Debit and Opening Credit differ by the accumulated profit of the earlier years.

## What you get

### Closeout Trial Balance

The standard Trial Balance plus one extra row, **Unclosed prior-year P&L (virtual)**. The row carries the profit from earlier fiscal years that no Period Closing Voucher has closed, so the opening and closing totals balance.

- Nothing is posted and no data is changed.
- The row is calculated as the net of all Profit and Loss ledger entries before the fiscal year, voucher entries included. In theory only the unclosed remainder is left, but this has not yet been tested on a company that has closed some years and not others.
- If you tick ERPNext's own "Show unclosed fiscal year's P&L balances", the report does not add the row, since that option already balances the opening.
- Filters: company, fiscal year, from and to date, cost center, project, finance book.

### Close Readiness

A pass or fail checklist for one company and fiscal year:

- GL debits equal credits up to the year end
- No draft Sales Invoice, Purchase Invoice, Journal Entry, Payment Entry, Delivery Note, Purchase Receipt or Stock Entry
- All bank transactions reconciled
- No unallocated payments
- Earlier fiscal years have a Period Closing Voucher

Each failing check shows a count, an amount where it applies, and a short hint.

## Who can use it

Users with the Accounts User, Accounts Manager or Auditor role.

## Requirements

- Frappe v16 and ERPNext v16

## Installation

```bash
bench get-app https://github.com/s1dharthpv/closeout --branch develop
bench --site your-site install-app closeout
```

## Limitations

- The virtual row is in the company currency. Presentation currency is not applied to it.
- The cost center, project and finance book filters are applied to the virtual row, but they have had little testing. Check the result against the General Ledger before relying on it.
- Reports do not apply per-user company permissions. Control access with roles.
- The checklist does not yet cover stock value against the ledger, exchange rate revaluation or accounting dimension gaps.
- Tested on a small demo company. Try it on a copy of your data first.

## Support

See [SUPPORT.md](SUPPORT.md). Privacy details are in [PRIVACY.md](PRIVACY.md).

## Contributing

This app uses `pre-commit` for formatting and linting.

```bash
cd apps/closeout
pre-commit install
```

Tests run on a site with a company that has at least two fiscal years of postings and no Period Closing Voucher. `closeout/tests/seed.py` builds such data for the demo company used in the tests.

## License

MIT
