# Isolated system QA

Audit the checked-out production frontend and saved public data without
production authentication, source scans, provider calls, Supabase writes,
Telegram calls or RentCast calls.

The workflow has read-only repository permission and no secrets. Browser
requests are fulfilled from localhost/repository fixtures or blocked. The
Supabase SDK is replaced only inside the browser route with an in-memory test
client. No real login or server-side RLS verification is claimed.

Tailwind and Lucide static scripts are downloaded once as test dependencies;
their hashes are recorded. Fonts, source images and map providers are mocked
or blocked. Screenshot layout uses real Tailwind CSS and browser engines but
does not prove live image access or Google coverage. Harmless malformed-data
probes run only against local response fixtures, never production data.

Run data_audit.py, then browser_audit.py. The generated JSON findings and
desktop/mobile screenshots are uploaded as a seven-day Actions artifact.
The script records individual failures so one defect does not suppress other
scenarios. The final workflow step fails when any browser finding remains open;
all earlier scenarios and evidence upload still run. Read the findings JSON
and the report for severity and scope. No product code is modified by this suite.
