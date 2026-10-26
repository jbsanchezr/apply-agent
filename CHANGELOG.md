# Changelog

## Unreleased

* A job offers page (`/offers`) lists the postings in your LinkedIn job alert
  emails and checks each one against your applications: already applied,
  applied to the same company for another role, or new. Offers can be marked
  as interesting or dismissed. *Sync now*, on either page, syncs mail and
  then looks for offers; `python -m apply_agent offers` looks for offers
  only. A search of LinkedIn's public listings is included but off by
  default. See D57 in [DECISIONS.md](DECISIONS.md).
* Offers are ranked by fit with a profile file
  (`~/.config/apply_agent/profile.toml`, see `profile.example.toml`): the
  title's seniority against your years, roles you want and avoid, preferred
  places and bonus words. Then by the last alert that mentioned them. Poor
  fits and offers older than 14 days fold away. See D58 and D59.
* Experience is counted per area (`[areas.ai] years = 0`): three years of
  backend no longer make an "AI Engineer" title a good fit, and a title with
  no seniority word is assumed to ask two years in its area. See D60.
* Languages: list yours in the profile (`languages = ["spanish", "english"]`). An
  offer that asks for another language, or is written in one, drops to poor
  fit with the reason; a language that is only a plus is noted. For alert
  offers, whose text the app never sees, "Needs a language I don't speak"
  dismisses the offer and flags the company's other offers. See D69.
* The Companies page says where each company's offers are ("2 in Spain · 1 in
  Switzerland"), with the group's market in bold, and which other company
  names they carry ("Includes Siemens Energy"). Salaries in LinkedIn alerts are
  no longer taken for places. See D68.
* **Web research** on each company page: an "Investigate" button (with an
  optional question) searches the web through a private SearXNG, reads the
  best pages and has your local model write a report citing them. SearXNG runs
  in Docker; `start.ps1` starts it. See D67.
* A **Companies page** (`/companies`): paste a list of companies (one group per
  line) and the app looks for each one's job board in the background, then
  shows per company its board, open offers, good fits and best match, with
  links to its news and contacts. A board found by guessing the name is
  confirmed only with enough postings and the right company name; otherwise
  you are asked. See D66.
* A **company page** (`/company?name=...`): recent news from Google News and
  Hacker News with an optional summary by your local model, how the company
  is hiring, the contacts it publishes, and LinkedIn searches for the people
  worth contacting, which you open yourself. Company names link to it. See D64.
* A **Search** page (`/search`): watch companies' own job boards (Greenhouse,
  Lever, Ashby, Recruitee, Personio, SmartRecruiters) by name or careers URL,
  and search Spain by title, province, company and age through InfoJobs'
  official API (an app registered at developer.infojobs.net, no price
  stated). Searches can be saved to run on every sync. Board postings come
  with their full description, so the years asked are read from it, and are
  marked closed when they disappear. Existing databases are migrated on
  start. See D62, D63 and D65.
* Fixed: the *Job offers* page failed as soon as it held an offer from any
  source other than LinkedIn alerts.
* An offer can be marked *No longer available*. It folds away, and once five
  are marked, offers fold into *Older offers* at the typical age those had
  instead of a fixed 14 days. Each offer shows how long ago its last alert
  was, in orange once it may be closed. See D61.
* The alert reader follows LinkedIn's real layout: the first one took hints
  such as "This company is actively hiring" for titles. `offers --rescan`
  repairs offers read before. See D58.
* A search box on the page filters the applications by company or role as
  you type, ignoring case and accents.
* A Workday email that never names the company (sent from
  `company@myworkday.com`) is filed under that company instead of under
  "Workday". The agent corrects this in code, not in the prompt. One fixture
  added (48 in total). See D56 in [DECISIONS.md](DECISIONS.md).
* A Gmail token that Google has expired or revoked now fails with a message
  that says how to renew it.

## 0.1.2 - 2026-10-05

* Screenshots retaken to show the per-application email list. No code changes.

## 0.1.1 - 2026-10-05

* Each application on the page unfolds into the emails it was built from,
  with a link to the original message when the mail comes from Gmail. The
  JSON gains an `emails` list.

## 0.1.0 - 2026-10-05

First release. An LLM agent that reads an email inbox, read-only, and keeps a
table of job applications and where each one stands.

### What it does

* Classifies each incoming email (rejection, interview invitation, information
  request, offer, other), extracts company, role and a one-line summary, and
  derives every application's status from its full history of emails.
* Reads Gmail with the `gmail.readonly` scope only, or a bundled synthetic
  inbox with no credentials at all.
* Classifies with a local model through Ollama (default `qwen3:8b`, nothing
  leaves the machine), with Claude, or with a keyword baseline.
* Serves a page that groups applications into advancing, sent and waiting for
  a reply, and rejected, and flags the ones with no news lately.
* Lets the user correct a status by hand, merge duplicate rows, and export
  the table to a colour-coded Excel workbook.
* Ships Prometheus metrics, a Grafana dashboard, optional Langfuse traces, a
  Docker image, a compose stack and a Windows launcher.

### How it was checked

* 47 labelled synthetic emails, including LinkedIn, InfoJobs, Indeed, Workday,
  Greenhouse and Lever notifications in English and Spanish. `qwen3:8b`
  classifies 95.7% of them correctly (95% CI 86-99%); the keyword baseline
  70.2%.
* CI replays recorded model responses, so the LLM evaluation runs on every
  push without a GPU or an API key.
* A real inbox: 424 emails over 30 days. That run found a model that could
  hang indefinitely, Gmail's per-minute quota, and housekeeping emails read
  as progress. See D47 to D54 in [DECISIONS.md](DECISIONS.md).

### Known limitations

* The local 8B model still mistakes some housekeeping emails for information
  requests. Manual correction covers it.
* One application can appear as two rows when the model writes its company or
  role two ways. Merging is manual, and cannot be undone.
* An email that fails is retried only while it is inside the sync overlap
  window (about four days).
* Single user: one API token, SQLite, no migrations.
