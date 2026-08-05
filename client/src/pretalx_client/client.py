"""HTTP client wrapping the pretalx REST API.

File uploads are a two-step process (see pretalx's
``pretalx/api/views/upload.py``, which uses DRF's ``FileUploadParser``):
the raw file bytes are POSTed to ``/api/upload/`` with ``Content-Type`` and
``Content-Disposition`` headers (NOT multipart/form-data), returning a
``file:<id>`` reference. That reference is then used as the value of a
file field (e.g. ``resource``, ``image``, ``avatar``) in a later request.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any, Optional

import httpx

from pretalx_client.config import Config
from pretalx_client.exceptions import PretalxAPIError


class PretalxClient:
    def __init__(self, config: Config):
        self.config = config
        headers = {"Authorization": f"Token {config.token}"}
        if config.api_version:
            headers["Pretalx-Version"] = config.api_version
        self._http = httpx.Client(
            base_url=f"{config.url}/api/",
            headers=headers,
            verify=config.verify_ssl,
            timeout=30.0,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "PretalxClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- low level ---------------------------------------------------------

    @staticmethod
    def _raise_for_error(response: httpx.Response) -> None:
        if response.status_code >= 400:
            try:
                payload = response.json()
            except ValueError:
                payload = response.text
            raise PretalxAPIError(response.status_code, payload)

    def get(self, path: str, params: Optional[dict[str, Any]] = None) -> Any:
        response = self._http.get(path, params=params)
        self._raise_for_error(response)
        return response.json()

    def paginated(
        self,
        path: str,
        params: Optional[dict[str, Any]] = None,
        all_pages: bool = False,
    ) -> list[dict]:
        data = self.get(path, params=params)
        if isinstance(data, list):
            return data

        if not isinstance(data, dict):
            raise TypeError(
                f"Expected list or dict response for list endpoint {path!r}, got {type(data).__name__}"
            )

        results = list(data.get("results", []))
        if all_pages:
            next_url = data.get("next")
            while next_url:
                response = self._http.get(next_url)
                self._raise_for_error(response)
                data = response.json()
                if isinstance(data, list):
                    results.extend(data)
                    break
                if not isinstance(data, dict):
                    raise TypeError(
                        "Expected list or dict response while following pagination, "
                        f"got {type(data).__name__}"
                    )
                results.extend(data.get("results", []))
                next_url = data.get("next")
        return results

    def post(self, path: str, json: Optional[dict] = None) -> dict:
        response = self._http.post(path, json=json or {})
        self._raise_for_error(response)
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()

    def patch(self, path: str, json: dict) -> dict:
        response = self._http.patch(path, json=json)
        self._raise_for_error(response)
        return response.json()

    def delete(self, path: str) -> None:
        response = self._http.delete(path)
        self._raise_for_error(response)

    # -- file upload ---------------------------------------------------------

    def upload_file(self, path: Path) -> str:
        """Upload a file for temporary storage, returning its ``file:<id>`` reference."""
        path = Path(path)
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        response = self._http.post(
            "upload/",
            content=path.read_bytes(),
            headers={
                "Content-Type": content_type,
                "Content-Disposition": f'attachment; filename="{path.name}"',
            },
        )
        self._raise_for_error(response)
        return response.json()["id"]

    # -- events ---------------------------------------------------------

    def list_events(self, all_pages: bool = False) -> list[dict]:
        return self.paginated("events/", all_pages=all_pages)

    def get_event(self, event: str) -> dict:
        return self.get(f"events/{event}/")

    # -- event-scoped lookups ---------------------------------------------------------

    def list_submission_types(self, event: str, all_pages: bool = False) -> list[dict]:
        return self.paginated(f"events/{event}/submission-types/", all_pages=all_pages)

    def get_submission_type(self, event: str, type_id: Any) -> dict:
        return self.get(f"events/{event}/submission-types/{type_id}/")

    def list_tracks(self, event: str, all_pages: bool = False) -> list[dict]:
        return self.paginated(f"events/{event}/tracks/", all_pages=all_pages)

    def get_track(self, event: str, track_id: Any) -> dict:
        return self.get(f"events/{event}/tracks/{track_id}/")

    def list_tags(self, event: str, all_pages: bool = False) -> list[dict]:
        return self.paginated(f"events/{event}/tags/", all_pages=all_pages)

    def get_tag(self, event: str, tag_id: Any) -> dict:
        return self.get(f"events/{event}/tags/{tag_id}/")

    def list_access_codes(self, event: str, all_pages: bool = False) -> list[dict]:
        return self.paginated(f"events/{event}/access-codes/", all_pages=all_pages)

    def get_access_code(self, event: str, code_id: Any) -> dict:
        return self.get(f"events/{event}/access-codes/{code_id}/")

    # -- speakers ---------------------------------------------------------

    def list_speakers(self, event: str, all_pages: bool = False) -> list[dict]:
        return self.paginated(f"events/{event}/speakers/", all_pages=all_pages)

    def get_speaker(self, event: str, code: str) -> dict:
        return self.get(f"events/{event}/speakers/{code}/")

    def update_speaker(self, event: str, code: str, data: dict) -> dict:
        return self.patch(f"events/{event}/speakers/{code}/", data)

    # -- submissions ---------------------------------------------------------

    def list_submissions(
        self,
        event: str,
        params: Optional[dict[str, Any]] = None,
        all_pages: bool = False,
    ) -> list[dict]:
        return self.paginated(
            f"events/{event}/submissions/", params=params, all_pages=all_pages
        )

    def get_submission(self, event: str, code: str, expand: Optional[str] = None) -> dict:
        params = {"expand": expand} if expand else None
        return self.get(f"events/{event}/submissions/{code}/", params=params)

    def create_submission(self, event: str, data: dict) -> dict:
        return self.post(f"events/{event}/submissions/", json=data)

    def update_submission(self, event: str, code: str, data: dict) -> dict:
        return self.patch(f"events/{event}/submissions/{code}/", data)

    def _submission_action(self, event: str, code: str, action: str) -> dict:
        return self.post(f"events/{event}/submissions/{code}/{action}/")

    def accept_submission(self, event: str, code: str) -> dict:
        return self._submission_action(event, code, "accept")

    def reject_submission(self, event: str, code: str) -> dict:
        return self._submission_action(event, code, "reject")

    def confirm_submission(self, event: str, code: str) -> dict:
        return self._submission_action(event, code, "confirm")

    def cancel_submission(self, event: str, code: str) -> dict:
        return self._submission_action(event, code, "cancel")

    def make_submitted(self, event: str, code: str) -> dict:
        return self._submission_action(event, code, "make-submitted")

    def add_speaker(
        self,
        event: str,
        code: str,
        email: str,
        name: Optional[str] = None,
        locale: Optional[str] = None,
    ) -> dict:
        data: dict[str, Any] = {"email": email}
        if name:
            data["name"] = name
        if locale:
            data["locale"] = locale
        return self.post(f"events/{event}/submissions/{code}/add-speaker/", json=data)

    def remove_speaker(self, event: str, code: str, speaker_code: str) -> dict:
        return self.post(
            f"events/{event}/submissions/{code}/remove-speaker/",
            json={"user": speaker_code},
        )

    def add_resource(
        self,
        event: str,
        code: str,
        resource: Optional[str] = None,
        link: Optional[str] = None,
        description: Optional[str] = None,
        is_public: bool = True,
    ) -> dict:
        data: dict[str, Any] = {"is_public": is_public}
        if description is not None:
            data["description"] = description
        if resource:
            data["resource"] = resource
        if link:
            data["link"] = link
        return self.post(f"events/{event}/submissions/{code}/resources/", json=data)

    def remove_resource(self, event: str, code: str, resource_id: Any) -> None:
        self.delete(f"events/{event}/submissions/{code}/resources/{resource_id}/")
