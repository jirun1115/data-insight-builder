import os
import re
import io
import csv
import json
import logging
import traceback
import math
import numpy as np
import pandas as pd
from datetime import timedelta
from flask import Flask, request, jsonify, render_template, session, send_from_directory
from dotenv import load_dotenv

# Import tool functions and catalog
from tools import TOOL_CATALOG

# Load environment variables
load_dotenv()

# Configure logging (Never use print; use Python logging)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("data-insight-builder")

app = Flask(__name__)

# Security: Session secret key and 4-digit PIN configuration
app.secret_key = os.getenv("FLASK_SECRET_KEY", "data-insight-builder-security-key-2026-secret")
APP_PIN = os.getenv("APP_PIN", "0110").strip()

# 24시간 세션 만료 설정 (24시간 경과 시 자동 로그아웃 및 잠금 해제 필요)
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=24)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

# Configurations from environment variables
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "10"))
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

# Memory storage for currently active dataset (single session/file)
CURRENT_DATASET = {
    "df": None,
    "filename": None,
    "file_size": 0,
    "encoding": None,
    "skipped_rows": 0,
    "raw_text": None,
    "inspect_result": None,
    "latest_run_result": None,
    "latest_explanation": None
}

def detect_header_skip(text_content):
    sample_stream = io.StringIO(text_content)
    reader = csv.reader(sample_stream)
    parsed_rows = []
    
    for i, row in enumerate(reader):
        if i >= 30:
            break
        cleaned = [cell.strip() for cell in row if cell.strip() != ""]
        parsed_rows.append(cleaned)
    
    if not parsed_rows:
        return 0
    
    max_cols = max(len(r) for r in parsed_rows)
    if max_cols <= 1:
        return 0
    
    threshold = max(2, int(max_cols * 0.7))
    for idx, row in enumerate(parsed_rows):
        if len(row) >= threshold:
            return idx
    return 0

def sanitize_for_json(obj):
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    elif isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_for_json(item) for item in obj]
    elif pd.isna(obj):
        return None
    return obj

@app.route("/")
@app.route("/api/index")
def index():
    logger.info("[REQUEST] GET /")
    try:
        return render_template("index.html")
    except Exception:
        return "AI Data Insight Builder Server Running"

@app.route("/health", methods=["GET"])
def health():
    logger.info("[REQUEST] GET /health")
    has_api_key = bool(GEMINI_API_KEY)
    response_data = {
        "status": "ok",
        "api_key_configured": has_api_key,
        "model": GEMINI_MODEL
    }
    logger.info(f"[RESPONSE] status=ok api_key_configured={has_api_key} model={GEMINI_MODEL}")
    return jsonify(response_data)

@app.route("/manifest.json", methods=["GET"])
def manifest():
    return send_from_directory("static", "manifest.json", mimetype="application/manifest+json")

@app.route("/sw.js", methods=["GET"])
def service_worker():
    return send_from_directory("static", "sw.js", mimetype="application/javascript")

# ==============================================================================
# Security: 4-digit PIN Authentication & Session Management
# ==============================================================================
@app.before_request
def check_authentication():
    exempt_prefixes = ["/static", "/health", "/api/verify-pin", "/api/auth-status", "/api/logout", "/manifest.json", "/sw.js"]
    if request.path in ["/", "/api/index"] or any(request.path.startswith(p) for p in exempt_prefixes):
        return None
    
    if not session.get("authenticated", False):
        logger.warning(f"[AUTH] 미인증 접근 차단: {request.path}")
        return jsonify({"error": "보안 인증이 필요합니다. 먼저 4자리 비밀번호를 입력해 주세요."}), 401

@app.route("/api/auth-status", methods=["GET"])
def auth_status():
    return jsonify({"authenticated": bool(session.get("authenticated", False))})

@app.route("/api/verify-pin", methods=["POST"])
def verify_pin():
    req_json = request.get_json(silent=True) or {}
    entered_pin = str(req_json.get("pin", "")).strip()
    
    if entered_pin == APP_PIN:
        session.permanent = True  # 24시간 세션 활성화
        session["authenticated"] = True
        logger.info("[AUTH] 4자리 PIN 비밀번호 인증 성공 (24시간 유효 세션 발급 완료)")
        return jsonify({"status": "success", "message": "인증에 성공했습니다 (24시간 동안 유지)." , "expires_in_hours": 24})
    else:
        logger.warning("[AUTH] 4자리 PIN 인증 실패")
        return jsonify({"status": "error", "message": "비밀번호가 올바르지 않습니다."}), 401

@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    # 메모리에 보관 중이던 데이터셋도 함께 초기화
    for k in CURRENT_DATASET:
        CURRENT_DATASET[k] = None if k not in ["file_size", "skipped_rows"] else 0
    logger.info("[AUTH] 로그아웃 및 메모리 데이터셋 완전 초기화 완료")
    return jsonify({"status": "success", "message": "세션 및 임시 데이터가 초기화되었습니다."})

@app.route("/inspect", methods=["POST"])
def inspect_file():
    logger.info("[REQUEST] POST /inspect")
    
    if "file" not in request.files:
        logger.warning("[ERROR] 파일이 전송되지 않음")
        return jsonify({"error": "업로드된 파일이 없습니다. CSV 파일을 선택해 주세요."}), 400
    
    uploaded_file = request.files["file"]
    filename = uploaded_file.filename or ""
    
    if not filename:
        logger.warning("[ERROR] 파일명이 비어있음")
        return jsonify({"error": "선택된 파일명이 없습니다."}), 400
    
    if not filename.lower().endswith(".csv"):
        logger.warning(f"[ERROR] 허용되지 않은 확장자: {filename}")
        return jsonify({"error": "CSV 파일(.csv)만 업로드할 수 있습니다."}), 400
    
    file_bytes = uploaded_file.read()
    file_size = len(file_bytes)
    logger.info(f"[FILE] name={filename} size={file_size} bytes")
    
    if file_size == 0:
        logger.warning("[ERROR] 빈 파일 업로드됨")
        return jsonify({"error": "파일 내용이 비어 있습니다. 유효한 CSV 파일을 올려주세요."}), 400
    
    encodings_to_try = ["utf-8-sig", "utf-8", "cp949", "euc-kr"]
    decoded_text = None
    successful_encoding = None
    
    for enc in encodings_to_try:
        try:
            decoded_text = file_bytes.decode(enc)
            successful_encoding = enc
            logger.info(f"[ENCODING] {enc} 성공")
            break
        except UnicodeDecodeError:
            logger.info(f"[ENCODING] {enc} 실패, 다음 인코딩 시도")
            continue
    
    if decoded_text is None or successful_encoding is None:
        logger.error("[ERROR] 모든 인코딩(utf-8-sig, utf-8, cp949, euc-kr) 디코딩 실패")
        return jsonify({"error": "파일 인코딩을 판별하지 못했습니다. (지원: UTF-8, CP949, EUC-KR)"}), 400
    
    try:
        skipped_rows = detect_header_skip(decoded_text)
        logger.info(f"[HEADER] 헤더 줄 감지 결과 (건너뛴 줄 수: {skipped_rows})")
    except Exception as e:
        logger.error(f"[ERROR] 헤더 줄 탐지 중 오류: {e}\n{traceback.format_exc()}")
        skipped_rows = 0
    
    try:
        text_stream = io.StringIO(decoded_text)
        df = pd.read_csv(text_stream, skiprows=skipped_rows)
        logger.info(f"[CSV] parsing 완료 rows={len(df)} columns={len(df.columns)}")
    except Exception as e:
        logger.error(f"[ERROR] CSV 파싱 실패: {e}\n{traceback.format_exc()}")
        return jsonify({"error": f"CSV 파일을 읽는 중 오류가 발생했습니다: {str(e)}"}), 400
    
    warnings = []
    total_rows = len(df)
    columns = [str(col) for col in df.columns]
    dtypes = {col: str(df[col].dtype) for col in df.columns}
    
    numeric_cols = []
    categorical_cols = []
    year_cols = []
    
    for col in df.columns:
        col_str = str(col).strip()
        if col_str.isdigit() and 1800 <= int(col_str) <= 2100:
            year_cols.append(col_str)
        if pd.api.types.is_numeric_dtype(df[col]):
            numeric_cols.append(col_str)
        else:
            categorical_cols.append(col_str)
            
    missing_counts = {}
    missing_ratios = {}
    for col in df.columns:
        n_missing = int(df[col].isna().sum())
        missing_counts[str(col)] = n_missing
        missing_ratios[str(col)] = round((n_missing / total_rows * 100), 2) if total_rows > 0 else 0.0
        
    duplicate_rows_count = int(df.duplicated().sum())
    if duplicate_rows_count > 0:
        warnings.append(f"완전히 동일한 중복 행이 {duplicate_rows_count}개 발견되었습니다.")
        
    unnamed_cols = [c for c in columns if c.startswith("Unnamed:") or c.strip() == ""]
    if unnamed_cols:
        warnings.append(f"이름이 없거나 자동으로 부여된 열({len(unnamed_cols)}개): {', '.join(unnamed_cols)}")
        
    empty_cols = [c for c in columns if missing_counts[c] == total_rows]
    if empty_cols:
        warnings.append(f"데이터가 100% 비어 있는 열({len(empty_cols)}개): {', '.join(empty_cols[:5])}{' 외...' if len(empty_cols) > 5 else ''}")
        
    if skipped_rows > 0:
        warnings.append(f"상단 {skipped_rows}개 줄의 메타데이터/빈 줄을 건너뛰고 헤더를 탐색했습니다.")
        
    year_coverage = {}
    recommended_year = None
    
    if year_cols:
        for y in year_cols:
            filled_cnt = int(df[y].notna().sum())
            year_coverage[y] = filled_cnt
            
        sorted_years = sorted(year_cols, key=int, reverse=True)
        max_coverage = max(year_coverage.values()) if year_coverage else 0
        for y in sorted_years:
            if year_coverage[y] >= max_coverage * 0.7:
                recommended_year = y
                break
                
        if recommended_year:
            latest_year = sorted_years[0]
            if latest_year != recommended_year and year_coverage.get(latest_year, 0) < max_coverage * 0.5:
                warnings.append(
                    f"가장 최신 열인 {latest_year}년은 데이터가 {year_coverage[latest_year]}개로 대부분 결측치입니다. "
                    f"충분한 데이터가 채워진 {recommended_year}년({year_coverage[recommended_year]}개)을 기준연도로 권장합니다."
                )

    agg_keywords = [
        "world", "oecd", "euro area", "european union", "high income", "low income",
        "middle income", "income", "area", "total", "aggregate", "dividend",
        "small states", "fragile", "ida", "ibrd", "least developed", "countries that",
        "demographic", "sub-saharan", "latin america", "arab world", "east asia",
        "europe & central asia", "south asia", "north america"
    ]
    aggregate_rows = []
    label_col = categorical_cols[0] if categorical_cols else columns[0]
    
    for idx, val in df[label_col].items():
        val_str = str(val).lower()
        if any(kw in val_str for kw in agg_keywords):
            aggregate_rows.append({
                "index": int(idx),
                "name": str(val)
            })
            
    if aggregate_rows:
        warnings.append(
            f"국가가 아닌 지역/소득 그룹 집계 행 후보가 {len(aggregate_rows)}개 감지되었습니다. "
            f"국가 간 비교 시 이를 제외하고 분석하는 것을 권장합니다."
        )

    preview_df = df.head(10)
    preview_rows = sanitize_for_json(preview_df.to_dict(orient="records"))
    
    CURRENT_DATASET["df"] = df
    CURRENT_DATASET["filename"] = filename
    CURRENT_DATASET["file_size"] = file_size
    CURRENT_DATASET["encoding"] = successful_encoding
    CURRENT_DATASET["skipped_rows"] = skipped_rows
    CURRENT_DATASET["raw_text"] = decoded_text
    CURRENT_DATASET["latest_run_result"] = None
    CURRENT_DATASET["latest_explanation"] = None
    
    inspect_result = {
        "filename": filename,
        "file_size": file_size,
        "encoding": successful_encoding,
        "skipped_rows": skipped_rows,
        "row_count": total_rows,
        "column_count": len(columns),
        "columns": columns,
        "dtypes": dtypes,
        "numeric_columns": numeric_cols,
        "categorical_columns": categorical_cols,
        "year_columns": year_cols,
        "missing_counts": missing_counts,
        "missing_ratios": missing_ratios,
        "duplicate_rows_count": duplicate_rows_count,
        "unnamed_columns": unnamed_cols,
        "empty_columns": empty_cols,
        "year_coverage": year_coverage,
        "recommended_year": recommended_year,
        "aggregate_rows": aggregate_rows,
        "preview_rows": preview_rows,
        "warnings": warnings,
        "message": "CSV 정밀 진단이 완료되었습니다. (데이터는 서버 메모리에 보관 중입니다)"
    }
    CURRENT_DATASET["inspect_result"] = inspect_result
    
    logger.info(f"[DIAGNOSE] 진단 완료 warnings={len(warnings)} aggregates={len(aggregate_rows)} rec_year={recommended_year}")
    return jsonify(inspect_result)

@app.route("/ask", methods=["POST"])
def ask_natural_query():
    """
    사용자의 자연어 질문을 받아 데이터 진단 맥락을 바탕으로 직접 답변하고,
    이에 맞는 최적의 시각화 도구(12종 중 1개)와 파라미터를 추천하는 엔드포인트
    """
    logger.info("[REQUEST] POST /ask")

    if not GEMINI_API_KEY:
        logger.error("[ERROR] GEMINI_API_KEY 미설정")
        return jsonify({"error": "Gemini API 키가 설정되어 있지 않습니다."}), 400

    req_data = request.get_json(silent=True) or {}
    query = (req_data.get("query") or "").strip()
    if not query:
        return jsonify({"error": "질문 내용을 입력해 주세요."}), 400

    inspect_data = CURRENT_DATASET.get("inspect_result")
    if not inspect_data:
        return jsonify({"error": "진단된 데이터가 없습니다. 먼저 CSV 파일을 업로드해 주세요."}), 400

    summary_for_ai = {
        "filename": inspect_data.get("filename"),
        "row_count": inspect_data.get("row_count"),
        "columns": inspect_data.get("columns", [])[:30],
        "recommended_year": inspect_data.get("recommended_year"),
        "year_range": f"{inspect_data.get('year_columns', [''])[0]}~{inspect_data.get('year_columns', [''])[-1]}" if inspect_data.get('year_columns') else "없음",
        "aggregate_count": len(inspect_data.get("aggregate_rows", []))
    }

    catalog_metadata = {
        tool_id: {
            "name": info["name"],
            "description": info["description"],
            "requires_reference_period": info["requires_reference_period"]
        }
        for tool_id, info in TOOL_CATALOG.items()
    }

    prompt = (
        "당신은 교육용 데이터 분석 웹앱의 수석 AI 분석가입니다.\n"
        "사용자가 데이터에 대해 자연어로 자유롭게 질문했습니다.\n"
        "데이터 진단 요약을 바탕으로 친절하고 명쾌하게 답변하고, "
        "질문한 내용을 실제로 시각화하여 검증할 수 있는 가장 적합한 도구(12종 중 1개)와 파라미터를 JSON으로 반환하세요.\n\n"
        "[절대 규칙]\n"
        "1. 반드시 아래 도구 카탈로그에 존재하는 도구 ID(key)만 매핑하세요.\n"
        "2. 답변은 초보자나 중학생도 쉽게 이해할 수 있도록 친절하게 작성하세요.\n"
        "3. 만약 순위 분석(top_bottom_n)이라면, 사용자가 하위(가장 낮은)를 요청하면 params에 'order': 'bottom'을 넣고, 상위를 요청하면 'order': 'top'을 넣으세요. 사용자가 요구한 개수(N)를 'n'에 정확히 반영하세요 (예: 20개국이면 n: 20).\n"
        "4. 사용자가 대한민국이나 한국을 직접 질문에 언급한 경우에만 params에 'target_item': 'Korea, Rep.'을 넣고, 언급하지 않았다면 target_item을 절대 포함하지 마세요.\n"
        "5. 반드시 순수 JSON으로만 응답하세요.\n\n"
        f"[도구 카탈로그]\n{json.dumps(catalog_metadata, ensure_ascii=False, indent=2)}\n\n"
        f"[데이터 진단 요약]\n{json.dumps(summary_for_ai, ensure_ascii=False, indent=2)}\n\n"
        f"[사용자의 자연어 질문]: {query}\n\n"
        "[출력 JSON 형식 예시]\n"
        "{\n"
        '  "answer": "사용자 질문에 대한 친절하고 명쾌한 자연어 답변",\n'
        '  "tool": "top_bottom_n",\n'
        '  "tool_name_kr": "상위·하위 N 순위 분석",\n'
        '  "params": {\n'
        '    "reference_period": "2024",\n'
        '    "n": 20,\n'
        '    "order": "bottom",\n'
        '    "exclude_aggregates": true\n'
        "  },\n"
        '  "button_label": "📊 2024년 최하위 20개국 분석 차트 보기"\n'
        "}\n"
    )

    logger.info(f"[AI] ask 요청 시작 (질문: {query[:50]}..., model={GEMINI_MODEL})")
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.2
            )
        )
        parsed = json.loads(response.text)
        tool_id = parsed.get("tool")
        if tool_id not in TOOL_CATALOG:
            tool_id = "top_bottom_n"
            parsed["tool"] = tool_id
            parsed["tool_name_kr"] = TOOL_CATALOG[tool_id]["name"]

        params = parsed.get("params", {})
        has_korea = any(k in query.lower() for k in ["대한민국", "한국", "korea", "우리나라"])
        is_bottom = any(w in query for w in ["하위", "낮은", "바닥", "최하위", "꼴찌", "bottom", "least", "lowest"])
        
        # 순위 질문 처리
        if tool_id == "top_bottom_n" or any(w in query for w in ["순위", "등", "상위", "하위", "낮은"]):
            tool_id = "top_bottom_n"
            parsed["tool"] = tool_id
            parsed["tool_name_kr"] = TOOL_CATALOG[tool_id]["name"]
            
            # 하위 vs 상위 모드 설정
            if is_bottom:
                params["order"] = "bottom"
            elif "order" not in params:
                params["order"] = "top"
                
            # 질문 속 숫자(개수) 추출 (예: 20개, 5개 등)
            n_match = re.search(r"(\d+)\s*(?:개|개국|등|위)", query)
            if n_match:
                params["n"] = int(n_match.group(1))
            elif "n" not in params:
                params["n"] = 10

        # 대한민국 비교는 사용자가 질문에 언급했을 때만 포함
        if has_korea:
            params["target_item"] = "Korea, Rep."
        else:
            params.pop("target_item", None)

        # 성장률(10년간 성장 등) 질문인 경우 change_rate 바인딩
        if any(w in query for w in ["성장", "증가폭", "변화량", "10년"]):
            tool_id = "change_rate"
            params["reference_period"] = params.get("reference_period") or "2024"
            params["start_period"] = params.get("start_period") or "2014"
            if "top_n" not in params:
                params["top_n"] = 10
            parsed["tool"] = tool_id
            parsed["tool_name_kr"] = TOOL_CATALOG[tool_id]["name"]

        # 도구 표시명(tool_name_kr) 및 버튼 라벨을 질문 맥락에 맞춰 동적으로 자연스럽게 구성
        n_val = params.get("n", 10)
        is_bottom = params.get("order") == "bottom"
        order_kr = "최하위" if is_bottom else "최상위"

        if tool_id == "top_bottom_n":
            if has_korea:
                tool_name_kr = f"글로벌 {order_kr} {n_val}개국 및 대한민국 비교 분석"
                parsed["button_label"] = f"📊 2024년 {order_kr} {n_val}개국 및 대한민국 비교 차트 보기"
            else:
                tool_name_kr = f"글로벌 {order_kr} {n_val}개국 순위 분석"
                parsed["button_label"] = f"📊 2024년 {order_kr} {n_val}개국 차트 보기"
            parsed["tool_name_kr"] = tool_name_kr
        elif tool_id == "distribution_bins":
            parsed["tool_name_kr"] = "인터넷 이용률 구간별 분포 분석"
            parsed["button_label"] = "📊 2024년 구간별 분포 차트 보기"
        elif tool_id == "change_rate":
            parsed["tool_name_kr"] = f"인터넷 이용률 {n_val}개년 성장률 상위 분석"
            parsed["button_label"] = "📊 인터넷 이용률 성장률 차트 보기"

        parsed["params"] = params
        logger.info(f"[AI] ask 응답 완료 (추천도구: {tool_id}, tool_name_kr={parsed.get('tool_name_kr')}, params={params})")
        return jsonify({
            "status": "success",
            "query": query,
            "answer": parsed.get("answer", ""),
            "tool": tool_id,
            "tool_name_kr": parsed.get("tool_name_kr", TOOL_CATALOG[tool_id]["name"]),
            "params": params,
            "button_label": parsed.get("button_label", "📊 이 분석 차트 및 결과 보기")
        })

    except Exception as e:
        logger.error(f"[ERROR] ask 처리 실패: {e}\n{traceback.format_exc()}")
        return jsonify({"error": f"자연어 질문 분석 중 오류가 발생했습니다: {str(e)}"}), 500

@app.route("/suggest", methods=["POST"])
def suggest_analyses():
    logger.info("[REQUEST] POST /suggest")
    
    if not GEMINI_API_KEY:
        logger.error("[ERROR] GEMINI_API_KEY 미설정")
        return jsonify({"error": "Gemini API 키가 설정되어 있지 않습니다. .env 파일에 GEMINI_API_KEY를 등록해 주세요."}), 400

    inspect_data = CURRENT_DATASET.get("inspect_result")
    req_json = request.get_json(silent=True) or {}
    if not inspect_data and "inspect_summary" in req_json:
        inspect_data = req_json["inspect_summary"]
        
    if not inspect_data:
        logger.warning("[ERROR] 진단된 데이터셋 없음")
        return jsonify({"error": "진단된 데이터셋이 없습니다. 먼저 CSV 파일을 업로드해 주세요."}), 400

    summary_for_ai = {
        "filename": inspect_data.get("filename"),
        "row_count": inspect_data.get("row_count"),
        "column_count": inspect_data.get("column_count"),
        "columns": inspect_data.get("columns", [])[:30],
        "numeric_columns": inspect_data.get("numeric_columns", [])[:20],
        "categorical_columns": inspect_data.get("categorical_columns", []),
        "year_columns_range": f"{inspect_data.get('year_columns', [''])[0]}~{inspect_data.get('year_columns', [''])[-1]}" if inspect_data.get('year_columns') else "없음",
        "recommended_year": inspect_data.get("recommended_year"),
        "aggregate_rows_count": len(inspect_data.get("aggregate_rows", [])),
        "warnings": inspect_data.get("warnings", [])
    }

    catalog_metadata = {
        tool_id: {
            "name": info["name"],
            "description": info["description"],
            "requires_reference_period": info["requires_reference_period"]
        }
        for tool_id, info in TOOL_CATALOG.items()
    }

    prompt = (
        "당신은 교육용 데이터 분석 웹앱의 수석 AI 분석 플래너입니다.\n"
        "제공된 데이터 진단 요약을 확인하고, 아래 '도구 카탈로그'에 있는 도구 중 "
        "가장 의미 있고 실행 가능한 분석 3~5개를 골라 JSON으로 제안하세요.\n\n"
        "[절대 규칙]\n"
        "1. 반드시 아래 도구 카탈로그에 존재하는 도구 ID(key)만 제안할 수 있습니다.\n"
        "2. 데이터에 없는 열이나 없는 연도를 절대 제안하지 마세요.\n"
        "3. 7~12번 도구는 reference_period(기준시점)가 필수입니다. "
        "특별한 사유가 없으면 검증된 권장 기준연도(recommended_year)를 사용하세요.\n"
        "4. 원본 CSV 계산은 하지 마세요. 오직 어떤 분석을 왜 제안하는지 계획만 반환합니다.\n"
        "5. 반드시 순수 JSON 형식으로만 응답하세요.\n\n"
        f"[도구 카탈로그]\n{json.dumps(catalog_metadata, ensure_ascii=False, indent=2)}\n\n"
        f"[데이터 진단 요약]\n{json.dumps(summary_for_ai, ensure_ascii=False, indent=2)}\n\n"
        "[출력 JSON 형식]\n"
        "{\n"
        '  "suggestions": [\n'
        "    {\n"
        '      "tool": "top_bottom_n",\n'
        '      "params": {\n'
        '        "reference_period": "2024",\n'
        '        "n": 10,\n'
        '        "exclude_aggregates": true\n'
        "      },\n"
        '      "reference_period": "2024",\n'
        '      "why": "이 분석이 이 데이터에서 의미 있는 이유",\n'
        '      "caution": "해석할 때 주의할 점"\n'
        "    }\n"
        "  ]\n"
        "}\n"
    )

    logger.info(f"[AI] suggest 요청 시작 (model={GEMINI_MODEL})")
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.2
            )
        )
        logger.info("[AI] suggest 응답 수신 완료")
    except Exception as e:
        error_msg = str(e)
        logger.error(f"[ERROR] Gemini API 호출 실패: {error_msg}\n{traceback.format_exc()}")
        if "API_KEY_INVALID" in error_msg or "PERMISSION_DENIED" in error_msg:
            return jsonify({"error": "Gemini API 키 인증에 실패했습니다. 올바른 키인지 확인해 주세요."}), 401
        elif "RESOURCE_EXHAUSTED" in error_msg or "429" in error_msg:
            return jsonify({"error": "Gemini API 호출 한도를 초과했습니다. 잠시 후 다시 시도해 주세요."}), 429
        elif "DEADLINE_EXCEEDED" in error_msg or "timeout" in error_msg.lower():
            return jsonify({"error": "Gemini API 응답 시간이 초과되었습니다 (Timeout)."}), 504
        return jsonify({"error": f"AI 분석 제안 생성 중 통신 오류가 발생했습니다: {error_msg}"}), 500

    try:
        parsed_result = json.loads(response.text)
        suggestions = parsed_result.get("suggestions", [])
        
        valid_suggestions = []
        for s in suggestions:
            tool_name = s.get("tool")
            if tool_name in TOOL_CATALOG:
                s["tool_name_kr"] = TOOL_CATALOG[tool_name]["name"]
                valid_suggestions.append(s)
            else:
                logger.warning(f"[AI] 카탈로그에 없는 잘못된 도구 제안 무시됨: {tool_name}")

        logger.info(f"[AI] JSON 파싱 완료 (유효 제안 개수: {len(valid_suggestions)})")
        return jsonify({
            "status": "success",
            "suggestions": valid_suggestions,
            "model_used": GEMINI_MODEL
        })

    except Exception as e:
        logger.error(f"[ERROR] AI 응답 JSON 파싱 실패: {e}\n응답원문: {getattr(response, 'text', '')}")
        return jsonify({"error": "AI가 반환한 제안 형식을 파싱하지 못했습니다. 다시 시도해 주세요."}), 500

@app.route("/run", methods=["POST"])
def run_tool():
    logger.info("[REQUEST] POST /run")

    df = CURRENT_DATASET.get("df")
    if df is None:
        logger.warning("[ERROR] 데이터셋 미로드 상태에서 /run 요청")
        return jsonify({"error": "분석할 데이터셋이 없습니다. 먼저 CSV 파일을 업로드해 주세요."}), 400

    req_data = request.get_json(silent=True) or {}
    tool_name = req_data.get("tool")
    params = req_data.get("params") or {}

    if not tool_name:
        logger.warning("[ERROR] 도구명 미지정")
        return jsonify({"error": "실행할 도구(tool)를 지정해 주세요."}), 400

    if tool_name not in TOOL_CATALOG:
        logger.warning(f"[ERROR] 존재하지 않는 도구: {tool_name}")
        return jsonify({
            "error": f"도구 목록에 존재하지 않는 도구입니다: '{tool_name}'",
            "available_tools": list(TOOL_CATALOG.keys())
        }), 400

    tool_info = TOOL_CATALOG[tool_name]
    tool_func = tool_info["func"]

    if tool_info["requires_reference_period"]:
        ref_period = params.get("reference_period")
        if not ref_period or str(ref_period).strip() == "":
            logger.warning(f"[ERROR] {tool_name} 실행 시 필수 기준시점(reference_period) 누락")
            return jsonify({
                "error": f"도구 '{tool_name}'({tool_info['name']})를 실행하려면 기준 시점(reference_period)을 반드시 입력해야 합니다."
            }), 400

    try:
        result_payload = tool_func(df, params)
        sanitized_result = sanitize_for_json(result_payload)
        
        CURRENT_DATASET["latest_run_result"] = sanitized_result
        CURRENT_DATASET["latest_explanation"] = None

        used_rows = sanitized_result.get("used_rows_count", 0)
        excluded_rows = sanitized_result.get("excluded_rows_count", 0)
        logger.info(f"[TOOL] 도구명={tool_name} 파라미터={params} 사용행수={used_rows} 제외행수={excluded_rows}")

        return jsonify({
            "status": "success",
            "tool_info": {
                "id": tool_name,
                "name": tool_info["name"],
                "description": tool_info["description"]
            },
            "data": sanitized_result
        })

    except ValueError as ve:
        logger.error(f"[ERROR] 도구 입력값 오류 ({tool_name}): {ve}")
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        logger.error(f"[ERROR] 도구 실행 중 오류 ({tool_name}): {e}\n{traceback.format_exc()}")
        return jsonify({"error": f"도구 계산 중 오류가 발생했습니다: {str(e)}"}), 500

@app.route("/explain", methods=["POST"])
def explain_result():
    logger.info("[REQUEST] POST /explain")

    if not GEMINI_API_KEY:
        logger.error("[ERROR] GEMINI_API_KEY 미설정")
        return jsonify({"error": "Gemini API 키가 설정되어 있지 않습니다. .env 파일에 GEMINI_API_KEY를 등록해 주세요."}), 400

    req_data = request.get_json(silent=True) or {}
    mode = str(req_data.get("mode", "B")).upper()
    if mode not in ["A", "B"]:
        mode = "B"

    inspect_data = CURRENT_DATASET.get("inspect_result") or {}
    calc_result = req_data.get("calculation_result") or CURRENT_DATASET.get("latest_run_result")
    original_query = (req_data.get("original_query") or req_data.get("user_query") or "").strip()
    tool_name = (req_data.get("tool_name") or "").strip()

    if mode == "B" and not calc_result:
        logger.warning("[ERROR] 모드 B 실행 시 계산 결과 누락")
        return jsonify({"error": "해석할 계산 결과가 없습니다. 먼저 도구 실행(/run)을 완료해 주세요."}), 400

    from google import genai
    client = genai.Client(api_key=GEMINI_API_KEY)

    if mode == "A":
        logger.info(f"[AI] explain 요청 시작 (모드 A - 허술한 비교용, model={GEMINI_MODEL})")
        prompt = (
            "당신은 AI 어시스턴트입니다.\n"
            "아래 데이터셋의 개요를 보고 이 데이터에 대한 분석 보고서를 작성해 주세요.\n\n"
            f"[데이터셋 개요]\n"
            f"- 파일명: {inspect_data.get('filename', '알 수 없음')}\n"
            f"- 전체 행 수: {inspect_data.get('row_count', 0)}, 열 수: {inspect_data.get('column_count', 0)}\n"
            f"- 열 목록: {', '.join(inspect_data.get('columns', [])[:20])}\n"
            f"- 연도 범위: {inspect_data.get('year_columns', [''])[0] if inspect_data.get('year_columns') else ''} ~ "
            f"{inspect_data.get('year_columns', [''])[-1] if inspect_data.get('year_columns') else ''}\n"
        )
    else:
        logger.info(f"[AI] explain 요청 시작 (모드 B - 정식 판다스 기반, model={GEMINI_MODEL})")
        analysis_topic = original_query if original_query else (tool_name if tool_name else "데이터 분석")
        prompt = (
            "당신은 교육용 데이터 분석 웹앱의 수석 데이터 사이언티스트입니다.\n"
            "오직 아래 제공된 Pandas 계산 결과만을 바탕으로 데이터 해석 보고서를 작성하세요.\n\n"
            "[엄격한 8대 규칙]\n"
            "1. 제공된 계산 결과에 있는 숫자만 사용하세요.\n"
            "2. 직접 계산하거나 추정하지 마세요.\n"
            "3. 제공된 데이터 결과에 없는 가상의 국가, 연도, 열을 절대 언급하지 마세요.\n"
            "4. 기준 시점과 제외된 행 수(및 제외 사유)를 본문에 반드시 명시하세요.\n"
            "5. 상관관계를 인과관계로 표현하지 마세요. (상관성은 원인-결과가 아님)\n"
            "6. 지표의 정의(인터넷 이용자 정의: 지난 3개월 동안 1회 이상 이용)와 출처(세계은행/ITU)를 보고서 앞부분에 표기하세요.\n"
            "7. 계산 결과로 확인할 수 없는 것은 '이 데이터로는 확인할 수 없다'라고 분명하게 쓰세요.\n"
            "8. 중학생도 쉽게 이해할 수 있는 친절한 어조로 쓰되 통계적 정확성을 절대 왜곡하지 마세요.\n\n"
            "[보고서 작성 지침]\n"
            "- '무엇을 봤는가' 섹션에서 '상위·하위 N 순위 분석 도구', '구간별 분포 도구', 'top_bottom_n' 같은 내부 시스템/함수 명칭을 절대 쓰지 마세요.\n"
            f"- 사용자의 분석 목적/주제: '{analysis_topic}'\n"
            "- 사용자의 원래 분석 의도(예: 2024년 기준 글로벌 최상위 10개국과 대한민국의 인터넷 이용률 및 순위 비교)를 주어로 삼아 무엇을 분석했는지 자연스럽고 명쾌하게 서술하세요.\n\n"
            "[반드시 지켜야 할 마크다운 출력 형식]\n"
            "## 무엇을 봤는가\n"
            "## 데이터 출처와 정의\n"
            "## 기준 시점과 제외된 항목\n"
            "## 계산 결과\n"
            "## 읽어낼 수 있는 것\n"
            "## 이 데이터로는 알 수 없는 것\n"
            "## 다음에 확인해볼 것\n\n"
            f"[파일명]: {inspect_data.get('filename', 'dataset.csv')}\n"
            f"[Pandas 계산 결과]\n{json.dumps(calc_result, ensure_ascii=False, indent=2)}\n"
        )

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt
        )
        explanation_text = response.text or ""
        logger.info(f"[AI] explain 응답 수신 완료 (모드 {mode}, 글자 수: {len(explanation_text)})")
        
        CURRENT_DATASET["latest_explanation"] = explanation_text

        return jsonify({
            "status": "success",
            "mode": mode,
            "explanation": explanation_text,
            "model_used": GEMINI_MODEL
        })

    except Exception as e:
        error_msg = str(e)
        logger.error(f"[ERROR] explain 생성 중 오류: {error_msg}\n{traceback.format_exc()}")
        if "API_KEY_INVALID" in error_msg or "PERMISSION_DENIED" in error_msg:
            return jsonify({"error": "Gemini API 키 인증에 실패했습니다."}), 401
        elif "RESOURCE_EXHAUSTED" in error_msg or "429" in error_msg:
            return jsonify({"error": "Gemini API 사용량 한도를 초과했습니다."}), 429
        elif "DEADLINE_EXCEEDED" in error_msg or "timeout" in error_msg.lower():
            return jsonify({"error": "Gemini API 응답 시간이 초과되었습니다 (Timeout)."}), 504
        return jsonify({"error": f"AI 해석 생성 중 통신 오류가 발생했습니다: {error_msg}"}), 500

@app.route("/chat", methods=["POST"])
def chat_followup():
    logger.info("[REQUEST] POST /chat")

    if not GEMINI_API_KEY:
        logger.error("[ERROR] GEMINI_API_KEY 미설정")
        return jsonify({"error": "Gemini API 키가 설정되어 있지 않습니다."}), 400

    req_data = request.get_json(silent=True) or {}
    user_question = (req_data.get("question") or "").strip()
    if not user_question:
        return jsonify({"error": "질문 내용을 입력해 주세요."}), 400

    # 클라이언트가 브라우저 화면에서 직접 실어 보낸 최신 계산 결과 및 원래 질문 우선 사용
    calc_result = req_data.get("calculation_result") or CURRENT_DATASET.get("latest_run_result")
    explanation_context = req_data.get("explanation_context") or CURRENT_DATASET.get("latest_explanation") or ""
    inspect_data = CURRENT_DATASET.get("inspect_result") or {}
    original_query = (req_data.get("original_query") or "").strip()
    tool_name = (req_data.get("tool_name") or "").strip()

    prompt = (
        "당신은 청소년과 코딩/데이터 분석 입문자에게 통계와 데이터의 의미를 아주 쉽고 재미있게 가르쳐주는 친절한 '데이터 과외 선생님'입니다.\n"
        "현재 사용자는 웹 화면에 표시된 데이터 분석 차트와 보고서를 확인하고 있습니다.\n\n"
        "[현재 분석 맥락 정보]\n"
        f"- 사용자가 애초에 궁금해했던 원래 질문: {original_query if original_query else '직접 분석 도구 선택 실행'}\n"
        f"- 현재 화면의 분석 도구명: {tool_name if tool_name else '데이터 분석 도구'}\n"
        f"- [현재 화면의 통계 계산 결과]:\n{json.dumps(calc_result, ensure_ascii=False, indent=2) if calc_result else '계산 결과 없음'}\n\n"
        f"- [현재 화면의 분석 보고서 내용]:\n{explanation_context[:2000] if explanation_context else '보고서 내용 없음'}\n\n"
        f"[사용자의 질문]:\n{user_question}\n\n"
        "[답변 절대 원칙]\n"
        "1. 사용자가 '쉽게 설명해줘', '이게 무슨 말이야?'처럼 짧게 질문하더라도, **반드시 현재 화면의 [통계 계산 결과]와 [분석 보고서 내용]만을 최우선 주제로 삼아** 명쾌하게 설명하세요.\n"
        "2. 특히 사용자가 처음에 했던 '원래 질문'(예: 30% 미만 정보 소외 국가 찾기)이 있다면, 지금 화면의 분석 결과(예: 50% 미만 저보급 그룹 40개국)가 그 원래 질문과 어떻게 연결되는지 중학생도 바로 무릎을 칠 수 있는 일상적 비유(학교 시험 등수나 반 편성, 달리기 시합, 과일 바구니 등)를 들어 쉽게 풀어주세요.\n"
        "3. 현재 보고서나 계산 결과에 없는 과거의 엉뚱한 분석 주제(예: 대한민국 13위 등)는 절대 꺼내지 마세요.\n"
        "4. 통계 전문 용어(표준편차, 중앙값, 결측치, 최댓값, 최솟값 등)가 나오면 일상 언어로 친절하게 풀어서 설명하세요.\n"
        "5. 답변 끝에는 초보자에게 힘이 되는 따뜻하고 든든한 응원의 한마디를 덧붙여 주세요.\n"
    )

    logger.info(f"[AI] chat 후속 질문 요청 시작 (질문: {user_question[:50]}..., model={GEMINI_MODEL})")
    try:
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt
        )
        answer_text = response.text or ""
        logger.info(f"[AI] chat 응답 수신 완료 (글자 수: {len(answer_text)})")

        return jsonify({
            "status": "success",
            "question": user_question,
            "answer": answer_text,
            "model_used": GEMINI_MODEL
        })

    except Exception as e:
        error_msg = str(e)
        logger.error(f"[ERROR] chat 처리 중 오류: {error_msg}\n{traceback.format_exc()}")
        return jsonify({"error": f"AI 답변 생성 중 오류가 발생했습니다: {error_msg}"}), 500

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "true").lower() == "true"
    logger.info(f"Starting server on port {port} (debug={debug})")
    app.run(host="127.0.0.1", port=port, debug=debug)
