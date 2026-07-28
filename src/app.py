"""Integration entrypoint for the baseline chatbot and ReAct agent."""

import argparse
import json
import os
import sys


sys.path.append(os.path.dirname(os.path.abspath(__file__)))

if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from prompts import (
    AGENT_FALLBACK_RESPONSE,
    CHATBOT_BASELINE_PROMPT,
    CRISIS_FALLBACK_RESPONSE,
    DISCLAIMER,
    MAX_ITERATIONS,
    MEDIUM_RISK_RESPONSE,
    REACT_SYSTEM_PROMPT,
)
from providers import get_llm_provider
from tools import AVAILABLE_TOOLS, assess_risk_level


SUPPORTED_TEST_GROUPS = {"normal", "high_stress"}


def load_test_cases():
    """Load Role 1's test document from config/test_cases.json."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_path = os.path.join(base_dir, "config", "test_cases.json")
    with open(config_path, "r", encoding="utf-8") as file:
        return json.load(file)


def get_supported_test_cases(test_document):
    """Return runnable text cases and preserve unsupported cases as skipped."""
    groups = test_document.get("test_cases") if isinstance(test_document, dict) else None
    if not isinstance(groups, dict):
        raise ValueError("Thiếu object 'test_cases' trong config/test_cases.json.")

    supported = []
    skipped = []
    for group_name, cases in groups.items():
        if not isinstance(cases, list):
            skipped.append((group_name, "Nhóm test không phải JSON array."))
            continue

        for case in cases:
            if not isinstance(case, dict):
                skipped.append((group_name, "Test case không phải JSON object."))
                continue

            case_id = case.get("case_id", "UNKNOWN")
            context = case.get("input_context")
            user_query = context.get("user_message") if isinstance(context, dict) else None
            expected = case.get("expected_behavior")
            expected_action = expected.get("action") if isinstance(expected, dict) else None
            is_crisis_case = (
                group_name == "self_harm_risk"
                and expected_action == "Trigger_Safe_Fallback"
            )

            if group_name not in SUPPORTED_TEST_GROUPS and not is_crisis_case:
                skipped.append((case_id, "Ngoài scope text-only của Phase 3."))
                continue
            if not isinstance(user_query, str) or not user_query.strip():
                skipped.append((case_id, "Thiếu input_context.user_message."))
                continue

            supported.append(
                {
                    "case_id": case_id,
                    "group": group_name,
                    "user_query": user_query.strip(),
                    "expected_action": expected_action,
                }
            )

    if not supported:
        raise ValueError("Không có test case text-only nào có thể chạy.")
    return supported, skipped


def run_baseline_chatbot(user_query: str, provider, show_prompt: bool = False):
    """Make exactly one LLM call and no agent tool calls."""
    print(f"\n💬 [CHATBOT BASELINE] Câu hỏi: {user_query}")
    if show_prompt:
        print(f"⚙️ System Prompt: {CHATBOT_BASELINE_PROMPT.strip()}")

    response = provider.generate(user_query, system_prompt=CHATBOT_BASELINE_PROMPT)
    response = _ensure_disclaimer(response)
    print(f"🤖 Chatbot trả lời:\n{response}")
    return response


def parse_agent_response(response: str):
    """Parse one strict JSON Action or a Final Answer from an LLM response."""
    action_lines = [
        line.split("Action:", 1)[1].strip()
        for line in response.splitlines()
        if line.strip().startswith("Action:")
    ]
    final_marker = "Final Answer:"
    has_final = final_marker in response

    if action_lines and has_final:
        raise ValueError("Phản hồi chứa đồng thời Action và Final Answer.")
    if len(action_lines) > 1:
        raise ValueError("Mỗi bước chỉ được có một Action.")
    if has_final:
        final_answer = response.split(final_marker, 1)[1].strip()
        if not final_answer:
            raise ValueError("Final Answer đang để trống.")
        return "final", final_answer
    if not action_lines:
        raise ValueError("Không tìm thấy Action hoặc Final Answer.")

    try:
        action = json.loads(action_lines[0])
    except json.JSONDecodeError as error:
        raise ValueError(f"Action không phải JSON hợp lệ: {error.msg}.") from error

    if not isinstance(action, dict):
        raise ValueError("Action phải là một JSON object.")
    tool_name = action.get("tool")
    arguments = action.get("args")
    if not isinstance(tool_name, str) or not tool_name:
        raise ValueError("Action thiếu tên tool.")
    if not isinstance(arguments, list) or not all(isinstance(arg, str) for arg in arguments):
        raise ValueError("Action.args phải là một JSON array chỉ chứa chuỗi.")
    return "action", {"tool": tool_name, "args": arguments}


def execute_agent_action(action, action_history):
    """Validate and execute a registered tool without eval."""
    action_key = json.dumps(action, ensure_ascii=False, sort_keys=True)
    if action_key in action_history:
        return (
            f"LOI: Không được gọi lại '{action['tool']}' với cùng tham số. "
            "Hãy dùng Observation đã có để chọn bước khác hoặc trả Final Answer."
        )
    action_history.add(action_key)

    tool_name = action["tool"]
    tool = AVAILABLE_TOOLS.get(tool_name)
    if tool is None:
        valid_tools = ", ".join(AVAILABLE_TOOLS)
        return f"LOI: Tool '{tool_name}' không tồn tại. Tool hợp lệ: {valid_tools}."

    try:
        result = tool(*action["args"])
    except TypeError as error:
        return f"LOI: Tham số cho tool '{tool_name}' không hợp lệ: {error}."
    except Exception as error:
        return f"LOI: Tool '{tool_name}' thất bại an toàn: {error}."
    return str(result)


def safety_response_for(user_query: str):
    """Apply a deterministic safety gate that is not part of AVAILABLE_TOOLS."""
    risk_level = assess_risk_level(user_query)
    if risk_level == "HIGH":
        return risk_level, CRISIS_FALLBACK_RESPONSE
    if risk_level == "MEDIUM":
        return risk_level, MEDIUM_RISK_RESPONSE
    return risk_level, None


def _ensure_disclaimer(answer: str) -> str:
    if DISCLAIMER.casefold() in answer.casefold():
        return answer
    return f"{answer.rstrip()}\n\n{DISCLAIMER}"


def run_react_agent(user_query: str, provider):
    """Run a bounded Thought -> Action -> Observation loop."""
    risk_level, safety_response = safety_response_for(user_query)
    if safety_response:
        label = "HỖ TRỢ KHẨN CẤP" if risk_level == "HIGH" else "HỖ TRỢ GIẢM TẢI"
        print(f"\n🛟 [{label}]\n{safety_response}")
        return safety_response

    print(f"\n🧠 [REACT AGENT] Câu hỏi: {user_query}")
    transcript = f"Question: {user_query}"
    action_history = set()

    for step in range(1, MAX_ITERATIONS + 1):
        print(f"\n--- ReAct Step {step}/{MAX_ITERATIONS} ---")
        response = provider.generate(transcript, system_prompt=REACT_SYSTEM_PROMPT)
        print(response)

        try:
            response_type, payload = parse_agent_response(response)
        except ValueError as error:
            observation = f"LOI_PARSER: {error}"
        else:
            if response_type == "final":
                final_answer = _ensure_disclaimer(payload)
                print(f"\n🏁 Final Answer:\n{final_answer}")
                return final_answer
            observation = execute_agent_action(payload, action_history)

        print(f"👁️ Observation: {observation}")
        transcript += f"\n\nAssistant:\n{response}\nObservation: {observation}"

    print(f"\n🛡️ GUARDRAIL: Đã đạt MAX_ITERATIONS={MAX_ITERATIONS}.")
    print(f"🏁 Safe Fallback:\n{AGENT_FALLBACK_RESPONSE}")
    return AGENT_FALLBACK_RESPONSE


def run_direct_chat(provider, mode: str):
    """Run a terminal chat loop in baseline or agent mode."""
    label = "CHATBOT BASELINE" if mode == "baseline" else "REACT AGENT"
    print(f"\n--- CHAT TRỰC TIẾP: {label} ---")
    print("Nhập chia sẻ hoặc câu hỏi của bạn. Gõ 'exit' để kết thúc.")

    while True:
        try:
            user_query = input("\nBạn: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nKết thúc cuộc trò chuyện.")
            return

        if not user_query:
            continue
        if user_query.casefold() in {"exit", "quit", "thoat", "thoát"}:
            print("Kết thúc cuộc trò chuyện.")
            return

        if mode == "agent":
            run_react_agent(user_query, provider)
            continue

        risk_level, safety_response = safety_response_for(user_query)
        if safety_response:
            label = "HỖ TRỢ KHẨN CẤP" if risk_level == "HIGH" else "HỖ TRỢ GIẢM TẢI"
            print(f"\n🛟 [{label}]\n{safety_response}")
        else:
            run_baseline_chatbot(user_query, provider)


def run_comparison_tests(provider):
    """Compare baseline and agent on supported text-only test cases."""
    test_cases, skipped = get_supported_test_cases(load_test_cases())
    print(f"\n✅ Có {len(test_cases)} test case chạy được; {len(skipped)} case SKIPPED.")
    for case_id, reason in skipped:
        print(f"⏭️ SKIPPED {case_id}: {reason}")

    for case in test_cases:
        print("\n" + "=" * 60)
        print(f"TEST {case['case_id']} [{case['group']}]")
        print(f"Question: {case['user_query']}")
        risk_level, safety_response = safety_response_for(case["user_query"])
        if safety_response:
            print(f"Safety route: {risk_level}\n{safety_response}")
            continue

        run_baseline_chatbot(case["user_query"], provider)
        run_react_agent(case["user_query"], provider)


def parse_arguments():
    parser = argparse.ArgumentParser(description="Shadow Self chatbot and ReAct agent.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--baseline", action="store_true", help="Chat trực tiếp không dùng tool.")
    mode.add_argument("--tests", action="store_true", help="Chạy so sánh trên test cases hỗ trợ.")
    parser.add_argument(
        "--provider",
        choices=("gemini", "openai", "anthropic", "openrouter", "mock"),
        help="Ghi đè LLM_PROVIDER cho lần chạy này.",
    )
    return parser.parse_args()


def main():
    args = parse_arguments()
    provider = get_llm_provider(args.provider)
    model_name = getattr(provider, "model_name", "Offline Mock Mode")

    print("=" * 58)
    print("TRỢ LÝ TỰ PHẢN CHIẾU: CHATBOT VS REACT AGENT")
    print("=" * 58)
    print(f"Provider: {provider.__class__.__name__} (Model: {model_name})")

    if args.tests:
        run_comparison_tests(provider)
    else:
        run_direct_chat(provider, "baseline" if args.baseline else "agent")


if __name__ == "__main__":
    main()
