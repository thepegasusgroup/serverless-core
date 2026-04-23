-- Pipelines v3: ordered step chain (model + transform).
-- Each element of `steps` is an object like:
--   {"kind":"model","model_slug":"qwen2.5-7b-instruct",
--    "system":"...","user_template":"Summarize: {{input}}",
--    "vllm_overrides":{"temperature":0.1,"max_tokens":256}}
-- or
--   {"kind":"transform","transform":"strip_markdown_fences","params":{}}
--
-- Templates support {{input}}, {{prev}}, {{step_N}} (1-indexed).
alter table public.pipelines add column if not exists steps jsonb not null default '[]'::jsonb;

-- Migrate existing v2 single-step pipelines into a 1-step chain.
update public.pipelines
   set steps = jsonb_build_array(jsonb_build_object(
     'kind','model',
     'model_slug', model_slug,
     'system', coalesce(system_prompt, ''),
     'user_template', coalesce(user_template, '{{input}}'),
     'vllm_overrides', coalesce(vllm_overrides, '{}'::jsonb),
     'response_format', coalesce(response_format, 'text'),
     'response_schema', response_schema
   ))
 where jsonb_typeof(steps) = 'array'
   and jsonb_array_length(steps) = 0
   and model_slug is not null;
