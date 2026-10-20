"""Real PostgreSQL integration tests. Run only against an empty local CI DB."""
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import uuid

def main():
    if os.environ.get('PA_STREET_USAGE_TEST_DATABASE') != 'isolated' or os.environ.get('PGHOST') not in ('127.0.0.1', 'localhost'):
        raise SystemExit('This suite requires an isolated local PostgreSQL database; never production.')

    OWNER = 'c8657ee2-42cd-47bc-b62c-73bab8d8954d'
    PERIOD = "date_trunc('month',timezone('Asia/Jerusalem',statement_timestamp()))::date"
    def sql(query):
        result = subprocess.run(['psql', '-X', '-A', '-t', '-v', 'ON_ERROR_STOP=1', '-c', query], text=True, capture_output=True, check=True)
        return result.stdout.strip()

    assert sql("select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and c.relname like 'street_view_usage_%'") == '0'
    sql("create role anon; create role authenticated; create schema auth; create function auth.uid() returns uuid language sql stable as $$ select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid $$; grant usage on schema auth to public;")
    sql((Path(__file__).parent / 'supabase/migrations/20261020190000_street_view_usage.sql').read_text())

    def as_owner(query):
        return sql(f"begin; set local role authenticated; select set_config('request.jwt.claim.sub','{OWNER}',true); {query}; commit;")

    # Permission checks execute as actual PostgreSQL roles, with real function ACLs.
    for identity, role in [('', 'authenticated'), ('00000000-0000-0000-0000-000000000001', 'authenticated'), (OWNER, 'anon')]:
        try:
            sql(f"begin; set local role {role}; select set_config('request.jwt.claim.sub','{identity}',true); select public.street_view_usage_status(); rollback;")
        except subprocess.CalledProcessError:
            pass
        else:
            raise AssertionError('Unexpected counter access: ' + role)

    try:
        as_owner('select * from public.street_view_usage_months')
    except subprocess.CalledProcessError:
        pass
    else:
        raise AssertionError('Authenticated role must not read/write the tables directly')

    def response(text):
        return json.loads(next(line for line in text.splitlines() if line.startswith('{')))

    initial = response(as_owner('select public.street_view_usage_status()'))
    assert sql("select count(*) from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='public' and p.proname in ('street_view_usage_status','reserve_street_view_attempt') and p.prosecdef") == '0'
    assert sql("select has_schema_privilege('anon','street_view_private','usage')") == 'f'
    assert initial['used'] == 0 and initial['allowed'] and initial['time_zone'] == 'Asia/Jerusalem'
    request_id = str(uuid.uuid4())
    first = response(as_owner(f"select public.reserve_street_view_attempt('{request_id}')"))
    replay = response(as_owner(f"select public.reserve_street_view_attempt('{request_id}')"))
    assert first['used'] == 1 and first['allowed']
    assert replay['used'] == 1 and not replay['allowed'] and replay['reason'] == 'duplicate'

    # Separate real DB connections compete for the final slot.
    sql(f"update public.street_view_usage_months set used=8999 where period={PERIOD}")
    def reserve(_):
        return response(as_owner(f"select public.reserve_street_view_attempt('{uuid.uuid4()}')"))
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(reserve, range(8)))
    assert sum(result['allowed'] for result in results) == 1, results
    assert all(result['used'] == 9000 for result in results), results
    assert response(as_owner('select public.street_view_usage_status()'))['used'] == 9000

    # An exhausted historical month must not consume the current month's allowance.
    sql(f"delete from public.street_view_usage_attempts; delete from public.street_view_usage_months; insert into public.street_view_usage_months(period,used) values(({PERIOD}-interval '1 month')::date,9000)")
    assert response(as_owner('select public.street_view_usage_status()'))['used'] == 0
    assert reserve(0)['used'] == 1
    print('PostgreSQL checks passed: roles, owner binding, direct table denial, replay rejection, final-slot concurrency and separate monthly buckets.')


if __name__ == '__main__':
    main()
