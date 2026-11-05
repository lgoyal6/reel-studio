from pathlib import Path
import subprocess
import time

from fastapi.testclient import TestClient
import pytest

from reelstudio.app import create_app


def wait(client, job):
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        current = client.get(f"/api/jobs/{job['id']}").json()
        if current["status"] in ("complete", "failed"):
            return current
        time.sleep(.05)
    raise AssertionError("Real media processing did not finish")


@pytest.fixture
def clip(tmp_path):
    file = tmp_path / "input.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=160x240:r=30:d=2",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(file)], check=True)
    return file


def test_upload_edit_checkout_and_export_are_real(tmp_path, clip):
    app = create_app(tmp_path / "state")
    with TestClient(app) as client:
        with clip.open("rb") as file:
            response = client.post("/api/projects", files={"file": ("clip.mp4", file, "video/mp4")})
        assert response.status_code == 202
        job = wait(client, response.json())
        assert job["status"] == "complete", job
        id = job["project_id"]
        project = client.get(f"/api/projects/{id}").json()
        original = project["current_revision"]
        assert project["has_video"] and len(project["revisions"][0]["waveform"]) >= 32
        assert client.get(project["revisions"][0]["video_url"]).status_code == 200
        response = client.post(f"/api/projects/{id}/edits", json={"prompt": "Half speed", "operations": [{"type": "speed", "factor": .5, "start": 0, "end": 2}]})
        job = wait(client, response.json())
        assert job["status"] == "complete", job
        edited = client.get(f"/api/projects/{id}").json()
        assert len(edited["revisions"]) == 2 and abs(edited["duration"] - 4) < .08
        assert edited["revisions"][-1]["planner"] == "controls"
        assert edited["revisions"][-1]["parent_id"] == original
        export = client.get(f"/api/projects/{id}/export?format=mp4")
        assert export.status_code == 200 and export.headers["content-type"].startswith("video/mp4")
        assert len(export.content) > 1000
        wav = client.get(f"/api/projects/{id}/export?format=wav")
        assert wav.content[:4] == b"RIFF"
        restored = client.post(f"/api/projects/{id}/checkout", json={"revision_id": original}).json()
        assert restored["current_revision"] == original and restored["duration"] < 2.1
        assert len(restored["revisions"]) == 2
        # Failed render must not select or publish a partial revision.
        failed = client.post(f"/api/projects/{id}/edits", json={"operations": [{"type": "cut", "start": 50, "end": 60}]}).json()
        assert wait(client, failed)["status"] == "failed"
        unchanged = client.get(f"/api/projects/{id}").json()
        assert unchanged["current_revision"] == original and len(unchanged["revisions"]) == 2


def test_bad_uploads_and_unsafe_requests(tmp_path):
    with TestClient(create_app(tmp_path / "state")) as client:
        assert client.post("/api/projects", files={"file": ("empty.wav", b"")}).status_code == 422
        bad = client.post("/api/projects", files={"file": ("../../bad.mp4", b"not media")})
        assert bad.status_code == 202
        assert wait(client, bad.json())["status"] == "failed"
        assert client.get("/api/projects").json() == []
        assert client.post("/api/demo", headers={"Origin": "https://foreign-site.example"}).status_code == 403
        assert client.get("/api/projects/not-an-id").status_code == 404
        assert client.get("/api/jobs/missing").status_code == 404
        assert not (tmp_path / "bad.mp4").exists()


def test_no_music_prerequisite_changes_no_revision(tmp_path, clip):
    with TestClient(create_app(tmp_path / "state")) as client:
        job = client.post("/api/projects", files={"file": ("input.mp4", clip.read_bytes())}).json()
        assert wait(client, job)["status"] == "complete"
        id = job["project_id"]
        job = client.post(f"/api/projects/{id}/edits", json={"operations": [{"type": "align_music", "anchor": 1}]}).json()
        failed = wait(client, job)
        assert failed["status"] == "failed" and "Upload a music" in failed["error"]
        assert len(client.get(f"/api/projects/{id}").json()["revisions"]) == 1
