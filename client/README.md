# pretalx-client

A standalone command-line client for the [pretalx](https://pretalx.org) conference
management REST API (https://docs.pretalx.org/api/resources/). It focuses on the
proposal-submission workflow: creating/updating submissions, managing speakers, and
attaching files (e.g. a proposal's PDF) as submission resources.

This is a separate [uv](https://docs.astral.sh/uv/) project — it is not part of the
pretalx Django application and talks to a pretalx instance purely over its public API.

## Install

```bash
cd client
uv sync
```

This creates a local virtual environment and installs the `pretalx-client` command.
Run it via `uv run pretalx-client ...`, or activate the venv (`.venv/bin/activate`).

## Configuration

The client needs a pretalx instance URL, an API token, and (for most commands) an
event slug. The `users` and `teams` commands also require an organiser slug. These
are resolved with the following precedence:

1. CLI flags: `--url`, `--token`, `--event`, `--organiser`
2. Environment variables: `PRETALX_URL`, `PRETALX_TOKEN`, `PRETALX_EVENT`, `PRETALX_ORGANISER`
3. A TOML config file (default `~/.config/pretalx-client/config.toml`, or if that file
  does not exist then `./config.toml` in the current working directory; override with
  `--config-file`), using named profiles selected via `--profile`/`PRETALX_PROFILE`
  (default profile name: `default`):

   ```toml
   [profiles.default]
   url = "https://pretalx.example.org"
   token = "your-api-token"
   event = "myevent"
  organiser = "myorg"
   submission_type = "1"
   content_locale = "en_gb"

   [profiles.default.custom_fields]
   # Question id or identifier for the submission custom file field.
   pdf_question = 123
   figshare_id = 124
   self_assessment = 125

   [profiles.ref11]
   url = "https://ref11dev.zrok.lcas.group"
   token = "your-api-token"
   event = "ref11"
  organiser = "ref11"
   submission_type = "1"
   content_locale = "en_gb"

   [profiles.ref11.custom_fields]
   pdf_question = 123
   figshare_id = 124
   self_assessment = 125
   ```

If not provided in environment/config, `content_locale` defaults to `en_gb` for
proposal creation commands.

If not provided in environment/config, `submission_type` defaults to `1` for
`submissions create`.

When `profiles.<name>.custom_fields.pdf_question` is set, pass
`--pdf-question /path/to/file.pdf` to `submissions create` to upload the file as
an Answer on that custom field (via `/answers/`).

Any key under `profiles.<name>.custom_fields` becomes a dynamic
`submissions create` flag by replacing `_` with `-`, e.g.:

```bash
uv run pretalx-client submissions create \
  --title "Test" \
  --figshare-id 12345 \
  --self-assessment 0.5 \
  --pdf-question paper.pdf
```

Dynamic custom-field flags are derived directly from configured keys. For
example, `pdf_question` becomes `--pdf-question`. There are no extra aliases.

`uv run pretalx-client submissions create --help` shows the currently configured
dynamic flags from the active profile in the command description.

Check what would be used with:

```bash
uv run pretalx-client config show
```

A ready-to-edit template is available in `config.toml.example`.

Create a local config from it with:

```bash
uv run pretalx-client config init
```

Use `--force` to overwrite an existing `config.toml`.

For organiser-scoped commands such as `users list`, `users create`, `users batch-create`,
`teams list`, and `teams add-member`, set `organiser` in the active profile (or pass
`--organiser` / set `PRETALX_ORGANISER`).

## Usage

```bash
uv run pretalx-client --help
```

### Look up reference data

Submission types, tracks, and tags are needed to create a proposal. You can pass
either their numeric ID or their name to any command that accepts them.

```bash
uv run pretalx-client submission-types list --event myevent
uv run pretalx-client tracks list --event myevent
uv run pretalx-client tags list --event myevent
```

### Create a proposal with custom field answers

```bash
uv run pretalx-client submissions create \
  --event myevent \
  --title "My Great Talk" \
  --submission-type Talk \
  --track "Main Track" \
  --abstract "A short abstract." \
  --description "A longer description." \
  --tag "backend" --tag "python" \
  --speaker-email alice@example.org,bob@example.org \
  --speaker-name "Alice Example,Bob Example" \
  --figshare-id 12345 \
  --self-assessment 0.5 \
  --pdf-question paper.pdf
```

This creates the submission and then posts answers to configured custom fields,
including file uploads for fields such as `pdf_question`.

`--speaker-email` supports comma-separated values to add multiple speakers in
one command. `--speaker-name` is optional; if provided with multiple speakers,
it must be comma-separated with the same number of entries and matching order.

### Bulk import/export CSV

Use `csvexport` to export submissions to CSV with accepted columns:

```bash
uv run pretalx-client submissions csvexport --output submissions_template.csv
```

Use `--template` if you only want a blank header row:

```bash
uv run pretalx-client submissions csvexport --template --output submissions_template.csv
```

Then fill rows and import with:

```bash
uv run pretalx-client submissions csvimport --file submissions_template.csv
```

By default, imports run in `upsert` mode: if a submission with the same title
already exists, it is updated instead of creating a duplicate. To always create
new submissions, use `insert` mode:

```bash
uv run pretalx-client submissions csvimport --file submissions_template.csv --mode insert
```

CSV headers mirror `submissions create` flags, for example:
`title`, `submission_type`, `content_locale`, `speaker_email`, `speaker_name`,
`figshare_id`, `self_assessment`, `pdf_question`.

For multiple speakers in CSV, use comma-separated values in `speaker_email`
and optionally `speaker_name` (same order and count). Because commas are used
inside a field value, ensure those cells are properly CSV-quoted.

### Manage submissions individually

```bash
uv run pretalx-client submissions create --event myevent --title "..." --submission-type Talk
uv run pretalx-client submissions resources add <code> --event myevent --file paper.pdf
uv run pretalx-client submissions show <code> --event myevent
uv run pretalx-client submissions accept <code> --event myevent
uv run pretalx-client submissions delete <code1> <code2> --event myevent
uv run pretalx-client submissions delete --all --event myevent
```

### Output format

Add `--format json` to any command for raw JSON output instead of a table (useful
for scripting):

```bash
uv run pretalx-client submissions list --event myevent --format json
```

## Notes on file uploads

pretalx's `/api/upload/` endpoint expects the raw file bytes as the request body,
with `Content-Type` and `Content-Disposition: attachment; filename="..."` headers —
not a `multipart/form-data` upload. This client implements that directly; you don't
need to think about it when using file-based options like
`submissions resources add --file`, `speakers update --avatar`, `submissions create --image`,
or dynamic file custom-field flags (e.g. `--pdf-question`).

## Scope

This client focuses on the core proposal-submission workflow: events, submissions
(including state transitions, speakers, and resources), speakers, submission-types,
tracks, tags, and access-codes (the latter four as read-only lookups). It does not
cover teams, schedules, rooms, reviews, mail templates, speaker-information, feedback,
or custom questions/answers as dedicated commands — for those, use `--extra <file.json>`
on `submissions create`/`update` to merge arbitrary extra fields into
the request body.
