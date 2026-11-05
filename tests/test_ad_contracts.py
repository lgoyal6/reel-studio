import subprocess
import time

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from reelstudio.app import create_app
from reelstudio.models import EditRequest


AD = {"type": "cinematic_ad", "headline": "Keep the moment", "brand": "Northstar", "cta": "Explore now"}


@pytest.mark.parametrize("changes", [
    {"headline": "   "}, {"brand": ""}, {"duration": 600},
    {"aspect": "script"}, {"output_path": "/tmp/forbidden"},
])
def test_ad_operation_rejects_unbounded_or_unsafe_fields(changes):
    with pytest.raises(ValidationError):
        EditRequest(operations=[{**AD, **changes}])


def test_renderer_failure_keeps_previous_revision_and_export(tmp_path, monkeypatch):
    from reelstudio import ads

    source = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=160x240:r=25:d=1",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:v", "libx264", "-c:a", "aac", str(source),
    ], check=True)

    def unavailable(*args, **kwargs):
        raise RuntimeError("Local cinematic ad renderer is unavailable. Run scripts/setup_ads.sh.")

    monkeypatch.setattr(ads, "render_ad", unavailable)

    def wait(client, job):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            state = client.get("/api/jobs/" + job["id"]).json()
            if state["status"] in ("complete", "failed"):
                return state
            time.sleep(.03)
        raise AssertionError("Job did not finish")

    with TestClient(create_app(tmp_path / "state")) as client:
        response = client.post("/api/projects", files={"file": ("source.mp4", source.read_bytes(), "video/mp4")})
        imported = wait(client, response.json())
        assert imported["status"] == "complete"
        pid = imported["project_id"]
        original = client.get("/api/projects/" + pid).json()
        before = client.get(f"/api/projects/{pid}/export?format=mp4").content
        job = client.post(f"/api/projects/{pid}/edits", json={"operations": [AD]}).json()
        result = wait(client, job)
        assert result["status"] == "failed" and "setup_ads" in result["error"]
        after = client.get("/api/projects/" + pid).json()
        assert after["current_revision"] == original["current_revision"]
        assert len(after["revisions"]) == 1
        assert client.get(f"/api/projects/{pid}/export?format=mp4").content == before
