-- Proposed additive migration: apply only after approval.
-- The primary key makes screenshot reply claims atomic across bot instances.
create table if not exists public.handled_screenshot_messages (
    chat_id bigint not null,
    message_id bigint not null,
    created_at timestamptz not null default now(),
    primary key (chat_id, message_id)
);

alter table public.handled_screenshot_messages enable row level security;
revoke all on table public.handled_screenshot_messages from anon, authenticated;
grant all on table public.handled_screenshot_messages to service_role;
