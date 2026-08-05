"""Command-line interface for the pretalx API client."""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import List, Optional

import typer

from pretalx_client.client import PretalxClient
from pretalx_client.config import Config, load_config
from pretalx_client.exceptions import ConfigError, PretalxAPIError
from pretalx_client.formatting import print_result

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
        data["content_locale"] = content_locale
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
    submission_type: str = typer.Option(..., "--submission-type", help="Submission type name or ID"),
    abstract: Optional[str] = typer.Option(None, help="Short abstract"),
    description: Optional[str] = typer.Option(None, help="Full description"),
    track: Optional[str] = typer.Option(None, help="Track name or ID"),
    tag: Optional[List[str]] = typer.Option(None, "--tag", help="Tag name or ID (repeatable)"),
    duration: Optional[int] = typer.Option(None, help="Duration in minutes"),
    content_locale: Optional[str] = typer.Option(None, "--content-locale"),
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
    description: str = typer.Option("", help="Description of the resource"),
    public: bool = typer.Option(True, "--public/--private", help="Whether the resource is public"),
):
    """Attach a file (e.g. a proposal's PDF) or link as a resource on a submission."""
    state: State = ctx.obj
    event = state.require_event()
    if not file and not link:
        typer.secho("Provide either --file or --link.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    resource_ref = state.client.upload_file(file) if file else None
    result = state.client.add_resource(
        event, code, resource=resource_ref, link=link, description=description, is_public=public
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


@app.command("submit-proposal")
@handle_errors
def submit_proposal(
    ctx: typer.Context,
    title: str = typer.Option(..., help="Proposal title"),
    submission_type: str = typer.Option(..., "--submission-type", help="Submission type name or ID"),
    abstract: Optional[str] = typer.Option(None, help="Short abstract"),
    description: Optional[str] = typer.Option(None, help="Full description"),
    track: Optional[str] = typer.Option(None, help="Track name or ID"),
    tag: Optional[List[str]] = typer.Option(None, "--tag", help="Tag name or ID (repeatable)"),
    duration: Optional[int] = typer.Option(None, help="Duration in minutes"),
    content_locale: Optional[str] = typer.Option(None, "--content-locale"),
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
    resource_description: str = typer.Option(
        "", "--resource-description", help="Description for the attached PDF/document"
    ),
    image: Optional[Path] = typer.Option(None, exists=True, help="Proposal card image to attach"),
):
    """Create a proposal and, in one step, attach its PDF and/or card image."""
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
    submission = state.client.create_submission(event, data)
    code = submission["code"]

    if pdf is not None:
        resource_ref = state.client.upload_file(pdf)
        submission = state.client.add_resource(
            event, code, resource=resource_ref, description=resource_description, is_public=True
        )
    if image is not None:
        image_ref = state.client.upload_file(image)
        submission = state.client.update_submission(event, code, {"image": image_ref})

    print_result(submission, state.format)


if __name__ == "__main__":
    app()
