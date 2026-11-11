"""Native planners commonly cite source lines; resolution keeps path controls."""

import copy
import hashlib
from types import SimpleNamespace

import pytest

from dutyloop.engine import Supervisor


def proposal(source):
    return {"key": "correct-price", "kind": "bugfix", "title": "Apply the discount",
            "priority": 3, "why": "The percentage is ignored", "acceptance": ["20% off 100 returns 80"],
            "evidence": [{"source": source, "observation": "The implementation returns the original price"}]}


@pytest.mark.parametrize("citation", ["app.py:1", "app.py:1-2", "src/app.py:10-12"])
def test_local_line_citation_resolves_to_existing_project_file_with_raw_receipt(tmp_path, citation):
    filename = citation.split(":")[0]
    target = tmp_path / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("value = 0\n", encoding="utf-8")
    candidate = proposal(citation)
    original = copy.deepcopy(candidate)
    supervisor = SimpleNamespace(spec={"kinds": ["bugfix"], "research_domains": []})
    result = Supervisor.candidate(supervisor, candidate, tmp_path)
    assert candidate == original
    assert result["evidence"] == original["evidence"]
    assert result["source_receipts"] == [{"source": citation, "verified": False,
                                          "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}]


@pytest.mark.parametrize("citation", ["../outside.py:1", "/outside.py:1", "C:/outside.py:1",
                                     ".git/config:1", "app.py:0", "app.py:3-1", "missing.py:1"])
def test_line_citation_does_not_bypass_existing_evidence_path_controls(tmp_path, citation):
    (tmp_path / "app.py").write_text("value = 0\n", encoding="utf-8")
    supervisor = SimpleNamespace(spec={"kinds": ["bugfix"], "research_domains": []})
    with pytest.raises(ValueError):
        Supervisor.candidate(supervisor, proposal(citation), tmp_path)
