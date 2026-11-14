-- Proposed additive migration: apply only after approval.
-- Tracks one active cooldown per Telegram user and exact image hash.
create table if not exists public.screenshot_image_cooldowns (
    user_id bigint not null,
    image_hash text not null check (image_hash ~ '^[0-9a-f]{64}$'),
    last_submitted_at timestamptz not null,
    primary key (user_id, image_hash)
);

create index if not exists idx_screenshot_image_cooldowns_expiry
    on public.screenshot_image_cooldowns (last_submitted_at);

alter table public.screenshot_image_cooldowns enable row level security;
revoke all on table public.screenshot_image_cooldowns from anon, authenticated;
grant all on table public.screenshot_image_cooldowns to service_role;

create or replace function public.reserve_screenshot_image_cooldown(
    p_user_id bigint,
    p_image_hash text
)
returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $function$
declare
    v_now timestamptz := clock_timestamp();
    v_reserved boolean := false;
begin
    if p_user_id is null or p_user_id <= 0
        or p_image_hash is null or p_image_hash !~ '^[0-9a-f]{64}$' then
        raise exception 'Invalid screenshot cooldown key';
    end if;

    delete from public.screenshot_image_cooldowns
    where last_submitted_at <= v_now - interval '5 minutes';

    insert into public.screenshot_image_cooldowns(
        user_id, image_hash, last_submitted_at
    )
    values (p_user_id, p_image_hash, v_now)
    on conflict (user_id, image_hash) do update
    set last_submitted_at = excluded.last_submitted_at
    where public.screenshot_image_cooldowns.last_submitted_at
        <= excluded.last_submitted_at - interval '5 minutes'
    returning true into v_reserved;

    return coalesce(v_reserved, false);
end;
$function$;

revoke all on function public.reserve_screenshot_image_cooldown(bigint, text)
    from public, anon, authenticated;
grant execute on function public.reserve_screenshot_image_cooldown(bigint, text)
    to service_role;
