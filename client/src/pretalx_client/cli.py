"""Command-line interface for the pretalx API client."""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any, List, Optional

import typer

from pretalx_client.client import PretalxClient
from pretalx_client.config import Config, load_config
from pretalx_client.exceptions import ConfigError, PretalxAPIError
from pretalx_client.formatting import print_result

DEFAULT_CONFIG_TEMPLATE = """# Copy this file to config.toml and fill in your values.
# The client will automatically use ./config.toml if the default
# ~/.config/pretalx-client/config.toml does not exist.

[profiles.default]
url = \"https://pretalx.example.org\"
token = \"your-api-token\"
event = \"myevent\"
submission_type = \"1\"
content_locale = \"en_gb\"

[profiles.default.custom_fields]
# Question id or identifier for the submission custom file field.
pdf_question = 123
figshare_id = 124
self_assessment = 125

[profiles.ref11]
url = \"https://ref11dev.zrok.lcas.group\"
token = \"your-api-token\"
event = \"ref11\"
submission_type = \"1\"
content_locale = \"en_gb\"

[profiles.ref11.custom_fields]
pdf_question = 123
figshare_id = 124
self_assessment = 125
"""

app = typer.Typer(
    name="pretalx-client",
    help="Command-line client for the pretalx conference management API.",
    no_args_is_help=True,
)

config_app = typer.Typer(help="Inspect the resolved configuration.")
events_app = typer.Typer(help="Browse pretalx events.")
submission_types_app = typer.Typer(help="Look up submission types.")
tracks_app = typer.Typer(help="Look up tracks.")
tags_app = typer.Typer(help="Look up tags.")
access_codes_app = typer.Typer(help="Look up access codes.")
speakers_app = typer.Typer(help="Manage speakers.")
submissions_app = typer.Typer(help="Manage submissions (proposals).")
resources_app = typer.Typer(help="Manage a submission's attached resources (files/links).")

submissions_app.add_typer(resources_app, name="resources")
app.add_typer(config_app, name="config")
app.add_typer(events_app, name="events")
app.add_typer(submission_types_app, name="submission-types")
app.add_typer(tracks_app, name="tracks")
app.add_typer(tags_app, name="tags")
app.add_typer(access_codes_app, name="access-codes")
app.add_typer(speakers_app, name="speakers")
app.add_typer(submissions_app, name="submissions")


class State:
    """Per-invocation state, built from the resolved config. The API client is
    created lazily so that commands like ``config show`` work without full
    credentials."""

    def __init__(self, config: Config, fmt: str):
        self.config = config
        self.format = fmt
        self._client: Optional[PretalxClient] = None

    @property
    def client(self) -> PretalxClient:
        if self._client is None:
            self.config.require()
            self._client = PretalxClient(self.config)
        return self._client

    def require_event(self) -> str:
        if not self.config.event:
            raise typer.BadParameter(
                "No event slug configured. Use --event/-e or set PRETALX_EVENT."
            )
        return self.config.event


def handle_errors(func):
    """Turn configuration/API errors into a clean message + exit code 1."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except ConfigError as exc:
            typer.secho(f"Configuration error: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        except PretalxAPIError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)

    return wrapper


_LOOKUP_CONFIG = {
    "submission_type": ("list_submission_types", "name"),
    "track": ("list_tracks", "name"),
    "tag": ("list_tags", "tag"),
}

_PDF_QUESTION_KEYS = (
    "pdf_question",
    "pdf_question_id",
    "output_pdf_question",
    "output_pdf",
)


def _label(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("en") or next(iter(value.values()), ""))
    return str(value or "")


def _resolve_reference(client: PretalxClient, event: str, kind: str, value: str) -> int:
    """Resolve a submission-type/track/tag reference from a bare ID or a matching name."""
    if value.isdigit():
        return int(value)
    list_method_name, name_field = _LOOKUP_CONFIG[kind]
    items = getattr(client, list_method_name)(event, all_pages=True)
    matches = [item for item in items if _label(item.get(name_field)).lower() == value.lower()]
    if not matches:
        raise typer.BadParameter(f"No {kind.replace('_', ' ')} found matching {value!r}")
    if len(matches) > 1:
        raise typer.BadParameter(
            f"Multiple {kind.replace('_', ' ')}s match {value!r}; use its numeric ID instead"
        )
    return matches[0]["id"]


def _resolve_question_reference(client: PretalxClient, event: str, value: str | int) -> int:
    """Resolve a custom field question reference from id or identifier."""
    if isinstance(value, int):
        return value
    if value.isdigit():
        return int(value)

    questions = client.list_questions(event, all_pages=True)
    identifier_matches = [
        question for question in questions if str(question.get("identifier") or "").lower() == value.lower()
    ]
    if len(identifier_matches) == 1:
        return int(identifier_matches[0]["id"])
    if len(identifier_matches) > 1:
        raise typer.BadParameter(
            f"Multiple custom fields match identifier {value!r}; use numeric id instead"
        )

    label_matches = [question for question in questions if _label(question.get("question")).lower() == value.lower()]
    if len(label_matches) == 1:
        return int(label_matches[0]["id"])
    if len(label_matches) > 1:
        raise typer.BadParameter(
            f"Multiple custom fields match label {value!r}; use question identifier or numeric id"
        )

    raise typer.BadParameter(
        f"No custom field question found matching {value!r}. "
        "Set profiles.<name>.custom_fields.pdf_question to a valid question id or identifier."
    )


def _normalise_locale(value: str) -> str:
    return value.strip().replace("_", "-").lower()


def _resolve_content_locale(client: PretalxClient, event: str, value: str) -> str:
    """Resolve a content locale against the event's configured locales.

    Accepts exact matches (case-insensitive, '_' or '-' separators) and short
    language prefixes like ``en`` when they map to exactly one configured
    locale (e.g. ``en-gb``).
    """
    normalised_input = _normalise_locale(value)
    event_data = client.get_event(event)
    available_locales = event_data.get("content_locales") or event_data.get("locales") or []

    if not available_locales:
        return normalised_input

    normalised_available = [
        (_normalise_locale(locale), str(locale)) for locale in available_locales
    ]

    # Exact match against any configured locale.
    for normalised_locale, raw_locale in normalised_available:
        if normalised_input == normalised_locale:
            return raw_locale

    # Short language alias: e.g. 'en' -> 'en-gb' if unique.
    prefix_matches = [
        raw_locale
        for normalised_locale, raw_locale in normalised_available
        if normalised_locale.startswith(f"{normalised_input}-")
    ]
    if len(prefix_matches) == 1:
        return prefix_matches[0]
    if len(prefix_matches) > 1:
        raise typer.BadParameter(
            "Ambiguous content locale "
            f"{value!r}. Matching event locales: {', '.join(prefix_matches)}"
        )

    raise typer.BadParameter(
        "Invalid content locale "
        f"{value!r}. Valid choices for event '{event}': {', '.join(map(str, available_locales))}"
    )


def _effective_content_locale(state: State, value: Optional[str]) -> str:
    """Resolve content locale input, falling back to configured/global default."""
    return value or state.config.content_locale


def _effective_submission_type(state: State, value: Optional[str]) -> str:
    """Resolve submission type input, falling back to configured/global default."""
    return value or state.config.submission_type


def _configured_pdf_question(config: Config) -> Optional[str | int]:
    for key in _PDF_QUESTION_KEYS:
        value = config.custom_fields.get(key)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _configured_custom_fields(config: Config) -> dict[str, str | int]:
    configured: dict[str, str | int] = {}
    for key, value in config.custom_fields.items():
        if not isinstance(key, str) or not key.strip():
            continue
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        configured[key.strip()] = value
    return configured


def _normalise_cli_field_name(name: str) -> str:
    return name.strip().lstrip("-").replace("-", "_")


def _parse_dynamic_custom_field_args(
    extra_args: list[str],
    configured_fields: dict[str, str | int],
) -> dict[str, str]:
    """Parse unknown submit-proposal args into configured custom field values.

    Accepts both --field-name value and --field_name=value styles, where the
    field name is derived from profiles.<name>.custom_fields keys.
    """
    if not extra_args:
        return {}

    allowed_names = {_normalise_cli_field_name(name): name for name in configured_fields}
    parsed: dict[str, str] = {}
    unknown: list[str] = []

    i = 0
    while i < len(extra_args):
        token = extra_args[i]
        if not token.startswith("--"):
            unknown.append(token)
            i += 1
            continue

        option = token[2:]
        if "=" in option:
            raw_name, raw_value = option.split("=", 1)
            i += 1
        else:
            raw_name = option
            if i + 1 >= len(extra_args):
                raise typer.BadParameter(f"Missing value for dynamic option '{token}'.")
            raw_value = extra_args[i + 1]
            i += 2

        normalised = _normalise_cli_field_name(raw_name)
        config_key = allowed_names.get(normalised)
        if not config_key:
            unknown.append(f"--{raw_name}")
            continue
        parsed[config_key] = raw_value

    if unknown:
        available = ", ".join(f"--{name.replace('_', '-')}" for name in configured_fields)
        raise typer.BadParameter(
            "Unknown dynamic option(s): "
            + ", ".join(unknown)
            + (f". Available configured custom fields: {available}" if available else "")
        )

    return parsed


def _resolve_choice_option_ids(question: dict, raw_value: str, multiple: bool) -> list[int]:
    options = question.get("options") or []
    if not isinstance(options, list) or not options:
        raise typer.BadParameter(
            f"Question {question.get('id')} does not expose selectable options."
        )

    wanted_values = [raw_value] if not multiple else [part.strip() for part in raw_value.split(",") if part.strip()]
    if multiple and not wanted_values:
        raise typer.BadParameter("Provide at least one option for multiple-choice custom fields.")

    resolved: list[int] = []
    for wanted in wanted_values:
        match = None
        wanted_lower = wanted.lower()
        for option in options:
            option_id = str(option.get("id", ""))
            option_identifier = str(option.get("identifier") or "").lower()
            option_label = _label(option.get("answer")).lower()
            if wanted == option_id or wanted_lower == option_identifier or wanted_lower == option_label:
                match = option
                break
        if not match:
            rendered_options = ", ".join(
                f"{opt.get('id')}:{opt.get('identifier') or _label(opt.get('answer'))}" for opt in options
            )
            raise typer.BadParameter(
                f"Invalid option {wanted!r} for question {question.get('id')}. "
                f"Valid options: {rendered_options}"
            )
        resolved.append(int(match["id"]))

    return resolved


def _create_custom_field_answer(
    state: State,
    event: str,
    submission_code: str,
    field_name: str,
    question_reference: str | int,
    raw_value: str,
) -> dict:
    question_id = _resolve_question_reference(state.client, event, question_reference)
    question = state.client.get_question(event, question_id, expand_options=True)
    variant = str(question.get("variant") or "").lower()

    if variant == "file":
        file_path = Path(raw_value).expanduser()
        if not file_path.is_file():
            raise typer.BadParameter(
                f"Custom field '{field_name}' expects a file path, but '{raw_value}' does not exist."
            )
        file_ref = state.client.upload_file(file_path)
        return state.client.create_answer(
            event=event,
            question=question_id,
            submission=submission_code,
            answer=_effective_resource_description(None, file=file_path),
            answer_file=file_ref,
        )

    if variant == "choices":
        option_ids = _resolve_choice_option_ids(question, raw_value, multiple=False)
        return state.client.create_answer(
            event=event,
            question=question_id,
            submission=submission_code,
            answer=raw_value,
            options=option_ids,
        )

    if variant == "multiple_choice":
        option_ids = _resolve_choice_option_ids(question, raw_value, multiple=True)
        return state.client.create_answer(
            event=event,
            question=question_id,
            submission=submission_code,
            answer=raw_value,
            options=option_ids,
        )

    return state.client.create_answer(
        event=event,
        question=question_id,
        submission=submission_code,
        answer=raw_value,
    )


def _effective_resource_description(
    description: Optional[str],
    file: Optional[Path] = None,
    link: Optional[str] = None,
) -> str:
    """Ensure resource descriptions are never blank for stricter API configs."""
    if description is not None and description.strip():
        return description.strip()
    if file is not None:
        return file.name
    if link:
        return link
    return "Attachment"


def _submit_proposal_help_text() -> str:
    """Build command help text including currently configured dynamic flags."""
    base = "Create a proposal and attach files/custom field answers in one step."
    try:
        config = load_config()
        configured_fields = _configured_custom_fields(config)
    except Exception:
        configured_fields = {}

    if not configured_fields:
        return base

    dynamic_flags = ", ".join(
        f"--{name.replace('_', '-')}" for name in configured_fields
    )
    return (
        base
        + "\n\nConfigured dynamic custom-field flags from the active profile: "
        + dynamic_flags
    )


def _build_submission_payload(
    event: str,
    client: PretalxClient,
    title: Optional[str] = None,
    abstract: Optional[str] = None,
    description: Optional[str] = None,
    submission_type: Optional[str] = None,
    track: Optional[str] = None,
    tags: Optional[List[str]] = None,
    duration: Optional[int] = None,
    content_locale: Optional[str] = None,
    slot_count: Optional[int] = None,
    do_not_record: Optional[bool] = None,
    notes: Optional[str] = None,
    internal_notes: Optional[str] = None,
    extra: Optional[Path] = None,
) -> dict:
    data: dict = {}
    if title is not None:
        data["title"] = title
    if abstract is not None:
        data["abstract"] = abstract
    if description is not None:
        data["description"] = description
    if submission_type is not None:
        data["submission_type"] = _resolve_reference(
            client, event, "submission_type", submission_type
        )
    if track is not None:
        data["track"] = _resolve_reference(client, event, "track", track)
    if tags is not None:
        data["tags"] = [_resolve_reference(client, event, "tag", tag) for tag in tags]
    if duration is not None:
        data["duration"] = duration
    if content_locale is not None:
        data["content_locale"] = _resolve_content_locale(client, event, content_locale)
    if slot_count is not None:
        data["slot_count"] = slot_count
    if do_not_record is not None:
        data["do_not_record"] = do_not_record
    if notes is not None:
        data["notes"] = notes
    if internal_notes is not None:
        data["internal_notes"] = internal_notes
    if extra is not None:
        data.update(json.loads(Path(extra).read_text()))
    return data


@app.callback()
def main(
    ctx: typer.Context,
    url: Optional[str] = typer.Option(None, "--url", "-u", help="Base URL of the pretalx instance"),
    token: Optional[str] = typer.Option(None, "--token", "-t", help="API token"),
    event: Optional[str] = typer.Option(None, "--event", "-e", help="Event slug"),
    profile: Optional[str] = typer.Option(
        None, "--profile", "-p", help="Config profile name (default: 'default')"
    ),
    config_file: Optional[Path] = typer.Option(
        None, "--config-file", help="Path to a TOML config file"
    ),
    api_version: Optional[str] = typer.Option(
        None, "--api-version", help="Value for the Pretalx-Version header"
    ),
    format_: str = typer.Option("table", "--format", "-f", help="Output format: table or json"),
    insecure: bool = typer.Option(False, "--insecure", help="Disable TLS certificate verification"),
):
    """Shared options for all commands. Precedence: CLI flag > env var > config file."""
    if format_ not in ("table", "json"):
        raise typer.BadParameter("Format must be 'table' or 'json'.")
    config = load_config(
        url=url,
        token=token,
        event=event,
        profile=profile,
        config_file=config_file,
        api_version=api_version,
        verify_ssl=not insecure,
    )
    ctx.obj = State(config=config, fmt=format_)


# -- config ---------------------------------------------------------


@config_app.command("show")
def config_show(ctx: typer.Context):
    """Show the resolved configuration (token redacted)."""
    state: State = ctx.obj
    config = state.config
    redacted_token = f"{config.token[:4]}…" if config.token else None
    print_result(
        {
            "url": config.url,
            "token": redacted_token,
            "event": config.event,
            "api_version": config.api_version,
            "submission_type": config.submission_type,
            "content_locale": config.content_locale,
            "custom_fields": config.custom_fields,
            "profile": config.profile,
            "config_file": str(config.config_file),
        },
        state.format,
    )


@config_app.command("path")
def config_path(ctx: typer.Context):
    """Print the path of the config file that would be used."""
    state: State = ctx.obj
    typer.echo(str(state.config.config_file))


@config_app.command("init")
def config_init(
    output: Path = typer.Option(
        Path("config.toml"), "--output", "-o", help="Path to write the generated config file"
    ),
    example_file: Path = typer.Option(
        Path("config.toml.example"), "--example-file", help="Template file to copy from"
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite output file if it already exists"),
):
    """Create a local config.toml from the example template."""
    if output.exists() and not force:
        typer.secho(
            f"Refusing to overwrite existing file: {output}. Use --force to overwrite.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    if example_file.is_file():
        content = example_file.read_text(encoding="utf-8")
    else:
        content = DEFAULT_CONFIG_TEMPLATE
        typer.secho(
            f"Template file not found ({example_file}); using built-in template.",
            fg=typer.colors.YELLOW,
            err=True,
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    typer.secho(f"Wrote config template to {output}", fg=typer.colors.GREEN)


# -- events ---------------------------------------------------------


@events_app.command("list")
@handle_errors
def events_list(ctx: typer.Context, all: bool = typer.Option(False, "--all", help="Fetch all pages")):
    """List events visible to the current token."""
    state: State = ctx.obj
    events = state.client.list_events(all_pages=all)
    print_result(events, state.format, columns=["slug", "name", "date_from", "date_to", "is_public"])


@events_app.command("show")
@handle_errors
def events_show(ctx: typer.Context, slug: str):
    """Show a single event."""
    state: State = ctx.obj
    print_result(state.client.get_event(slug), state.format)


# -- submission-types / tracks / tags / access-codes (lookup helpers) ------------------


@submission_types_app.command("list")
@handle_errors
def submission_types_list(ctx: typer.Context, all: bool = typer.Option(False, "--all")):
    """List submission types for the current event."""
    state: State = ctx.obj
    event = state.require_event()
    print_result(state.client.list_submission_types(event, all_pages=all), state.format)


@submission_types_app.command("show")
@handle_errors
def submission_types_show(ctx: typer.Context, type_id: int):
    """Show a single submission type."""
    state: State = ctx.obj
    event = state.require_event()
    print_result(state.client.get_submission_type(event, type_id), state.format)


@tracks_app.command("list")
@handle_errors
def tracks_list(ctx: typer.Context, all: bool = typer.Option(False, "--all")):
    """List tracks for the current event."""
    state: State = ctx.obj
    event = state.require_event()
    print_result(state.client.list_tracks(event, all_pages=all), state.format)


@tracks_app.command("show")
@handle_errors
def tracks_show(ctx: typer.Context, track_id: int):
    """Show a single track."""
    state: State = ctx.obj
    event = state.require_event()
    print_result(state.client.get_track(event, track_id), state.format)


@tags_app.command("list")
@handle_errors
def tags_list(ctx: typer.Context, all: bool = typer.Option(False, "--all")):
    """List tags for the current event."""
    state: State = ctx.obj
    event = state.require_event()
    print_result(state.client.list_tags(event, all_pages=all), state.format)


@tags_app.command("show")
@handle_errors
def tags_show(ctx: typer.Context, tag_id: int):
    """Show a single tag."""
    state: State = ctx.obj
    event = state.require_event()
    print_result(state.client.get_tag(event, tag_id), state.format)


@access_codes_app.command("list")
@handle_errors
def access_codes_list(ctx: typer.Context, all: bool = typer.Option(False, "--all")):
    """List submitter access codes for the current event."""
    state: State = ctx.obj
    event = state.require_event()
    print_result(state.client.list_access_codes(event, all_pages=all), state.format)


@access_codes_app.command("show")
@handle_errors
def access_codes_show(ctx: typer.Context, code_id: int):
    """Show a single access code."""
    state: State = ctx.obj
    event = state.require_event()
    print_result(state.client.get_access_code(event, code_id), state.format)


# -- speakers ---------------------------------------------------------


@speakers_app.command("list")
@handle_errors
def speakers_list(ctx: typer.Context, all: bool = typer.Option(False, "--all")):
    """List speakers for the current event."""
    state: State = ctx.obj
    event = state.require_event()
    print_result(state.client.list_speakers(event, all_pages=all), state.format)


@speakers_app.command("show")
@handle_errors
def speakers_show(ctx: typer.Context, code: str):
    """Show a single speaker."""
    state: State = ctx.obj
    event = state.require_event()
    print_result(state.client.get_speaker(event, code), state.format)


@speakers_app.command("update")
@handle_errors
def speakers_update(
    ctx: typer.Context,
    code: str,
    name: Optional[str] = typer.Option(None, help="Speaker's display name"),
    biography: Optional[str] = typer.Option(None, help="Speaker's biography"),
    avatar: Optional[Path] = typer.Option(None, exists=True, help="Path to an avatar image"),
):
    """Update a speaker's profile."""
    state: State = ctx.obj
    event = state.require_event()
    data: dict = {}
    if name is not None:
        data["name"] = name
    if biography is not None:
        data["biography"] = biography
    if avatar is not None:
        data["avatar"] = state.client.upload_file(avatar)
    if not data:
        typer.secho("Nothing to update.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)
    print_result(state.client.update_speaker(event, code, data), state.format)


# -- submissions ---------------------------------------------------------


@submissions_app.command("list")
@handle_errors
def submissions_list(
    ctx: typer.Context,
    state_filter: Optional[str] = typer.Option(None, "--state", help="Filter by submission state"),
    q: Optional[str] = typer.Option(None, "--q", help="Search query"),
    track: Optional[str] = typer.Option(None, help="Filter by track name or ID"),
    submission_type: Optional[str] = typer.Option(
        None, "--submission-type", help="Filter by submission type name or ID"
    ),
    all: bool = typer.Option(False, "--all", help="Fetch all pages"),
):
    """List submissions (proposals) for the current event."""
    state: State = ctx.obj
    event = state.require_event()
    params: dict = {}
    if state_filter:
        params["state"] = state_filter
    if q:
        params["q"] = q
    if track:
        params["track"] = _resolve_reference(state.client, event, "track", track)
    if submission_type:
        params["submission_type"] = _resolve_reference(
            state.client, event, "submission_type", submission_type
        )
    submissions = state.client.list_submissions(event, params=params, all_pages=all)
    print_result(submissions, state.format, columns=["code", "title", "state", "submission_type", "track"])


@submissions_app.command("show")
@handle_errors
def submissions_show(
    ctx: typer.Context,
    code: str,
    expand: Optional[str] = typer.Option(None, help="Comma-separated related fields to expand"),
):
    """Show a single submission."""
    state: State = ctx.obj
    event = state.require_event()
    print_result(state.client.get_submission(event, code, expand=expand), state.format)


@submissions_app.command("create")
@handle_errors
def submissions_create(
    ctx: typer.Context,
    title: str = typer.Option(..., help="Proposal title"),
    submission_type: Optional[str] = typer.Option(
        None,
        "--submission-type",
        help="Submission type name or ID (defaults to configured submission_type or 1)",
    ),
    abstract: Optional[str] = typer.Option(None, help="Short abstract"),
    description: Optional[str] = typer.Option(None, help="Full description"),
    track: Optional[str] = typer.Option(None, help="Track name or ID"),
    tag: Optional[List[str]] = typer.Option(None, "--tag", help="Tag name or ID (repeatable)"),
    duration: Optional[int] = typer.Option(None, help="Duration in minutes"),
    content_locale: Optional[str] = typer.Option(
        None, "--content-locale", help="Proposal locale (defaults to configured content_locale or en_gb)"
    ),
    slot_count: Optional[int] = typer.Option(None, "--slot-count"),
    do_not_record: Optional[bool] = typer.Option(None, "--do-not-record/--record"),
    notes: Optional[str] = typer.Option(None, help="Notes to the organizers"),
    internal_notes: Optional[str] = typer.Option(None, "--internal-notes", help="Organizer-only notes"),
    extra: Optional[Path] = typer.Option(
        None, exists=True, help="JSON file merged into the request body"
    ),
):
    """Create a new submission (proposal)."""
    state: State = ctx.obj
    event = state.require_event()
    resolved_content_locale = _effective_content_locale(state, content_locale)
    resolved_submission_type = _effective_submission_type(state, submission_type)
    data = _build_submission_payload(
        event,
        state.client,
        title=title,
        abstract=abstract,
        description=description,
        submission_type=resolved_submission_type,
        track=track,
        tags=tag,
        duration=duration,
        content_locale=resolved_content_locale,
        slot_count=slot_count,
        do_not_record=do_not_record,
        notes=notes,
        internal_notes=internal_notes,
        extra=extra,
    )
    print_result(state.client.create_submission(event, data), state.format)


@submissions_app.command("update")
@handle_errors
def submissions_update(
    ctx: typer.Context,
    code: str,
    title: Optional[str] = typer.Option(None),
    submission_type: Optional[str] = typer.Option(None, "--submission-type"),
    abstract: Optional[str] = typer.Option(None),
    description: Optional[str] = typer.Option(None),
    track: Optional[str] = typer.Option(None),
    tag: Optional[List[str]] = typer.Option(None, "--tag", help="Tag name or ID (repeatable)"),
    duration: Optional[int] = typer.Option(None),
    content_locale: Optional[str] = typer.Option(None, "--content-locale"),
    slot_count: Optional[int] = typer.Option(None, "--slot-count"),
    do_not_record: Optional[bool] = typer.Option(None, "--do-not-record/--record"),
    notes: Optional[str] = typer.Option(None),
    internal_notes: Optional[str] = typer.Option(None, "--internal-notes"),
    extra: Optional[Path] = typer.Option(None, exists=True),
):
    """Update fields of an existing submission (only the given options are sent)."""
    state: State = ctx.obj
    event = state.require_event()
    data = _build_submission_payload(
        event,
        state.client,
        title=title,
        abstract=abstract,
        description=description,
        submission_type=submission_type,
        track=track,
        tags=tag,
        duration=duration,
        content_locale=content_locale,
        slot_count=slot_count,
        do_not_record=do_not_record,
        notes=notes,
        internal_notes=internal_notes,
        extra=extra,
    )
    if not data:
        typer.secho("Nothing to update.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)
    print_result(state.client.update_submission(event, code, data), state.format)


@submissions_app.command("accept")
@handle_errors
def submissions_accept(ctx: typer.Context, code: str):
    """Accept a submission."""
    state: State = ctx.obj
    event = state.require_event()
    print_result(state.client.accept_submission(event, code), state.format)


@submissions_app.command("reject")
@handle_errors
def submissions_reject(ctx: typer.Context, code: str):
    """Reject a submission."""
    state: State = ctx.obj
    event = state.require_event()
    print_result(state.client.reject_submission(event, code), state.format)


@submissions_app.command("confirm")
@handle_errors
def submissions_confirm(ctx: typer.Context, code: str):
    """Confirm a submission."""
    state: State = ctx.obj
    event = state.require_event()
    print_result(state.client.confirm_submission(event, code), state.format)


@submissions_app.command("cancel")
@handle_errors
def submissions_cancel(ctx: typer.Context, code: str):
    """Cancel a submission."""
    state: State = ctx.obj
    event = state.require_event()
    print_result(state.client.cancel_submission(event, code), state.format)


@submissions_app.command("withdraw")
@handle_errors
def submissions_withdraw(ctx: typer.Context, code: str):
    """Withdraw a submission back to the 'submitted' state."""
    state: State = ctx.obj
    event = state.require_event()
    print_result(state.client.make_submitted(event, code), state.format)


@submissions_app.command("add-speaker")
@handle_errors
def submissions_add_speaker(
    ctx: typer.Context,
    code: str,
    email: str = typer.Option(..., help="Speaker's email address"),
    name: Optional[str] = typer.Option(None, help="Speaker's name, for new accounts"),
    locale: Optional[str] = typer.Option(None, help="Invitation locale"),
):
    """Invite/add a speaker to a submission."""
    state: State = ctx.obj
    event = state.require_event()
    result = state.client.add_speaker(event, code, email=email, name=name, locale=locale)
    print_result(result, state.format)


@submissions_app.command("remove-speaker")
@handle_errors
def submissions_remove_speaker(
    ctx: typer.Context,
    code: str,
    speaker_code: str = typer.Argument(..., help="Speaker code to remove"),
):
    """Remove a speaker from a submission."""
    state: State = ctx.obj
    event = state.require_event()
    print_result(state.client.remove_speaker(event, code, speaker_code), state.format)


# -- submissions resources (file/link attachments, e.g. PDFs) ------------------


@resources_app.command("add")
@handle_errors
def resources_add(
    ctx: typer.Context,
    code: str,
    file: Optional[Path] = typer.Option(
        None, exists=True, help="File to upload and attach (e.g. a PDF)"
    ),
    link: Optional[str] = typer.Option(None, help="External URL instead of an uploaded file"),
    description: Optional[str] = typer.Option(
        None,
        help="Description of the resource (defaults to filename/link)",
    ),
    public: bool = typer.Option(True, "--public/--private", help="Whether the resource is public"),
):
    """Attach a file (e.g. a proposal's PDF) or link as a resource on a submission."""
    state: State = ctx.obj
    event = state.require_event()
    if not file and not link:
        typer.secho("Provide either --file or --link.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    resource_ref = state.client.upload_file(file) if file else None
    effective_description = _effective_resource_description(description, file=file, link=link)
    result = state.client.add_resource(
        event,
        code,
        resource=resource_ref,
        link=link,
        description=effective_description,
        is_public=public,
    )
    print_result(result, state.format)


@resources_app.command("remove")
@handle_errors
def resources_remove(ctx: typer.Context, code: str, resource_id: int):
    """Remove a resource from a submission."""
    state: State = ctx.obj
    event = state.require_event()
    state.client.remove_resource(event, code, resource_id)
    typer.echo(f"Removed resource {resource_id} from {code}.")


# -- top-level convenience command ---------------------------------------------------------


@app.command(
    "submit-proposal",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    help=_submit_proposal_help_text(),
)
@handle_errors
def submit_proposal(
    ctx: typer.Context,
    title: str = typer.Option(..., help="Proposal title"),
    submission_type: Optional[str] = typer.Option(
        None,
        "--submission-type",
        help="Submission type name or ID (defaults to configured submission_type or 1)",
    ),
    abstract: Optional[str] = typer.Option(None, help="Short abstract"),
    description: Optional[str] = typer.Option(None, help="Full description"),
    track: Optional[str] = typer.Option(None, help="Track name or ID"),
    tag: Optional[List[str]] = typer.Option(None, "--tag", help="Tag name or ID (repeatable)"),
    duration: Optional[int] = typer.Option(None, help="Duration in minutes"),
    content_locale: Optional[str] = typer.Option(
        None, "--content-locale", help="Proposal locale (defaults to configured content_locale or en_gb)"
    ),
    slot_count: Optional[int] = typer.Option(None, "--slot-count"),
    do_not_record: Optional[bool] = typer.Option(None, "--do-not-record/--record"),
    notes: Optional[str] = typer.Option(None, help="Notes to the organizers"),
    internal_notes: Optional[str] = typer.Option(None, "--internal-notes"),
    extra: Optional[Path] = typer.Option(
        None, exists=True, help="JSON file merged into the submission body"
    ),
    pdf: Optional[Path] = typer.Option(
        None, exists=True, help="Paper/slides PDF (or other document) to attach as a resource"
    ),
    resource_description: Optional[str] = typer.Option(
        None,
        "--resource-description",
        help="Description for the attached PDF answer/resource (defaults to filename)",
    ),
    image: Optional[Path] = typer.Option(None, exists=True, help="Proposal card image to attach"),
):
    """Create a proposal and attach files/custom field answers in one step."""
    state: State = ctx.obj
    event = state.require_event()
    resolved_content_locale = _effective_content_locale(state, content_locale)
    resolved_submission_type = _effective_submission_type(state, submission_type)
    configured_custom_fields = _configured_custom_fields(state.config)
    custom_field_values = _parse_dynamic_custom_field_args(ctx.args, configured_custom_fields)

    pdf_question_reference = _configured_pdf_question(state.config)
    if pdf is not None:
        if pdf_question_reference is not None:
            pdf_field_key = next(
                (key for key in configured_custom_fields if _normalise_cli_field_name(key) in _PDF_QUESTION_KEYS),
                None,
            )
            if pdf_field_key and pdf_field_key not in custom_field_values:
                custom_field_values[pdf_field_key] = str(pdf.expanduser())
        else:
            # Backward-compatible fallback: if no configured question exists, keep
            # using submission resources for --pdf.
            pass

    data = _build_submission_payload(
        event,
        state.client,
        title=title,
        abstract=abstract,
        description=description,
        submission_type=resolved_submission_type,
        track=track,
        tags=tag,
        duration=duration,
        content_locale=resolved_content_locale,
        slot_count=slot_count,
        do_not_record=do_not_record,
        notes=notes,
        internal_notes=internal_notes,
        extra=extra,
    )
    submission = state.client.create_submission(event, data)
    code = submission["code"]
    created_answers: list[dict] = []

    if pdf is not None and pdf_question_reference is None:
        resource_ref = state.client.upload_file(pdf)
        effective_resource_description = _effective_resource_description(
            resource_description,
            file=pdf,
        )
        state.client.add_resource(
            event,
            code,
            resource=resource_ref,
            description=effective_resource_description,
            is_public=True,
        )

    for field_name, value in custom_field_values.items():
        question_reference = configured_custom_fields[field_name]
        created_answers.append(
            _create_custom_field_answer(
                state=state,
                event=event,
                submission_code=code,
                field_name=field_name,
                question_reference=question_reference,
                raw_value=value,
            )
        )

    if image is not None:
        image_ref = state.client.upload_file(image)
        submission = state.client.update_submission(event, code, {"image": image_ref})

    if created_answers:
        if state.format == "json":
            print_result({"submission": submission, "custom_answers": created_answers}, state.format)
        else:
            print_result(submission, state.format)
            typer.echo("Custom field answers:")
            print_result(
                created_answers,
                state.format,
                columns=["id", "question", "answer", "answer_file", "submission"],
            )
    else:
        print_result(submission, state.format)


if __name__ == "__main__":
    app()
