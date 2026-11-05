import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from reelstudio.models import EditPlan, EditRequest
from reelstudio.planner import _rules, provider_schema
from reelstudio.store import Store


@pytest.mark.parametrize("operation", [
    {"type": "shell", "command": "echo unsafe"},
    {"type": "speed", "factor": 0},
    {"type": "speed", "factor": float("inf")},
    {"type": "gain", "gain_db": float("nan")},
    {"type": "trim", "start": 5, "end": 2},
    {"type": "stem_mix", "gains": {"drums": 99}},
    {"type": "isolate", "target": "drums", "command": "anything"},
])
def test_unsafe_or_invalid_operations_rejected(operation):
    with pytest.raises(ValidationError):
        EditPlan.model_validate({"summary": "test", "operations": [operation]})


def test_command_fallback_preserves_explicit_selection_and_provenance():
    request = EditRequest(prompt="Slow this section to 0.75x", selection={"start": 2, "end": 5})
    plan = _rules(request, 12)
    operation = plan.operations[0]
    assert operation.type == "speed" and operation.factor == .75
    assert (operation.start, operation.end) == (2, 5)
    assert "command parser" in plan.summary


def test_unknown_language_is_not_pretended_to_be_understood():
    with pytest.raises(ValueError, match="free-form"):
        _rules(EditRequest(prompt="Make it sound like the best day of my life"), 12)


def test_reorder_requires_positive_ranges():
    with pytest.raises(ValidationError):
        EditRequest(operations=[{"type": "reorder", "ranges": [{"start": 4, "end": 2}]}])


def test_provider_schema_omits_rejected_keywords_but_keeps_operation_types():
    text = json.dumps(provider_schema())
    assert 'maxProperties' not in text and '$ref' not in text
    for operation in ('stem_mix', 'isolate', 'reorder', 'speed'):
        assert operation in text
    def check(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert set(node["required"]) == set(node["properties"])
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)
    check(provider_schema())


def test_atomic_persistence_and_interrupted_jobs(tmp_path):
    store = Store(tmp_path)
    store.save("projects", {"id": "saved", "current_revision": "complete"})
    store.save("jobs", {"id": "unfinished", "status": "running"})
    store.close()
    reopened = Store(tmp_path)
    assert reopened.get("jobs", "unfinished")["status"] == "running"
    reopened.recover_interrupted()
    assert reopened.get("projects", "saved")["current_revision"] == "complete"
    assert reopened.get("jobs", "unfinished")["status"] == "failed"
    assert "restart" in reopened.get("jobs", "unfinished")["error"]
    with pytest.raises(ValueError):
        reopened.project_dir("../escape")
    reopened.close()
