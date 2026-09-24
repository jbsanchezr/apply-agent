# apply-agent

An LLM agent that keeps track of job applications by reading an email inbox,
**read-only**. It classifies replies (rejection, interview invitation, request
for information, offer, other), extracts company / role / date / summary, and keeps a
table of applications and their current status.

> Work in progress. The full README (architecture, eval results, design
> decisions, limitations) arrives with milestone M7. Non-obvious choices are
> recorded in [DECISIONS.md](DECISIONS.md) as they are made.

## Run it

With no configuration, the agent reads the bundled fixture emails and uses a
keyword baseline instead of an LLM, so no credentials are needed:

```bash
uv sync
uv run python -m apply_agent sync
```

To classify with Claude, set `ANTHROPIC_API_KEY` and `APPLY_AGENT_LLM=anthropic`.
All settings are listed in [.env.example](.env.example).

## Evaluation

```bash
uv run python scripts/evaluate.py            # keyword baseline, no API key
```

This runs the full agent over the 26 labelled fixtures and prints per-class
precision/recall, accuracy with a 95% confidence interval, a confusion matrix
and the misclassified cases. Results so far are in [eval_results/](eval_results/).

## Development

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest
```

## Test data

All email fixtures in `tests/fixtures/emails` are synthetic. Every address and
URL uses a reserved domain (`*.example`, `example.com`, `*.test`), and a test
fails the build if a non-reserved domain appears.

## Using a real Gmail inbox (optional)

Access is read-only (`gmail.readonly`). The agent cannot send, delete or modify
mail, and refuses tokens with any broader scope.

1. In Google Cloud, create an OAuth client of type *Desktop app* with the
   Gmail API enabled, and save its JSON to
   `~/.config/apply_agent/client_secret.json`.
2. Run the one-time consent flow:
   `uv run python -m apply_agent.providers.gmail_auth`
3. Set `APPLY_AGENT_EMAIL_PROVIDER=gmail`.
