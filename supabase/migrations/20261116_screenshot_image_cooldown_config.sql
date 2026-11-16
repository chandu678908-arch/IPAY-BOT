-- Optional configurable cooldown overload for screenshot image deduplication.
-- Apply this migration only when SCREENSHOT_IMAGE_COOLDOWN_SECONDS is not 300.
create or replace function public.reserve_screenshot_image_cooldown(
    p_user_id bigint,
    p_image_hash text,
    p_cooldown_seconds integer
)
returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $function$
declare
    v_now timestamptz := clock_timestamp();
    v_cooldown interval;
    v_reserved boolean := false;
begin
    if p_user_id is null or p_user_id <= 0
        or p_image_hash is null or p_image_hash !~ '^[0-9a-f]{64}$'
        or p_cooldown_seconds is null or p_cooldown_seconds <= 0 then
        raise exception 'Invalid screenshot cooldown key or duration';
    end if;

    v_cooldown := make_interval(secs => p_cooldown_seconds);

    delete from public.screenshot_image_cooldowns
    where last_submitted_at <= v_now - v_cooldown;

    insert into public.screenshot_image_cooldowns(
        user_id, image_hash, last_submitted_at
    )
    values (p_user_id, p_image_hash, v_now)
    on conflict (user_id, image_hash) do update
    set last_submitted_at = excluded.last_submitted_at
    where public.screenshot_image_cooldowns.last_submitted_at
        <= excluded.last_submitted_at - v_cooldown
    returning true into v_reserved;

    return coalesce(v_reserved, false);
end;
$function$;

revoke all on function public.reserve_screenshot_image_cooldown(bigint, text, integer)
    from public, anon, authenticated;
grant execute on function public.reserve_screenshot_image_cooldown(bigint, text, integer)
    to service_role;
