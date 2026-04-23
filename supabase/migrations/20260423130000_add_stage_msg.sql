-- Add columns for the background status poller to surface real-time
-- vast.ai state to the dashboard.
alter table public.instances add column if not exists stage_msg text;
alter table public.instances add column if not exists vast_actual_status text;
