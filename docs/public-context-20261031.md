# Official education and crime context

`report_public_context.collect(p, county, point, root, allow_public=True)` requires
an Allegheny County parcel matched to the property address. It then queries the
assessment dataset for that exact `PARID` and uses `SCHOOLDESC` to identify the
school district. Postal city names never identify the district. This matters for
Verona postal addresses in Penn Hills and for Pittsburgh, which includes more
than one school district.

PDE workbook links are discovered on the official Future Ready PA Data Files
page, selecting the most recent performance workbook. The standard-library XLSX
reader retains published percentages and suppression markers and excludes
subgroup rows from the general student display. Schools are explicitly schools
in the recorded district, not verified attendance assignments for the address.
No overall school, street or neighborhood grade is computed.

Shared normalized education snapshots and parcel district matches live under
`COMPS_REPORTS/official_education`. They are reused within the calendar quarter.
A failed refresh preserves the previous observation date and reports stale data.

Current crime metrics remain unavailable pending a verified machine feed. The
official Pittsburgh WPRDC blotter states that updates ceased on 2023-11-14. It is
not used as current data, and absence of records is not treated as zero crime.
The report provides the official PA State Police UCR dashboard as a reference.

Sources:

- https://www.futurereadypa.org/Home/DataFiles
- https://data.wprdc.org/dataset/property-assessments
- https://tools.wprdc.org/property-api/ (documents SCHOOLDESC)
- https://www.ucr.pa.gov/PAUCRSPUBLIC
- https://data.wprdc.org/dataset/police-incident-blotter

Validation command for an environment with outbound HTTPS:

    python -X utf8 report_public_context.py --live-check

This prints the official source period, target district school counts and workbook
header diagnostics, then rejects unrecognized target districts. No key or paid
provider is used. Offline tests use synthetic XLSX fixtures and do not claim a
live data validation.
