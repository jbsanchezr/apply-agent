# Changelog

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
