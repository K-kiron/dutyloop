import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from dutyloop.drivers import DriverRequest, _native_argv, get_driver, probe_driver
from dutyloop.process import ProcessResult
from dutyloop.schemas import DISCOVERY_SCHEMA, IMPLEMENT_SCHEMA, REVIEW_SCHEMA, validate_payload


class DriverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.request = DriverRequest("implement", "Fix the defect.", self.root, IMPLEMENT_SCHEMA,
                                     self.root / "output", 5)

    def native(self, name, events, **fields):
        captured = {}
        def fake_run(argv, **kwargs):
            captured.update(argv=argv, **kwargs)
            return ProcessResult(stdout="\n".join(json.dumps(x) for x in events), **fields)
        with patch("dutyloop.drivers._native_argv", return_value=[name]), \
             patch("dutyloop.drivers.run_process", side_effect=fake_run):
            result = get_driver({"name": name}).run(self.request, lambda: False)
        return result, captured

    def codex_events(self, usage=None):
        events = [{"type": "thread.started", "thread_id": "session-one"},
                  {"type": "item.completed", "item": {"type": "agent_message", "text":
                    json.dumps({"summary": "Fixed", "completed": True})}},
                  {"type": "turn.completed"}]
        if usage is not None:
            events[-1]["usage"] = usage
        return events

    def test_codex_parses_final_json_usage_and_safe_phase_flags(self):
        result, invocation = self.native("codex", self.codex_events({"input_tokens": 12}), returncode=0)
        self.assertIsNone(result.error)
        self.assertTrue(result.payload["completed"])
        self.assertEqual(result.usage, {"input_tokens": 12})
        self.assertIsNone(result.cost_usd)
        self.assertEqual(result.session_id, "session-one")
        self.assertIn("workspace-write", invocation["argv"])
        self.assertIn("--ephemeral", invocation["argv"])
        self.assertIn('approval_policy="never"', invocation["argv"])
        self.assertEqual(invocation["input_text"], self.request.prompt)
        self.request.phase = "review"
        result, invocation = self.native("codex", self.codex_events(), returncode=0)
        self.assertIn("read-only", invocation["argv"])

    def test_missing_usage_is_unknown_in_both_hosts(self):
        result, _ = self.native("codex", self.codex_events(), returncode=0)
        self.assertIsNone(result.error)
        self.assertEqual(result.usage, {})
        self.assertIsNone(result.cost_usd)
        result, _ = self.native("claude", [{"type": "result", "subtype": "success",
                                "structured_output": {"summary": "Fixed", "completed": True}}], returncode=0)
        self.assertIsNone(result.error)
        self.assertEqual(result.usage, {})
        self.assertIsNone(result.cost_usd)

    def test_codex_final_file_is_parsed_without_scraping_prose(self):
        def fake_run(argv, **kwargs):
            final = Path(argv[argv.index("--output-last-message") + 1])
            final.write_text(json.dumps({"summary": "From final file", "completed": True}), encoding="utf-8")
            return ProcessResult(returncode=0, stdout=json.dumps({"type": "turn.completed"}))
        with patch("dutyloop.drivers._native_argv", return_value=["codex"]), \
             patch("dutyloop.drivers.run_process", side_effect=fake_run):
            result = get_driver({"name": "codex"}).run(self.request, lambda: False)
        self.assertIsNone(result.error)
        self.assertEqual(result.payload["summary"], "From final file")

    def test_codex_npm_wrapper_uses_node_argv_and_rejects_unknown_wrappers(self):
        wrapper = self.root / "codex.cmd"
        entry = self.root / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
        entry.parent.mkdir(parents=True)
        entry.touch()
        with patch("dutyloop.drivers.shutil.which", side_effect=lambda name: str(wrapper) if name == "codex" else "node.exe"):
            self.assertEqual(_native_argv("codex"), ["node.exe", str(entry)])
        with patch("dutyloop.drivers.shutil.which", return_value=str(self.root / "claude.cmd")):
            with self.assertRaises(ValueError):
                _native_argv("claude")

    def test_claude_result_preserves_cost_tree_usage_and_denies_mcp(self):
        event = {"type": "result", "subtype": "success", "session_id": "claude-one",
                 "structured_output": {"summary": "Fixed", "completed": True},
                 "usage": {"input_tokens": 5}, "modelUsage": {"model": {"inputTokens": 10}},
                 "total_cost_usd": 0.25}
        result, invocation = self.native("claude", [event], returncode=0)
        self.assertIsNone(result.error)
        self.assertEqual(result.cost_usd, 0.25)
        self.assertIn("model_usage", result.usage)
        self.assertEqual(result.session_id, "claude-one")
        self.assertIn("dontAsk", invocation["argv"])
        self.assertIn("--no-session-persistence", invocation["argv"])
        self.assertIn("mcp__*", invocation["argv"])
        self.request.phase = "review"
        _, invocation = self.native("claude", [event], returncode=0)
        self.assertEqual(invocation["argv"][invocation["argv"].index("--tools") + 1], "Read,Glob,Grep")

    def test_host_failure_missing_final_and_invalid_schema_are_failures(self):
        cases = [
            ("codex", [{"type": "turn.completed"}]),
            ("codex", [{"type": "turn.failed"}]),
            ("codex", self.codex_events()[:-1]),
            ("claude", [{"type": "result", "subtype": "success", "result": "{\"completed\":true}"}]),
            ("claude", [{"type": "result", "subtype": "error_max_budget_usd"}]),
            ("claude", [{"type": "result", "subtype": "success", "structured_output": {"completed": True}}]),
        ]
        for name, events in cases:
            with self.subTest(name=name, events=events):
                result, _ = self.native(name, events, returncode=0)
                self.assertIsNotNone(result.error)
                self.assertIsNone(result.payload)

    def test_process_stop_deadline_failure_and_truncation_are_not_success(self):
        for state in ({"cancelled": True}, {"timed_out": True}, {"returncode": 2},
                      {"cleanup_confirmed": False, "error": "cleanup failed"}, {"output_truncated": True}):
            with self.subTest(state=state):
                state.setdefault("returncode", 0)
                result, _ = self.native("codex", self.codex_events(), **state)
                self.assertIsNotNone(result.error)
                self.assertIsNone(result.payload)

    def test_custom_command_protocol_runs_real_executable_without_model(self):
        script = ("import json,sys,os; r=json.load(sys.stdin); "
                  "assert r['phase']=='implement'; assert r['cwd']==os.getcwd(); "
                  "print(json.dumps({'payload':{'summary':r['prompt'],'completed':True}}))")
        result = get_driver({"name": "command", "argv": [sys.executable, "-c", script]}).run(
            self.request, lambda: False)
        self.assertIsNone(result.error)
        self.assertEqual(result.payload["summary"], "Fix the defect.")
        self.assertEqual(result.usage, {})
        self.assertIsNone(result.cost_usd)

    def test_custom_command_invalid_or_multiple_json_objects_fail(self):
        for script in ("print('{}')", "print('{}\\n{}')", "print('not json')",
                       "print('{\"payload\":{},\"usage\":{\"input_tokens\":NaN}}')",
                       "print('{\"payload\":{},\"usage\":{\"input_tokens\":1e999}}')",
                       "print('{\"payload\":{\"summary\":\"x\",\"completed\":1}}')"):
            with self.subTest(script=script):
                result = get_driver({"name": "command", "argv": [sys.executable, "-c", script]}).run(
                    self.request, lambda: False)
                self.assertIsNotNone(result.error)

    def test_probe_never_launches_host_or_reads_authentication(self):
        with patch("dutyloop.drivers.shutil.which", return_value="/installed/codex"), \
             patch("dutyloop.drivers.run_process") as runner:
            self.assertTrue(probe_driver("codex")["available"])
            runner.assert_not_called()
        self.assertFalse(probe_driver("unknown")["available"])

    def test_rejects_shell_wrappers_and_unknown_driver(self):
        for config in ({"name": "unknown"}, {"name": "command", "argv": []},
                       {"name": "command", "argv": ["script.cmd"]}):
            with self.assertRaises(ValueError):
                get_driver(config)

    def test_closed_schemas_and_discovery_boundaries(self):
        def assert_closed(schema):
            if schema.get("type") == "object":
                self.assertIs(schema["additionalProperties"], False)
                self.assertEqual(set(schema["required"]), set(schema["properties"]))
                for child in schema["properties"].values():
                    assert_closed(child)
            elif schema.get("type") == "array":
                assert_closed(schema["items"])
        for schema in (DISCOVERY_SCHEMA, IMPLEMENT_SCHEMA, REVIEW_SCHEMA):
            assert_closed(schema)
        candidate = {"key": "one-task", "title": "Fix", "kind": "bugfix", "why": "Broken",
                     "priority": 1, "evidence": [{"source": "test.py:1", "observation": "Fails"}],
                     "acceptance": ["Tests pass"]}
        validate_payload({"status": "work", "summary": "One", "candidates": [candidate]}, DISCOVERY_SCHEMA)
        for change in ({"acceptance": []}, {"priority": True}, {"unexpected": 1}, {"key": "../escape"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_payload({"status": "work", "summary": "One", "candidates": [candidate | change]}, DISCOVERY_SCHEMA)


if __name__ == "__main__":
    unittest.main()
