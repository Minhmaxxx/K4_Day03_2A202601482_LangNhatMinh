import contextlib
import io
import json
import os
import sys
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from app import (
    execute_agent_action,
    get_supported_test_cases,
    load_test_cases,
    parse_agent_response,
    run_baseline_chatbot,
    run_react_agent,
)
from prompts import AGENT_FALLBACK_RESPONSE, MAX_ITERATIONS
from providers import MockProvider
from tools import AVAILABLE_TOOLS, assess_risk_level


class CountingProvider:
    def __init__(self, response="response"):
        self.response = response
        self.calls = 0

    def generate(self, prompt, system_prompt=""):
        self.calls += 1
        return self.response


class AppTests(unittest.TestCase):
    def test_parser_accepts_strict_json_action(self):
        payload = {"tool": "suggest_reflection_exercise", "args": ["cam_xuc"]}
        response_type, action = parse_agent_response(
            f"Thought: Cần bài tập.\nAction: {json.dumps(payload)}"
        )
        self.assertEqual(response_type, "action")
        self.assertEqual(action, payload)

    def test_parser_rejects_python_literal(self):
        with self.assertRaisesRegex(ValueError, "JSON hợp lệ"):
            parse_agent_response(
                "Thought: Cần tool.\n"
                "Action: {'tool': 'suggest_reflection_exercise', 'args': ['cam_xuc']}"
            )

    def test_unknown_tool_is_an_observation_error(self):
        result = execute_agent_action({"tool": "unknown", "args": []}, set())
        self.assertIn("không tồn tại", result)

    def test_baseline_makes_exactly_one_provider_call(self):
        provider = CountingProvider()
        with contextlib.redirect_stdout(io.StringIO()):
            run_baseline_chatbot("Tôi thích ở một mình.", provider)
        self.assertEqual(provider.calls, 1)

    def test_high_risk_never_calls_provider(self):
        provider = CountingProvider()
        with contextlib.redirect_stdout(io.StringIO()):
            response = run_react_agent("Tôi muốn kết thúc tất cả ngay đêm nay.", provider)
        self.assertEqual(provider.calls, 0)
        self.assertIn("an toàn", response)

    def test_react_mock_uses_real_tool_observations(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            response = run_react_agent(
                "Ban ngày tôi rất hòa đồng nhưng tối lại thích nhốt mình.",
                MockProvider(),
            )
        trace = output.getvalue()
        self.assertIn("Observation: KẾT QUẢ QUÉT TÍN HIỆU", trace)
        self.assertIn("Observation: MẶT ÍT BỘC LỘ", trace)
        self.assertIn("không phải chẩn đoán", response)

    def test_max_iterations_returns_safe_fallback(self):
        provider = CountingProvider("Phản hồi sai định dạng")
        with contextlib.redirect_stdout(io.StringIO()):
            response = run_react_agent("Tôi thích sáng tạo và thử điều mới.", provider)
        self.assertEqual(provider.calls, MAX_ITERATIONS)
        self.assertEqual(response, AGENT_FALLBACK_RESPONSE)

    def test_only_supported_text_cases_are_selected(self):
        supported, skipped = get_supported_test_cases(load_test_cases())
        case_ids = {case["case_id"] for case in supported}
        self.assertTrue({"NORM_001", "STRS_001", "RISK_001"}.issubset(case_ids))
        self.assertNotIn("LOC_ERR_001", case_ids)
        self.assertGreater(len(skipped), 0)

    def test_safety_gate_is_not_an_agent_tool(self):
        self.assertEqual(assess_risk_level("Tôi muốn kết thúc tất cả"), "HIGH")
        self.assertNotIn("screen_risk_signals", AVAILABLE_TOOLS)


if __name__ == "__main__":
    unittest.main()
