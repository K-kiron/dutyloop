"""Offline native protocol fixtures; these do not claim live model validation."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from dutyloop.adapters.extended import parse_dsh, parse_opencode, supported_version
from dutyloop.drivers import DriverRequest, NATIVE_DRIVERS, get_driver, _native_argv
from dutyloop.process import ProcessResult
from dutyloop.schemas import IMPLEMENT_SCHEMA


def ndjson(items):
    return "\n".join(json.dumps(item) for item in items)


def opencode_events(payload=None):
    part = {"sessionID": "ses_one", "messageID": "msg_one"}
    return [
        {"type": "step_start", "sessionID": "ses_one", "part": part | {"type": "step-start"}},
        {"type": "text", "sessionID": "ses_one", "part": part | {
            "type": "text", "text": json.dumps(payload or {"summary": "Fixed", "completed": True})}},
        {"type": "step_finish", "sessionID": "ses_one", "part": part | {
            "type": "step-finish", "reason": "stop", "cost": 0,
            "tokens": {"input": 20, "output": 5}}},
    ]


def dsh_events(payload=None):
    return [
        {"type": "session", "sessionId": "session-one", "cwd": "/workspace"},
        {"type": "status", "phase": "step_end", "usage": {"inputTokens": 10, "outputTokens": 4}},
        {"type": "final", "sessionId": "session-one", "reason": {"kind": "completed"},
         "text": json.dumps(payload or {"summary": "Fixed", "completed": True})},
    ]


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.request = DriverRequest("implement", "Resolve a scoped defect. $(literal)", self.root,
                                     IMPLEMENT_SCHEMA, self.root / "output", 8)

    def run_host(self, name, stream, version=None, returncode=0):
        seen = []
        version = version or ("2.2.5" if name == "opencode" else "0.2.2-rc.1")

        def run(argv, **kwargs):
            seen.append((argv, kwargs))
            if argv[-1] == "--version":
                return ProcessResult(returncode=0, stdout=version + "\n")
            return ProcessResult(returncode=returncode, stdout=ndjson(stream))

        with patch("dutyloop.drivers._native_argv", return_value=[name]), \
             patch("dutyloop.drivers.run_process", side_effect=run):
            result = get_driver({"name": name}).run(self.request, lambda: False)
        return result, seen

    def test_registry_and_pinned_versions_fail_closed(self):
        self.assertIn("opencode", NATIVE_DRIVERS)
        self.assertIn("dsh", NATIVE_DRIVERS)
        self.assertEqual(supported_version("opencode", "opencode 2.2.5\n"), "2.2.5")
        for name, version in [("opencode", "1.2.27"), ("opencode", "2.2.6"),
                              ("opencode", "2.2.5-dev"), ("dsh", "0.2.1-rc.2"),
                              ("dsh", "0.2.2-rc.1\n0.2.2-rc.1")]:
            with self.subTest(name=name, version=version):
                result, calls = self.run_host(name, [], version=version)
                self.assertEqual(len(calls), 1)
                self.assertEqual(result.error_kind, "config")
                self.assertIsNone(result.payload)

    def test_opencode_v2_uses_private_server_and_v2_phase_policy(self):
        result, seen = self.run_host("opencode", opencode_events())
        self.assertIsNone(result.error)
        self.assertEqual(result.session_id, "ses_one")
        argv, invocation = seen[-1]
        self.assertIn("--standalone", argv)
        self.assertNotIn("--auto", argv)
        self.assertNotIn("--server", argv)
        self.assertNotIn(self.request.prompt, argv)
        self.assertIn(self.request.prompt, invocation["input_text"])
        config = json.loads(invocation["env"]["OPENCODE_CONFIG_CONTENT"])
        self.assertNotIn("permission", config)
        self.assertEqual(config["share"], "disabled")
        self.assertEqual(config["update"], "disable")
        self.assertEqual(invocation["env"]["OPENCODE_FLAGS"], "-schedules,-background_compaction")
        self.assertEqual(invocation["env"]["OPENCODE_DISABLE_AUTOUPDATE"], "1")
        self.assertEqual(config["permissions"][0], {"action": "*", "resource": "*", "effect": "deny"})
        self.assertIn({"action": "shell", "resource": "*", "effect": "allow"}, config["permissions"])
        self.assertIsNone(result.cost_usd)
        self.assertEqual(result.usage["scope"], "reported_steps")
        self.request.phase = "review"
        _, seen = self.run_host("opencode", opencode_events())
        rules = json.loads(seen[-1][1]["env"]["OPENCODE_CONFIG_CONTENT"])["permissions"]
        self.assertEqual([rule["action"] for rule in rules], ["*", "read", "glob", "grep"])

    def test_opencode_rejects_incomplete_mixed_or_non_json_final(self):
        good = opencode_events()
        cases = [good[:-1], good[:1] + good[-1:], good + [good[1]],
                 good[:-1] + [good[-1] | {"sessionID": "other"}],
                 good[:-1] + [good[-1] | {"part": good[-1]["part"] | {"reason": "length"}}],
                 [good[0], good[1] | {"part": good[1]["part"] | {"text": "```json\n{}\n```"}}, good[2]],
                 good + [{"type": "error", "error": {"type": "authentication_error"}}]]
        for stream in cases:
            with self.subTest(stream=stream):
                result, _ = self.run_host("opencode", stream)
                self.assertIsNotNone(result.error)
                self.assertIsNone(result.payload)

    def test_opencode_uses_text_of_final_message_only(self):
        old = opencode_events({"summary": "Earlier intermediate answer", "completed": False})
        old[-1]["part"]["reason"] = "tool-calls"
        new = opencode_events()
        for event in new:
            event["part"]["messageID"] = "msg_two"
        payload, _, _, _ = parse_opencode(ndjson(old + new))
        self.assertEqual(payload["summary"], "Fixed")

    def test_dsh_mode_never_approval_and_terminal_completion(self):
        result, seen = self.run_host("dsh", dsh_events())
        self.assertIsNone(result.error)
        argv, invocation = seen[-1]
        self.assertEqual(argv[-1], "-")
        self.assertIn("workspace-write", argv)
        self.assertEqual(argv[argv.index("--approval-policy") + 1], "never")
        self.assertNotIn("--session-id", argv)
        self.assertNotIn("--add-dir", argv)
        self.assertIn('"additionalProperties":false', invocation["input_text"])
        self.assertEqual(result.session_id, "session-one")
        self.assertIsNone(result.cost_usd)
        self.assertEqual(result.usage["scope"], "parent_session_only")
        self.request.phase = "discover"
        _, seen = self.run_host("dsh", dsh_events())
        self.assertIn("read-only", seen[-1][0])

    def test_dsh_rejects_truncated_mismatched_failed_and_missing_final(self):
        good = dsh_events()
        cases = [good[1:], good[:-1], good + [good[1]],
                 [good[0] | {"truncated": True}] + good[1:],
                 good[:-1] + [good[-1] | {"truncated": True}],
                 good[:-1] + [good[-1] | {"sessionId": "different"}],
                 good[:-1] + [good[-1] | {"reason": None}],
                 good[:-1] + [good[-1] | {"reason": {"kind": "error", "error": {"code": "quota_exceeded"}}}],
                 [good[0], {"type": "error", "message": "private provider diagnostic"}]]
        for stream in cases:
            with self.subTest(stream=stream):
                result, _ = self.run_host("dsh", stream)
                self.assertIsNotNone(result.error)
                self.assertIsNone(result.payload)
                self.assertNotIn("private provider diagnostic", result.error)

    def test_dsh_partial_or_child_usage_is_never_claimed_as_total(self):
        stream = dsh_events()
        stream.insert(1, {"type": "status", "phase": "step_end"})
        _, usage, cost, _ = parse_dsh(ndjson(stream))
        self.assertFalse(usage["complete"])
        self.assertEqual(usage["scope"], "parent_session_only")
        self.assertIsNone(cost)

    def test_dsh_rejects_unimplemented_model_override_without_model_work(self):
        self.request.model = "provider/model"
        result, seen = self.run_host("dsh", dsh_events())
        self.assertEqual(len(seen), 1)
        self.assertEqual(result.error_kind, "config")

    def test_native_nonzero_machine_errors_are_classified_without_leaking_prose(self):
        for name, stream, category in [
            ("opencode", [{"type": "error", "error": {"type": "authentication_error", "message": "secret"}}], "auth"),
            ("dsh", dsh_events()[:-1] + [{"type": "final", "reason": {"kind": "error", "error": {"code": "quota_exceeded", "message": "secret"}}}], "quota"),
        ]:
            result, _ = self.run_host(name, stream, returncode=1)
            self.assertEqual(result.error_kind, category)
            self.assertNotIn("secret", result.error)

    def test_duplicate_json_keys_and_schema_violations_fail_closed(self):
        for name, stream in [("opencode", opencode_events({"summary": "Missing completed"})),
                             ("dsh", dsh_events({"summary": "Wrong", "completed": 1}))]:
            result, _ = self.run_host(name, stream)
            self.assertEqual(result.error_kind, "protocol")
            self.assertIsNone(result.payload)
        for parser, stream in [(parse_dsh, dsh_events()), (parse_opencode, opencode_events())]:
            text = ndjson(stream).replace('"type": "', '"type": "bad", "type": "', 1)
            with self.assertRaises(ValueError):
                parser(text)

    def test_version_failure_and_stop_never_launch_task(self):
        for process in [ProcessResult(returncode=1), ProcessResult(returncode=0, cancelled=True),
                        ProcessResult(returncode=0, timed_out=True),
                        ProcessResult(returncode=0, output_truncated=True)]:
            with self.subTest(process=process), \
                 patch("dutyloop.drivers._native_argv", return_value=["opencode"]), \
                 patch("dutyloop.drivers.run_process", return_value=process) as runner:
                result = get_driver({"name": "opencode"}).run(self.request, lambda: False)
                self.assertEqual(runner.call_count, 1)
                self.assertIsNone(result.payload)

    def test_real_executable_fixture_receives_policy_and_stdin_without_shell(self):
        fixture = self.root / "host.py"
        fixture.write_text(
            "import json,os,sys\n"
            "if sys.argv[-1]=='--version': print('2.2.5');sys.exit()\n"
            "assert '--standalone' in sys.argv\n"
            "assert '--auto' not in sys.argv\n"
            "cfg=json.loads(os.environ['OPENCODE_CONFIG_CONTENT'])\n"
            "assert cfg['share']=='disabled'\n"
            "assert os.environ['OPENCODE_FLAGS']=='-schedules,-background_compaction'\n"
            "assert os.environ['OPENCODE_DISABLE_AUTOUPDATE']=='1'\n"
            "assert '$(literal)' in sys.stdin.read()\n"
            "events=" + repr(opencode_events()) + "\n"
            "for event in events: print(json.dumps(event))\n", encoding="utf-8")
        with patch("dutyloop.drivers._native_argv", return_value=[sys.executable, str(fixture)]):
            result = get_driver({"name": "opencode"}).run(self.request, lambda: False)
        self.assertIsNone(result.error)
        self.assertTrue(result.payload["completed"])

    def test_known_npm_entrypoints_are_executed_directly(self):
        for name, relative in [("opencode", "@opencode/cli/bin/opencode.cjs"),
                               ("dsh", "@deepseek-ai/dsh/lib/bin.js")]:
            with self.subTest(name=name):
                entry = self.root / "node_modules" / relative
                entry.parent.mkdir(parents=True, exist_ok=True)
                entry.touch()
                with patch("dutyloop.drivers.shutil.which", side_effect=lambda item: str(self.root / (name + ".cmd")) if item == name else "node.exe"):
                    self.assertEqual(_native_argv(name), ["node.exe", str(entry)])


if __name__ == "__main__":
    unittest.main()
