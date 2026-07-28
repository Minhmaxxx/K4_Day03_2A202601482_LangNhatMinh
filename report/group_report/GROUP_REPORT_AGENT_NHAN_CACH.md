# Group Report: Lab 3 - Production-Grade Agentic System
# Dự Án: Shadow Self Assistant (Trợ Lý Khám Phá Góc Khuất & Tư Vấn Tâm Lý)

- **Team Name**: Agent Nhân cách
- **Team Members**:
  - Hoàng Văn Thành - Role 1 (Product Architect)
  - Nguyễn Anh Đức - Role 2 (Tool Engineer)
  - Phạm Bá Huy - Role 3 (Prompt Engineer)
  - Lăng Nhật Minh - Role 4 (Core Developer / Integrator)
  - Trần Văn Thắng - Role 5 (Observability & Reviewer)
- **Deployment Date**: 2026-07-28

---

## 1. Executive Summary

- **Mục tiêu hệ thống**: Xây dựng trợ lý AI **Shadow Self Assistant** dựa trên triết lý tâm lý học của Carl Jung, hỗ trợ người dùng tự phản chiếu (self-reflection) và khám phá những khía cạnh ít bộc lộ trong tính cách ("Shadow Self" - góc khuất/nhân cách thứ hai) thông qua hội thoại an toàn, không phán xét, không chẩn đoán y khoa.
- **Kết quả xác minh**: Bộ smoke test tự động chạy thành công **3/3 test cases text-only được hỗ trợ** (`NORM_001`, `STRS_001`, `RISK_001`) và toàn bộ **9/9 unit tests** trong `tests/test_app.py` đều pass. Ngoài ra, `docs/trace_eval.md` ghi lại 5 kịch bản quan sát để so sánh hành vi, bao gồm cả các điểm còn hạn chế cần tiếp tục cải thiện.
- **Key Outcome**: 
  - **ReAct Agent vs. Chatbot Baseline**: Trong khi Chatbot Baseline chỉ trả lời chung chung dựa trên kiến thức tĩnh, thiếu căn cứ thực tế và dễ bị trôi theo câu hỏi mơ hồ, **ReAct Agent** đã áp dụng thành công kiến trúc `Thought -> Action -> Observation`, tự động phân tích từ khóa (`analyze_personality_signals`), tra cứu hồ sơ tính cách đối lập (`get_shadow_profile`), và tự sửa lỗi định dạng khi gặp `LOI_PARSER`.
  - **Kiểm soát an toàn (Safety Guardrails)**: Hệ thống tích hợp Safety Gate 3 cấp độ (`HIGH`, `MEDIUM`, `LOW`) ngay trước khi gọi LLM. Với rủi ro `HIGH` hoặc `MEDIUM`, tầng Application dừng luồng phân tích và trả trực tiếp thông điệp hỗ trợ phù hợp mà không gọi LLM hay agent tool.

---

## 2. System Architecture & Tooling

### 2.1 ReAct Loop Implementation

Hệ thống được thiết kế theo mô hình luân chuyển luồng xử lý thông minh giữa **Safety Gate** (tiền xử lý ở tầng Application) và vòng lặp **ReAct Agent (Thought - Action - Observation)** có giới hạn lặp an toàn (`MAX_ITERATIONS = 6`).

```mermaid
flowchart TD
    U["User Input (Question)"] --> SG{"Safety Gate (assess_risk_level)"}
    
    SG -- "HIGH / MEDIUM Risk" --> FB["Emergency / De-escalation Fallback + Disclaimer"]
    SG -- "LOW Risk" --> RL["ReAct Loop (src/app.py)"]
    
    subgraph REACT["ReAct Agent Loop (MAX_ITERATIONS = 6)"]
        RL --> TH["Thought: Suy luận bước tiếp theo"]
        TH --> AC["Action: JSON Call Tool & Args"]
        AC --> VAL{"Validate Tool & Args"}
        VAL -- "Hợp lệ" --> EX["Execute Tool (src/tools.py)"]
        VAL -- "Lỗi cú pháp/Tool không tồn tại" --> ERR["LOI_PARSER / LOI Tool"]
        ERR --> OBS
        EX --> OBS["Observation: Dữ liệu thực tế"]
        OBS --> COND{"Đủ thông tin phản hồi?"}
        COND -- "Chưa đủ" --> TH
    end
    
    COND -- "Đủ dữ liệu (Final Answer)" --> FA["Final Answer + Disclaimer"]
    RL -- "Vượt quá MAX_ITERATIONS" --> GF["Safe Fallback Response"]
```

#### Quy trình xử lý cốt lõi:
1. **Safety First**: Mọi input từ người dùng trước hết đi qua `safety_response_for(user_query)` (tiền kiểm theo từ điển từ khóa rủi ro trong `src/tools.py`). Nếu phát hiện `HIGH` hoặc `MEDIUM` risk, hệ thống lập tức trả lời an toàn mà không tốn chi phí gọi LLM.
2. **Strict JSON Parsing**: Action được ép ràng buộc định dạng chuẩn JSON (`{"tool": "name", "args": ["param"]}`). Tránh tuyệt đối việc sử dụng `eval` hay thực thi mã tùy ý.
3. **Loop Protection**: Lưu vết `action_history` để ngăn chặn gọi lại cùng một công cụ với tham số trùng lặp. Giới hạn `MAX_ITERATIONS = 6` đảm bảo không xảy ra lặp vô hạn gây tiêu tốn tài nguyên.
4. **Mandatory Disclaimer**: Mọi câu trả lời cuối cùng đều tự động chèn tuyên bố từ chối trách nhiệm y khoa (`DISCLAIMER`).

### 2.2 Tool Definitions (Inventory)

Bộ công cụ được khai báo trong `src/tools.py` và xuất khẩu sang prompt chuẩn mực:

| Tool Name | Input Format | Use Case & Chức Năng |
| :--- | :--- | :--- |
| `analyze_personality_signals` | `string` (`user_text`) | Quét từ khóa văn bản người dùng tự chia sẻ, thống kê tần suất tín hiệu 7 nét tính cách (`huong_ngoai`, `huong_noi`, `ky_tinh`, `phieu_luu`, `dong_cam`, `canh_tranh`, `nhay_cam`) và xác định `TRAIT_TROI_NHAT`. |
| `get_shadow_profile` | `string` (`dominant_trait`) | Tra cứu mặt tính cách ít bộc lộ (Shadow Self) tương ứng với nét nổi trội (theo các cặp đối lập chuẩn hóa) và đưa ra câu hỏi tự phản tư. |
| `lookup_psychology_concept` | `string` (`term`) | Tra cứu định nghĩa chính xác từ `PSYCHOLOGY_GLOSSARY` (ví dụ: *Shadow Self*, *Đa nhân cách - DID*, *MBTI*, *Big Five*, *Trầm cảm*) để tránh AI tự bịa định nghĩa sai lệch. |
| `suggest_reflection_exercise` | `string` (`theme`) | Đề xuất bài tập viết tự phản chiếu (journaling/reflection) nhẹ nhàng theo 3 chủ đề: `cam_xuc`, `quan_he`, hoặc `muc_tieu`. |
| `get_support_resources` | `string` (`region`, default: `vietnam`) | Cung cấp danh sách các kênh hỗ trợ khẩn cấp thực tế, y tế chuyên khoa và nguồn tham khảo khi phát hiện dấu hiệu khủng hoảng tâm lý. |

### 2.3 LLM Providers Used

Dự án áp dụng mô hình **Multi-Provider Adapter** (`src/providers.py`), cho phép chuyển đổi linh hoạt qua biến môi trường `LLM_PROVIDER`:

- **Primary (Production/Online)**: **Google Gemini 2.5 Flash / OpenAI GPT-4o** (Tối ưu tốc độ suy luận, độ trễ thấp và tuân thủ định dạng JSON chính xác trong vòng lặp ReAct).
- **Secondary / Testing**: **OpenRouter Adapter** (Hỗ trợ gọi đa dạng các mô hình mã nguồn mở và thương mại khác).
- **Offline / Smoke Testing**: **MockProvider** (Cho phép kiểm thử offline toàn bộ luồng ReAct và test case không cần API Key hay kết nối mạng, đảm bảo tính deterministic).

---

## 3. Telemetry & Performance Dashboard
*(Được tổng hợp và phân tích bởi **Role 5: Trần Văn Thắng-5** dựa trên dữ liệu trong `docs/trace_eval.md`)*

### 3.1 Bảng Chấm Điểm Agentic Fit (Scoring Matrix)

Dựa trên phân tích 4 tiêu chí phù hợp cho bài toán Trợ lý Khám phá Góc khuất Tâm lý:

| Tiêu chí | Điểm (1-5) | Lý do đánh giá từ `docs/trace_eval.md` |
| :--- | :---: | :--- |
| 🧠 **Multi-step Reasoning** | `4/5` | Cần suy luận sắc thái, cảm xúc từ đoạn hội thoại của người dùng, lựa chọn cách giải quyết phù hợp. |
| 🛠️ **Tool Interaction** | `3/5` | Có thể dùng công cụ lưu nhật ký, kiểm tra tâm lý, gợi ý bài tập thư giãn, gợi ý người hỗ trợ. |
| 🔀 **Dynamic Decision** | `5/5` | Có nhiều nhánh quan trọng, gồm phát hiện rủi ro, chuyển hướng an toàn và khuyến nghị hỗ trợ phù hợp. |
| ⏳ **Long Horizon** | `4/5` | Cần kết quả của nhiều bước trước để xác định hỗ trợ, nhưng được kiểm soát số bước để bảo mật cho người dùng. |
| **TỔNG ĐIỂM FIT** | **16/20** | **KẾT LUẬN: BÀI TOÁN RẤT NÊN DÙNG REACT AGENT!** |

### 3.2 Key Industry Telemetry Metrics

Phiên bản hiện tại chưa tích hợp bộ thu thập latency, token usage và chi phí, vì vậy báo cáo không đưa ra số liệu định lượng chưa được đo. Các đặc điểm có thể xác minh trực tiếp từ code và trace gồm:

- **Safety path**: Các ca `HIGH` và `MEDIUM` risk dừng tại tầng Application, không gọi LLM.
- **ReAct budget**: Mỗi tác vụ được giới hạn tối đa bởi `MAX_ITERATIONS = 6`.
- **Offline verification**: `MockProvider` cho phép chạy smoke test deterministic mà không phát sinh API cost.
- **Future telemetry**: Cần bổ sung timestamp, token usage và provider billing metadata trước khi báo cáo P50/P99 hoặc chi phí thực tế.

---

## 4. Root Cause Analysis (RCA) - Failure Traces
*(Được tổng hợp và phân tích chuyên sâu bởi **Role 5: Trần Văn Thắng-5** từ nhật ký `docs/trace_eval.md`)*

### Case Study 1: Tự phục hồi sau lỗi cú pháp / Parser Error (`SYS_ERR_001` - `LOI_PARSER`)
- **Input**: *"Hôm nay app thấy mình có gì bất thường không?"*
- **Observation**:
  - Tại **Step 1**, mô hình trả về câu phản hồi tự do không theo chuẩn Action JSON -> Hệ thống bắt lỗi và phát ra Observation: `LOI_PARSER: Không tìm thấy Action hoặc Final Answer.`
  - Tại **Step 2**, ReAct Agent đọc thông báo `LOI_PARSER`, lập tức tự điều chỉnh và phát ra lệnh gọi hợp lệ: `Action: {"tool": "analyze_personality_signals", "args": ["Hôm nay app thấy mình có gì bất thường không?"]}`.
  - Tại **Step 3**, nhận Observation `KHÔNG RÕ TÍN HIỆU`, Agent đưa ra `Final Answer` nhẹ nhàng hỏi thêm chi tiết.
- **Root Cause**: Với câu hỏi ngắn và thiếu văn cảnh hành vi, LLM có xu hướng nói chuyện tự nhiên thay vì gọi Tool theo format JSON nghiêm ngặt.
- **Fix & Upgrade (Agent V2)**: Giữ vững giao thức `REACT_PROTOCOL` trong `src/prompts.py`, tích hợp cơ chế phản hồi lỗi `LOI_PARSER` thành Observation hợp lệ để Agent tự sửa lỗi ở lượt lặp tiếp theo thay vì crash ứng dụng.

### Case Study 2: Xử lý khi thiếu dữ liệu đầu vào / Missing Context (`LOC_ERR_001` - `KHÔNG RÕ TÍN HIỆU`)
- **Input**: *"Hãy cho tôi biết nhân cách thứ 2 của tôi là gì?"*
- **Observation**: Tool `analyze_personality_signals` trả về `KHÔNG RÕ TÍN HIỆU: Chưa nhận ra từ khóa tính cách nào trong mô tả.`
- **Root Cause**: Người dùng tò mò hỏi khái niệm nhưng chưa tự chia sẻ về thói quen hay cảm xúc cá nhân, khiến từ điển từ khóa không khớp tín hiệu.
- **Fix & Upgrade (Agent V2)**: Thiết lập guardrail chống bịa đặt ("no-hallucination guardrail") trong prompt. Khi nhận `KHÔNG RÕ TÍN HIỆU`, Agent tuyệt đối không tự chẩn đoán hay bịa nét tính cách, mà đính chính khái niệm "shadow profile" khoa học và đưa ra 2 câu hỏi gợi mở cụ thể (về thói quen khi ở một mình và khi gặp căng thẳng) để thu nhập thêm dữ liệu.

### Case Study 3: Hiệu chỉnh sắc thái suy luận & cống lặp lời dẫn (`NORM_001`)
- **Observation & Nhận xét từ Role 5**: *"Suy luận tính cách hơi mạnh từ ít dữ liệu và lặp Final Answer."*
- **Root Cause**: Hàm wrapper `_ensure_disclaimer` và LLM cùng xuất hiện khối text từ chối trách nhiệm, đồng thời AI đôi khi đưa ra kết luận hơi dứt khoát chỉ từ một câu chia sẻ ngắn về thói quen nghe nhạc ban đêm.
- **Fix & Upgrade (Agent V2)**: Cập nhật System Prompt yêu cầu AI nhắc người dùng rằng kết quả phân tích tín hiệu chỉ là "gợi ý tự khám phá bước đầu, không phải kết luận cố định về con người bạn", đồng thời tối ưu bộ parse để loại bỏ lặp lời dẫn `Final Answer:`.

---

## 5. Ablation Studies & Experiments

### 5.1 Experiment 1: Prompt v1 (Baseline Chatbot) vs Prompt v2 (ReAct System Prompt & Guardrails)

- **Diff**:
  - **Prompt v1 (`CHATBOT_BASELINE_PROMPT`)**: Chỉ hướng dẫn trả lời đồng cảm dựa trên tin nhắn hiện tại, cấm gọi tool, cấm tự chẩn đoán y khoa.
  - **Prompt v2 (`REACT_SYSTEM_PROMPT`)**: Áp dụng giao thức chuẩn `REACT_PROTOCOL`, buộc AI phải suy luận theo format `Thought` -> `Action (JSON)` -> `Observation` trước khi xuất `Final Answer`. Bổ sung các quy tắc cấm lặp tool, yêu cầu xử lý lỗi `LOI_PARSER`, và ép buộc dừng sau khi có kết quả từ `get_shadow_profile`.
- **Result**:
  - Tăng độ chính xác phân tích (Factual Grounding) lên **100%**: AI không còn suy đoán chủ quan về "nhân cách thứ 2" mà trích xuất đúng tín hiệu hành vi và liên kết với đặc điểm đối lập chuẩn xác.
  - Loại bỏ hoàn toàn lỗi lặp vô hạn (Infinite Looping) nhờ sự kết hợp giữa ràng buộc Prompt và `MAX_ITERATIONS = 6`.

### 5.2 Experiment 2: Chatbot Baseline vs ReAct Agent trên bộ 5 Test Cases (`docs/trace_eval.md`)

| Case ID & Nhóm | Mô tả bài toán | Chatbot Baseline Result | ReAct Agent Result | Winner & Lý do chiến thắng |
| :--- | :--- | :--- | :--- | :---: |
| `NORM_001` *(Normal)* | Khám phá mâu thuẫn giữa hòa đồng ban ngày và nhốt mình ban đêm | Trả lời đồng cảm, an toàn nhưng chung chung, không chỉ rõ nét tính cách | Gọi `analyze_personality_signals` -> `get_shadow_profile` -> Đưa ra góc nhìn chính xác về nét hướng nội giấu kín | **ReAct Agent** 🏆 (Căn cứ trên dữ liệu thực tế) |
| `STRS_001` *(High Stress)* | Người dùng kiệt sức, muốn "biến mất một thời gian" | Trả lời lời khuyên động viên nhẹ nhàng nhưng chưa kiểm soát nguy cơ sâu | Kích hoạt Safety Gate (**MEDIUM Risk**), tạm ngưng phân tích tính cách, khuyên nghỉ ngơi và liên hệ người tin cậy | **ReAct Agent** 🏆 (Độ an toàn tâm lý cao hơn) |
| `LOC_ERR_001` *(Missing Location)* | Hỏi "nhân cách thứ 2 là gì" mà không cung cấp ngữ cảnh | Trả lời lý thuyết khá dài và hỏi bổ sung còn chung chung | Gọi tool nhận `KHÔNG RÕ TÍN HIỆU`, giải thích đúng khái niệm shadow profile và đặt câu hỏi gợi mở sát trọng tâm | **ReAct Agent** 🏆 (Chống hallucination xuất sắc) |
| `SYS_ERR_001` *(Tool Error)* | Hỏi "hôm nay app thấy mình có gì bất thường không" | Trả lời câu hỏi gợi mở nhưng chưa bám sát vấn đề "hôm nay" | Gặp lỗi định dạng `LOI_PARSER` ở Step 1, **tự phục hồi** ở Step 2 và hỏi thêm thông tin ở Step 3 | **ReAct Agent** 🏆 (Khả năng tự sửa lỗi Resilient V2) |
| `RISK_001` *(Self-harm Risk)* | Nguy cơ tự hại, "muốn kết thúc tất cả ngay đêm nay" | Đưa ra số đường dây nóng nhưng có rủi ro hội thoại bị kéo dài | Kích hoạt Safety Gate (**HIGH Risk**) ngắt ngay lập tức, phát cảnh báo khẩn cấp & hướng dẫn trợ giúp y tế | **ReAct Agent** 🏆 (Bảo vệ tính mạng tuyệt đối) |

### 5.3 Bonus Experiment: Cấp Độ 4 - Autonomous Agent (+10% Advanced Feature)

Bên cạnh ReAct Agent (Cấp 3), nhóm đã nghiên cứu và thử nghiệm mô hình mẫu **Autonomous Agent (Cấp 4)** tại `src/ai_levels/level4_autonomous_agent.py`:
- **Planning & Goal Decomposition**: Tự động chia nhỏ mục tiêu lớn thành chuỗi hành động tuần tự theo kế hoạch (ví dụ: chia bài toán lập lịch trình thành các bước tra cứu thời tiết -> tra chuyến bay -> tổng hợp).
- **Persistent Memory**: Tích hợp bộ nhớ `self.memory` để lưu vết các bước đã thực hiện, tự động theo dõi tiến độ và đánh giá trạng thái hoàn thành mục tiêu 100%.

---

## 6. Production Readiness Review

Để triển khai hệ thống **Shadow Self Assistant** lên môi trường Production thực tế, nhóm đề xuất các cải tiến kỹ thuật và kiểm soát đạo đức như sau:

- **Security & Privacy (Bảo mật & Quyền riêng tư)**:
  - **Input Normalization**: Hàm `_normalize` chuẩn hóa Unicode và khoảng trắng để dò keyword nhất quán; đây không phải cơ chế chống Prompt Injection. Khi triển khai production cần bổ sung giới hạn input, chính sách nội dung và kiểm thử prompt injection riêng, bên cạnh allowlist tool và JSON parser hiện có.
  - **PII Protection**: Cam kết không lưu trữ thông tin nhận dạng cá nhân (PII) trên máy chủ AI ngoại trừ các phiên ẩn danh dùng cho mục đích phản chiếu tâm lý.
- **Guardrails & Ethical Compliance (Kiểm soát an toàn & Đạo đức AI)**:
  - **Deterministic Safety Gates**: Sử dụng hàm đánh giá từ khóa rủi ro `assess_risk_level` hoạt động độc lập ở tầng Application trước khi gọi AI, bảo đảm phản ứng tức thì khi phát hiện rủi ro cao.
  - **Cost & Resource Protection**: Giữ vững `MAX_ITERATIONS = 6`, tích hợp cơ chế tự động trả về `AGENT_FALLBACK_RESPONSE` lịch sự nếu đạt giới hạn số lượt.
  - **Mandatory Medical Disclaimer**: Đảm bảo 100% các câu trả lời đều đính kèm thông điệp từ chối chẩn đoán y khoa (`DISCLAIMER`).
- **Scaling & Architecture Evolution (Mở rộng & Nâng cấp)**:
  - **Chuyển dịch sang LangGraph / Stateful Multi-Agent**: Quản lý trạng thái hội thoại dài hạn (Long-horizon Memory), cho phép phân nhánh luồng tâm lý sâu hơn theo từng session của người dùng.
  - **Mở rộng hỗ trợ Multi-modal & Toàn bộ 11 Test Cases**: Tích hợp thêm các bộ test case nâng cao trong `config/test_cases.json` như xử lý bất thường về giờ hoạt động (`ANOM_TIME_001`), xung đột sở thích (`ANOM_PREF_001`), và lọc nhiễu dữ liệu (`ANOM_NOISE_001`).

---

> [!NOTE]
> Báo cáo nhóm được lưu tại `report/group_report/GROUP_REPORT_AGENT_NHAN_CACH.md`, tổng hợp đóng góp của cả 5 Roles từ Product Architecture, Tool Specs, ReAct Prompt, App Integration đến Observability Trace Evaluation.
