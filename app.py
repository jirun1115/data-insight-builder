import os
import io
import csv
import json
import logging
import traceback
import math
import numpy as np
import pandas as pd
from flask import Flask, request, jsonify, render_template
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
        "3. 만약 2024년 순위나 특정 국가 비교라면 'top_bottom_n'이나 'compare_one_vs_all' 등을 정확한 파라미터와 함께 연결하세요. "
        "특히 대한민국이나 특정 국가와의 비교를 요청한 경우 params에 'target_item': 'Korea, Rep.'(또는 해당 국가명)을 반드시 포함하세요.\n"
        "4. 반드시 순수 JSON으로만 응답하세요.\n\n"
        f"[도구 카탈로그]\n{json.dumps(catalog_metadata, ensure_ascii=False, indent=2)}\n\n"
        f"[데이터 진단 요약]\n{json.dumps(summary_for_ai, ensure_ascii=False, indent=2)}\n\n"
        f"[사용자의 자연어 질문]: {query}\n\n"
        "[출력 JSON 형식]\n"
        "{\n"
        '  "answer": "사용자 질문에 대한 친절하고 명쾌한 자연어 답변 (데이터 맥락 및 대한민국 13위 위치 설명)",\n'
        '  "tool": "top_bottom_n",\n'
        '  "tool_name_kr": "상위·하위 N 순위 분석",\n'
        '  "params": {\n'
        '    "reference_period": "2024",\n'
        '    "n": 10,\n'
        '    "exclude_aggregates": true,\n'
        '    "target_item": "Korea, Rep."\n'
        "  },\n"
        '  "button_label": "📊 2024년 상위 10개국 및 대한민국(13위) 비교 차트 보기"\n'
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
        if any(k in query.lower() for k in ["대한민국", "한국", "korea"]):
            params["target_item"] = "Korea, Rep."

        # 순위(1~10등 등)와 대한민국 비교 질문인 경우 확실히 top_bottom_n과 target_item을 바인딩
        if any(w in query for w in ["순위", "등", "10등", "1~10", "상위"]) and any(k in query.lower() for k in ["대한민국", "한국", "korea"]):
            tool_id = "top_bottom_n"
            params["n"] = 10
            parsed["tool"] = tool_id
            parsed["tool_name_kr"] = TOOL_CATALOG[tool_id]["name"]

        parsed["params"] = params
        logger.info(f"[AI] ask 응답 완료 (추천도구: {tool_id}, params={params})")
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

    calc_result = CURRENT_DATASET.get("latest_run_result")
    explanation_context = req_data.get("explanation_context") or CURRENT_DATASET.get("latest_explanation") or ""
    inspect_data = CURRENT_DATASET.get("inspect_result") or {}

    prompt = (
        "당신은 청소년과 코딩/데이터 분석 입문자에게 통계와 데이터의 의미를 아주 쉽고 재미있게 가르쳐주는 친절한 '데이터 과외 선생님'입니다.\n"
        "방금 사용자가 확인한 데이터 분석 보고서와 통계 계산 결과를 바탕으로, 사용자의 질문에 답변해 주세요.\n\n"
        "[답변 원칙]\n"
        "1. 질문에 '쉽게 설명해줘'라는 요청이 있거나 일반 질문이더라도, **중학생도 바로 이해할 수 있도록 일상적인 비유(학교 시험, 달리기 시합, 아이스크림 등)**를 사용하세요.\n"
        "2. 통계 전문 용어가 나오면 풀어서 설명하세요 (예: 결측치 = 빈칸/누락된 숫자, 상관관계 = 함께 움직이는 짝꿍 관계).\n"
        "3. 제공된 계산 결과와 보고서에 있는 사실만을 근거로 답변하고, 없는 숫자를 지어내지 마세요.\n"
        "4. 마크다운(강조, 불릿 등)을 사용해 읽기 편하게 구성하세요.\n"
        "5. 답변 끝에는 초보자에게 힘이 되는 따뜻한 응원의 한마디를 덧붙여 주세요.\n\n"
        f"[파일명]: {inspect_data.get('filename', 'dataset.csv')}\n"
        f"[최근 분석 통계 결과]:\n{json.dumps(calc_result, ensure_ascii=False, indent=2) if calc_result else '계산 결과 없음'}\n\n"
        f"[최근 생성된 분석 보고서 요약]:\n{explanation_context[:1500] if explanation_context else '보고서 내용 없음'}\n\n"
        f"[사용자의 질문]:\n{user_question}\n"
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
