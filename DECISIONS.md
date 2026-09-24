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
