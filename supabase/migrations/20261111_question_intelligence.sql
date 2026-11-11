-- Additive question analytics migration. Safe to rerun.
create table if not exists public.problem_categories (
    id uuid primary key default gen_random_uuid(),
    key text not null unique,
    title text not null,
    description text,
    created_at timestamptz not null default now()
);
insert into public.problem_categories(key,title,description) values
 ('withdrawal','Withdrawal problems','Payout delays, pending withdrawals, and withdrawal errors'),
 ('deposit','Deposit problems','Missing or delayed deposits'),
 ('payment','Payment issues','Payment processing and UPI problems'),
 ('wallet','Wallet issues','Wallet eligibility and connection problems'),
 ('account_verification','Account verification','KYC and verification issues'),
 ('login','Login problems','Sign-in and access issues'),
 ('transaction_tracking','Transaction tracking','Order and transaction status queries'),
 ('account_restriction','Account restrictions','Locked or restricted accounts'),
 ('referral','Referral problems','Referral and invite questions'),
 ('technical_error','Technical errors','App failures and technical errors'),
 ('bot_functionality','Bot functionality','Telegram bot features and commands'),
 ('feature_request','Feature requests','Requested product or bot improvements'),
 ('general_support','General support','Other support requests')
on conflict(key) do nothing;

create table if not exists public.problem_clusters (
    id uuid primary key default gen_random_uuid(),
    title text not null,
    category text not null,
    normalized_text text not null,
    occurrence_count bigint not null default 0,
    unique_user_count bigint not null default 0,
    first_reported_at timestamptz not null,
    last_reported_at timestamptz not null,
    status text not null default 'open' check (status in ('open','investigating','resolved','ignored')),
    priority text not null default 'normal' check (priority in ('low','normal','high','critical')),
    notes text
);
create index if not exists idx_problem_clusters_category_time on public.problem_clusters(category,last_reported_at desc);
create index if not exists idx_problem_clusters_status_priority on public.problem_clusters(status,priority,last_reported_at desc);

create table if not exists public.question_events (
    id uuid primary key default gen_random_uuid(),
    user_id bigint,
    username text,
    display_name text,
    chat_id bigint not null,
    chat_type text not null check (chat_type in ('private','group','supergroup','channel','unknown')),
    chat_title text,
    message_id bigint not null,
    original_message text not null,
    language text not null default 'unknown',
    bot_response text,
    answer_status text not null default 'unanswered' check (answer_status in ('unanswered','partially_answered','answered','needs_human_review','resolved','ignored')),
    classification_confidence numeric(4,3) not null default 0 check (classification_confidence between 0 and 1),
    category text not null,
    subcategory text,
    intent text,
    problem_summary text,
    assigned_admin_id bigint,
    priority text not null default 'normal' check (priority in ('low','normal','high','critical')),
    sentiment text,
    is_complaint boolean not null default false,
    is_feature_request boolean not null default false,
    related_question_id uuid references public.question_events(id) on delete set null,
    created_at timestamptz not null,
    updated_at timestamptz not null default now(),
    unique(chat_id,message_id)
);
create index if not exists idx_question_events_created on public.question_events(created_at,id);
create index if not exists idx_question_events_user on public.question_events(user_id,created_at desc);
create index if not exists idx_question_events_chat on public.question_events(chat_id,created_at desc);
create index if not exists idx_question_events_status on public.question_events(answer_status,priority,created_at desc);
create index if not exists idx_question_events_category on public.question_events(category,created_at desc);
create index if not exists idx_question_events_message_fts on public.question_events using gin(to_tsvector('simple',original_message));

create table if not exists public.question_cluster_links (
    question_id uuid primary key references public.question_events(id) on delete cascade,
    cluster_id uuid not null references public.problem_clusters(id) on delete cascade,
    linked_at timestamptz not null default now()
);
create index if not exists idx_question_cluster_links_cluster on public.question_cluster_links(cluster_id);

create or replace function public.refresh_problem_cluster(p_cluster_id uuid)
returns void
language sql
security definer
set search_path = public
as $function$
    update public.problem_clusters c
    set occurrence_count = (select count(*) from public.question_cluster_links l where l.cluster_id = p_cluster_id),
        unique_user_count = (
            select count(distinct q.user_id)
            from public.question_cluster_links l
            join public.question_events q on q.id = l.question_id
            where l.cluster_id = p_cluster_id and q.user_id is not null
        ),
        last_reported_at = coalesce((
            select max(q.created_at)
            from public.question_cluster_links l
            join public.question_events q on q.id = l.question_id
            where l.cluster_id = p_cluster_id
        ), c.last_reported_at)
    where c.id = p_cluster_id;
$function$;
revoke all on function public.refresh_problem_cluster(uuid) from public, anon, authenticated;
grant execute on function public.refresh_problem_cluster(uuid) to service_role;

create table if not exists public.user_question_history (
    user_id bigint not null,
    question_id uuid not null references public.question_events(id) on delete cascade,
    created_at timestamptz not null,
    primary key(user_id,question_id)
);
create index if not exists idx_user_question_history_time on public.user_question_history(user_id,created_at desc);

create table if not exists public.group_question_analytics (
    chat_id bigint not null,
    report_day date not null,
    group_title text,
    question_count bigint not null default 0,
    unique_users bigint not null default 0,
    common_problems jsonb not null default '[]'::jsonb,
    updated_at timestamptz not null default now(),
    primary key(chat_id,report_day)
);

create table if not exists public.admin_notes (
    id bigint generated by default as identity primary key,
    question_id uuid not null references public.question_events(id) on delete cascade,
    admin_user_id bigint not null,
    note text not null,
    created_at timestamptz not null default now()
);
create index if not exists idx_admin_notes_question_time on public.admin_notes(question_id,created_at desc);

create table if not exists public.question_resolutions (
    id bigint generated by default as identity primary key,
    question_id uuid not null references public.question_events(id) on delete cascade,
    resolution text not null,
    answer_key text,
    resolved_by bigint,
    resolved_at timestamptz not null default now()
);

create table if not exists public.bot_knowledge_gaps (
    id uuid primary key default gen_random_uuid(),
    category text not null,
    summary text not null,
    occurrence_count bigint not null default 1,
    first_seen_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    status text not null default 'open' check (status in ('open','reviewing','addressed','ignored')),
    unique(category,summary)
);
create index if not exists idx_knowledge_gaps_status_time on public.bot_knowledge_gaps(status,last_seen_at desc);

create table if not exists public.daily_reports (
    report_date date not null,
    timezone text not null,
    report_json jsonb not null,
    delivery_status text not null default 'pending' check (delivery_status in ('pending','sent','failed','not_configured')),
    delivery_attempts integer not null default 0,
    delivered_at timestamptz,
    last_error text,
    created_at timestamptz not null default now(),
    primary key(report_date,timezone)
);

create table if not exists public.bot_activity_logs (
    id bigint generated by default as identity primary key,
    event_type text not null,
    severity text not null default 'info' check (severity in ('debug','info','warning','error','critical')),
    chat_id bigint,
    user_id bigint,
    message_id bigint,
    detail jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    unique(chat_id,message_id)
);
create index if not exists idx_bot_activity_type_time on public.bot_activity_logs(event_type,created_at desc);
create index if not exists idx_bot_activity_severity_time on public.bot_activity_logs(severity,created_at desc);

alter table public.problem_categories enable row level security;
alter table public.problem_clusters enable row level security;
alter table public.question_events enable row level security;
alter table public.question_cluster_links enable row level security;
alter table public.user_question_history enable row level security;
alter table public.group_question_analytics enable row level security;
alter table public.admin_notes enable row level security;
alter table public.question_resolutions enable row level security;
alter table public.bot_knowledge_gaps enable row level security;
alter table public.daily_reports enable row level security;
alter table public.bot_activity_logs enable row level security;

revoke all on table public.problem_categories, public.problem_clusters, public.question_events,
    public.question_cluster_links, public.user_question_history, public.group_question_analytics,
    public.admin_notes, public.question_resolutions, public.bot_knowledge_gaps,
    public.daily_reports, public.bot_activity_logs from anon, authenticated;
grant all on table public.problem_categories, public.problem_clusters, public.question_events,
    public.question_cluster_links, public.user_question_history, public.group_question_analytics,
    public.admin_notes, public.question_resolutions, public.bot_knowledge_gaps,
    public.daily_reports, public.bot_activity_logs to service_role;
grant usage, select on all sequences in schema public to service_role;
