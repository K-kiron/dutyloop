"""Closed backlog planning contracts and deterministic stage selection.

Model assessments organize evidence; only stored verification records establish
completion. This module neither dispatches a host nor changes execution policy.
"""

from .schemas import _object, validate_payload


_TEXT = {"type": "string", "minLength": 1}
_NUMBER = {"type": "integer", "minimum": 1}
_HASH = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
_DEPENDENCIES = {"type": "array", "items": _NUMBER}
_EVIDENCE = {"type": "array", "minItems": 1,
             "items": _object({"source": _TEXT, "observation": _TEXT})}

ASSESSMENT_SCHEMA = _object({
    "summary": _TEXT,
    "current_stage": _TEXT,
    "evidence": _EVIDENCE,
    "items": {"type": "array", "items": _object({
        "number": _NUMBER, "source_sha256": _HASH, "assessment": _TEXT, "dependencies": _DEPENDENCIES,
    })},
})

ROADMAP_SCHEMA = _object({
    "summary": _TEXT,
    "current_stage": _TEXT,
    "evidence": _EVIDENCE,
    "stages": {"type": "array", "items": _object({
        "id": {"type": "string", "pattern": "^[a-z0-9]+(?:-[a-z0-9]+)*$"},
        "title": _TEXT, "goal": _TEXT, "rationale": _TEXT,
        "entry_conditions": {"type": "array", "minItems": 1, "items": _TEXT},
        "exit_criteria": {"type": "array", "minItems": 1, "items": _TEXT},
        "requires_human_acceptance": {"type": "boolean"},
        "issue_numbers": {"type": "array", "minItems": 1, "items": _NUMBER},
    })},
    "items": {"type": "array", "items": _object({
        "number": _NUMBER, "source_sha256": _HASH,
        "disposition": {"type": "string", "enum": ["work", "needs_decision", "defer", "done"]},
        "reason": _TEXT, "question": {"type": "string"},
        "dependencies": _DEPENDENCIES, "stage_id": {"type": "string"},
    })},
})


def _nonblank(*values):
    if any(not value.strip() for value in values):
        raise ValueError("Planning text must not be blank")


def _coverage(payload, entries, schema):
    validate_payload(payload, schema)
    _nonblank(payload["summary"], payload["current_stage"])
    for evidence in payload["evidence"]:
        _nonblank(evidence["source"], evidence["observation"])
    sources = {}
    for entry in entries:
        number = entry.get("number")
        if type(number) is not int or number < 1 or number in sources:
            raise ValueError("Backlog entries must have unique positive issue numbers")
        sources[number] = entry
    covered = set()
    for item in payload["items"]:
        number = item["number"]
        if number in covered or number not in sources:
            raise ValueError("Planning coverage contains a duplicate or foreign issue")
        covered.add(number)
        if item["source_sha256"] != sources[number]["source_sha256"]:
            raise ValueError("Planning source fingerprint differs from the captured issue")
        dependencies = item["dependencies"]
        if number in dependencies or len(set(dependencies)) != len(dependencies):
            raise ValueError("Issue dependencies must be unique and cannot include the issue itself")
    if covered != sources.keys():
        raise ValueError("Planning coverage must include every captured issue exactly once")
    return sources


def validate_assessment(payload, entries):
    """Require a full, source-bound assessment of one coordinator-selected chunk."""
    _coverage(payload, entries, ASSESSMENT_SCHEMA)
    for item in payload["items"]:
        _nonblank(item["assessment"])
    return payload


def _acyclic(items):
    """Check internal edges without imposing limits on external issue references."""
    graph = {item["number"]: set(item["dependencies"]) for item in items}
    incoming = {number: len(dependencies & graph.keys()) for number, dependencies in graph.items()}
    dependants = {number: [] for number in graph}
    for number, dependencies in graph.items():
        for dependency in dependencies & graph.keys():
            dependants[dependency].append(number)
    ready = [number for number, count in incoming.items() if count == 0]
    visited = 0
    while ready:
        number = ready.pop()
        visited += 1
        for dependant in dependants[number]:
            incoming[dependant] -= 1
            if incoming[dependant] == 0:
                ready.append(dependant)
    if visited != len(graph):
        raise ValueError("Roadmap issue dependencies contain a cycle")


def validate_roadmap(payload, entries):
    """Validate complete coverage, authority, stage membership, and dependencies."""
    sources = _coverage(payload, entries, ROADMAP_SCHEMA)
    stages, membership = {}, {}
    for index, stage in enumerate(payload["stages"]):
        if stage["id"] in stages:
            raise ValueError("Roadmap stage identifiers must be unique")
        stages[stage["id"]] = index
        _nonblank(stage["title"], stage["goal"], stage["rationale"],
                  *stage["entry_conditions"], *stage["exit_criteria"])
        for number in stage["issue_numbers"]:
            if number in membership or number not in sources:
                raise ValueError("Stage membership contains a duplicate or foreign issue")
            membership[number] = stage["id"]
    items = {item["number"]: item for item in payload["items"]}
    for number, item in items.items():
        source = sources[number]
        _nonblank(item["reason"])
        if item["disposition"] == "work":
            if (source.get("admitted") is not True or source.get("state") in {"needs_decision", "external_closed"}
                    or source.get("reason") == "excluded_label" or source["source"].get("state") != "open"):
                raise ValueError("Roadmap work cannot override intake authority, closure, or an existing decision")
            if item["stage_id"] not in stages or membership.get(number) != item["stage_id"]:
                raise ValueError("Every work issue must belong to exactly its declared roadmap stage")
        elif item["stage_id"] or number in membership:
            raise ValueError("Only work issues may belong to a roadmap stage")
        if item["disposition"] == "needs_decision":
            _nonblank(item["question"])
        if item["disposition"] == "done" and source.get("verified") is not True:
            raise ValueError("A done disposition requires an existing verified outcome")
    validate_dependencies(payload)
    return payload


def validate_dependencies(payload):
    """Check a JIT edge change without reinterpreting an authorized roadmap.

    New roadmaps still require validate_roadmap. Runtime blocked siblings stay
    in their planned stages; next_work enforces their live row states.
    """
    stages = {stage["id"]: index for index, stage in enumerate(payload["stages"])}
    items = {item["number"]: item for item in payload["items"]}
    for number, item in items.items():
        if number in item["dependencies"] or len(set(item["dependencies"])) != len(item["dependencies"]):
            raise ValueError("Issue dependencies must be unique and cannot include the issue itself")
        if item["disposition"] == "work":
            for dependency in item["dependencies"]:
                other = items.get(dependency)
                if (other and other["disposition"] == "work"
                        and stages[other["stage_id"]] > stages[item["stage_id"]]):
                    raise ValueError("A roadmap issue cannot depend on a later stage")
    _acyclic(payload["items"])


def _checks_pass(checks):
    return (isinstance(checks, list) and bool(checks)
            and all(isinstance(check, dict) and type(check.get("returncode")) is int
                    and check["returncode"] == 0 and not check.get("error")
                    and not check.get("timed_out") and not check.get("cancelled")
                    and check.get("cleanup_confirmed") is not False for check in checks))


def verified(row, delivery):
    """Recognize runner verification records, never issue state alone."""
    if (not isinstance(row, dict) or not isinstance(row.get("verified_tree"), str)
            or not row["verified_tree"].strip() or not isinstance(row.get("review"), dict)
            or row["review"].get("accepted") is not True or not _checks_pass(row.get("verification"))):
        return False
    if delivery == "local":
        return row.get("state") == "accepted_local"
    return (delivery in {"pull_request", "merge"} and row.get("state") in {"delivered", "closed"}
            and bool(row.get("merge_sha")) and row.get("post_merge_verified") == row["merge_sha"]
            and _checks_pass(row.get("post_merge_checks")))


def next_work(roadmap, rows, delivery):
    """Return eligible work in the earliest incomplete stage's explicit order.

    Dependencies outside the roadmap are left for the caller's live GitHub
    checks. Human acceptance requires a new, explicitly resolved roadmap; issue
    implementation records do not constitute that acceptance.
    """
    rows = {row["number"]: row for row in rows}
    items = {item["number"]: item for item in roadmap["items"]}
    for stage in roadmap["stages"]:
        incomplete = [items[number] for number in stage["issue_numbers"]
                      if not verified(rows.get(number), delivery)]
        if stage["requires_human_acceptance"]:
            return stage, [], "stage_requires_human_acceptance"
        if not incomplete:
            continue
        work = [item for item in incomplete
                if item["disposition"] == "work"
                and rows.get(item["number"], {}).get("state") in {"queued", "planned", "waiting_dependency"}
                and all(dependency not in items or verified(rows.get(dependency), delivery)
                        for dependency in item["dependencies"])]
        return stage, work, None if work else "stage_has_no_runnable_work"
    return None, [], None
