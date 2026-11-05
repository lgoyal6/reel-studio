import subprocess
import time

from fastapi.testclient import TestClient

from reelstudio.app import create_app


def finish(client, id):
    for _ in range(300):
        job = client.get(f"/api/jobs/{id}").json()
        if job["status"] in ("complete", "failed"):
            assert job["status"] == "complete", job
            return job
        time.sleep(.05)
    raise AssertionError("Import did not complete")


def test_invited_browsers_have_isolated_projects_jobs_media_and_exports(tmp_path, monkeypatch):
    monkeypatch.setenv("REEL_PUBLIC_ORIGIN", "https://studio.example")
    monkeypatch.setenv("REEL_DEMO_ACCESS_CODE", "test-invite-long-enough")
    app = create_app(tmp_path / "hosted")
    source = tmp_path / "test.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1:sample_rate=48000", str(source)], check=True)
    with TestClient(app, base_url="https://studio.example") as first:
        assert first.get("/api/projects").status_code == 401
        assert first.post("/api/session", json={"access_code": "wrong"}).status_code == 401
        login = first.post("/api/session", json={"access_code": "test-invite-long-enough"})
        cookie = login.headers["set-cookie"].lower()
        assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
        upload = first.post("/api/projects", files={"file": ("private.wav", source.read_bytes())}).json()
        job = finish(first, upload["id"])
        id = job["project_id"]
        p = first.get(f"/api/projects/{id}").json()
        media = p["revisions"][0]["audio_url"]
        assert first.get(media).status_code == 200
        assert first.get(f"/api/projects/{id}/export?format=wav").status_code == 200
        # Another invited browser receives a separate owner, including background jobs.
        first.cookies.clear()
        first.post("/api/session", json={"access_code": "test-invite-long-enough"}).raise_for_status()
        assert first.get("/api/projects").json() == []
        assert first.get(f"/api/projects/{id}").status_code == 404
        assert first.get(f"/api/jobs/{upload['id']}").status_code == 404
        assert first.get(media).status_code == 404
        assert first.get(f"/api/projects/{id}/export?format=wav").status_code == 404
        assert first.post("/api/session", json={"access_code": "test-invite-long-enough"}, headers={"Origin": "https://unrelated.example"}).status_code == 403


def test_tampered_session_and_rate_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("REEL_PUBLIC_ORIGIN", "https://studio.example")
    monkeypatch.setenv("REEL_DEMO_ACCESS_CODE", "test-invite-long-enough")
    with TestClient(create_app(tmp_path / "state"), base_url="https://studio.example") as client:
        client.cookies.set("reel_workspace", "forged-owner-cookie")
        assert client.get("/api/projects").status_code == 401
        for _ in range(8):
            assert client.post("/api/session", json={"access_code": "wrong"}).status_code == 401
        assert client.post("/api/session", json={"access_code": "wrong"}).status_code == 429
