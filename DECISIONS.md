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
