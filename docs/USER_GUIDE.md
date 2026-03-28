# PMTCT Triple Elimination Tool User Guide

## Overview

The PMTCT Triple Elimination Tool is a DHIS2-connected analytics workspace for Uganda Ministry of Health teams monitoring HIV, syphilis, and hepatitis B performance. The application is stateless:

- no local database
- no persistent report store
- session data and caches live in memory only
- restarting the service clears active sessions and warm caches

## Signing in

1. Open the application URL.
2. Enter your DHIS2 base URL.
3. Sign in with either:
   - DHIS2 username and password
   - a DHIS2 personal access token if your deployment supports it
4. After a successful login, the application creates an in-memory session for your browser.

Your visible organisation units and features depend on the DHIS2 authorities attached to your account.

## Main pages

### Dashboard
- WHO validation scorecard
- quick view of current performance
- alert summary for the selected period

### Indicators
- calculate monthly or weekly indicators
- filter by category or specific indicator IDs
- review numerator, denominator, target, and status

### Cascades
- HIV cascade
- HBV cascade
- syphilis cascade

### Supply Chain
- stock consumption and stockout days for mapped tracer commodities
- days of use calculations
- reorder guidance and forecast summaries
- clear marking of commodities whose DHIS2 mapping is still pending

### Data Quality
- rule-based validation checks
- overall DQ score
- findings grouped by severity

### Alerts
- monthly threshold monitoring
- supply, indicator, data quality, and system alerts
- acknowledgement for users with alert-management privileges

### Trends
- multi-period monthly trend analysis
- compare up to 10 indicators at once
- see direction, change, and target attainment across time

### AI Insights
- programme interpretation for allowed users
- indicator, cascade, alert, and data-quality narratives
- session-scoped Q and A

### Admin
- visible to system administrators only
- diagnostics, cache status, configuration validation, and session utilities

## Selecting organisation units and periods

Most pages use the organisation units attached to your DHIS2 account. Choose:

- an organisation unit from the dropdown
- a period from the available DHIS2 period list
- where relevant, a periodicity or history depth

## Reports and exports

The application supports exports generated on demand:

- scorecard
- cascade
- supply status

Supported formats:

- PDF
- XLSX
- CSV where implemented

Exports are generated in memory and streamed back to the browser. Files are not stored on the server after download.

## Alerts and acknowledgement

Alerts are derived from live indicator and supply results. Depending on your role, you may be able to acknowledge alerts in the current session. Acknowledgements are session-scoped and are cleared when the session expires or the service restarts.

## Supply chain notes

Mapped supply commodities currently focus on:

- HBsAg test kits
- HIV/syphilis duo test kits

Additional tracer commodities may appear as "mapping pending" until their DHIS2 mapping is confirmed. This is expected and does not mean the application is malfunctioning.

## Sessions and expiry

- Sessions expire after the configured timeout.
- A browser refresh does not normally log you out while the session is still active.
- Redeploys and service restarts clear all sessions.

If you see "Session expired", sign in again.

## Troubleshooting

### No data returned
- confirm the selected period has submitted DHIS2 data
- confirm your account can access the chosen organisation unit
- try a lower-level organisation unit if an aggregate appears empty

### Results differ from DHIS2
- in-memory caches can keep derived results warm briefly
- refresh the page and rerun the report
- if the difference persists, review the relevant config mapping and indicator formula

### Export failed
- ensure your role includes export permissions
- large exports may take longer on lower-resource deployments
- check with an administrator if the export dependency stack is unavailable

### Admin page not visible
- only users resolved to the `admin` role can access `/admin`
- verify the account has the DHIS2 authorities configured for admin mapping in `config/rbac.yaml`

## Role summary

### Viewer
- dashboard, indicators, alerts, trends, data quality, org-unit navigation

### Analyst
- viewer permissions
- report exports
- AI insights

### Data manager
- analyst permissions
- alert acknowledgement
- audit-log access permissions in the application model

### Admin
- full system access
- admin dashboard and utilities

## Support checklist

When reporting an issue, capture:

- page or endpoint involved
- organisation unit
- period
- whether the problem affects HTML view, JSON API, or export
- request ID from the response header if available
