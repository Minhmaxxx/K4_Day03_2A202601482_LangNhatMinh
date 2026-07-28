# Individual Report: Lab 3 - Chatbot vs ReAct Agent

- **Student Name**: Lăng Nhật Minh
- **Student ID**: 2A202601482
- **Role**: Role 4 - Core Developer / Integrator
- **Date**: 2026-07-28

---

## I. Technical Contribution (15 Points)

### 1. Phạm vi đóng góp cá nhân

Vai trò của tôi là tích hợp các thành phần do các thành viên khác cung cấp thành một ứng dụng có thể chạy hoàn chỉnh. Tôi không nhận phần triển khai bộ test case của Role 1, thuật toán tool của Role 2, nội dung prompt của Role 3 hoặc báo cáo trace của Role 5.

| Hạng mục | Đóng góp của Role 4 |
| :--- | :--- |
| `src/app.py` | Kết nối provider, prompts, tool registry và test cases; triển khai chatbot baseline, ReAct loop, parser Action JSON, tool executor, guardrail số vòng lặp và CLI. |
| `src/web_app.py` | Tạo HTTP API cục bộ để giao diện gọi chung logic baseline/Agent, không sao chép business logic. |
| `web/index.html` | Tạo giao diện so sánh hai cửa sổ độc lập: Chatbot Baseline và ReAct Agent; hiển thị Action/Observation theo từng bước. |
| `tests/test_app.py` | Kiểm thử các contract tích hợp: baseline chỉ gọi provider một lần, parser không dùng `eval`, Agent bị giới hạn vòng lặp và high-risk input không tới provider. |

### 2. Core integration trong `src/app.py`

#### Chatbot baseline

Hàm `run_baseline_chatbot()` tại `src/app.py:90-99` giữ đúng đường cơ sở so sánh: gọi `provider.generate()` đúng một lần và không gọi agent tool. Kết quả được bổ sung disclaimer trước khi trả cho người dùng.

#### Parser và tool executor

Tôi triển khai `parse_agent_response()` tại `src/app.py:102-137` để chỉ chấp nhận Action dạng JSON:

```json
{"tool": "analyze_personality_signals", "args": ["nội dung người dùng"]}
```

Parser dùng `json.loads` thay vì `eval`. Sau khi parse, `execute_agent_action()` tại `src/app.py:140-162` kiểm tra tên tool qua allowlist `AVAILABLE_TOOLS`, chặn Action lặp và chuyển lỗi tool thành Observation thay vì làm ứng dụng crash.

#### ReAct loop

`run_react_agent()` tại `src/app.py:181-256` thực hiện luồng tích hợp:

```text
Question
  -> LLM sinh Thought + Action
  -> Application parse Action
  -> Application chạy tool thật
  -> Observation quay lại transcript
  -> LLM sinh bước tiếp theo hoặc Final Answer
```

Vòng lặp được chặn bởi `MAX_ITERATIONS`. Mỗi Observation được tạo từ kết quả thực thi của application, không lấy Observation do LLM tự viết. Hàm cũng thu thập trace có cấu trúc để giao diện hiển thị từng bước Agent.

#### CLI và test-case integration

Tôi tích hợp ba chế độ chạy trong `src/app.py`:

```bash
python3 src/app.py
python3 src/app.py --baseline
python3 src/app.py --tests --provider mock
```

Các test case ngoài phạm vi text-only vẫn được giữ trong JSON nhưng được đánh dấu `SKIPPED`, tránh làm hỏng smoke test của Phase 3.

### 3. Web integration

`src/web_app.py` cung cấp `/api/status`, `/api/chat` và `/api/compare`. Endpoint so sánh gọi cùng một input qua baseline và Agent, sau đó trả về:

```json
{
  "baseline": "...",
  "agent": "...",
  "steps": [
    {"step": 1, "tool": "...", "observation": "..."}
  ]
}
```

Giao diện `web/index.html` chia kết quả thành hai cửa sổ cuộn độc lập. Phía Agent có timeline Action/Observation để minh họa rõ sự khác biệt với chatbot thông thường.

### 4. Ranh giới với đóng góp của các Role khác

- Role 1 sở hữu nội dung `config/test_cases.json`; tôi chỉ viết loader và bộ lọc để ứng dụng sử dụng dữ liệu đó.
- Role 2 sở hữu các hàm trong `src/tools.py`; tôi chỉ đăng ký, validate, gọi tool và đưa kết quả vào Observation.
- Role 3 sở hữu nội dung `src/prompts.py`; tôi chỉ truyền prompt đúng cho baseline hoặc ReAct flow.
- Role 5 sở hữu `docs/trace_eval.md`; tôi cung cấp output và trace runtime để Role 5 đánh giá.

---

## II. Debugging Case Study (10 Points)

### Agent trả sai định dạng và không gọi tool

- **Problem Description**: Trong một số lượt Gemini trả câu tự do hoặc output trống thay vì `Action` JSON. Ở trường hợp khác, model gọi lặp lại cùng tool và tham số. Nếu application tin trực tiếp output này, Agent có thể crash hoặc lặp đến hết ngân sách.
- **Log Source**: `docs/trace_eval.md:172-194` ghi lại trường hợp `SYS_ERR_001`, trong đó Step 1 không có Action/Final Answer và application tạo `LOI_PARSER`.
- **Diagnosis**: LLM sinh text xác suất nên không phải lúc nào cũng tuân thủ protocol. Prompt chỉ là hướng dẫn; tầng application vẫn phải xem output của model là dữ liệu không tin cậy và validate trước khi thực thi.
- **Solution của Role 4**:
  - Parse Action bằng `json.loads` và từ chối Python literal.
  - Kiểm tra tool bằng allowlist `AVAILABLE_TOOLS`.
  - Lưu `action_history` để chặn cùng một Action bị gọi lặp.
  - Chuyển malformed Action thành `LOI_PARSER` Observation để model có cơ hội tự sửa.
  - Dừng bằng `MAX_ITERATIONS` và trả `AGENT_FALLBACK_RESPONSE` nếu không hoàn thành.

Đoạn xử lý cốt lõi trong `src/app.py`:

```python
try:
    response_type, payload = parse_agent_response(response)
except ValueError as error:
    observation = f"LOI_PARSER: {error}"
else:
    if response_type == "final":
        return _ensure_disclaimer(payload)
    observation = execute_agent_action(payload, action_history)
```

### Kết quả kiểm chứng

- `test_parser_accepts_strict_json_action`: Action JSON hợp lệ được parse đúng.
- `test_parser_rejects_python_literal`: cú pháp giống Python bị từ chối.
- `test_unknown_tool_is_an_observation_error`: tool không tồn tại không làm app crash.
- `test_max_iterations_returns_safe_fallback`: Agent dừng đúng ngân sách.
- `test_react_mock_uses_real_tool_observations`: trace chứa Observation thật từ hai tool.
- Toàn bộ 9 unit tests hiện tại đều pass.

---

## III. Personal Insights: Chatbot vs ReAct (10 Points)

### 1. Reasoning

Chatbot baseline phù hợp với câu hỏi đơn giản vì chỉ cần một lần gọi LLM và trả lời nhanh. Tuy nhiên, câu trả lời chủ yếu dựa vào suy luận nội tại của model. ReAct Agent buộc quá trình xử lý đi qua Action và Observation, nhờ đó tôi có thể nhìn thấy model đã chọn công cụ nào và dữ liệu nào thực sự được dùng trước Final Answer.

### 2. Reliability

Agent không phải lúc nào cũng tốt hơn chatbot. Với câu hỏi ngắn hoặc quá mơ hồ, Agent có thể tốn nhiều LLM call, chọn tool chưa cần thiết hoặc gặp lỗi định dạng. Baseline trong các trường hợp này nhanh và tự nhiên hơn. Vì vậy application cần parser, allowlist, repeated-action guard và giới hạn vòng lặp; không thể chỉ dựa vào system prompt.

### 3. Observation

Observation biến tool output thành dữ liệu phản hồi cho bước tiếp theo. Ví dụ, sau khi `analyze_personality_signals` trả `TRAIT_TROI_NHAT`, Agent mới có căn cứ gọi `get_shadow_profile`. Nếu tool trả `KHÔNG RÕ TÍN HIỆU`, Agent phải hỏi thêm thay vì tự bịa kết luận. Đây là điểm khác biệt kỹ thuật quan trọng nhất so với chatbot baseline.

---

## IV. Future Improvements (5 Points)

- **Scalability**: Chuyển web server sang xử lý bất đồng bộ để baseline và Agent có thể chạy song song thay vì tuần tự; thêm timeout riêng cho từng provider call.
- **Streaming UX**: Dùng Server-Sent Events hoặc WebSocket để hiển thị từng Action/Observation ngay khi Agent thực thi, thay vì chờ toàn bộ response hoàn thành.
- **Structured Output**: Sử dụng native structured output/tool calling của provider khi có thể, nhưng vẫn giữ application-side validation và allowlist.
- **Observability**: Ghi latency, số LLM call, tool call và trạng thái kết thúc dưới dạng telemetry không chứa nội dung nhạy cảm.
- **Privacy**: Thêm consent rõ ràng trước khi gửi nội dung tới provider ngoài, giới hạn dữ liệu đầu vào và không lưu lịch sử mặc định.
- **Testing**: Bổ sung integration tests cho HTTP API, malformed request, timeout provider và concurrent requests.

---

> Báo cáo này chỉ mô tả phần đóng góp của **Role 4 - Core Developer / Integrator**. Các phần Product Architecture, Tool Design, Prompt Engineering và Observability thuộc đóng góp của các thành viên tương ứng.
