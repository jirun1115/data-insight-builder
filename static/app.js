/**
 * app.js - AI Data Insight Builder Frontend Application
 * Vanilla JavaScript + Chart.js + Marked.js
 */

// =============================================================================
// 1. 전역 상태 관리 (State)
// =============================================================================
const AppState = {
    server: { status: "checking", model: "-", apiKeyConfigured: false },
    file: null,
    inspect: null,
    suggestions: [],
    selectedTool: null,
    runResult: null,
    explainMode: "B",
    explainResult: { A: null, B: null },
    chartInstance: null,
    originalQuery: ""
};

// 12종 도구 메타데이터 (프론트엔드 수동 선택용)
const TOOLS_METADATA = [
    { id: "reshape_wide_to_long", name: "1. 와이드-롱 구조 변환", requiresRef: false, defaultParams: {} },
    { id: "quality_report", name: "2. 데이터 종합 품질 진단", requiresRef: false, defaultParams: {} },
    { id: "year_coverage", name: "3. 연도별 데이터 충실도 분석", requiresRef: false, defaultParams: {} },
    { id: "detect_aggregates", name: "4. 집계 묶음 행 감지", requiresRef: false, defaultParams: {} },
    { id: "describe_numeric", name: "5. 기술 통계 요약 (평균/중앙값)", requiresRef: false, defaultParams: { reference_period: "" } },
    { id: "trend_line", name: "6. 선택 항목 시계열 추이 분석", requiresRef: false, defaultParams: { items: "Korea, Rep., United States, Japan, China", start_year: "1995", end_year: "2024" } },
    { id: "top_bottom_n", name: "7. 상위·하위 N 순위 분석", requiresRef: true, defaultParams: { reference_period: "2024", n: 10, exclude_aggregates: true } },
    { id: "group_summary", name: "8. 그룹/구간별 요약 집계", requiresRef: true, defaultParams: { reference_period: "2024", exclude_aggregates: true } },
    { id: "change_rate", name: "9. 두 시점 간 변화량·성장폭 분석", requiresRef: true, defaultParams: { start_period: "2014", reference_period: "2024", top_n: 10, exclude_aggregates: true } },
    { id: "distribution", name: "10. 구간별 빈도 분포 (히스토그램)", requiresRef: true, defaultParams: { reference_period: "2024", bins: 10, exclude_aggregates: true } },
    { id: "correlation_scatter", name: "11. 두 시점/변수 상관관계 분석", requiresRef: true, defaultParams: { column_x: "2014", reference_period: "2024", exclude_aggregates: true } },
    { id: "compare_one_vs_all", name: "12. 특정 대상 vs 전체 대푯값 비교", requiresRef: true, defaultParams: { reference_period: "2024", target_item: "Korea, Rep.", exclude_aggregates: true } }
];

// =============================================================================
// 2. 초기화 및 헬스 체크
// =============================================================================
document.addEventListener("DOMContentLoaded", () => {
    console.log("[PAGE] 페이지 로드 완료");
    initPinLockEvents();
    initHealthCheck();
    initUploadEvents();
    initManualCatalogDropdown();
    initNlQueryEvents();
    initSuggestionEvents();
    initToolRunEvents();
    initExplainEvents();
    initChatEvents();
    initDebugModalEvents();
});

// =============================================================================
// 0. 보안 PIN 잠금 관리 (Lock Screen)
// =============================================================================
let currentEnteredPin = "";

function initPinLockEvents() {
    const overlay = document.getElementById("pinLockOverlay");
    const dots = [document.getElementById("dot0"), document.getElementById("dot1"), document.getElementById("dot2"), document.getElementById("dot3")];
    const hiddenInput = document.getElementById("pinInputHidden");
    const errorMsg = document.getElementById("pinErrorMsg");
    const lockCard = document.querySelector(".pin-lock-card");
    const btnSubmit = document.getElementById("btnSubmitPin");
    const btnLogout = document.getElementById("btnLogout");

    checkAuthStatus();

    async function checkAuthStatus() {
        try {
            const res = await fetch("/api/auth-status");
            const data = await res.json();
            if (data.authenticated) {
                overlay.classList.add("unlocked");
            } else {
                overlay.classList.remove("unlocked");
                currentEnteredPin = "";
                updateDots();
                if (hiddenInput) hiddenInput.focus();
            }
        } catch (e) {
            console.warn("인증 상태 확인 실패:", e);
        }
    }

    function updateDots() {
        dots.forEach((dot, idx) => {
            if (dot) {
                if (idx < currentEnteredPin.length) {
                    dot.classList.add("filled");
                } else {
                    dot.classList.remove("filled");
                }
            }
        });
        if (hiddenInput) hiddenInput.value = currentEnteredPin;
        if (errorMsg) errorMsg.classList.add("hidden");
    }

    function addDigit(digit) {
        if (currentEnteredPin.length < 4) {
            currentEnteredPin += digit;
            updateDots();
            if (currentEnteredPin.length === 4) {
                setTimeout(submitPin, 150);
            }
        }
    }

    function removeDigit() {
        if (currentEnteredPin.length > 0) {
            currentEnteredPin = currentEnteredPin.slice(0, -1);
            updateDots();
        }
    }

    function clearPin() {
        currentEnteredPin = "";
        updateDots();
    }

    async function submitPin() {
        if (currentEnteredPin.length !== 4) {
            if (errorMsg) {
                errorMsg.textContent = "비밀번호 4자리를 모두 입력해 주세요.";
                errorMsg.classList.remove("hidden");
            }
            return;
        }

        try {
            const res = await fetch("/api/verify-pin", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ pin: currentEnteredPin })
            });
            const data = await res.json();

            if (res.ok && data.status === "success") {
                console.log("[AUTH] 보안 잠금 해제 성공");
                overlay.classList.add("unlocked");
                currentEnteredPin = "";
                updateDots();
            } else {
                console.warn("[AUTH] 비밀번호 불일치");
                if (errorMsg) {
                    errorMsg.textContent = data.message || "비밀번호가 올바르지 않습니다.";
                    errorMsg.classList.remove("hidden");
                }
                if (lockCard) {
                    lockCard.classList.add("shake");
                    setTimeout(() => lockCard.classList.remove("shake"), 400);
                }
                currentEnteredPin = "";
                updateDots();
            }
        } catch (err) {
            console.error("[AUTH] 인증 통신 오류:", err);
            if (errorMsg) {
                errorMsg.textContent = "서버 통신 오류가 발생했습니다.";
                errorMsg.classList.remove("hidden");
            }
        }
    }

    // 가상 키패드 이벤트
    document.querySelectorAll(".key-btn[data-val]").forEach(btn => {
        btn.addEventListener("click", () => {
            const val = btn.getAttribute("data-val");
            addDigit(val);
        });
    });

    const btnClear = document.getElementById("btnPinClear");
    if (btnClear) btnClear.addEventListener("click", clearPin);

    const btnBackspace = document.getElementById("btnPinBackspace");
    if (btnBackspace) btnBackspace.addEventListener("click", removeDigit);

    if (btnSubmit) btnSubmit.addEventListener("click", submitPin);

    // 물리 키보드 입력 지원
    document.addEventListener("keydown", (e) => {
        if (overlay && overlay.classList.contains("unlocked")) return;
        if (e.key >= "0" && e.key <= "9") {
            addDigit(e.key);
        } else if (e.key === "Backspace") {
            removeDigit();
        } else if (e.key === "Escape") {
            clearPin();
        } else if (e.key === "Enter") {
            submitPin();
        }
    });

    // 헤더 잠금/로그아웃 버튼
    if (btnLogout) {
        btnLogout.addEventListener("click", async () => {
            try {
                await fetch("/api/logout", { method: "POST" });
                overlay.classList.remove("unlocked");
                currentEnteredPin = "";
                updateDots();
                if (hiddenInput) hiddenInput.focus();
            } catch (err) {
                console.error("로그아웃 실패:", err);
            }
        });
    }
}

async function initHealthCheck() {
    const badge = document.getElementById("healthBadge");
    const text = document.getElementById("healthText");

    try {
        const res = await fetch("/health");
        const data = await res.json();
        AppState.server = {
            status: data.status,
            model: data.model,
            apiKeyConfigured: data.api_key_configured
        };

        if (data.status === "ok") {
            badge.className = "status-badge online";
            text.textContent = `온라인 (${data.model})`;
        } else {
            badge.className = "status-badge error";
            text.textContent = "서버 상태 이상";
        }
    } catch (err) {
        console.error("[ERROR] 헬스체크 실패:", err);
        badge.className = "status-badge error";
        text.textContent = "서버 오프라인";
    }
}

function initManualCatalogDropdown() {
    const select = document.getElementById("selectManualTool");
    TOOLS_METADATA.forEach(t => {
        const opt = document.createElement("option");
        opt.value = t.id;
        opt.textContent = t.name;
        select.appendChild(opt);
    });

    select.addEventListener("change", (e) => {
        const toolId = e.target.value;
        if (!toolId) return;
        const toolMeta = TOOLS_METADATA.find(t => t.id === toolId);
        if (toolMeta) {
            selectToolForRun(toolId, toolMeta.defaultParams, toolMeta.name, "사용자가 카탈로그에서 직접 선택한 분석입니다.");
        }
    });
}

// =============================================================================
// 3. 파일 업로드 및 정밀 진단 (/inspect)
// =============================================================================
function initUploadEvents() {
    const dropzone = document.getElementById("dropzone");
    const fileInput = document.getElementById("fileInput");
    const btnBrowse = document.getElementById("btnBrowseFile");

    btnBrowse.addEventListener("click", () => fileInput.click());
    dropzone.addEventListener("click", (e) => {
        if (e.target !== btnBrowse) fileInput.click();
    });

    ["dragenter", "dragover"].forEach(evt => {
        dropzone.addEventListener(evt, (e) => {
            e.preventDefault();
            e.stopPropagation();
            dropzone.classList.add("dragover");
        });
    });

    ["dragleave", "drop"].forEach(evt => {
        dropzone.addEventListener(evt, (e) => {
            e.preventDefault();
            e.stopPropagation();
            dropzone.classList.remove("dragover");
        });
    });

    dropzone.addEventListener("drop", (e) => {
        const files = e.dataTransfer.files;
        if (files && files.length > 0) handleFileSelection(files[0]);
    });

    fileInput.addEventListener("change", (e) => {
        if (e.target.files && e.target.files.length > 0) {
            handleFileSelection(e.target.files[0]);
        }
    });
}

function handleFileSelection(file) {
    if (!file.name.toLowerCase().endsWith(".csv")) {
        alert("CSV 파일(.csv)만 업로드할 수 있습니다.");
        console.error("[ERROR] 허용되지 않은 파일 형식:", file.name);
        return;
    }

    AppState.file = file;
    console.log(`[UPLOAD] 파일 선택 (파일명: ${file.name}, 크기: ${(file.size / 1024).toFixed(1)} KB)`);
    uploadAndInspectFile(file);
}

async function uploadAndInspectFile(file) {
    const loading = document.getElementById("inspectLoading");
    const resultsArea = document.getElementById("inspectResultArea");
    const suggestSection = document.getElementById("sectionSuggest");

    loading.classList.remove("hidden");
    resultsArea.classList.add("hidden");

    console.log("[INSPECT] 진단 요청 시작");

    const formData = new FormData();
    formData.append("file", file);

    try {
        const res = await fetch("/inspect", {
            method: "POST",
            body: formData
        });

        const data = await res.json();
        loading.classList.add("hidden");

        if (!res.ok) {
            throw new Error(data.error || "파일 진단 중 오류가 발생했습니다.");
        }

        AppState.inspect = data;
        console.log(`[INSPECT] 진단 응답 수신 (행 수: ${data.row_count}, 열 수: ${data.column_count}, 경고 수: ${data.warnings.length})`);

        renderInspectResults(data);
        resultsArea.classList.remove("hidden");
        suggestSection.classList.remove("hidden");

        if (data.recommended_year) {
            TOOLS_METADATA.forEach(t => {
                if (t.defaultParams && "reference_period" in t.defaultParams) {
                    t.defaultParams.reference_period = data.recommended_year;
                }
            });
        }
    } catch (err) {
        loading.classList.add("hidden");
        console.error("[ERROR] 진단 실패:", err.message);
        alert(`진단 오류: ${err.message}`);
    }
}

function renderInspectResults(data) {
    document.getElementById("resEncoding").textContent = data.encoding.toUpperCase();
    document.getElementById("resSkippedRows").textContent = `${data.skipped_rows}줄`;
    document.getElementById("resShape").textContent = `${data.row_count.toLocaleString()}행 × ${data.column_count}열`;
    document.getElementById("resRecYear").textContent = data.recommended_year ? `${data.recommended_year}년` : "해당없음";

    const warningBox = document.getElementById("warningBox");
    const warningList = document.getElementById("warningList");
    warningList.innerHTML = "";
    if (data.warnings && data.warnings.length > 0) {
        data.warnings.forEach(w => {
            const li = document.createElement("li");
            li.textContent = w;
            warningList.appendChild(li);
        });
        warningBox.classList.remove("hidden");
    } else {
        warningBox.classList.add("hidden");
    }

    const thead = document.getElementById("previewThead");
    const tbody = document.getElementById("previewTbody");
    thead.innerHTML = "";
    tbody.innerHTML = "";

    if (data.preview_rows && data.preview_rows.length > 0) {
        const cols = Object.keys(data.preview_rows[0]);
        const trHead = document.createElement("tr");
        cols.forEach(c => {
            const th = document.createElement("th");
            th.textContent = c;
            trHead.appendChild(th);
        });
        thead.appendChild(trHead);

        data.preview_rows.forEach(row => {
            const tr = document.createElement("tr");
            cols.forEach(c => {
                const td = document.createElement("td");
                const val = row[c];
                if (val === null || val === undefined) {
                    td.textContent = "null";
                    td.className = "null-cell";
                } else {
                    td.textContent = String(val);
                }
                tr.appendChild(td);
            });
            tbody.appendChild(tr);
        });
    }
}

// =============================================================================
// 3-1. 자연어 자유 질문 및 원클릭 시각화 (/ask)
// =============================================================================
function initNlQueryEvents() {
    const form = document.getElementById("nlQueryForm");
    const input = document.getElementById("nlQueryInput");
    const chips = document.querySelectorAll(".nl-chip-btn");

    chips.forEach(chip => {
        chip.addEventListener("click", () => {
            const query = chip.getAttribute("data-q");
            if (query) {
                input.value = query;
                handleNlQuery(query);
            }
        });
    });

    form.addEventListener("submit", (e) => {
        e.preventDefault();
        const query = input.value.trim();
        if (!query) {
            alert("질문 내용을 입력해 주세요.");
            return;
        }
        handleNlQuery(query);
    });
}

let latestNlAnalysis = null;

async function handleNlQuery(query) {
    if (!AppState.inspect) {
        alert("먼저 CSV 파일을 업로드하여 진단을 완료해 주세요.");
        return;
    }

    const loading = document.getElementById("nlQueryLoading");
    const resultCard = document.getElementById("nlQueryResultCard");
    const answerContent = document.getElementById("nlAnswerText");
    const matchedTag = document.getElementById("nlMatchedToolTag");
    const actionBtn = document.getElementById("btnNlExecuteVisual");
    const actionBtnText = document.getElementById("nlActionBtnText");

    loading.classList.remove("hidden");
    resultCard.classList.add("hidden");

    console.log(`[NL_QUERY] 자연어 질문 요청: ${query}`);
    AppState.originalQuery = query;

    try {
        const res = await fetch("/ask", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ query: query })
        });
        const data = await res.json();
        loading.classList.add("hidden");

        if (!res.ok) {
            throw new Error(data.error || "자연어 질문 처리 중 오류가 발생했습니다.");
        }

        latestNlAnalysis = data;
        console.log(`[NL_QUERY] 자연어 답변 수신 및 도구 매핑 완료: tool=${data.tool}`);

        // Markdown 서식 렌더링
        answerContent.innerHTML = typeof marked !== "undefined" ? marked.parse(data.answer) : data.answer;
        
        // 자연어 질문 모드에서는 기계적인 내부 도구 태그 대신 질문 기반 맞춤 안내 표시
        if (data.tool_name_kr) {
            matchedTag.textContent = `💡 맞춤 분석: ${data.tool_name_kr}`;
            matchedTag.classList.remove("hidden");
        } else {
            matchedTag.classList.add("hidden");
        }
        actionBtnText.textContent = data.button_label || "📊 이 분석 차트 및 결과 바로 보기";

        resultCard.classList.remove("hidden");

        // 시각화 실행 액션 버튼 바인딩: 중간 도구 설정창을 거치지 않고 질문에 맞춰 즉시 Pandas 시각화 직행
        actionBtn.onclick = () => {
            console.log(`[NL_QUERY] 시각화 버튼 클릭 -> 다이렉트 차트 실행: ${data.tool}`, data.params);
            
            AppState.originalQuery = query;
            AppState.selectedTool = {
                id: data.tool,
                params: data.params || {},
                title: data.tool_name_kr || "맞춤 분석",
                desc: data.answer || ""
            };

            // 자연어 직행 시에는 수동 파라미터 폼을 숨기고 질문 기반 타이틀 표시
            const paramForm = document.getElementById("toolParamForm");
            if (paramForm) paramForm.classList.add("hidden");

            const activeToolBanner = document.getElementById("activeToolBanner");
            if (activeToolBanner) {
                activeToolBanner.classList.remove("hidden");
                const badge = activeToolBanner.querySelector(".active-tool-badge");
                if (badge) badge.textContent = "자연어 맞춤 분석";
                document.getElementById("activeToolTitle").textContent = data.tool_name_kr || "질문 기반 맞춤 분석";
                document.getElementById("activeToolDesc").textContent = `질문 "${query}"의 의도에 맞춰 추출된 최적 조건으로 계산합니다.`;
            }

            const runSection = document.getElementById("sectionRun");
            runSection.classList.remove("hidden");

            executeToolRun(data.tool, data.params, true);
        };

    } catch (err) {
        loading.classList.add("hidden");
        console.error("[ERROR] 자연어 질문 처리 실패:", err.message);
        alert(`질문 오류: ${err.message}`);
    }
}

// =============================================================================
// 4. Gemini 맞춤 분석 제안 (/suggest)
// =============================================================================
function initSuggestionEvents() {
    const btnSuggest = document.getElementById("btnGetSuggestions");
    btnSuggest.addEventListener("click", fetchSuggestions);
}

async function fetchSuggestions() {
    const loading = document.getElementById("suggestLoading");
    const grid = document.getElementById("suggestionsGrid");

    loading.classList.remove("hidden");
    grid.innerHTML = "";

    console.log("[SUGGEST] 제안 요청 시작");

    try {
        const res = await fetch("/suggest", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ inspect_summary: AppState.inspect })
        });

        const data = await res.json();
        loading.classList.add("hidden");

        if (!res.ok) {
            throw new Error(data.error || "분석 제안 생성 중 오류가 발생했습니다.");
        }

        AppState.suggestions = data.suggestions || [];
        console.log(`[SUGGEST] 제안 응답 수신 (제안 개수: ${AppState.suggestions.length})`);

        renderSuggestions(AppState.suggestions);
    } catch (err) {
        loading.classList.add("hidden");
        console.error("[ERROR] 제안 요청 실패:", err.message);
        alert(`제안 오류: ${err.message}`);
    }
}

function renderSuggestions(suggestions) {
    const grid = document.getElementById("suggestionsGrid");
    grid.innerHTML = "";

    if (!suggestions || suggestions.length === 0) {
        grid.innerHTML = "<p class='text-muted'>생성된 제안이 없습니다. 수동 선택 메뉴를 이용해 주세요.</p>";
        return;
    }

    suggestions.forEach((s) => {
        const card = document.createElement("div");
        card.className = "suggestion-card";

        card.innerHTML = `
            <div>
                <span class="card-tool-tag">${s.tool}</span>
                <h4 class="suggestion-title">${s.tool_name_kr || s.tool}</h4>
                <p class="suggestion-why">${s.why}</p>
                <div class="suggestion-caution"><strong>주의:</strong> ${s.caution}</div>
            </div>
            <button class="btn btn-secondary btn-sm" style="align-self: flex-start; margin-top: 8px;">
                선택하여 분석 실행
            </button>
        `;

        card.addEventListener("click", () => {
            document.querySelectorAll(".suggestion-card").forEach(c => c.classList.remove("selected"));
            card.classList.add("selected");
            selectToolForRun(s.tool, s.params, s.tool_name_kr, s.why);
        });

        grid.appendChild(card);
    });
}

function selectToolForRun(toolId, params, toolTitle, toolDesc) {
    AppState.selectedTool = {
        id: toolId,
        params: params || {},
        title: toolTitle || toolId,
        desc: toolDesc || ""
    };

    // 카탈로그나 AI 추천으로 수동 선택한 경우 해당 주제를 원래 질문으로 갱신
    AppState.originalQuery = toolDesc || toolTitle || toolId;

    const runSection = document.getElementById("sectionRun");
    runSection.classList.remove("hidden");

    // 수동 선택 시에는 파라미터 입력 폼과 표준 배너 표시
    const paramForm = document.getElementById("toolParamForm");
    if (paramForm) paramForm.classList.remove("hidden");

    const activeToolBanner = document.getElementById("activeToolBanner");
    if (activeToolBanner) {
        activeToolBanner.classList.remove("hidden");
        const badge = activeToolBanner.querySelector(".active-tool-badge");
        if (badge) badge.textContent = "선택된 도구";
    }

    document.getElementById("activeToolTitle").textContent = toolTitle || toolId;
    document.getElementById("activeToolDesc").textContent = toolDesc || "";

    renderDynamicParamsForm(toolId, params);
    runSection.scrollIntoView({ behavior: "smooth" });
}

// =============================================================================
// 5. 도구 파라미터 동적 폼 & 실행 (/run)
// =============================================================================
function renderDynamicParamsForm(toolId, initialParams) {
    const container = document.getElementById("paramsContainer");
    container.innerHTML = "";

    const params = Object.assign({}, initialParams || {});
    const toolMeta = TOOLS_METADATA.find(t => t.id === toolId);
    const recYear = (AppState.inspect && AppState.inspect.recommended_year) ? AppState.inspect.recommended_year : "2024";

    if (toolMeta && toolMeta.requiresRef) {
        const val = params.reference_period || recYear;
        container.appendChild(createFormGroup("reference_period", "기준 시점 (연도)", "text", val, "예: 2024"));
    } else if (["describe_numeric", "trend_line"].includes(toolId)) {
        if (toolId === "describe_numeric") {
            const val = params.reference_period || params.column || recYear;
            container.appendChild(createFormGroup("reference_period", "분석 대상 연도/열", "text", val, "예: 2024"));
        }
    }

    if (toolId === "top_bottom_n") {
        container.appendChild(createFormGroup("n", "표시할 개수 (N)", "number", params.n || 10));
        container.appendChild(createFormGroup("target_item", "함께 비교할 국가명 (선택)", "text", params.target_item || "", "예: Korea, Rep."));
    } else if (toolId === "change_rate") {
        container.appendChild(createFormGroup("start_period", "비교 시작 연도", "text", params.start_period || "2014"));
        container.appendChild(createFormGroup("top_n", "상위 성장국 개수", "number", params.top_n || 10));
        container.appendChild(createFormGroup("target_item", "함께 비교할 국가명 (선택)", "text", params.target_item || "Korea, Rep.", "예: Korea, Rep."));
    } else if (toolId === "trend_line") {
        container.appendChild(createFormGroup("items", "비교 국가 목록 (쉼표 구분)", "text", params.items || "Korea, Rep., United States, Japan, China"));
        container.appendChild(createFormGroup("start_year", "시작 연도", "text", params.start_year || "1995"));
        container.appendChild(createFormGroup("end_year", "종료 연도", "text", params.end_year || recYear));
    } else if (toolId === "compare_one_vs_all") {
        container.appendChild(createFormGroup("target_item", "비교 대상 국가명", "text", params.target_item || "Korea, Rep."));
    } else if (toolId === "correlation_scatter") {
        container.appendChild(createFormGroup("column_x", "X축 비교 연도", "text", params.column_x || "2014"));
    } else if (toolId === "distribution") {
        container.appendChild(createFormGroup("bins", "구간 분할 수 (Bins)", "number", params.bins || 10));
    }

    if (toolMeta && (toolMeta.requiresRef || ["describe_numeric"].includes(toolId))) {
        const fg = document.createElement("div");
        fg.className = "form-group";
        fg.style.gridColumn = "1 / -1";
        fg.innerHTML = `
            <label style="display: flex; align-items: center; gap: 8px; cursor: pointer;">
                <input type="checkbox" id="param_exclude_aggregates" name="exclude_aggregates" ${params.exclude_aggregates !== false ? "checked" : ""}>
                <span>집계 행(World, 소득 그룹 등 43개) 제외하고 순수 국가만 분석 (권장)</span>
            </label>
        `;
        container.appendChild(fg);
    }
}

function createFormGroup(name, labelText, type = "text", defaultValue = "", placeholder = "") {
    const fg = document.createElement("div");
    fg.className = "form-group";
    fg.innerHTML = `
        <label for="param_${name}">${labelText}</label>
        <input type="${type}" id="param_${name}" name="${name}" class="form-input" value="${defaultValue}" placeholder="${placeholder}">
    `;
    return fg;
}

function initToolRunEvents() {
    const form = document.getElementById("toolParamForm");
    form.addEventListener("submit", async (e) => {
        e.preventDefault();
        if (!AppState.selectedTool) {
            alert("선택된 도구가 없습니다.");
            return;
        }

        const formData = new FormData(form);
        const params = {};
        for (let [k, v] of formData.entries()) {
            if (k === "exclude_aggregates") {
                params[k] = true;
            } else if (k === "n" || k === "top_n" || k === "bins") {
                params[k] = parseInt(v, 10);
            } else if (k === "items") {
                params[k] = v.split(",").map(s => s.trim()).filter(Boolean);
            } else {
                params[k] = v;
            }
        }
        if (params.target_item !== undefined && !params.target_item.trim()) {
            delete params.target_item;
        }
        if (!formData.has("exclude_aggregates") && document.getElementById("param_exclude_aggregates")) {
            params.exclude_aggregates = false;
        }

        executeToolRun(AppState.selectedTool.id, params);
    });
}

async function executeToolRun(toolId, params, isFromNl = false) {
    const loading = document.getElementById("runLoading");
    const resultsArea = document.getElementById("runResultArea");
    const explainSection = document.getElementById("sectionExplain");

    loading.classList.remove("hidden");
    resultsArea.classList.add("hidden");

    console.log(`[RUN] 도구 실행 시작 (도구명: ${toolId}, 기준시점: ${params.reference_period || "N/A"}, isFromNl: ${isFromNl})`);

    try {
        const res = await fetch("/run", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ tool: toolId, params: params })
        });

        const data = await res.json();
        loading.classList.add("hidden");

        if (!res.ok) {
            throw new Error(data.error || "도구 실행 중 오류가 발생했습니다.");
        }

        AppState.runResult = data.data;
        console.log(`[RUN] 도구 실행 결과 수신 (사용한 행: ${data.data.used_rows_count}, 제외한 행: ${data.data.excluded_rows_count})`);

        renderRunResults(data.data);
        resultsArea.classList.remove("hidden");
        explainSection.classList.remove("hidden");

        document.getElementById("explainOutputArea").classList.add("hidden");
        document.getElementById("chatSectionArea").classList.add("hidden");
        AppState.explainResult = { A: null, B: null };

        const chatThread = document.getElementById("chatThread");
        if (chatThread) {
            chatThread.innerHTML = "";
        }
        const chatInput = document.getElementById("chatInput");
        if (chatInput) {
            chatInput.value = "";
        }

        if (isFromNl) {
            // 자연어 질문 직행 시: 시각화 차트 결과 영역으로 바로 포커스
            resultsArea.scrollIntoView({ behavior: "smooth" });
        } else {
            explainSection.scrollIntoView({ behavior: "smooth" });
        }
    } catch (err) {
        loading.classList.add("hidden");
        console.error("[ERROR] 도구 실행 실패:", err.message);
        alert(`실행 오류: ${err.message}`);
    }
}

function renderRunResults(runData) {
    document.getElementById("metaRefPeriod").textContent = runData.reference_period || "전체";
    document.getElementById("metaUsedRows").textContent = `${runData.used_rows_count.toLocaleString()}개`;
    document.getElementById("metaExcludedRows").textContent = `${runData.excluded_rows_count.toLocaleString()}개`;
    document.getElementById("metaExclusionReason").textContent = runData.exclusion_reason || "없음";
    document.getElementById("toolCautionText").textContent = runData.caution || "-";

    renderChart(runData.chart_data);
    renderCalcSummary(runData);
}

function renderChart(chartData) {
    const canvas = document.getElementById("resultChart");
    if (!canvas) return;

    // 1. Chart.js 내장 getChart로 캔버스에 등록된 기존 인스턴스 완전 파괴 (재사용 오류 방지)
    if (typeof Chart !== "undefined" && typeof Chart.getChart === "function") {
        const existingChart = Chart.getChart(canvas);
        if (existingChart) {
            existingChart.destroy();
        }
    }

    // 2. 전역 상태에 보관된 인스턴스 파괴
    if (AppState.chartInstance) {
        try {
            AppState.chartInstance.destroy();
        } catch (e) {
            console.warn("이전 차트 인스턴스 해제 중 경고:", e);
        }
        AppState.chartInstance = null;
    }

    if (!chartData) {
        canvas.style.display = "none";
        return;
    }

    canvas.style.display = "block";
    const ctx = canvas.getContext("2d");

    // 수치 분석 (돋보기 확대 필요 여부 계산)
    const dataVals = (chartData.datasets && chartData.datasets[0] && Array.isArray(chartData.datasets[0].data)) ? chartData.datasets[0].data : [];
    const numVals = dataVals.filter(v => typeof v === 'number');
    const minVal = numVals.length > 0 ? Math.min(...numVals) : 0;
    const maxVal = numVals.length > 0 ? Math.max(...numVals) : 100;

    // 만약 최저값이 80 이상이고 최고값이 100 부근이면(순위 상위권 데이터), 차이를 선명하게 보도록 돋보기 확대 적용
    const canZoom = (chartData.type === 'bar' || !chartData.type) && chartData.indexAxis !== 'y' && minVal >= 80 && maxVal <= 100;
    let isZoomed = canZoom;
    const zoomedMin = Math.max(0, Math.floor(minVal - 2)); // 97.9%면 95%부터 시작

    const toolbar = document.getElementById("chartToolbar");
    const btnToggleZoom = document.getElementById("btnToggleZoom");
    const zoomDesc = document.getElementById("zoomDescText");

    if (toolbar && btnToggleZoom) {
        if (canZoom) {
            toolbar.classList.remove("hidden");
            btnToggleZoom.textContent = isZoomed ? `🔍 차이 돋보기 확대 모드 (${zoomedMin}%~100%)` : "🌐 전체 비율 모드 (0%~100%)";
            if (zoomDesc) {
                const diff = (maxVal - minVal).toFixed(2);
                zoomDesc.textContent = isZoomed ? `1위(${maxVal}%)와 ${minVal}% 간 ${diff}%p 격차가 계단식으로 선명하게 표시됩니다` : "0%부터 전체 높이를 객관적으로 표시 중입니다";
            }
        } else {
            toolbar.classList.add("hidden");
        }
    }

    // 막대 꼭대기에 수치(% 값) 상시 표기 플러그인
    const barDataLabelsPlugin = {
        id: 'barDataLabels',
        afterDatasetsDraw(chart) {
            if (chart.config.type !== 'bar' || (chart.config.options && chart.config.options.indexAxis === 'y')) return;
            const { ctx } = chart;
            chart.data.datasets.forEach((dataset, i) => {
                const meta = chart.getDatasetMeta(i);
                if (meta.hidden) return;
                meta.data.forEach((element, index) => {
                    const val = dataset.data[index];
                    if (val === undefined || val === null) return;
                    ctx.save();
                    ctx.textAlign = 'center';
                    ctx.textBaseline = 'bottom';
                    const isTarget = Array.isArray(dataset.backgroundColor) && dataset.backgroundColor[index] === '#ea580c';
                    ctx.font = isTarget ? 'bold 12px sans-serif' : 'bold 11px sans-serif';
                    ctx.fillStyle = isTarget ? '#c2410c' : '#1e3a8a';
                    ctx.fillText(val + '%', element.x, element.y - 4);
                    ctx.restore();
                });
            });
        }
    };

    const config = {
        type: chartData.type || "bar",
        data: {
            labels: chartData.labels,
            datasets: chartData.datasets
        },
        plugins: [barDataLabelsPlugin],
        options: {
            responsive: true,
            maintainAspectRatio: false,
            indexAxis: chartData.indexAxis || "x",
            layout: {
                padding: { top: 20 }
            },
            plugins: {
                legend: {
                    position: "top",
                    labels: { font: { family: "sans-serif", size: 12 } }
                },
                tooltip: {
                    callbacks: {
                        label: function (context) {
                            return `${context.dataset.label || ""}: ${context.raw}%`;
                        }
                    }
                }
            },
            scales: chartData.type === "doughnut" ? {} : {
                y: {
                    beginAtZero: !isZoomed,
                    min: isZoomed ? zoomedMin : 0,
                    max: 101.5,
                    grid: { color: "#f1f5f9" },
                    ticks: {
                        callback: function (val) {
                            return val + "%";
                        }
                    }
                },
                x: {
                    grid: { display: false },
                    ticks: {
                        maxRotation: 35,
                        minRotation: 0,
                        font: { size: 12, weight: "600" }
                    }
                }
            }
        }
    };

    AppState.chartInstance = new Chart(ctx, config);

    // 돋보기 버튼 클릭 시 즉시 줌 토글 연동
    if (btnToggleZoom) {
        btnToggleZoom.onclick = () => {
            isZoomed = !isZoomed;
            if (AppState.chartInstance && AppState.chartInstance.options && AppState.chartInstance.options.scales && AppState.chartInstance.options.scales.y) {
                AppState.chartInstance.options.scales.y.min = isZoomed ? zoomedMin : 0;
                AppState.chartInstance.options.scales.y.beginAtZero = !isZoomed;
                AppState.chartInstance.update();
            }
            btnToggleZoom.textContent = isZoomed ? `🔍 차이 돋보기 확대 모드 (${zoomedMin}%~100%)` : "🌐 전체 비율 모드 (0%~100%)";
            if (zoomDesc) {
                const diff = (maxVal - minVal).toFixed(2);
                zoomDesc.textContent = isZoomed ? `1위(${maxVal}%)와 ${minVal}% 간 ${diff}%p 격차가 계단식으로 선명하게 표시됩니다` : "0%부터 전체 높이를 객관적으로 표시 중입니다";
            }
        };
    }
    console.log(`[CHART] 차트 렌더 완료 (차트 종류: ${config.type}, 축: ${chartData.indexAxis || "x"})`);
}

function renderCalcSummary(runData) {
    const container = document.getElementById("calcSummaryContainer");
    container.innerHTML = "";

    const res = runData.result;
    if (!res) return;

    const displayList = res.displayed_items || (res.order === "bottom" ? res.bottom_n : res.top_n);
    if (displayList) {
        const orderText = res.order === "bottom" ? "최하위" : "상위";
        let titleSuffix = res.target_item_comparison ? ` 및 ${res.target_item_comparison.label_kr || res.target_item_comparison.label} 비교` : "";
        let html = `<h4>📊 산출 데이터 목록 (${orderText} ${displayList.length}개${titleSuffix})</h4><div class='table-responsive'><table class='data-table'><thead><tr><th style='width: 90px;'>순위</th><th>국가명 (한국어 / 원문)</th><th style='width: 120px;'>이용률</th></tr></thead><tbody>`;
        displayList.forEach(item => {
            const isTarget = res.target_item_comparison && res.target_item_comparison.rank === item.rank;
            const rowStyle = isTarget ? "style='background: #fff7ed; font-weight: 700; color: #c2410c;'" : "";
            const star = isTarget ? "⭐ " : "";
            const krName = item.label_kr ? `<strong>${item.label_kr}</strong> <span style='color: #64748b; font-size: 0.85em;'>(${item.label})</span>` : item.label;
            html += `<tr ${rowStyle}><td><strong>${star}${item.rank}위</strong></td><td>${krName}${isTarget ? " (비교 대상)" : ""}</td><td><strong>${item.value}${runData.unit || "%"}</strong></td></tr>`;
        });
        if (res.target_item_comparison && !displayList.some(item => item.rank === res.target_item_comparison.rank)) {
            const t = res.target_item_comparison;
            const tKrName = t.label_kr ? `<strong>${t.label_kr}</strong> <span style='color: #64748b; font-size: 0.85em;'>(${t.label})</span>` : t.label;
            html += `<tr style="background: #fff7ed; font-weight: 700; color: #c2410c; border-top: 2px dashed #f97316;">
                <td>⭐ <strong>${t.rank}위</strong></td>
                <td>🇰🇷 ${tKrName} <span style="background: #ea580c; color: #fff; padding: 2px 8px; border-radius: 9999px; font-size: 0.75em; margin-left: 6px; font-weight: 600;">비교 대상</span></td>
                <td><strong style="color: #ea580c; font-size: 1.05em;">${t.value}${runData.unit || "%"}</strong></td>
            </tr>`;
        }
        html += "</tbody></table></div>";
        container.innerHTML = html;
    } else if (res.top_gainers) {
        let titleSuffix = res.target_item_comparison ? ` 및 ${res.target_item_comparison.label_kr || res.target_item_comparison.label} 비교` : "";
        let html = `<h4>📊 10년간 성장폭 상위 국가 목록 (${res.start_period}~${res.end_period}년${titleSuffix})</h4><div class='table-responsive'><table class='data-table'><thead><tr><th style='width: 90px;'>성장 순위</th><th>국가명 (한국어 / 원문)</th><th>${res.start_period}년 수치</th><th>${res.end_period}년 수치</th><th style='width: 140px;'>10년간 증가폭</th></tr></thead><tbody>`;
        res.top_gainers.forEach(item => {
            const isTarget = res.target_item_comparison && res.target_item_comparison.rank === item.rank;
            const rowStyle = isTarget ? "style='background: #fff7ed; font-weight: 700; color: #c2410c;'" : "";
            const star = isTarget ? "⭐ " : "";
            const krName = item.label_kr ? `<strong>${item.label_kr}</strong> <span style='color: #64748b; font-size: 0.85em;'>(${item.label})</span>` : item.label;
            html += `<tr ${rowStyle}><td><strong>${star}${item.rank}위</strong></td><td>${krName}${isTarget ? " (비교 대상)" : ""}</td><td>${item.start_value}%</td><td>${item.end_value}%</td><td><strong style='color: #2563eb;'>+${item.change_diff}%p</strong></td></tr>`;
        });
        if (res.target_item_comparison && res.target_item_comparison.rank > res.top_gainers.length) {
            const t = res.target_item_comparison;
            const tKrName = t.label_kr ? `<strong>${t.label_kr}</strong> <span style='color: #64748b; font-size: 0.85em;'>(${t.label})</span>` : t.label;
            html += `<tr style="background: #fff7ed; font-weight: 700; color: #c2410c; border-top: 2px dashed #f97316;">
                <td>⭐ <strong>${t.rank}위</strong></td>
                <td>🇰🇷 ${tKrName} <span style="background: #ea580c; color: #fff; padding: 2px 8px; border-radius: 9999px; font-size: 0.75em; margin-left: 6px; font-weight: 600;">비교 대상</span></td>
                <td>${t.start_value}%</td>
                <td>${t.end_value}%</td>
                <td><strong style="color: #ea580c; font-size: 1.05em;">+${t.change_diff}%p</strong></td>
            </tr>`;
        }
        html += "</tbody></table></div>";
        container.innerHTML = html;
    } else if (res.mean !== undefined) {
        container.innerHTML = `
            <h4>📊 기술 통계 핵심 지표</h4>
            <div class='results-grid'>
                <div class='metric-card'><span class='metric-label'>평균값</span><span class='metric-value'>${res.mean}${runData.unit}</span></div>
                <div class='metric-card'><span class='metric-label'>중앙값</span><span class='metric-value highlight'>${res.median}${runData.unit}</span></div>
                <div class='metric-card'><span class='metric-label'>최소 / 최대</span><span class='metric-value'>${res.min} ~ ${res.max}</span></div>
                <div class='metric-card'><span class='metric-label'>표준편차</span><span class='metric-value'>${res.std}</span></div>
            </div>
        `;
    }
}

// =============================================================================
// 6. Gemini 해석 보고서 (모드 A vs 모드 B) (/explain)
// =============================================================================
function initExplainEvents() {
    const tabB = document.getElementById("tabModeB");
    const tabA = document.getElementById("tabModeA");
    const btnExplain = document.getElementById("btnGenerateExplain");

    tabB.addEventListener("click", () => switchExplainMode("B"));
    tabA.addEventListener("click", () => switchExplainMode("A"));
    btnExplain.addEventListener("click", generateExplanation);
}

function switchExplainMode(mode) {
    AppState.explainMode = mode;
    document.getElementById("tabModeB").classList.toggle("active", mode === "B");
    document.getElementById("tabModeA").classList.toggle("active", mode === "A");

    if (AppState.explainResult[mode]) {
        displayExplanation(AppState.explainResult[mode], mode);
    }
}

async function generateExplanation() {
    const mode = AppState.explainMode;
    const loading = document.getElementById("explainLoading");
    const outputArea = document.getElementById("explainOutputArea");
    const chatSection = document.getElementById("chatSectionArea");

    loading.classList.remove("hidden");
    outputArea.classList.add("hidden");

    console.log(`[EXPLAIN] 해석 요청 시작 (모드: ${mode})`);

    try {
        const res = await fetch("/explain", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                mode: mode,
                calculation_result: AppState.runResult,
                original_query: AppState.originalQuery || "",
                tool_name: AppState.selectedTool ? AppState.selectedTool.title : ""
            })
        });

        const data = await res.json();
        loading.classList.add("hidden");

        if (!res.ok) {
            throw new Error(data.error || "해석 보고서 생성 중 오류가 발생했습니다.");
        }

        AppState.explainResult[mode] = data.explanation;
        console.log(`[EXPLAIN] 해석 응답 수신 (모드: ${mode}, 글자수: ${data.explanation.length})`);

        displayExplanation(data.explanation, mode);
        // 보고서가 생성되면 하단 후속 Q&A 채팅창 표시
        chatSection.classList.remove("hidden");
    } catch (err) {
        loading.classList.add("hidden");
        console.error("[ERROR] 해석 생성 실패:", err.message);
        alert(`해석 오류: ${err.message}`);
    }
}

function displayExplanation(text, mode) {
    const outputArea = document.getElementById("explainOutputArea");
    const noticeBox = document.getElementById("modeNoticeBox");
    const markdownBody = document.getElementById("markdownReport");

    if (mode === "B") {
        noticeBox.className = "mode-notice mode-b";
        noticeBox.innerHTML = "✅ <strong>모드 B (정식 보고서):</strong> Pandas가 실제로 계산한 숫자와 출처, 기준 시점, 제외된 항목만을 근거로 작성된 검증된 보고서입니다.";
    } else {
        noticeBox.className = "mode-notice mode-a";
        noticeBox.innerHTML = "⚠️ <strong>모드 A (비교용 보고서):</strong> Pandas 계산 수치를 제공하지 않고 일반 AI에게 작성을 맡긴 보고서입니다. 모호한 표현이나 추측이 포함될 수 있습니다.";
    }

    if (window.marked && typeof marked.parse === "function") {
        markdownBody.innerHTML = marked.parse(text);
    } else {
        markdownBody.textContent = text;
    }

    outputArea.classList.remove("hidden");
    outputArea.scrollIntoView({ behavior: "smooth" });
}

// =============================================================================
// 7. 초보자 맞춤형 후속 질의응답 (Follow-up Chat)
// =============================================================================
function initChatEvents() {
    const chatForm = document.getElementById("chatForm");
    const chatInput = document.getElementById("chatInput");
    const chips = document.querySelectorAll(".chip-btn");

    // 말풍선 칩 클릭 시 즉시 질문 전송
    chips.forEach(chip => {
        chip.addEventListener("click", () => {
            const questionText = chip.getAttribute("data-q");
            if (questionText) {
                chatInput.value = questionText;
                submitChatQuestion(questionText);
            }
        });
    });

    chatForm.addEventListener("submit", (e) => {
        e.preventDefault();
        const text = chatInput.value.trim();
        if (!text) return;
        submitChatQuestion(text);
    });
}

async function submitChatQuestion(question) {
    const chatThread = document.getElementById("chatThread");
    const chatInput = document.getElementById("chatInput");
    const btnSend = document.getElementById("btnSendChat");

    // 1. 사용자 질문 말풍선 추가
    appendUserChatMessage(question);
    chatInput.value = "";
    btnSend.disabled = true;

    // 2. AI 생각 중 인디케이터 말풍선 추가
    const loadingBubble = appendAILoadingMessage();
    chatThread.scrollTop = chatThread.scrollHeight;

    console.log(`[CHAT] 후속 질문 요청 시작: "${question}"`);

    const currentReport = AppState.explainResult[AppState.explainMode] || "";
    const activeToolTitle = AppState.selectedTool ? (AppState.selectedTool.title || AppState.selectedTool.id) : "";

    try {
        const res = await fetch("/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                question: question,
                mode: AppState.explainMode,
                explanation_context: currentReport,
                calculation_result: AppState.runResult,
                original_query: AppState.originalQuery || "",
                tool_name: activeToolTitle
            })
        });

        const data = await res.json();
        btnSend.disabled = false;
        loadingBubble.remove();

        if (!res.ok) {
            throw new Error(data.error || "답변을 불러오지 못했습니다.");
        }

        console.log(`[CHAT] 후속 질문 답변 수신 완료 (글자수: ${data.answer.length})`);
        appendAIChatMessage(data.answer);
    } catch (err) {
        btnSend.disabled = false;
        loadingBubble.remove();
        console.error("[ERROR] 채팅 통신 오류:", err.message);
        appendAIChatMessage(`⚠️ **답변 생성 실패:** ${err.message}`);
    }

    chatThread.scrollTop = chatThread.scrollHeight;
}

function appendUserChatMessage(text) {
    const chatThread = document.getElementById("chatThread");
    const bubble = document.createElement("div");
    bubble.className = "chat-msg user";
    bubble.textContent = text;
    chatThread.appendChild(bubble);
}

function appendAILoadingMessage() {
    const chatThread = document.getElementById("chatThread");
    const bubble = document.createElement("div");
    bubble.className = "chat-msg ai";
    bubble.innerHTML = `
        <div class="ai-author">🤖 AI 과외 선생님</div>
        <div class="ai-content" style="color: var(--text-muted); font-style: italic;">
            초보자 눈높이에 맞춰 쉽게 답변을 정리하는 중입니다...
        </div>
    `;
    chatThread.appendChild(bubble);
    return bubble;
}

function appendAIChatMessage(markdownText) {
    const chatThread = document.getElementById("chatThread");
    const bubble = document.createElement("div");
    bubble.className = "chat-msg ai";

    let htmlContent = markdownText;
    if (window.marked && typeof marked.parse === "function") {
        htmlContent = marked.parse(markdownText);
    }

    bubble.innerHTML = `
        <div class="ai-author">🤖 AI 과외 선생님</div>
        <div class="ai-content">${htmlContent}</div>
    `;
    chatThread.appendChild(bubble);
}

// =============================================================================
// 8. 디버깅 상태 복사 모달 (오류 시 AI에게 현재 상태 전달)
// =============================================================================
function initDebugModalEvents() {
    const btnExport = document.getElementById("btnExportState");
    const modal = document.getElementById("debugModal");
    const btnClose = document.getElementById("btnCloseModal");
    const btnCopy = document.getElementById("btnCopyDebug");
    const textarea = document.getElementById("debugTextarea");

    btnExport.addEventListener("click", () => {
        const stateDump = {
            timestamp: new Date().toISOString(),
            server: AppState.server,
            file_meta: AppState.file ? { name: AppState.file.name, size: AppState.file.size } : null,
            inspect_summary: AppState.inspect ? {
                encoding: AppState.inspect.encoding,
                skipped_rows: AppState.inspect.skipped_rows,
                row_count: AppState.inspect.row_count,
                column_count: AppState.inspect.column_count,
                recommended_year: AppState.inspect.recommended_year,
                warnings: AppState.inspect.warnings
            } : null,
            selected_tool: AppState.selectedTool,
            latest_run_meta: AppState.runResult ? {
                tool: AppState.runResult.tool,
                reference_period: AppState.runResult.reference_period,
                used_rows: AppState.runResult.used_rows_count,
                excluded_rows: AppState.runResult.excluded_rows_count,
                exclusion_reason: AppState.runResult.exclusion_reason
            } : null,
            explain_mode: AppState.explainMode
        };

        const markdownText = "```json\n" + JSON.stringify(stateDump, null, 2) + "\n```";
        textarea.value = markdownText;
        modal.classList.remove("hidden");
    });

    btnClose.addEventListener("click", () => modal.classList.add("hidden"));
    modal.addEventListener("click", (e) => {
        if (e.target === modal) modal.classList.add("hidden");
    });

    btnCopy.addEventListener("click", () => {
        textarea.select();
        navigator.clipboard.writeText(textarea.value).then(() => {
            alert("클립보드에 복사되었습니다! AI 어시스턴트 대화창에 그대로 붙여넣으세요.");
            modal.classList.add("hidden");
        });
    });
}
