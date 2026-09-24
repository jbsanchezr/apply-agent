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
