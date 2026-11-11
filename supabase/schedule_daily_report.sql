-- Enable pg_cron and pg_net in Supabase Dashboard > Integrations/Extensions first.
-- Store these values in Supabase Vault before running this file:
--   name: ipay_report_url       value: https://<project-ref>.supabase.co
--   name: ipay_report_api_key   value: the Supabase server secret key
--   name: ipay_report_secret    value: same random value as DAILY_REPORT_WEBHOOK_SECRET
-- Never commit actual credentials in this file.

do $$
begin
    if not exists (select 1 from vault.decrypted_secrets where name='ipay_report_url')
       or not exists (select 1 from vault.decrypted_secrets where name='ipay_report_api_key')
       or not exists (select 1 from vault.decrypted_secrets where name='ipay_report_secret') then
        raise exception 'Add ipay_report_url, ipay_report_api_key, and ipay_report_secret to Supabase Vault first';
    end if;
end;
$$;

select cron.unschedule(jobid)
from cron.job
where jobname in ('ipay-daily-question-report', 'ipay-daily-question-report-retry');

-- Supabase Cron runs in UTC by default. 18:29 UTC is 23:59 Asia/Kolkata.
select cron.schedule(
    'ipay-daily-question-report',
    '29 18 * * *',
    $$
    select net.http_post(
        url := (select decrypted_secret from vault.decrypted_secrets where name='ipay_report_url') || '/functions/v1/daily-question-report',
        headers := jsonb_build_object(
            'Content-Type', 'application/json',
            'apikey', (select decrypted_secret from vault.decrypted_secrets where name='ipay_report_api_key'),
            'x-report-secret', (select decrypted_secret from vault.decrypted_secrets where name='ipay_report_secret')
        ),
        body := '{}'::jsonb
    );
    $$
);

-- Retries the previous day's failed/pending delivery every ten minutes.
select cron.schedule(
    'ipay-daily-question-report-retry',
    '*/10 * * * *',
    $$
    select net.http_post(
        url := (select decrypted_secret from vault.decrypted_secrets where name='ipay_report_url') || '/functions/v1/daily-question-report',
        headers := jsonb_build_object(
            'Content-Type', 'application/json',
            'apikey', (select decrypted_secret from vault.decrypted_secrets where name='ipay_report_api_key'),
            'x-report-secret', (select decrypted_secret from vault.decrypted_secrets where name='ipay_report_secret')
        ),
        body := '{}'::jsonb
    );
    $$
);
