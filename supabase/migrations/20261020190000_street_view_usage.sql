-- Central application attempt counter. No Google key, address or coordinates.
-- Additive migration; existing property/deal tables are untouched.
begin;

create table public.street_view_usage_months (
    period date primary key check (extract(day from period)=1),
    used integer not null default 0 check (used between 0 and 9000),
    started_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create table public.street_view_usage_attempts (
    request_id uuid primary key,
    period date not null references public.street_view_usage_months(period),
    reserved_at timestamptz not null default now()
);
alter table public.street_view_usage_months enable row level security;
alter table public.street_view_usage_attempts enable row level security;
revoke all on public.street_view_usage_months from public,anon,authenticated;
revoke all on public.street_view_usage_attempts from public,anon,authenticated;

create function public.street_view_usage_status() returns jsonb
language plpgsql security definer set search_path=''
as $$
declare
    v_period date:=date_trunc('month',timezone('Asia/Jerusalem',statement_timestamp()))::date;
    v_used integer;
begin
    if auth.uid() is distinct from 'c8657ee2-42cd-47bc-b62c-73bab8d8954d'::uuid then
        raise exception 'Not authorized' using errcode='42501';
    end if;
    select used into v_used from public.street_view_usage_months where period=v_period;
    v_used:=coalesce(v_used,0);
    return jsonb_build_object('schema_version',1,'period',to_char(v_period,'YYYY-MM'),
        'used',v_used,'limit',9000,'warning_at',8000,'time_zone','Asia/Jerusalem','allowed',v_used<9000);
end;
$$;

create function public.reserve_street_view_attempt(p_request_id uuid) returns jsonb
language plpgsql security definer set search_path=''
as $$
declare
    v_period date:=date_trunc('month',timezone('Asia/Jerusalem',statement_timestamp()))::date;
    v_used integer;
    v_allowed boolean:=false;
    v_reason text:='limit';
begin
    if auth.uid() is distinct from 'c8657ee2-42cd-47bc-b62c-73bab8d8954d'::uuid then
        raise exception 'Not authorized' using errcode='42501';
    end if;
    if p_request_id is null then raise exception 'Missing request ID' using errcode='22023'; end if;
    insert into public.street_view_usage_months(period) values(v_period) on conflict do nothing;
    -- All tabs/devices serialize on this row before reserving their last slot.
    select used into strict v_used from public.street_view_usage_months where period=v_period for update;
    if v_used<9000 then
        insert into public.street_view_usage_attempts(request_id,period) values(p_request_id,v_period) on conflict do nothing;
        if found then
            update public.street_view_usage_months set used=used+1,updated_at=clock_timestamp()
                where period=v_period returning used into v_used;
            v_allowed:=true;v_reason:='reserved';
        else
            -- Replayed IDs never authorize another frame, even in a later month.
            v_reason:='duplicate';
        end if;
    end if;
    return jsonb_build_object('schema_version',1,'period',to_char(v_period,'YYYY-MM'),
        'used',v_used,'limit',9000,'warning_at',8000,'time_zone','Asia/Jerusalem',
        'allowed',v_allowed,'reason',v_reason,'request_id',p_request_id);
end;
$$;
revoke all on function public.street_view_usage_status() from public,anon,authenticated;
revoke all on function public.reserve_street_view_attempt(uuid) from public,anon,authenticated;
grant execute on function public.street_view_usage_status() to authenticated;
grant execute on function public.reserve_street_view_attempt(uuid) to authenticated;
comment on table public.street_view_usage_months is 'Application Embed attempts since activation; not Google billing or historical usage. Calendar months in Asia/Jerusalem.';
notify pgrst,'reload schema';
commit;
