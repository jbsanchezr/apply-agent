# Decisions

One short entry per non-obvious choice. Newest at the bottom.

## D1 - uv with a committed lockfile, `uv_build` backend, src layout (M1)
uv gives reproducible installs (`uv.lock`) and fast CI, and it needs no extra
build dependency. The src layout means tests run against the installed package,
not whatever happens to be on `sys.path`.

## D2 - Dependencies are added in the milestone that first uses them (M1)
M1 depends only on `pydantic` and `sqlalchemy`. FastAPI, LangGraph and the
others arrive with the code that imports them, so the lockfile never lists
something the code doesn't use.

## D3 - Status is derived from events, not mutated per message (M1)
`derive_status(events)` is a pure function: the most recent status-bearing
event wins, and `OTHER` never changes the status. Re-running a sync or
receiving messages out of order therefore always produces the same result,
which is what makes the upserts idempotent. A later interview invitation
overrides an earlier rejection, because a role reopening or a second role at
the same company is more likely than a misordered inbox.

## D4 - An application is identified by thread *and* by (company, role) (M1)
Companies often reply outside the original thread: the ATS confirmation, the
recruiter's own mailbox and the scheduling tool all start new threads. So
threads map many-to-one onto applications (`application_threads`), and a
normalised `(company_key, role_key)` pair is unique as a fallback identity.
Known trade-off: applying twice to the same role at the same company resolves
to one application.

## D5 - Every processed message gets an `events` row, noise included (M1)
`events.message_id` is unique and is the idempotency key. Noise is stored with
`application_id = NULL`, so a re-run skips messages it has already classified
and never pays for the same LLM call twice.

## D6 - No email bodies in the database (M1)
Only the category, the one-line summary, ids and timestamps are persisted. Bodies can be re-read from the provider when needed, so the database
holds the minimum personal data.

## D7 - Timezone-aware UTC everywhere (M1)
Pydantic models accept only aware datetimes and normalise them to UTC. A
column type refuses naive datetimes on write, because SQLite has no timezone
support and would otherwise store local time as if it were UTC. Ruff's `DTZ`
rules catch naive `datetime.now()` in code.

## D8 - Enums stored as VARCHAR with a CHECK constraint (M1)
Readable in `sqlite3`, portable to Postgres, and the database still rejects
invalid values written outside the ORM.

## D9 - `create_all` instead of Alembic for now (M1)
The schema has no deployed versions yet. Alembic is not in the agreed stack.
It is the right tool once a schema change has to preserve existing data.

## D10 - Fixture corpus rules (M1)
* Every address and URL uses RFC 2606/6761 reserved names. `tests/test_fixtures.py`
  fails the build otherwise.
* Filenames carry a number and company but not the label. Labels live in
  `tests/fixtures/labels.json` with a `notes` field explaining tricky cases.
* `.eml` files are marked `-text` in `.gitattributes` so git never rewrites
  their bytes.

## D11 - Labelling rules for ambiguous messages (M1)
* A take-home or online assessment counts as `interview_invitation`: it is a
  stage in the process, not a request for information.
* "Send us your availability for the next stage" counts as
  `interview_invitation`: the requested information only serves scheduling.
* "The requisition was closed" counts as `rejection`: the application ends.
* An automated confirmation of an interview the candidate booked counts as
  `interview_invitation`.
* Recruiter cold outreach counts as `other`: it is not a reply to an
  application.
* "We'd love to have you, let's set up a call to walk through the offer" counts
  as `offer`, even though it proposes a call.
* A conditional offer that also asks for references counts as `offer`: the
  offer outranks the request for information.

## D12 - Deterministic fake LLM by default, real provider behind an env var (M1, applies from M3)
Tests and CI use a scripted fake chat model, so the project runs and the test
suite passes with zero credentials and zero cost. A real provider is selected
by environment variable. Which provider, and its dependency, is decided at M3.

## D13 - "Is this about a job application?" is a separate flag, not a category (M1)
`other` covers both a status-neutral message about an application (e.g. an
acknowledgement, which should *create* the application row) and unrelated
noise (which should create nothing). Instead of splitting the category, the
extraction output carries `is_job_application: bool`. The fixture labels carry
the same flag so M4 can score it. Rules checked by a test: every non-`other`
label is a job application, and every job application names a company.

## D14 - `offer` is a fifth category (M1)
An offer is the most important message in the domain, and labelling it `other`
would leave the status unchanged. `offer` maps to the `offer_received` status.
Status still follows the latest status-bearing event (D3), so a later
`other` (e.g. a reference check) does not demote an offer.

## D15 - The provider interface is a read-only `Protocol` (M2)
`EmailProvider` has two methods, `list_messages(since)` and `get_thread(id)`,
and nothing that could write. `since` is an inclusive lower bound with
superset semantics: every message at or after it is returned, a few older ones
may be too, and callers deduplicate by id (they must anyway, see D5).
Listings exclude the owner's own mail; threads include it, flagged
`outbound`, because the original application email is the best evidence of
company and role.

## D16 - One RFC 5322 parser for both providers (M2)
Gmail is asked for `format=raw`, which returns the same bytes as an `.eml`
file. Both providers go through `parse_message`, so the fixtures exercise the
production parsing path, not a parallel one. HTML-only mail is converted with
the stdlib `html.parser` (no new dependency), and bodies are truncated at
20,000 characters to bound LLM cost.

## D17 - A typed `GmailApi` port in front of `googleapiclient` (M2)
The official client is untyped and exposes every endpoint, including send and
delete. It is imported in exactly one module (a test enforces this), which
wraps the three GET calls we need behind a typed `GmailApi` protocol.
`GmailProvider` depends only on that protocol, so the shared provider contract
suite runs against Gmail offline with an in-memory `GmailApi`. The wrapper
itself is tested through the client's mock HTTP transport, asserting the
requests on the wire.

## D18 - Read-only is enforced in layers, not just by scope (M2)
1. Only `gmail.readonly` is ever requested.
2. Loading a token checks the scopes *stored in it* and refuses anything else,
   so a broader token created by some other tool cannot be picked up.
3. No interface in the codebase has a write method (a test checks the
   protocols expose only `list_*`/`get_*`).
4. A test fails if any provider module calls a Gmail write method.

## D19 - Credentials live outside the repository (M2)
The token and client secret default to `~/.config/apply_agent/`, and the token
is written with mode 0600. The server only loads and refreshes an existing
token. The browser consent flow is a separate, manual command, so a server
never blocks waiting for a login.

## D20 - Gmail operational defaults (M2)
* The `after:` filter is widened by one day: Gmail filters on receipt time, the
  contract is about the Date header, and duplicates are cheap.
* A listing is capped at 500 messages per sync and logs a warning when
  truncated, so a first sync on a large inbox cannot run away.
* A message deleted between listing and fetching is skipped, not fatal.
* Drafts are never returned.
* A thread is fetched as N+1 calls, because `threads.get` has no raw format.
  That is fine at personal-inbox scale; batch requests would be the next step.

## D21 - Settings are a plain Pydantic model read from the environment (M2)
`pydantic-settings` would be one more dependency for about fifteen lines of
code. Settings come from `APPLY_AGENT_*` variables, and an unknown
`APPLY_AGENT_*` variable is an error, so typos fail at startup.

## D22 - One graph, one short conversation per message (M3)
`fetch -> next_message -> call_model <-> run_tools`, looping until the queue is
empty. Each message gets a fresh conversation (system prompt plus that one
email) and a step budget (`max_agent_steps`, default 4). A shared, growing
conversation over a whole inbox would cost more per message as the sync went
on, and one confusing email could derail the rest.

## D23 - The graph calls `list_new_messages`; the model gets two tools (M3)
All three tools live in `Toolbox`. Choosing what to read needs no judgement,
so the graph calls `list_new_messages` itself, which keeps a sync's cost
predictable. The model is offered `get_thread` and `upsert_application`, and
both act on the message being processed, which the graph supplies. The model
cannot name another thread or message, so a prompt-injected email can at worst
get itself misclassified.

## D24 - Structured output is the `upsert_application` tool call (M3)
The model's only way to produce a result is a tool call whose arguments must
validate as `MessageAssessment` (extra fields forbidden, and the same
consistency rules the fixture labels follow). A validation error goes back as
a tool error naming the fields, so the model can correct itself within its
step budget. Anthropic's strict tool mode is not enabled yet: it could not be
verified without an API key, and the schema's length limits may not be
supported by it. Revisit with M4 data on how often validation fails.

## D25 - Failure semantics: skip, don't record, retry next sync (M3)
A model error, a refusal, or no valid result within the step budget fails
that message only. It is logged and reported, but not written to `events`, so
the next sync picks it up again. A refusal is not retried within the run.
Known gap: a message that always fails is retried, and paid for, on every
sync. A dead-letter table after N attempts is the next step.

## D26 - Default model: `claude-opus-5`, low effort, refusal fallback on (M3)
The model is configurable (`APPLY_AGENT_ANTHROPIC_MODEL`). The default is the
most capable general model. Effort is `low`, because classifying one short
email doesn't benefit from long reasoning. Thinking stays adaptive. Parallel
tool calls are off, so the loop is strictly think, act, observe. The
server-side refusal fallback is on by default, and can be switched off with
`APPLY_AGENT_ANTHROPIC_REFUSAL_FALLBACK=false`, for example for a model that
does not support it. Cheaper models (`claude-sonnet-5`, `claude-haiku-4-5`)
are one environment variable away. M4 will measure whether they hold accuracy.

## D27 - A keyword baseline is the default "model" (M3)
`KeywordBaselineModel` speaks the same tool-calling protocol as the real LLM,
so the whole pipeline runs with zero credentials. It is also the baseline the
LLM is compared against in the evaluation. It is deliberately naive.

## D28 - The event date is the email's date, not an LLM extraction (M3)
The spec asks to extract a "date". The Date header is authoritative and
free, so `occurred_at` comes from it. Dates *mentioned* in an email
(interview time, deadline) are not extracted yet; see the open question in
the M3 notes.

## D29 - Structured logs: stdlib JSON, ids only (M3)
One JSON object per line, with `thread_id` and `message_id` as correlation
ids on every per-message record. Logs never contain email bodies or subjects.
No `structlog`: the stdlib covers it in about fifty lines.

## D30 - Evaluate the agent, not a prompt (M4)
`scripts/evaluate.py` runs the production graph (same tools, prompts,
validation and step budget) over the fixtures, against a throwaway SQLite
database. A bare "classify this text" prompt would score something the
product never does. Get-thread calls, validation retries and failures are all
part of what gets measured.

## D31 - Metrics and how much to trust them (M4)
Per class: precision, recall, F1 and support. Overall: accuracy with a 95%
Wilson interval, and macro-F1. Extraction is scored separately: the
job-application flag, and company and role matched after the same
normalisation the database uses. A message the agent failed on is scored as
its own `(failed)` label rather than dropped. With 26 examples the interval
spans about 30 points, so a gap of a few points between two models means
nothing. The corpus is meant to catch regressions and show failure modes, not
to rank models finely.

## D32 - Record once, replay for free (M4)
`--record` stores every real model response in a JSON file, keyed by a hash of
the full prompt and the model configuration. `--replay` serves only from that
file and never calls the API. CI can therefore run the LLM eval on every push
at no cost and with deterministic results. Any change to the prompt, the tools
or the model misses the recording, and the script exits with code 2 ("stale
recording, re-record") instead of silently calling a paid API.

## D33 - The baseline's scores are pinned in a test (M4)
The keyword baseline is deterministic, so its accuracy (19/26, 28/42 since
D46) is asserted exactly. An unintended change anywhere in the pipeline (parsing, rendering,
the graph, the metrics) shows up as a failing test.

## D34 - A local model through Ollama is the free LLM option (M4)
The project must be runnable and evaluable without paying for an API.
`APPLY_AGENT_LLM=ollama` runs an open-weight model locally (default
`qwen3:8b`, which fits a 6 GB laptop GPU at 4-bit and supports tool calling).
It also has a privacy property the hosted options lack: a real inbox's content
never leaves the machine. Decoding is deterministic (temperature 0, fixed
seed), so evaluations and recordings are reproducible. The context window is
set explicitly to 8k tokens, because Ollama's default silently truncates the
system prompt, email and thread. Anthropic stays available as a configuration
switch, with no code change needed to compare the two.

## D35 - Recording keys: canonical prompt plus an explicit model fingerprint (M4)
The first real recording could not be replayed. LangChain gives every model
reply a random id, and that reply is part of the next step's prompt, so every
second step (after `get_thread` or a validation retry) missed. The same
investigation showed that `ChatOllama`'s cache key omits temperature, seed and
context size, so changing them would silently replay stale answers. The key
is now built from:
1. a model fingerprint (`model_fingerprint`), defined next to
   `make_chat_model` from the same constants;
2. the bound tools;
3. the prompt reduced to role, content and tool calls (name and arguments),
   with no ids or timing metadata.
A regression test drives two-step conversations through the real graph,
records them and replays them with no model. It fails under the old scheme.

## D36 - How to read the local model's perfect score (M4)
`qwen3:8b` classified 26/26 fixtures correctly. That figure is an upper bound,
not a production estimate:
* The same author wrote the fixtures, their labels and the system prompt,
  which spells out the labelling rules (D11). The eval checks that the model
  follows the rules, not that the rules cover real inboxes.
* 26 examples give a 95% interval of 87-100%.
* Real mail is messier: long threads, forwarded chains, languages beyond
  English and Spanish, and marketing HTML.
The next step for a trustworthy number is a held-out set labelled by someone
who has not seen the prompt, ideally from real (anonymised) mail. Extraction
is where the model is visibly imperfect (company 86%, role 95%). Latency is
the other real cost: about 40 s per email on a laptop GPU with reasoning on.

## D37 - Instrumentation goes through one `Observer` interface (M5)
The graph and runner report events (sync, message and tool events), and
model calls are observed through LangChain callbacks. Metrics and tracing
are two implementations of the same interface, composed at startup, so agent
code never imports Prometheus or Langfuse. The metrics use a dedicated
Prometheus registry, and every label value comes from a closed set
(outcome, category, tool, provider, model, direction), never from email
content. A test enforces this. The CLI `sync` is a short-lived process, so
its metrics become scrapeable once M6 serves `/metrics` from the API.

## D38 - Langfuse via its core SDK, with email bodies redacted by default (M5)
Langfuse's LangChain integration requires the full `langchain` package, which
is outside the agreed stack. A small tracer on the core SDK (already a
dependency) produces the same trace shape (sync, then message, then
generation and tool spans) and controls exactly what leaves the process. By
default, email bodies inside prompts are replaced by their length
(`APPLY_AGENT_LANGFUSE_REDACT_BODIES`). Sender, subject, correlation ids,
model output, tokens and cost are kept. Tracing is off by default. Turning it
on without credentials fails at startup rather than silently tracing
nothing. For a real inbox, point `LANGFUSE_HOST` at a self-hosted instance:
otherwise the local model's privacy benefit (D34) is lost. Tests capture
spans with OpenTelemetry's in-memory exporter, so they need no network or keys.

## D39 - Cost and latency are reported honestly, including their gaps (M5)
Cost is tokens times a price table (Anthropic list prices; $0 for local
models). A model with no known price is counted in
`apply_agent_llm_unpriced_calls_total` instead of being treated as free, and
the dashboard shows that counter next to spend. Latency is measured wall time
live. The evaluation prefers server-reported time (Ollama's `total_duration`),
which is stored in recordings and therefore survives replay. When neither is
available (replaying a hosted model), the report says "unavailable" instead
of printing a near-zero replay time.

## D40 - The Grafana dashboard is tested against the code (M5)
`deploy/grafana/dashboards/apply-agent.json` has 19 panels in four rows:
sync health, agent behaviour, LLM, and applications. A test extracts every
PromQL expression and fails if it references a metric the registry does not
expose, so renaming a metric cannot silently blank a panel.

## D41 - `POST /sync` starts a background job; one sync at a time (M6)
With a local model a sync takes minutes. `POST /sync` returns 202 with a job
id, `GET /sync/{id}` reports status and the final report, and the page's
button polls it. A single-worker executor runs the jobs, and a second
`POST /sync` while one runs gets 409 with the running job, so two syncs never
process the same messages concurrently. Known limit: job history is in
memory (the last 20) and is lost on restart. The data itself is not, since it
lives in SQLite.

## D42 - One API token, accepted as Bearer or as a Basic password (M6)
Scripts send `Authorization: Bearer <token>`. Browsers get a 401 with
`WWW-Authenticate: Basic`, so the browser's own prompt handles login (any
username, the token as password) and re-sends it on the page's `fetch` calls.
No cookies or login page are needed. Tokens are compared in constant time.
If `APPLY_AGENT_API_TOKEN` is unset, a random token is generated and printed
once at startup (like Jupyter), so the API is never open by accident and
still needs no setup. `/healthz` and `/metrics` are open: they carry no
personal data, and Prometheus scrapes them inside the compose network. Every
compose port binds to 127.0.0.1.

## D43 - Container: multi-stage uv build, non-root, fixtures baked in (M6)
The build stage installs the locked, non-dev dependencies with uv (a separate
layer, so code changes don't reinstall them) and the project non-editable.
The runtime stage has the virtualenv and the fixture emails only, runs as
UID 10001, keeps SQLite on a volume, and has a health check. SQLite runs in
WAL mode with a busy timeout, so the API can read while a background sync
writes.

## D44 - CI runs the LLM evaluation on every push, at no cost (M6)
GitHub Actions runs ruff, ruff format, mypy, pytest, the baseline eval (at
least 70%, 65% since D46) and the qwen3 eval replayed from its recording (at least 95%). The
replay needs no model server or key; a changed prompt fails it with exit
code 2 until the recording is regenerated. A second job builds the image and
smoke-tests health, auth and the JSON endpoint. The replay was verified
inside the Linux image, so recordings made on Windows replay identically.

## D45 - What running the real stack caught that tests did not (M6)
Bringing up Prometheus and Grafana, rather than trusting the dashboard test,
exposed five bugs, each now fixed and covered by a test:
1. Panels used `increase(...[$__interval])`. At short time ranges the window
   is shorter than the scrape interval and returns nothing. They now use
   `$__rate_interval`.
2. Counters that first appear already incremented are invisible to
   `increase()`, so the first sync never showed on a graph. Known label
   combinations are now created at zero at startup.
3. Stat panels over a series that did not exist yet showed "No data" instead
   of 0. They now use `or vector(0)`.
4. The baseline reported `provider="keywordbaselinemodel"` (LangChain's
   default from the class name). The price table did not recognise it, so
   every call counted as "unpriced". The baseline now declares its provider,
   and a test checks every model's provider against the price table.
5. The `tool` label took whatever name the model asked for, so invented tool
   names could create unbounded series. Unknown tools are now labelled
   `unknown`.

## D46 - Job-board and ATS notifications in the corpus (M7)
Most applications are sent through LinkedIn or a company's ATS, so most
replies come from a platform, not from the company. Sixteen fixtures were
added (corpus 26 -> 42, size guard raised to 50): LinkedIn Easy Apply
confirmations, "application viewed" notices, rejections relayed by LinkedIn,
a recruiter InMail about a real application next to an agency InMail about
none, a LinkedIn job alert, InfoJobs state changes ("CV leido", "Descartado"),
and Indeed, Workday, Greenhouse and Lever messages, in English and Spanish.
Labelling rules, in addition to D11:
* A job board saying the company viewed the application or read the CV is
  `other`: being looked at is not a stage.
* Recruiter outreach through a job board's messaging is not a job application
  unless it refers to the user's application, even if it describes an
  interview process.
* The company is the hiring company, never the job board relaying the message.

The first `qwen3:8b` run scored 95.2%. Its mistakes would have hurt a real
user: an "application viewed" notice read as an interview, an agency InMail
recorded as an application (a phantom row), and a Spanish job title translated
to English, which gives the same application a second role key and splits it
in two (D4). The prompt now states the first two rules and asks for the title
verbatim, in its original language. After that change the model scored 97.6%;
its one error is an older fixture (a friend's mock-interview offer). Because
the prompt was changed after seeing these failures, the score is optimistic
in the same way as D36.

The keyword baseline drops to 66.7% and matches the company on 17% of emails,
because it reads the company from the sender's domain. Its CI floor moves
from 70% to 65%; the floor guards against regressions, not quality.

## D47 - The local model's output is capped (M7)
Re-recording the evaluation hung for half an hour on one email: with greedy
decoding and no output limit, `qwen3:8b` can loop in its reasoning, and the
Ollama client had no timeout either. A real sync would have hung the same way
and blocked every later sync (D41). `num_predict` is now capped at 4,096
tokens (the same budget as the Anthropic path) and requests time out after
5 minutes. A capped reply without a tool call gets the usual reminder and
retry, then counts as a failed message (D25). The cap is part of the model
fingerprint, so the change invalidated and regenerated the recording.

## D48 - The page groups applications into three stages (M7)
The user's question is "who replied, who didn't, and where am I moving
forward", not "what is each status". `stage_of` collapses the five statuses
into three stages, as a pure function next to `derive_status` (D3):
* advancing: information requested, interviewing or offer received;
* sent: only acknowledgements so far ("we received your application",
  "application viewed"), meaning nobody has replied yet;
* rejected.

The page shows advancing first, because it needs the user, then sent, then
rejected inside a closed `<details>`. Every row shows how many days ago its
last news arrived. An open application at or past `quiet_after_days` (default
14) is flagged, so silence becomes visible rather than inferred from a date.
The JSON adds `stage` and `days_since_last_news`. The current time is
injected into the app (`clock`), so the day arithmetic is tested without
freezing time.

Running the page over the fixtures with `qwen3:8b` showed one application
split in two: "Cinderpeak" and "Cinderpeak Games" give different company keys
(D4). Matching company names that differ by a trailing word is the next fix,
and needs care so that distinct companies sharing a first word are not merged.

## D49 - Gmail per-user quota errors are waited out (M7)
The first sync of a real inbox (424 messages in 30 days) died after a few
seconds: fetching every message back to back exceeded Gmail's per-user quota
per minute, which Gmail reports as 403 or 429. The client's own retries give
up within seconds, and a failure while listing aborts the whole sync. The
adapter now recognises quota errors (429, or 403 with a rate-limit reason)
and waits 5, 15, 30, 60 and 60 seconds before giving up. Other 403s, such as
a missing permission, still fail at once. The real sync then waited six times
and completed.

## D50 - Housekeeping is not an information request; a rejection stays closed (M7)
The first sync of a real inbox (30 days, 424 emails, 82 applications) put six
applications under "advancing", all as information requests, and all six
were wrong: candidate-experience surveys, "confirm your email" and
verification-code steps, a reminder to finish an incomplete application, and
a privacy-consent request. One survey arrived after a rejection and, because
status follows the latest status-bearing event (D3), reopened it.

Five fixtures of those kinds were added (corpus 42 -> 47), and the prompt now
says that an information request comes from a hiring team that has reviewed
the submitted application, and that form steps, consent and surveys are
`other`. `qwen3:8b` resists this rule: four wordings were tried against a
ten-email subset (the five new emails plus five genuine requests). Listing the
exclusions in the `other` definition, listing them as "NOT
information_request", and writing the summary before the category all left
three or four of the five wrong. Describing a request as "having reviewed the
submitted application" did best. On the full corpus the model scores 95.7%,
still missing two of the five (which two varies between runs).

Because the classifier will keep making this mistake, status derivation now
guards against its worst effect. After a rejection, only an interview
invitation or an offer reopens an application; a later information request
does not. Status is still a pure function of the events, sorted by time
(D3), so processing order still does not matter.

## D51 - A manual correction is a starting point, not a frozen status (M8)
The classifier makes mistakes (D50), so the user can set an application's
status by hand. The correction must fit D3, where status is derived from
events and never mutated. Three designs were considered:
* Overwrite `applications.status`: lost on the next `_refresh`.
* A permanent override: a real interview arriving next week would be ignored.
* An override with a timestamp (chosen): `derive_status` starts from the
  status the user set and applies only events dated after it. Earlier emails,
  including ones processed later, no longer count. Later emails still move
  the status, and a manual rejection is as sticky as a real one (D50).

The override lives in its own table, `status_overrides`, one row per
application. A new table is created by `create_all` on existing databases,
whereas a new column would need the migrations D9 deferred; a test drops the
table and checks it comes back. `PUT /applications/{id}/status` sets it and
`DELETE` removes it, returning to the derived status. The page marks a
corrected row as "set by hand". The mailbox stays read-only: a correction
only changes the local database.

## D52 - Excel export writes email text as strings, never formulas (M8)
`GET /applications?format=xlsx` returns the table as a workbook (openpyxl),
with real date cells, a frozen header and an auto-filter. Company, role and
summary come from email content or the LLM. openpyxl stores a value starting
with `=` as a formula, so a hostile email could have a formula run when the
user opens the export. Every text cell is therefore forced to the string
type, and a test exports `=HYPERLINK(...)` as a company name and checks the
cell type. This is the spreadsheet counterpart of escaping on the HTML page.

## D53 - A sync downloads only the messages it has not processed (M8)
A later sync re-lists a few days before the newest recorded message
(`SYNC_OVERLAP`, plus Gmail's one-day margin, D20) so late mail is not
missed. It used to download every listed message in full and only then drop
the ones already processed. `list_messages` now takes an optional
`already_have` callback: the provider lists ids, asks which ones the caller
has, and fetches only the rest. Listing ids is one cheap request per page;
each body is a request against the per-minute quota (D49). On a real inbox a
routine sync went from 85 downloads to 26.

The callback is a hint, not a contract: a provider may ignore it, so the
toolbox still filters the result. Messages that failed are not recorded
(D25), so they are still fetched and retried.

## D54 - Duplicates are merged by the user, never automatically (M8)
A model sometimes writes one application two ways: "Cinderpeak" with role
"Backend Engineer" in one email, "Cinderpeak Games" with "Backend Engineer -
Technical Interview" in another. The two differ in both halves of the
identity (D4), so they become two rows.

Merging similar names automatically was rejected. Fixing the company alone
would not have joined that pair, and fuzzy role matching cannot tell a noisy
copy of one role from a second role at the same company. The second case is
the common one: in a real inbox, 9 of 68 companies had several applications
for different roles, against 2 possible name variants. A wrong merge also
hides an application silently, whereas a duplicate is at least visible.

So the user decides. `POST /applications/{id}/merge` folds one application
into another: its events, threads and drafts move, the status is derived
again from the combined history (D3), and the more recent manual correction
is kept (D51). The merged-away (company, role) is stored in
`application_aliases`, and matching consults it right after the exact match,
so the next email with that spelling joins the surviving application instead
of recreating the duplicate. Aliases follow the application through later
merges.

The page offers the merge only between applications whose company keys are
equal or where one extends the other (`similar_company`), which keeps the
menu short. That function only decides what to offer; it never merges. A
merge cannot be undone, because afterwards nothing records which event
came from which row, so the page asks for confirmation.

## D55 - Every application shows the emails behind it
A status the user cannot trace is a status the user cannot trust, and the
classifier is wrong often enough (D50) that tracing has to be one click.
Each row unfolds into its events, newest first: date, the category the
classifier chose and its summary. That alone usually explains a wrong
status ("information request: confirm your email").

To read the original, each event links to the message in Gmail. No schema
change was needed and nothing new is stored: the link is built from the
message id already kept for idempotency (D5), so subjects and bodies stay out
of the database (D6). Links appear only when the provider is Gmail, since a
fixture's id is an RFC Message-ID that Gmail would not resolve. The link
opens the first signed-in Google account, which is a known rough edge for
people with several. It is opened with `noreferrer`, and the id is
percent-encoded, as it is text that originated outside the application.

## D56 - The company of a Workday email is corrected in code, not in the prompt
A real acknowledgement sent from `teamsystem@myworkday.com` never named the
company in its text. `qwen3:8b` first answered with no company and, after the
validation error, with "MyWorkday", so the application was filed under the
platform. The model does not read a mailbox-like word before the @ as a
company name; with a sender that looks like one it gets it right.

A prompt rule was tried first, in eight wordings. Only one kind fixed the
real email: listing the platforms' domains and giving an example. On the full
corpus each of the four wordings measured cost category accuracy (95.8% down to 91.7-93.8%), on the
emails D50 is about: housekeeping read as an information request, and an
"application viewed" notice read as an interview. That would trade one wrong
company name for applications wrongly shown as advancing.

So the prompt is unchanged and `with_tenant_company` corrects the tool
arguments before they are validated. It applies only when the sender is on a
platform that sends as `<company>@<domain>` (today: Workday), the email is
about an application, and the model gave no company or the platform's. A
company the model found in the text is kept. An application to Workday itself
arrives from `workday@...`, so it still reads "Workday". The name is the
address's first part capitalised ("Teamsystem"); its key matches the proper
spelling (D4), and the platform list is one dictionary to extend.

One fixture was added (corpus 47 -> 48): the same email with a fictional
sender. The recording gained only that email's responses, since the prompt
did not change (D35).

## D57 - Job offers come from LinkedIn's alert emails; searching LinkedIn is opt-in
The user wanted to see which offers are open and whether they have already
applied to them. LinkedIn has no job search API for individuals, so the
offers had to come from somewhere else. There were three options:

1. **Job alert emails.** LinkedIn emails the postings that match the alerts
   the user sets up. The inbox is already read, read-only (D18), so this adds
   no credential, breaks no terms and puts no account at risk. It only sees
   what the alerts send, a handful of postings per alert per day.
2. **LinkedIn's public listings** (`/jobs-guest/...`, the pages shown when
   signed out). It needs no account, but it is scraping, which LinkedIn's
   terms forbid, and its HTML can change at any time.
3. **Automating a signed-in browser.** Rejected: LinkedIn restricts accounts
   it catches doing this, and that is the account the user applies with.

Alerts are the default. The public search is built but off
(`APPLY_AGENT_LINKEDIN_SEARCH_ENABLED`). It sends few requests (two pages per
search by default, three seconds apart), says what it is in its User-Agent
instead of posing as a browser, and stops at the first 403, 429 or 999
without retrying. The first real request, on 2026-10-19, got a 429 with a
JavaScript bot challenge. It will not be worked around: getting past bot
detection is exactly what the terms forbid. So the page reports the refusal,
and alerts remain the source that works.

Offers are kept apart from applications. A scan never calls a model and never
writes an event or an application. It has its own table of scanned emails, so
it neither re-downloads mail nor depends on the agent's sync, and it asks the
provider only for LinkedIn mail (a new `sender` filter, Gmail's `from:`).
Alerts are parsed with rules, not a model: their layout is fixed, rules cost
nothing per email, and a model would only be one more thing for a hostile email
to steer (D23). A posting is identified by LinkedIn's id when the email links to
it, otherwise by company + role + location keys (D4). A posting seen again
keeps the user's decision (interested / dismissed).

"Already applied" is worked out on every read, never stored, so an
application recorded after the offer was found counts at once. It needs the
same company (D4, including longer and shorter spellings) and a role that is
equal or extends the other ("Senior ML Engineer, Recommendations" against
"Senior ML Engineer"). An application at the same company with another role,
or with no role, is shown as a hint, never as proof: one company often has
several openings.

One *Sync now* serves both pages. It is one job (D41): mail first, then
offers, so an application sent today already counts when new offers are
checked against it. A failed offer scan is reported in the job's report and
on the offers page, but the job still succeeds, since the mail part did.

## D58 - Real alerts are read link by link; offers are ranked by title fit and age
The first reader (D57) was written from an assumed layout. On the user's real
inbox it misread 103 of 344 offers: LinkedIn puts a blank line between a
posting's place and its hints ("This company is actively hiring", "1 school
alum", "Apply with resume & profile"), and the reader split postings on
blank lines, so a hint became the title and another the company. City-only
places ("Stockholm") were also dropped. Three real alerts were read, with the
user's permission, to learn the layout.

A posting is now the lines before each "View job" link since the previous
link or rule of dashes, without URLs and known hints; the last three are
title, company and place. Read against 151 real alerts, every one of the 927
links gave exactly one posting, with no hint as a title and no missing place.
`offers --rescan` deletes offers whose title or company is a hint, forgets
which alerts were read and reads them again. On the real database it
replaced all 103, lost no offer and kept every decision.

The user, just out of a master's, wanted offers asking for years they do not
have to sink, and old offers out of the way. Requirements are not in the
alerts, and reading each posting's page would mean the scraping LinkedIn
refuses (D57). So fit is judged from the title: seniority words map to the
years they usually mean (entry 0, mid 2, senior 5, lead or architect 7),
compared with `APPLY_AGENT_PROFILE_YEARS_EXPERIENCE`; words from
`APPLY_AGENT_PROFILE_KEYWORDS` in the title add points. A title with no
seniority word is "possible", never "good" by itself. The reasons are shown
on hover, so a ranking can be checked. It only orders offers: it hides none.

The honest limit is coverage: 85% of the real titles state no seniority, so
for most offers only the keywords separate them. "Member of Technical Staff"
is not a staff role.

Age is when an alert last mentioned the offer, since the alerts give no
posting date and a re-sent offer is likely still open. Offers older than
`APPLY_AGENT_OFFERS_OLD_AFTER_DAYS` (14) fold into "Older offers". Within each
section offers are ranked by score, then by that date.

## D59 - The fit profile is a TOML file outside the repository
D58 read a profile from two variables, years and keywords. The user's real
profile was richer: roles they want (applied AI, LLM agents, backend), roles
to avoid whatever else the title says (MLOps, DevOps, SRE, platform or infra,
frontend-only, research, freelance), preferred places (the Netherlands or
Madrid), bonus industries, and a ceiling on years asked (five, though they
have three). It also corrected an assumption: three years of experience, not
none. That does not fit in environment variables, and it is personal, so it
is a TOML file at `~/.config/apply_agent/profile.toml`, next to the Gmail
token, with a synthetic `profile.example.toml` in the repository. The two
variables are gone (they were never released). A missing file means an empty
profile; a broken one stops the app, since ranking with half a profile would
look right and be wrong.

The score, from 0 to 100, keeps D58's base from the title's seniority, now
in three bands: within the years the candidate has (75), above them but
within the ceiling (60), and beyond it (40 or 10). One wanted word adds 10,
two or more 15; a preferred place and a bonus word in the title or company
add 10 each. An avoided word caps the score at 15. Words match whole words
of the role key, so "back end" matches "Back-End" and "ia" does not match
"Ingeniería". Offers that score under 40 fold into "Poor fit", next to the
old ones: hidden from the first view, never deleted.

Tuned on the user's 863 real offers. The first weights put 64% in "good" and
many at 100, so the top said nothing; a senior title, within the ceiling,
scored like a mid one. With the bands above: 45% good, 47% possible, 8% poor,
and the top is mid-level AI roles in Utrecht and Madrid. A bare "frontend"
also sank full-stack titles ("Backend, AI & Frontend"); the profile avoids
"frontend developer" and "frontend engineer" instead.

## D60 - Experience is counted per area, and an unstated seniority is a guess
With D59 the user still saw plain "AI Engineer" offers ranked as good fits,
and opened postings that asked for three years as an AI engineer. They have
three years, but of backend work; their AI is a master's. The profile had one
number of years and applied it to every title, and a title with no seniority
word was scored as neutral. Of the 391 offers ranked good, 244 were AI titles
with no seniority word, and 19 were senior AI titles.

The requirements are still not in the alerts (D57, D58), so "asks for three
years" cannot be read. Two changes make the guess honest instead:

* **Years per area.** The profile can name areas, each with its years and
  the words that recognise it in a title (`[areas.ai] years = 0`). A title is
  judged against the years in its area; one that names several ("AI Backend
  Engineer") against the weakest, since it asks for each; one that names none
  against the total.
* **An unstated seniority is assumed, not ignored.** A title with no
  seniority word is taken to ask `unstated_years_asked` (2) years in its area.
  For the user, "AI Engineer" now ranks as possible, "Backend Engineer" still
  as good, and "Junior AI Engineer" stays at the top.

Within the ceiling the base score is 75 when the candidate has the years
asked (65 if the years were assumed), 40 when they are two or fewer short,
and 10 beyond that or beyond the ceiling. On the user's offers: 264 good
(was 391), 515 possible, 84 poor. The cost is the other side of the same
guess: an AI role that is entry level but does not say so also ranks as
possible, one step down, never hidden.

## D61 - Closed offers are reported by the user, and teach when offers go old
The user kept opening offers LinkedIn no longer accepts applications for. On
their database, 604 of 873 offers (69%) had had no alert for over 14 days,
and 814 had appeared in a single alert, so the date of that email is the
only sign of an offer's age.

Whether a posting is still open is on its LinkedIn page. Reading those pages
is the scraping LinkedIn refuses to clients it takes for bots (D57), and
doing it through the user's signed-in session would put their account at
risk. So the app does not check; the user tells it, and it learns from that:

* **No longer available** is a choice on each offer. It moves the offer to a
  folded section of that name and records how many days had passed since the
  last alert that listed it. It is a table of its own (`offer_closures`), not
  a new `state` value: the state column's CHECK constraint was fixed when the
  table was created, and a new table needs no migration (D9). Undoing it is
  one click, as marking it was.
* **The fold learns.** With five closures or more, offers fold into "Older
  offers" after the median of those ages (at least 3 days) instead of the
  configured 14. The page says which rule applies and how many more closures
  it needs. The age measured is when the user found the offer closed, not
  when it closed, so it errs late: it hides fewer offers, never more.
* **Age is visible before the click.** Each offer shows how long ago its last
  alert was: green when two days or fewer, orange "may be closed" once past
  half of the fold age.

Age is measured from the last alert that listed an offer, the same for the
fold and for closures, because an offer an alert repeats is likely still open.

## D62 - A Search page over Adzuna and companies' own job boards
LinkedIn alerts were not enough: the user wanted to search by title,
company and country themselves. Searching Google or LinkedIn directly is
scraping, which they refuse (D57). Two sources are built for it instead:

* **Adzuna's API**, a job aggregator. Its key is not free: a new one gets 25
  test calls, and full access (5,000 calls a month) was £8 a month on
  2026-10-24, which the first version of this decision missed. It
  searches by title or keywords, place, country (19, the Netherlands and
  Spain among them), company and age, and returns a snippet of each
  description. Its key travels in the URL, so the httpx request log is
  silenced and errors never repeat it. It is optional
  (`APPLY_AGENT_ADZUNA_APP_ID` and `_APP_KEY`); its parser follows the
  documented shape and was not checked against the live API, which needs
  the key.
* **Companies' own job boards** through the public APIs of Greenhouse,
  Lever (and its EU host) and Ashby, which publish every open posting with
  its full description, without a key, so that sites can list them. A
  company is found from its careers URL, or by trying slugs made from its
  name on each board ("Mollie" was found on Ashby). Checked against real
  boards on 2026-10-24.

The boards bring two things the alerts never could. The **description**,
from which `years_required` reads what a posting asks ("3+ years of
experience", "at least four years", "3 años de experiencia", "3 jaar
ervaring"); when it says, that beats the title's guess (D60). On 363 real
postings, 94 stated years and the samples read right; the rest name none.
And **closure**: a board's listing is complete, so a posting missing from a
fresh one has closed, and is marked so. Board closures do not teach the fold
age (D61), which is about when users find LinkedIn offers closed. A posting
listed again is reopened.

A search typed on the page runs at once and its results become offers, with
the same fit score and applied check; it can be saved to run on every sync.
Watched companies are read on every sync too, keeping only titles with the
profile's target words (or the company's own) in the places asked, and
always remote postings. One failure never stops the rest of a sync.

The offers table had to change: its source column's CHECK constraint listed
the two LinkedIn sources, and SQLite cannot alter a CHECK. So D9's rule (no
migrations, `create_all` only) gets its first exception, `storage.migrations`:
it rebuilds `job_offers` when its CHECK or columns are behind (create the new
table, copy every row, drop, rename, with foreign keys off so closures keep
their ids) and adds `offer_closures.detected`. It checks before acting, so it
runs on every start and changes nothing the second time. Tested against the
user's real table definitions; on their database, every row came through and
the integrity check passed. Saved searches and watched companies store board
names as plain strings, so a new board needs no migration.

## D63 - More sources: InfoJobs, and Recruitee, Personio, SmartRecruiters boards
Adzuna turned out not to be free (25 test calls, then a paid plan), so the
user asked for free ways to search by title, company and country. There is
no free API that searches everything: LinkedIn and Indeed closed theirs, and
what sells that today is paid or scraping under another name. Checked on
2026-10-24, two kinds of source cost nothing to try and fit where the user
looks (the Netherlands and Madrid):

* **InfoJobs**, Spain's largest job board, has an official offer search: a
  public operation, authenticated with the client id and secret of an app
  the user registers with their InfoJobs account (its developer site states
  no price; this was not confirmed further). It filters by keyword,
  province and age, and every offer states its minimum experience from a
  fixed list ("Al menos 2 años", "Más de 5 años"), which goes into the
  description for `years_required` (now reading the singular "año" too). It
  cannot filter by company name, so that is matched locally (D4). Without
  credentials it answers 401; the client follows the documented operation
  and was not run against the live API.
* **Three more company boards** with public listings and no key: Recruitee
  (common among Dutch startups), Personio (an XML feed, common in Spain and
  Germany, which states the years asked as a code such as "2-5") and
  SmartRecruiters (large employers). Recruitee and Personio list complete
  descriptions. SmartRecruiters lists none, and pages: a description costs
  one request per posting, so it is fetched only for postings that pass the
  filters and are not stored yet; and a listing is read up to 1,000
  postings, past which it is partial and no closure is inferred from it.
  It also answers any name with an empty listing, so empty means not found.
  All three were checked against real boards.

A title search now runs on every source set up for its country, Adzuna for
any and InfoJobs for Spain, merges their offers, and reports a failing source
without losing the others' results. Jooble (a key by request, truncated
descriptions), Careerjet (needs each end user's IP and browser) and EURES (no
documented API) were left out.

Adding these sources also exposed a bug from D62: the offers page labelled
sources from a map that only knew LinkedIn's two, and failed on any other
offer. Every source now has a label, and a test renders one of each.

## D64 - Company research: news, hiring and whom to contact, without scraping people
Before writing to a company the user wanted to know what it is going through
and whom to contact. A company page (`/company?name=...`) brings together:

* **News** from Google News' RSS search and Hacker News' search API, both
  public and meant for programs. A company named like a person drowns in
  namesakes ("Mollie" returned obituaries), so the page takes an optional
  context ("payments") and otherwise adds generic business words and drops
  obituaries; a headline that does not name the company is dropped too,
  since searches also match article bodies. Results are cached 15 minutes.
* **A summary** of the headlines by the configured model, on request: the
  local Ollama model by default, so it costs nothing and the headlines stay
  on the machine (about 20 s with qwen3:8b). The headlines are untrusted, so
  the prompt says they are data, and the reply is escaped. A plain model is
  built for it (`make_text_model`), leaving the classifier and its
  recordings untouched.
* **Hiring**, from offers the app already keeps: open, new this week and
  month, closed, and where. These are the offers that passed the user's
  filters, not the company's whole headcount, and the page says so.
* **Contacts the company publishes**: its careers page, and email addresses
  written into its own job descriptions. Nothing is guessed.
* **People to look for**: roles (a technical recruiter, talent acquisition,
  the engineering manager and head of each area the company hires in), each
  a LinkedIn people search the user opens with their own account.

Two things were left out on purpose. Reading people from LinkedIn would be
scraping it with the user's account (D57). Guessing or looking up personal
email addresses would compile personal data about people who never agreed
to it, which the GDPR frowns on. The app stores no one's details. A first
version also suggested the CTO when few offers were open; it was dropped,
since the count reflects the user's filters, not the company's size.

## D65 - Adzuna is removed: no paid sources
Adzuna (D62) gives a new key 25 test calls, then charges for access. The
user's rule is plain: a source that has to be paid for does not belong in
the project. So it is gone, not switched off: the client, its settings
(`APPLY_AGENT_ADZUNA_*`, now rejected at startup like any unknown variable),
its offer source, its tests and its documentation. The httpx request log is
back at INFO, since no key travels in a URL any more.

What remains is free to use: company job boards (D62, D63), which need no
key, and InfoJobs (D63), which needs an app registered at
developer.infojobs.net; on 2026-10-24 its registrations were paused. So a
search by title now covers Spain only, and only once InfoJobs reopens; for
other countries, the way in is watching the companies themselves. The
Search form defaults to Spain. Databases keep "adzuna" in their source
constraint (D62's migration only widens it), which is harmless: no offer
uses it.

D62 and D63 still describe Adzuna as it was decided then; this entry is
where it ends.

## D66 - A list of companies, and a board found by guessing is only a guess
The user wanted to follow a list of companies in one go (one their father
drew up: 45 names in seven markets) and see a breakdown for each. Checking
that list showed two problems with how boards were found by name (D62):

* **Namesakes.** Guessing slugs from a name found "meta" and "accenture" on
  Recruitee and "amazon" on Personio. They were other companies, or trial
  accounts holding only the board's sample postings ("Senior Marketer
  (Sample)"). Short, common names are taken.
* **One failing board ended the search.** A 403 from one board raised
  instead of trying the next.

So `find` now returns a `BoardMatch`, not just a board. A careers URL the
user gives is confirmed. A board found by guessing is confirmed only with at
least five real postings and, where the board reports the company's name
(Greenhouse, Recruitee, Personio and SmartRecruiters do), a name that
matches, spaces aside ("BoschGroup" is "Bosch Group"). Anything less is a
possible match: the user sees its postings and says whether it is the
company. Sample postings are never counted or stored, and a board that fails
is skipped.

The **Companies page** takes a pasted list, one group per line before a
colon, names separated by commas or "and" (also "y", "e", "und", "et", as
lists are written in several languages). Finding the boards takes seconds
per company, so it runs as a background job, one company at a time. Sure
matches are watched at once; possible ones wait for the user; companies on
no readable board are followed through LinkedIn alerts, which the page
suggests. Each company shows its board, its open offers, how many fit well,
the best one, and a link to its company page (news and contacts, D64). The
list is a table of its own, with statuses as plain strings, so it needs no
migration.

On the user's real list (45 companies, checked against the live boards on
2026-10-25): 7 found and watched (Adyen, TomTom, Databricks, Nebius, Wise,
Monzo, Celonis), 3 possible, which were exactly the three namesakes above,
and 35 on no board this app reads: large employers on Workday,
SuccessFactors or their own sites, followed through LinkedIn alerts. A name
in several groups is kept once, in its first group.

## D67 - Web research through a private SearXNG, reported by the local model
The user wanted the agent to research companies on the open web, not only
in job boards and news feeds. Searching needs a search API, and on
2026-10-25 the free ones had mostly gone: Google's web search API took no
new customers and closes on 2027-01-01, Bing's closed in 2025, Brave's free
tier now needs a card. Tavily and Exa had free tiers without a card; the
user chose SearXNG on their own machine instead, unlimited and free.

SearXNG is a meta-search engine: it asks Google, Bing and others the way a
browser would, which their terms do not allow. For one person searching a
few times a company it is unlikely to matter, but it is the user's call and
it is written down here. The agent searches little and slowly: four
queries per investigation, a pause between them. SearXNG runs in Docker
(`deploy/searxng`), pinned to a release, bound to 127.0.0.1, with its JSON
output on and its limiter off (one user, nothing to protect). `start.ps1`
starts it when Docker is there and sets `APPLY_AGENT_SEARXNG_URL`; without
it research is off and the page says how to turn it on.

An investigation is queued from a company page, with an optional question,
and run in the background one at a time (a report takes minutes with a
local model). It searches with queries made from the company, its context,
the question and the profile; picks the best results, at most two per site,
skipping sites that need an account (LinkedIn, Glassdoor, Indeed); reads up
to five pages as text; and has the configured model write a report under
fixed headings, every point citing the numbered pages. Reports are kept with
their sources.

Pages are the open web, so they are treated as hostile. The reader fetches
only http(s), never an address on this machine or its network, checked again
on every redirect (a page cannot bounce it to the local API), only text, and
at most 2 MB. The prompt says the pages are data, the model has no tools, and
the report is escaped. It asks for nothing about people: no private names,
no contact details.

Tried for real on 2026-10-25, with Docker Desktop on the user's machine: a
search for Adyen's ML roles returned 41 results with no engine failing, and
investigations of ASML and Booking.com took 33 to 38 seconds each with
qwen3:8b, the Booking.com one citing its careers page and engineering
write-ups by OpenAI, AWS and Astronomer. The first try showed two faults,
now fixed. A Reddit thread, built by JavaScript, was read as an empty page
yet kept as a source, and the model cited it for its main answer: pages with
under 300 characters of text are now not read. And `start.ps1` would have
stopped every time: Docker reports progress on stderr, which Windows
PowerShell under `$ErrorActionPreference = 'Stop'` takes for an error, so the
compose call now runs with errors allowed and its exit code checked.

## D68 - Where offers are, and under which company name
On the Companies page the user saw a Siemens job, "Software engineer (Java)
Rail Signalling", under the group "Alemania", and it was in Spain. Nothing
was wrong, but the page hid two things. The group is only the label of the
pasted list, while a company's breakdown counts all its offers wherever they
are, here from LinkedIn alerts in several countries; and LinkedIn wrote the
place as "Tres Cantos", which says nothing to someone not from Madrid.
Also, two of the four "Siemens" offers were Siemens Energy's: matching
company names tolerates a longer name (D4), right for "Adyen N.V." and
wrong for a spun-off company.

So the breakdown now says where the offers are ("2 in Spain · 1 in
Switzerland · 1 in Sweden"), in bold for the country the group names
("Alemania" is Germany), and lists any other name its offers carry
("Includes Siemens Energy (2)"). The company page adds the country to a
place that does not say it, and the other name under the title. Offers are
not filtered by market: the user chose to see them all, labelled.

`domain.places` finds a country by its name in the languages lists are
written in, or by a city: the cities of the user's markets that their
offers name. On their 1,257 places it found 1,250; the other seven were
salaries ("€65K-€90K / year") that LinkedIn writes where the place goes,
which the alert reader now treats as hints, not places.

## D69 - Languages a job asks for
The user kept seeing good fits for jobs that, once opened, asked for fluent
Dutch or German, which they do not speak. The profile had no languages and
the score never looked for them. On their 1,197 offers, 926 (77%) came from
LinkedIn alerts and had no description at all, so the requirement could not
be read; of the 271 with one, those naming a language as required showed as
good fits ("Fluent in English and German", an intern role in Munich).

The profile now lists the languages the candidate can work in. A posting
asks for a language when a sentence names it with a requirement ("fluent",
"vloeiend", "verhandlungssicher", "(Dutch only)", "German speaking"), or
when it is written in it: its description, by the share of common words of
each language, or its title, by a word for the job ("ontwikkelaar",
"utvecklare"; not "(m/w/d)", which jobs in Madrid carry too). A sentence
that offers a choice ("French and/or Spanish", "at least one Nordic language
(...)") is met by any of them. One that calls it a plus is only noted. A
language the candidate lacks caps the score as an avoided word does. On the
user's offers, 21 changed: German, Dutch, French, Nordic and Japanese
requirements, and postings written in Dutch or Swedish, all now poor fits.

For the alert offers, whose text the app never sees, the user notes it:
"Needs a language I don't speak" on an offer dismisses it and remembers the
company and language, suggested from the place (Utrecht: Dutch). The
company's other offers are then capped, less hard, with "This company asked
for Dutch before", since another of its postings may be in English. The
note matches the exact company key, so Siemens' note does not touch Siemens
Energy. Looking each alert offer up on the web with SearXNG was tried: of
ten, three or four pages were the same posting and the rest only similar,
which would give wrong verdicts, so it was not built.

## D70 - A good fit needs evidence of the years asked
The user kept seeing good fits for jobs that, once opened, asked for eight
or more years. Three causes, measured on their offers:

- 926 of 1,257 offers are LinkedIn alerts with no description. A title with
  no seniority word ("AI Engineer") was assumed to ask two years (D60) and,
  with a wanted word, reached good. Most good fits (283 of 349) were these.
  Such an offer is now at best possible, with "Years asked unknown", until
  the user opens it. A seniority word ("Junior", "Medior", "II") still
  counts as evidence.
- The description reader (D62) needed the word "experience" after the
  years, so "6+ years in engineering", "10+ years in ML/AI" or "5+ years as
  an ML engineer" were missed; 105 descriptions asked for more than was
  read. A plus sign or a range is now enough, and a bare number counts when
  followed by "as a", "in backend", "building" and the like. German ("4 Jahre
  Berufserfahrung") is read too. "Kept for up to 2 years" or "in less than 5
  years" are not requirements. 26 mentions are still left out, all of them
  correctly (onboarding periods, company history, a range's upper end).
- A wanted word in a title that is not the candidate's kind of job made it
  good: "Junior Customer Service Agent" (wanted: agent), "Growth & Sales
  Intern (Stealth AI venture)". The profile can list its roles ("engineer",
  "developer"); a title naming none ranks low.

A senior title whose description asks for fewer years ("Senior Backend, 3+
years") is now judged by the title, the higher of the two, so it shows as
possible rather than good. Good fits went from 349 to 89, all engineering
roles with the years stated or a junior or mid-level title.

## D71 - Workday, found by a pasted link
On the user's list of 223 companies, about half had no board the app reads,
most of them large employers (NVIDIA, Philips, ING, Siemens, Salesforce).
The user asked whether the app could query LinkedIn instead. It cannot: its
public search answered the first real request with a 429 (D57), and getting
past that would mean posing as a browser to beat a bot check. Many large
employers publish on Workday, whose careers sites are served by a public
JSON API (`/wday/cxs/{tenant}/{site}/jobs`) that the sites themselves use;
on 2026-10-26 it answered for NVIDIA, Salesforce, Philips, NXP and ING.

A Workday board is named by a tenant, a server (`wd1`...`wd12`) and a site,
and the site's name ("NVIDIAExternalCareerSite", "ICSGBLCOR") cannot be
guessed, so a Workday board is never guessed: the user pastes the careers
link, on the Companies page next to a company not found by name, or as the
company on the Search page. That box takes any board's link, which also
covers boards whose slug differs from the name (Bosch is "BoschGroup").

Workday serves 20 postings a page, newest first, without descriptions. Big
employers list thousands (NVIDIA: over 2,000; reading 400 took 40 seconds),
so the newest 200 are read on each sync, and as with SmartRecruiters (D63)
a partial listing closes nothing. The postings kept by the profile's words
are then read one by one, once, for their description, places and date;
"Posted 30+ Days Ago" is not taken as a date. Read again later from the
listing, a posting keeps what its own page said, since the listing's place
("2 Locations") and date are coarser.
