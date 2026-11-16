-- Additive issue-spike history and atomic alert claims.
-- Apply only after review; retains report metadata for 30 days.

create table if not exists public.issue_spike_reports (
    category text not null check (category in (
        'ORDER_LOCKED', 'WITHDRAWAL_LOCKED', 'WITHDRAWAL_PENDING',
        'DEPOSIT_PENDING', 'PAYMENT_FAILED'
    )),
    chat_id bigint not null,
    message_id bigint not null,
    user_id bigint,
    created_at timestamptz not null,
    primary key (category, chat_id, message_id)
);
create index if not exists idx_issue_spike_reports_category_time
    on public.issue_spike_reports(category, created_at desc);

create table if not exists public.issue_spike_alert_state (
    category text primary key check (category in (
        'ORDER_LOCKED', 'WITHDRAWAL_LOCKED', 'WITHDRAWAL_PENDING',
        'DEPOSIT_PENDING', 'PAYMENT_FAILED'
    )),
    last_alert_at timestamptz,
    incident_active boolean not null default false
);

alter table public.issue_spike_reports enable row level security;
alter table public.issue_spike_alert_state enable row level security;
revoke all on table public.issue_spike_reports, public.issue_spike_alert_state
    from anon, authenticated;
grant all on table public.issue_spike_reports, public.issue_spike_alert_state
    to service_role;

create or replace function public.claim_issue_spike_alert(
    p_category text,
    p_window_start timestamptz,
    p_now timestamptz,
    p_threshold integer,
    p_cooldown_seconds integer
)
returns table(should_alert boolean, report_count bigint, unique_user_count bigint)
language plpgsql
security definer
set search_path = public, pg_temp
as $function$
declare
    v_active boolean := false;
    v_last_alert_at timestamptz;
begin
    if p_category is null or p_category not in (
        'ORDER_LOCKED', 'WITHDRAWAL_LOCKED', 'WITHDRAWAL_PENDING',
        'DEPOSIT_PENDING', 'PAYMENT_FAILED'
    ) or p_window_start is null or p_now is null
        or p_threshold is null or p_threshold < 1
        or p_cooldown_seconds is null or p_cooldown_seconds < 1 then
        raise exception 'Invalid issue spike alert parameters';
    end if;

    delete from public.issue_spike_reports
    where created_at < p_now - interval '30 days';

    select count(*), count(distinct user_id)
    into report_count, unique_user_count
    from public.issue_spike_reports
    where category = p_category
      and created_at >= p_window_start
      and created_at <= p_now;

    insert into public.issue_spike_alert_state(category, last_alert_at, incident_active)
    values (p_category, null, false)
    on conflict (category) do nothing;

    select incident_active, last_alert_at
    into v_active, v_last_alert_at
    from public.issue_spike_alert_state
    where category = p_category
    for update;

    if report_count < p_threshold then
        update public.issue_spike_alert_state
        set incident_active = false
        where category = p_category;
        should_alert := false;
    else
        should_alert := not v_active and (
            v_last_alert_at is null
            or v_last_alert_at <= p_now - make_interval(secs => p_cooldown_seconds)
        );
        if should_alert then
            update public.issue_spike_alert_state
            set last_alert_at = p_now, incident_active = true
            where category = p_category;
        end if;
    end if;

    return next;
end;
$function$;

revoke all on function public.claim_issue_spike_alert(text, timestamptz, timestamptz, integer, integer)
    from public, anon, authenticated;
grant execute on function public.claim_issue_spike_alert(text, timestamptz, timestamptz, integer, integer)
    to service_role;
