import subprocess

import pytest

from dutyloop.config import relative_path, validate_spec


def test_preflight_rejects_an_uncommitted_repository(tmp_path):
    project = tmp_path / "project"
    subprocess.run(["git", "init", "-b", "main", str(project)], check=True, capture_output=True)
    spec = {"mission": "Maintain reliability", "project": str(project), "allowed_paths": ["."],
            "verification": [["python", "-m", "unittest"]],
            "limits": {"max_invocations": 3, "max_wall_seconds": 60}}
    with pytest.raises(ValueError, match="committed"):
        validate_spec(spec, tmp_path / "state")
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize("path", [".GIT/config", "src/.Git/HEAD", "  "])
def test_git_metadata_is_not_an_accepted_scope_even_on_case_sensitive_hosts(path):
    with pytest.raises(ValueError):
        relative_path(path)
