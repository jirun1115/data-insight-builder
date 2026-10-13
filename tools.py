"""
tools.py - 도구 카탈로그 (Data Analysis Tool Functions)

모든 함수는 계산 결과와 함께:
- 기준시점 (reference_period)
- 사용한 행 수 (used_rows_count)
- 제외한 행 수와 이유 (excluded_rows_count, exclusion_reason)
- 사용한 열 (used_columns)
- 단위 (unit)
- 주의사항 (caution)
을 반드시 반환합니다.
절대 Gemini가 계산하지 않고, 모든 계산은 Pandas가 수행합니다.
"""

import math
import numpy as np
import pandas as pd


def sanitize_val(val):
    """NaN, Inf 값을 JSON 직렬화 가능한 None 또는 안전한 값으로 변환합니다."""
    if isinstance(val, (float, np.floating)):
        if math.isnan(val) or math.isinf(val):
            return None
        return float(val)
    elif isinstance(val, (int, np.integer)):
        return int(val)
    elif pd.isna(val):
        return None
    return val


def sanitize_dict_or_list(obj):
    """딕셔너리 및 리스트 내부의 모든 NaN, Inf를 None으로 치환합니다."""
    if isinstance(obj, dict):
        return {str(k): sanitize_dict_or_list(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_dict_or_list(v) for v in obj]
    return sanitize_val(obj)


def detect_year_columns(df):
    """데이터프레임에서 4자리 연도로 추정되는 열들을 찾아 반환합니다."""
    year_cols = []
    for col in df.columns:
        c_str = str(col).strip()
        if c_str.isdigit() and 1800 <= int(c_str) <= 2100:
            year_cols.append(str(col))
    return sorted(year_cols, key=int)


def detect_aggregate_indices(df, label_col=None):
    """집계 행(World, Income group 등)의 행 인덱스 목록을 반환합니다."""
    if label_col is None or label_col not in df.columns:
        text_cols = [c for c in df.columns if not pd.api.types.is_numeric_dtype(df[c])]
        label_col = text_cols[0] if text_cols else df.columns[0]

    agg_keywords = [
        "world", "oecd", "euro area", "european union", "high income", "low income",
        "middle income", "income", "area", "total", "aggregate", "dividend",
        "small states", "fragile", "ida", "ibrd", "least developed", "countries that",
        "demographic", "sub-saharan", "latin america", "arab world", "east asia",
        "europe & central asia", "south asia", "north america"
    ]
    agg_indices = []
    for idx, val in df[label_col].items():
        val_str = str(val).lower()
        if any(kw in val_str for kw in agg_keywords):
            agg_indices.append(idx)
    return agg_indices, label_col


# ==============================================================================
# 도구 1. reshape_wide_to_long: 연도 열을 행으로 접기 (Unpivot / Melt)
# ==============================================================================
def reshape_wide_to_long(df, params=None):
    params = params or {}
    year_cols = params.get("year_columns") or detect_year_columns(df)
    
    if not year_cols:
        raise ValueError("변환할 연도 열을 찾을 수 없습니다.")

    id_vars = params.get("id_vars")
    if not id_vars:
        id_vars = [c for c in df.columns if c not in year_cols]

    var_name = params.get("var_name", "Year")
    value_name = params.get("value_name", "Value")
    drop_na = params.get("drop_na", True)

    total_orig_rows = len(df)
    melted = pd.melt(
        df,
        id_vars=id_vars,
        value_vars=year_cols,
        var_name=var_name,
        value_name=value_name
    )
    
    initial_melted_len = len(melted)
    if drop_na:
        melted_clean = melted.dropna(subset=[value_name])
        excluded_rows = initial_melted_len - len(melted_clean)
        exclusion_reason = f"결측치({value_name}가 빈칸)인 변환 행 {excluded_rows:,}개 제외"
        result_df = melted_clean
    else:
        excluded_rows = 0
        exclusion_reason = "제외 행 없음 (결측치 포함)"
        result_df = melted

    preview = sanitize_dict_or_list(result_df.head(10).to_dict(orient="records"))

    return {
        "tool": "reshape_wide_to_long",
        "reference_period": f"{year_cols[0]}~{year_cols[-1]} (전체 연도)",
        "used_rows_count": total_orig_rows,
        "excluded_rows_count": excluded_rows,
        "exclusion_reason": exclusion_reason,
        "used_columns": id_vars + year_cols,
        "unit": "행 (Rows)",
        "caution": "와이드 형태를 롱 포맷으로 변환했습니다. 차트 시각화 및 필터링에 최적화된 구조입니다.",
        "result": {
            "transformed_rows": len(result_df),
            "columns": list(result_df.columns),
            "id_vars": id_vars,
            "year_column_name": var_name,
            "value_column_name": value_name,
            "preview": preview
        },
        "chart_data": None
    }


# ==============================================================================
# 도구 2. quality_report: 결측·중복·타입·이상한 열 이름 진단
# ==============================================================================
def quality_report(df, params=None):
    params = params or {}
    total_rows = len(df)
    total_cols = len(df.columns)
    
    missing_by_col = {str(c): int(df[c].isna().sum()) for c in df.columns}
    missing_ratio_by_col = {str(c): round(df[c].isna().sum() / total_rows * 100, 2) if total_rows > 0 else 0.0 for c in df.columns}
    duplicate_rows = int(df.duplicated().sum())

    unnamed_cols = [str(c) for c in df.columns if str(c).startswith("Unnamed:") or str(c).strip() == ""]
    empty_cols = [str(c) for c in df.columns if missing_by_col[str(c)] == total_rows]
    dtypes_summary = {str(c): str(df[c].dtype) for c in df.columns}

    return {
        "tool": "quality_report",
        "reference_period": "전체 데이터셋",
        "used_rows_count": total_rows,
        "excluded_rows_count": 0,
        "exclusion_reason": "전체 행 진단 (제외 없음)",
        "used_columns": [str(c) for c in df.columns],
        "unit": "건 / 비율(%)",
        "caution": "데이터 품질 분석 결과입니다. 결측치가 과도하게 높은 열이나 유령 열은 분석 대상에서 제외해야 합니다.",
        "result": {
            "total_rows": total_rows,
            "total_columns": total_cols,
            "duplicate_rows": duplicate_rows,
            "unnamed_columns": unnamed_cols,
            "completely_empty_columns": empty_cols,
            "missing_counts": missing_by_col,
            "missing_ratios": missing_ratio_by_col,
            "dtypes": dtypes_summary
        },
        "chart_data": None
    }


# ==============================================================================
# 도구 3. year_coverage: 연도별 값 개수와 권장 기준연도
# ==============================================================================
def year_coverage(df, params=None):
    params = params or {}
    year_cols = params.get("year_columns") or detect_year_columns(df)
    
    if not year_cols:
        raise ValueError("데이터에서 연도 열을 찾을 수 없습니다.")

    total_rows = len(df)
    coverage_list = []
    max_filled = 0
    
    for y in year_cols:
        filled = int(df[y].notna().sum())
        ratio = round(filled / total_rows * 100, 2) if total_rows > 0 else 0.0
        coverage_list.append({
            "year": str(y),
            "filled_count": filled,
            "missing_count": total_rows - filled,
            "coverage_ratio": ratio
        })
        if filled > max_filled:
            max_filled = filled

    recommended_year = None
    sorted_years = sorted(year_cols, key=int, reverse=True)
    for y in sorted_years:
        cnt = int(df[y].notna().sum())
        if cnt >= max_filled * 0.7:
            recommended_year = str(y)
            break

    chart_data = {
        "type": "bar",
        "labels": [item["year"] for item in coverage_list],
        "datasets": [
            {
                "label": "데이터 보유 행 수",
                "data": [item["filled_count"] for item in coverage_list],
                "backgroundColor": [
                    "#3b82f6" if item["year"] != recommended_year else "#10b981"
                    for item in coverage_list
                ]
            }
        ]
    }

    return {
        "tool": "year_coverage",
        "reference_period": f"{year_cols[0]}~{year_cols[-1]} (전체 연도)",
        "used_rows_count": total_rows,
        "excluded_rows_count": 0,
        "exclusion_reason": "전체 행 검사 (제외 없음)",
        "used_columns": year_cols,
        "unit": "개 (유효 데이터 개수)",
        "caution": f"최신 열이라도 결측치가 많을 수 있습니다. 데이터 충실도가 안정적인 {recommended_year}년을 분석 기준연도로 권장합니다.",
        "result": {
            "recommended_year": recommended_year,
            "max_coverage_count": max_filled,
            "coverage_by_year": coverage_list
        },
        "chart_data": chart_data
    }


# ==============================================================================
# 도구 4. detect_aggregates: 집계 행 후보 탐지
# ==============================================================================
def detect_aggregates(df, params=None):
    params = params or {}
    label_col = params.get("label_column")
    agg_indices, label_col = detect_aggregate_indices(df, label_col)

    total_rows = len(df)
    agg_rows_detail = []
    for idx in agg_indices:
        val = df.loc[idx, label_col]
        agg_rows_detail.append({
            "index": int(idx),
            "label": str(val)
        })

    pure_rows_count = total_rows - len(agg_indices)

    return {
        "tool": "detect_aggregates",
        "reference_period": "전체 데이터셋",
        "used_rows_count": total_rows,
        "excluded_rows_count": 0,
        "exclusion_reason": "전체 행 대상 집계 후보 탐지 (제외 없음)",
        "used_columns": [label_col],
        "unit": "개 (행 수)",
        "caution": "순수 개별 국가 간의 순위 및 평균 계산 시에는 반드시 이 집계 행들을 제외해야 통계 왜곡이 발생하지 않습니다.",
        "result": {
            "label_column": label_col,
            "total_rows": total_rows,
            "aggregate_count": len(agg_indices),
            "pure_entities_count": pure_rows_count,
            "aggregate_candidates": agg_rows_detail
        },
        "chart_data": {
            "type": "doughnut",
            "labels": ["순수 개별 항목(국가)", "집계/그룹 항목"],
            "datasets": [
                {
                    "data": [pure_rows_count, len(agg_indices)],
                    "backgroundColor": ["#3b82f6", "#f59e0b"]
                }
            ]
        }
    }


# ==============================================================================
# 도구 5. describe_numeric: 개수·평균·중앙값·최소·최대·표준편차
# ==============================================================================
def describe_numeric(df, params=None):
    params = params or {}
    col = params.get("column") or params.get("reference_period")
    
    if not col:
        year_cols = detect_year_columns(df)
        col = year_cols[-2] if len(year_cols) >= 2 else (year_cols[-1] if year_cols else None)
        
    if not col or col not in df.columns:
        raise ValueError(f"분석할 숫자형 열 '{col}'을(를) 찾을 수 없습니다.")

    exclude_aggregates = params.get("exclude_aggregates", True)
    working_df = df.copy()
    excluded_agg_count = 0
    if exclude_aggregates:
        agg_indices, _ = detect_aggregate_indices(working_df)
        excluded_agg_count = len(agg_indices)
        working_df = working_df.drop(index=agg_indices, errors="ignore")

    series = pd.to_numeric(working_df[col], errors="coerce")
    missing_count = int(series.isna().sum())
    valid_series = series.dropna()

    used_count = int(valid_series.count())
    total_excluded = excluded_agg_count + missing_count
    exclusion_reason = f"집계 행 {excluded_agg_count}개 제외, 결측치 {missing_count}개 제외"

    if used_count == 0:
        raise ValueError(f"선택한 열 '{col}'에 유효한 숫자 데이터가 없습니다.")

    mean_val = float(valid_series.mean())
    std_val = float(valid_series.std()) if used_count > 1 else 0.0
    median_val = float(valid_series.median())
    min_val = float(valid_series.min())
    max_val = float(valid_series.max())
    q25 = float(valid_series.quantile(0.25))
    q75 = float(valid_series.quantile(0.75))

    chart_data = {
        "type": "bar",
        "labels": ["최소값", "25% 분위", "중앙값", "평균값", "75% 분위", "최대값"],
        "datasets": [
            {
                "label": f"{col} 요약 통계",
                "data": [
                    round(min_val, 2),
                    round(q25, 2),
                    round(median_val, 2),
                    round(mean_val, 2),
                    round(q75, 2),
                    round(max_val, 2)
                ],
                "backgroundColor": ["#94a3b8", "#60a5fa", "#2563eb", "#8b5cf6", "#60a5fa", "#94a3b8"]
            }
        ]
    }

    return {
        "tool": "describe_numeric",
        "reference_period": str(col),
        "used_rows_count": used_count,
        "excluded_rows_count": total_excluded,
        "exclusion_reason": exclusion_reason,
        "used_columns": [str(col)],
        "unit": "% (또는 해당 지표 단위)",
        "caution": f"평균({round(mean_val, 2)})과 중앙값({round(median_val, 2)})의 차이를 확인하면 데이터의 치우침(왜곡) 정도를 파악할 수 있습니다.",
        "result": {
            "column": str(col),
            "count": used_count,
            "mean": round(mean_val, 2),
            "std": round(std_val, 2),
            "median": round(median_val, 2),
            "min": round(min_val, 2),
            "max": round(max_val, 2),
            "q25": round(q25, 2),
            "q75": round(q75, 2)
        },
        "chart_data": chart_data
    }


# ==============================================================================
# 도구 6. trend_line: 선택 항목들의 연도별 추이 (선 차트)
# ==============================================================================
def trend_line(df, params=None):
    params = params or {}
    label_col = params.get("label_column")
    if not label_col or label_col not in df.columns:
        text_cols = [c for c in df.columns if not pd.api.types.is_numeric_dtype(df[c])]
        label_col = text_cols[0] if text_cols else df.columns[0]

    items = params.get("items")
    if not items:
        candidates = ["Korea, Rep.", "United States", "China", "Japan", "Germany", "World"]
        items = [c for c in candidates if c in df[label_col].values]
        if not items:
            items = df[label_col].dropna().head(5).tolist()

    year_cols = detect_year_columns(df)
    start_year = str(params.get("start_year", "1995"))
    end_year = str(params.get("end_year", "2024"))

    selected_years = [y for y in year_cols if start_year <= y <= end_year]
    if not selected_years:
        selected_years = year_cols[-10:] if len(year_cols) >= 10 else year_cols

    matched_df = df[df[label_col].isin(items)]
    used_rows = len(matched_df)
    excluded_rows = len(df) - used_rows

    palette = ["#2563eb", "#dc2626", "#16a34a", "#f59e0b", "#9333ea", "#06b6d4"]
    datasets = []
    trends_result = {}

    for i, (_, row) in enumerate(matched_df.iterrows()):
        item_name = str(row[label_col])
        series_vals = []
        for y in selected_years:
            val = row.get(y)
            num = pd.to_numeric(val, errors="coerce")
            series_vals.append(round(float(num), 2) if pd.notna(num) else None)

        color = palette[i % len(palette)]
        datasets.append({
            "label": item_name,
            "data": series_vals,
            "borderColor": color,
            "backgroundColor": color,
            "tension": 0.2,
            "fill": False
        })
        trends_result[item_name] = {
            "start_val": next((v for v in series_vals if v is not None), None),
            "end_val": next((v for v in reversed(series_vals) if v is not None), None)
        }

    chart_data = {
        "type": "line",
        "labels": selected_years,
        "datasets": datasets
    }

    return {
        "tool": "trend_line",
        "reference_period": f"{selected_years[0]}~{selected_years[-1]}년",
        "used_rows_count": used_rows,
        "excluded_rows_count": excluded_rows,
        "exclusion_reason": f"선택된 {used_rows}개 비교 항목 외 다른 행({excluded_rows}개) 제외",
        "used_columns": [label_col] + selected_years,
        "unit": "% (비율 추이)",
        "caution": "연도별 결측 구간이 있을 경우 선 차트에서 연결이 끊어질 수 있습니다.",
        "result": {
            "selected_items": items,
            "years": selected_years,
            "trends": trends_result
        },
        "chart_data": chart_data
    }


# 전 세계 주요 국가 영문 -> 한국어 국명 매핑 사전
COUNTRY_NAME_KR = {
    "Korea, Rep.": "대한민국",
    "Korea, Dem. People's Rep.": "북한",
    "United Arab Emirates": "아랍에미리트",
    "Bahrain": "바레인",
    "Saudi Arabia": "사우디아라비아",
    "Denmark": "덴마크",
    "Kuwait": "쿠웨이트",
    "Monaco": "모나코",
    "Norway": "노르웨이",
    "Luxembourg": "룩셈부르크",
    "Liechtenstein": "리히텐슈타인",
    "Iceland": "아이슬란드",
    "Qatar": "카타르",
    "Switzerland": "스위스",
    "Singapore": "싱가포르",
    "Sweden": "스웨덴",
    "Finland": "핀란드",
    "Netherlands": "네덜란드",
    "United Kingdom": "영국",
    "United States": "미국",
    "Japan": "일본",
    "China": "중국",
    "Germany": "독일",
    "France": "프랑스",
    "Canada": "캐나다",
    "Australia": "호주",
    "New Zealand": "뉴질랜드",
    "Ireland": "아일랜드",
    "Spain": "스페인",
    "Italy": "이탈리아",
    "Hong Kong SAR, China": "홍콩",
    "Macao SAR, China": "마카오",
    "Taiwan, China": "대만",
    "Israel": "이스라엘",
    "Austria": "오스트리아",
    "Belgium": "벨기에",
    "Estonia": "에스토니아",
    "Cyprus": "키프로스",
    "Oman": "오만",
    "Malaysia": "말레이시아",
    "Vietnam": "베트남",
    "Thailand": "태국",
    "Indonesia": "인도네시아",
    "India": "인도",
    "Brazil": "브라질",
    "Mexico": "멕시코",
    "South Africa": "남아프리카공화국",
    "Russian Federation": "러시아",
    "Poland": "폴란드",
    "Turkey": "튀르키예",
    "Turkiye": "튀르키예",
    "Greece": "그리스",
    "Portugal": "포르투갈",
    "Czechia": "체코",
    "Hungary": "헝가리"
}

def get_country_kr(eng_name):
    clean = str(eng_name).strip()
    return COUNTRY_NAME_KR.get(clean, clean)


# ==============================================================================
# 도구 7. top_bottom_n: 기준연도 기준 상위·하위 N (세로 막대 차트)
# ==============================================================================
def top_bottom_n(df, params=None):
    params = params or {}
    
    ref_period = params.get("reference_period")
    if not ref_period or str(ref_period).strip() == "":
        raise ValueError("도구 7(top_bottom_n) 실행 시 기준 시점(reference_period)은 필수 입력 항목입니다.")

    ref_col = str(ref_period).strip()
    if ref_col not in df.columns:
        raise ValueError(f"지정한 기준 시점 열 '{ref_col}'이(가) 데이터에 존재하지 않습니다.")

    label_col = params.get("label_column")
    if not label_col or label_col not in df.columns:
        text_cols = [c for c in df.columns if not pd.api.types.is_numeric_dtype(df[c])]
        label_col = text_cols[0] if text_cols else df.columns[0]

    n = int(params.get("n", 10))
    exclude_aggregates = params.get("exclude_aggregates", True)

    working_df = df.copy()
    excluded_agg_count = 0
    if exclude_aggregates:
        agg_indices, _ = detect_aggregate_indices(working_df, label_col)
        excluded_agg_count = len(agg_indices)
        working_df = working_df.drop(index=agg_indices, errors="ignore")

    num_series = pd.to_numeric(working_df[ref_col], errors="coerce")
    missing_count = int(num_series.isna().sum())
    
    clean_df = working_df[num_series.notna()].copy()
    clean_df["_val"] = num_series.dropna()

    used_rows = len(clean_df)
    total_excluded = excluded_agg_count + missing_count
    exclusion_reason = f"집계 행 {excluded_agg_count}개 제외, {ref_col}년 결측치 {missing_count}개 제외"

    if used_rows == 0:
        raise ValueError(f"기준 시점 '{ref_col}'에 유효한 데이터가 전혀 없습니다.")

    sorted_df = clean_df.sort_values(by="_val", ascending=False)
    
    top_items = []
    for rank, (_, row) in enumerate(sorted_df.head(n).iterrows(), 1):
        raw_label = str(row[label_col])
        top_items.append({
            "rank": rank,
            "label": raw_label,
            "label_kr": get_country_kr(raw_label),
            "value": round(float(row["_val"]), 2)
        })

    bottom_items = []
    for rank, (_, row) in enumerate(sorted_df.tail(n).iloc[::-1].iterrows(), 1):
        raw_label = str(row[label_col])
        bottom_items.append({
            "rank": used_rows - n + rank,
            "label": raw_label,
            "label_kr": get_country_kr(raw_label),
            "value": round(float(row["_val"]), 2)
        })

    # target_item 비교 처리 (예: Korea, Rep.)
    target_item_param = params.get("target_item")
    target_info = None
    if target_item_param:
        target_str = str(target_item_param).lower().strip()
        matched_mask = sorted_df[label_col].astype(str).str.lower().str.contains(target_str, regex=False)
        if not matched_mask.any() and any(k in target_str for k in ["korea", "대한민국", "한국"]):
            matched_mask = sorted_df[label_col].astype(str).str.lower().str.contains("korea, rep.", regex=False)
        
        target_rows = sorted_df[matched_mask]
        if not target_rows.empty:
            target_idx = target_rows.index[0]
            target_rank = int(sorted_df.index.get_loc(target_idx) + 1)
            raw_target_label = str(target_rows.iloc[0][label_col])
            target_val = round(float(target_rows.iloc[0]["_val"]), 2)
            target_info = {
                "rank": target_rank,
                "label": raw_target_label,
                "label_kr": get_country_kr(raw_target_label),
                "value": target_val
            }

    # 한국어 국명 라벨과 세로 막대 데이터 구성
    chart_labels = [f"{item['rank']}위 {item.get('label_kr', item['label'])}" for item in top_items]
    chart_vals = [item["value"] for item in top_items]
    chart_colors = ["#2563eb"] * len(top_items)

    if target_info:
        target_kr = target_info.get("label_kr", target_info["label"])
        # 이미 top_n 안에 포함되어 있는지 확인
        if target_info["rank"] <= len(top_items):
            chart_colors[target_info["rank"] - 1] = "#ea580c"
        else:
            # 10위 밖인 경우 비교를 위해 차트 끝에 주황색 강조 막대로 추가
            chart_labels.append(f"⭐ {target_info['rank']}위 {target_kr}")
            chart_vals.append(target_info["value"])
            chart_colors.append("#ea580c")

    # [사용자 요청] 세로 막대 그래프 (indexAxis: "x" 로 위로 솟은 세로 기둥 형태)
    chart_data = {
        "type": "bar",
        "indexAxis": "x",
        "labels": chart_labels,
        "datasets": [
            {
                "label": f"{ref_col}년 인터넷 이용률 (%)",
                "data": chart_vals,
                "backgroundColor": chart_colors
            }
        ]
    }

    target_kr_display = target_info.get("label_kr", target_info["label"]) if target_info else ""
    caution_msg = f"집계 행({excluded_agg_count}개)을 제외한 순수 개별 국가 {used_rows}개국 기준 순위입니다."
    if target_info:
        caution_msg += f" (비교 대상인 {target_kr_display}({target_info['label']})은(는) 전체 {used_rows}개국 중 {target_info['rank']}위({target_info['value']}%)입니다)"

    res_dict = {
        "reference_period": ref_col,
        "n": n,
        "top_n": top_items,
        "bottom_n": bottom_items
    }
    if target_info:
        res_dict["target_item_comparison"] = target_info

    return {
        "tool": "top_bottom_n",
        "reference_period": ref_col,
        "used_rows_count": used_rows,
        "excluded_rows_count": total_excluded,
        "exclusion_reason": exclusion_reason,
        "used_columns": [label_col, ref_col],
        "unit": "%",
        "caution": caution_msg,
        "result": res_dict,
        "chart_data": chart_data
    }


# ==============================================================================
# 도구 8. group_summary: 그룹별 평균·합계·개수 (막대 차트)
# ==============================================================================
def group_summary(df, params=None):
    params = params or {}

    ref_period = params.get("reference_period")
    if not ref_period or str(ref_period).strip() == "":
        raise ValueError("도구 8(group_summary) 실행 시 기준 시점(reference_period)은 필수 입력 항목입니다.")

    ref_col = str(ref_period).strip()
    if ref_col not in df.columns:
        raise ValueError(f"지정한 기준 시점 열 '{ref_col}'이(가) 데이터에 존재하지 않습니다.")

    exclude_aggregates = params.get("exclude_aggregates", True)
    group_col = params.get("group_column")

    working_df = df.copy()
    excluded_agg_count = 0
    if exclude_aggregates:
        agg_indices, _ = detect_aggregate_indices(working_df)
        excluded_agg_count = len(agg_indices)
        working_df = working_df.drop(index=agg_indices, errors="ignore")

    num_series = pd.to_numeric(working_df[ref_col], errors="coerce")
    missing_count = int(num_series.isna().sum())
    
    clean_df = working_df[num_series.notna()].copy()
    clean_df["_val"] = num_series.dropna()

    used_rows = len(clean_df)
    total_excluded = excluded_agg_count + missing_count
    exclusion_reason = f"집계 행 {excluded_agg_count}개 제외, {ref_col}년 결측치 {missing_count}개 제외"

    if used_rows == 0:
        raise ValueError(f"기준 시점 '{ref_col}'에 유효한 데이터가 없습니다.")

    if group_col and group_col in clean_df.columns:
        grouped = clean_df.groupby(group_col)["_val"].agg(["count", "mean", "min", "max", "std"]).reset_index()
        group_name_col = group_col
    else:
        bins = [-1, 50, 70, 90, 101]
        labels = ["50% 미만 (저보급)", "50%~70% (중보급)", "70%~90% (고보급)", "90% 이상 (초고보급)"]
        clean_df["_tier"] = pd.cut(clean_df["_val"], bins=bins, labels=labels)
        grouped = clean_df.groupby("_tier", observed=False)["_val"].agg(["count", "mean", "min", "max", "std"]).reset_index()
        group_name_col = "_tier"

    groups_summary = []
    for _, row in grouped.iterrows():
        g_name = str(row[group_name_col])
        cnt = int(row["count"])
        mean_v = round(float(row["mean"]), 2) if pd.notna(row["mean"]) else 0.0
        min_v = round(float(row["min"]), 2) if pd.notna(row["min"]) else 0.0
        max_v = round(float(row["max"]), 2) if pd.notna(row["max"]) else 0.0
        std_v = round(float(row["std"]), 2) if pd.notna(row["std"]) else 0.0
        groups_summary.append({
            "group": g_name,
            "count": cnt,
            "mean": mean_v,
            "min": min_v,
            "max": max_v,
            "std": std_v
        })

    chart_data = {
        "type": "bar",
        "labels": [g["group"] for g in groups_summary],
        "datasets": [
            {
                "label": f"그룹별 평균 ({ref_col}년)",
                "data": [g["mean"] for g in groups_summary],
                "backgroundColor": "#3b82f6"
            },
            {
                "label": "국가 수",
                "data": [g["count"] for g in groups_summary],
                "backgroundColor": "#10b981",
                "type": "line"
            }
        ]
    }

    return {
        "tool": "group_summary",
        "reference_period": ref_col,
        "used_rows_count": used_rows,
        "excluded_rows_count": total_excluded,
        "exclusion_reason": exclusion_reason,
        "used_columns": [ref_col] + ([group_col] if group_col in clean_df.columns else []),
        "unit": "% (평균값) / 개 (국가 수)",
        "caution": "그룹별 규모(국가 수) 차이가 크면 평균 해석에 유의해야 합니다.",
        "result": {
            "group_column": group_name_col,
            "reference_period": ref_col,
            "summary": groups_summary
        },
        "chart_data": chart_data
    }


# ==============================================================================
# 도구 9. change_rate: 두 시점 사이 변화량·변화율 (막대 차트)
# ==============================================================================
def change_rate(df, params=None):
    params = params or {}

    # [필수 검증] 기준 시점 필수
    ref_period = params.get("reference_period")
    if not ref_period or str(ref_period).strip() == "":
        raise ValueError("도구 9(change_rate) 실행 시 기준 시점(reference_period)은 필수 입력 항목입니다.")

    end_col = str(ref_period).strip()
    start_col = str(params.get("start_period", "2014")).strip()

    if end_col not in df.columns:
        raise ValueError(f"종료 시점 열 '{end_col}'이(가) 데이터에 존재하지 않습니다.")
    if start_col not in df.columns:
        year_cols = detect_year_columns(df)
        start_col = year_cols[0] if year_cols else None
        if not start_col:
            raise ValueError("비교할 시작 시점을 찾을 수 없습니다.")

    label_col = params.get("label_column")
    if not label_col or label_col not in df.columns:
        text_cols = [c for c in df.columns if not pd.api.types.is_numeric_dtype(df[c])]
        label_col = text_cols[0] if text_cols else df.columns[0]

    exclude_aggregates = params.get("exclude_aggregates", True)
    top_n = int(params.get("top_n", 10))

    working_df = df.copy()
    excluded_agg_count = 0
    if exclude_aggregates:
        agg_indices, _ = detect_aggregate_indices(working_df, label_col)
        excluded_agg_count = len(agg_indices)
        working_df = working_df.drop(index=agg_indices, errors="ignore")

    start_series = pd.to_numeric(working_df[start_col], errors="coerce")
    end_series = pd.to_numeric(working_df[end_col], errors="coerce")
    
    valid_mask = start_series.notna() & end_series.notna()
    clean_df = working_df[valid_mask].copy()
    clean_df["_start"] = start_series[valid_mask]
    clean_df["_end"] = end_series[valid_mask]
    clean_df["_abs_diff"] = clean_df["_end"] - clean_df["_start"]  # %p 차이

    used_rows = len(clean_df)
    missing_count = len(working_df) - used_rows
    total_excluded = excluded_agg_count + missing_count
    exclusion_reason = f"집계 행 {excluded_agg_count}개 제외, 두 시점 중 결측 행 {missing_count}개 제외"

    if used_rows == 0:
        raise ValueError(f"{start_col}년과 {end_col}년 모두 유효한 데이터를 가진 행이 없습니다.")

    # 가장 큰 폭으로 상승한 상위 N개국
    sorted_df = clean_df.sort_values(by="_abs_diff", ascending=False)
    top_gainers = []
    for rank, (_, row) in enumerate(sorted_df.head(top_n).iterrows(), 1):
        top_gainers.append({
            "rank": rank,
            "label": str(row[label_col]),
            "start_value": round(float(row["_start"]), 2),
            "end_value": round(float(row["_end"]), 2),
            "change_diff": round(float(row["_abs_diff"]), 2)
        })

    chart_data = {
        "type": "bar",
        "labels": [item["label"] for item in top_gainers],
        "datasets": [
            {
                "label": f"{start_col}년 대비 {end_col}년 증가폭 (%p)",
                "data": [item["change_diff"] for item in top_gainers],
                "backgroundColor": "#10b981"
            }
        ]
    }

    return {
        "tool": "change_rate",
        "reference_period": f"{start_col}~{end_col}년",
        "used_rows_count": used_rows,
        "excluded_rows_count": total_excluded,
        "exclusion_reason": exclusion_reason,
        "used_columns": [label_col, start_col, end_col],
        "unit": "%p (퍼센트포인트 증가폭)",
        "caution": f"비율 데이터 간의 단순 차이는 퍼센트(%)가 아닌 퍼센트포인트(%p)로 해석해야 합니다.",
        "result": {
            "start_period": start_col,
            "end_period": end_col,
            "top_gainers": top_gainers,
            "overall_avg_change": round(float(clean_df["_abs_diff"].mean()), 2)
        },
        "chart_data": chart_data
    }


# ==============================================================================
# 도구 10. distribution: 구간별 분포 (히스토그램)
# ==============================================================================
def distribution(df, params=None):
    params = params or {}

    # [필수 검증] 기준 시점 필수
    ref_period = params.get("reference_period")
    if not ref_period or str(ref_period).strip() == "":
        raise ValueError("도구 10(distribution) 실행 시 기준 시점(reference_period)은 필수 입력 항목입니다.")

    ref_col = str(ref_period).strip()
    if ref_col not in df.columns:
        raise ValueError(f"지정한 기준 시점 열 '{ref_col}'이(가) 데이터에 존재하지 않습니다.")

    exclude_aggregates = params.get("exclude_aggregates", True)
    bins_count = int(params.get("bins", 10))

    working_df = df.copy()
    excluded_agg_count = 0
    if exclude_aggregates:
        agg_indices, _ = detect_aggregate_indices(working_df)
        excluded_agg_count = len(agg_indices)
        working_df = working_df.drop(index=agg_indices, errors="ignore")

    series = pd.to_numeric(working_df[ref_col], errors="coerce").dropna()
    used_rows = len(series)
    missing_count = len(working_df) - used_rows
    total_excluded = excluded_agg_count + missing_count
    exclusion_reason = f"집계 행 {excluded_agg_count}개 제외, 결측치 {missing_count}개 제외"

    if used_rows == 0:
        raise ValueError(f"기준 시점 '{ref_col}'에 유효한 숫자 데이터가 없습니다.")

    counts, bin_edges = np.histogram(series, bins=bins_count)
    
    bin_labels = []
    distribution_details = []
    for i in range(len(counts)):
        low = round(float(bin_edges[i]), 1)
        high = round(float(bin_edges[i+1]), 1)
        label = f"{low}%~{high}%"
        cnt = int(counts[i])
        bin_labels.append(label)
        distribution_details.append({
            "range": label,
            "count": cnt,
            "ratio": round(cnt / used_rows * 100, 1)
        })

    chart_data = {
        "type": "bar",
        "labels": bin_labels,
        "datasets": [
            {
                "label": f"{ref_col}년 국가 수 분포",
                "data": [int(c) for c in counts],
                "backgroundColor": "#3b82f6"
            }
        ]
    }

    return {
        "tool": "distribution",
        "reference_period": ref_col,
        "used_rows_count": used_rows,
        "excluded_rows_count": total_excluded,
        "exclusion_reason": exclusion_reason,
        "used_columns": [ref_col],
        "unit": "개국 (빈도수)",
        "caution": f"분포의 봉우리(최빈 구간)를 확인하면 전 세계 국가들이 어느 보급률 구간에 가장 많이 몰려 있는지 알 수 있습니다.",
        "result": {
            "bins": bins_count,
            "distribution": distribution_details,
            "skewness": round(float(series.skew()), 2)
        },
        "chart_data": chart_data
    }


# ==============================================================================
# 도구 11. correlation_scatter: 두 숫자 열 상관계수 (산점도)
# ==============================================================================
def correlation_scatter(df, params=None):
    params = params or {}

    # [필수 검증] 기준 시점 필수
    ref_period = params.get("reference_period")
    if not ref_period or str(ref_period).strip() == "":
        raise ValueError("도구 11(correlation_scatter) 실행 시 기준 시점(reference_period)은 필수 입력 항목입니다.")

    col_y = str(params.get("column_y", ref_period)).strip()
    col_x = params.get("column_x")
    
    if not col_x:
        year_cols = detect_year_columns(df)
        if col_y in year_cols and len(year_cols) >= 6:
            idx = year_cols.index(col_y)
            col_x = year_cols[max(0, idx - 10)]
        else:
            num_cols = [c for c in df.select_dtypes(include=[np.number]).columns if c != col_y]
            col_x = num_cols[0] if num_cols else col_y

    col_x = str(col_x).strip()

    if col_x not in df.columns or col_y not in df.columns:
        raise ValueError(f"분석할 두 열('{col_x}', '{col_y}') 중 존재하지 않는 열이 있습니다.")

    label_col = params.get("label_column")
    if not label_col or label_col not in df.columns:
        text_cols = [c for c in df.columns if not pd.api.types.is_numeric_dtype(df[c])]
        label_col = text_cols[0] if text_cols else df.columns[0]

    exclude_aggregates = params.get("exclude_aggregates", True)

    working_df = df.copy()
    excluded_agg_count = 0
    if exclude_aggregates:
        agg_indices, _ = detect_aggregate_indices(working_df, label_col)
        excluded_agg_count = len(agg_indices)
        working_df = working_df.drop(index=agg_indices, errors="ignore")

    sx = pd.to_numeric(working_df[col_x], errors="coerce")
    sy = pd.to_numeric(working_df[col_y], errors="coerce")
    
    valid_mask = sx.notna() & sy.notna()
    clean_df = working_df[valid_mask].copy()
    clean_df["_x"] = sx[valid_mask]
    clean_df["_y"] = sy[valid_mask]

    used_rows = len(clean_df)
    missing_count = len(working_df) - used_rows
    total_excluded = excluded_agg_count + missing_count
    exclusion_reason = f"집계 행 {excluded_agg_count}개 제외, 두 변수 중 결측 행 {missing_count}개 제외"

    if used_rows < 3:
        raise ValueError("상관계수를 계산하기 위한 유효한 데이터 쌍(최소 3개)이 부족합니다.")

    corr_coef = float(clean_df["_x"].corr(clean_df["_y"]))

    scatter_points = []
    for _, row in clean_df.iterrows():
        scatter_points.append({
            "x": round(float(row["_x"]), 2),
            "y": round(float(row["_y"]), 2),
            "label": str(row[label_col])
        })

    chart_data = {
        "type": "scatter",
        "datasets": [
            {
                "label": f"{col_x} vs {col_y}",
                "data": scatter_points,
                "backgroundColor": "#2563eb"
            }
        ]
    }

    # [절대 규칙] "상관관계는 인과관계가 아니다" 주의사항 명시
    caution_msg = "주의: 상관관계는 인과관계가 아닙니다. 두 변수가 밀접한 상관성을 보인다고 해서 한쪽이 다른 쪽의 직접적인 원인이 되는 것은 아닙니다."

    return {
        "tool": "correlation_scatter",
        "reference_period": f"{col_x} vs {col_y}",
        "used_rows_count": used_rows,
        "excluded_rows_count": total_excluded,
        "exclusion_reason": exclusion_reason,
        "used_columns": [label_col, col_x, col_y],
        "unit": "피어슨 상관계수 (r)",
        "caution": caution_msg,
        "result": {
            "column_x": col_x,
            "column_y": col_y,
            "correlation_coefficient": round(corr_coef, 4),
            "strength": "매우 강한 양의 상관관계" if corr_coef > 0.7 else ("뚜렷한 양의 상관관계" if corr_coef > 0.4 else "약한 상관관계")
        },
        "chart_data": chart_data
    }


# ==============================================================================
# 도구 12. compare_one_vs_all: 특정 항목 vs 전체 평균·중앙값 (강조 막대)
# ==============================================================================
def compare_one_vs_all(df, params=None):
    params = params or {}

    # [필수 검증] 기준 시점 필수
    ref_period = params.get("reference_period")
    if not ref_period or str(ref_period).strip() == "":
        raise ValueError("도구 12(compare_one_vs_all) 실행 시 기준 시점(reference_period)은 필수 입력 항목입니다.")

    ref_col = str(ref_period).strip()
    if ref_col not in df.columns:
        raise ValueError(f"지정한 기준 시점 열 '{ref_col}'이(가) 데이터에 존재하지 않습니다.")

    label_col = params.get("label_column")
    if not label_col or label_col not in df.columns:
        text_cols = [c for c in df.columns if not pd.api.types.is_numeric_dtype(df[c])]
        label_col = text_cols[0] if text_cols else df.columns[0]

    target_item = params.get("target_item", "Korea, Rep.")
    exclude_aggregates = params.get("exclude_aggregates", True)

    working_df = df.copy()
    excluded_agg_count = 0
    if exclude_aggregates:
        agg_indices, _ = detect_aggregate_indices(working_df, label_col)
        excluded_agg_count = len(agg_indices)
        working_df = working_df.drop(index=agg_indices, errors="ignore")

    num_series = pd.to_numeric(working_df[ref_col], errors="coerce")
    clean_df = working_df[num_series.notna()].copy()
    clean_df["_val"] = num_series.dropna()

    used_rows = len(clean_df)
    missing_count = len(working_df) - used_rows
    total_excluded = excluded_agg_count + missing_count
    exclusion_reason = f"집계 행 {excluded_agg_count}개 제외, 결측치 {missing_count}개 제외"

    # 타깃 항목 검색 (대소문자 무시 부분 일치 지원)
    target_matches = clean_df[clean_df[label_col].astype(str).str.contains(target_item, case=False, na=False)]
    if target_matches.empty:
        raise ValueError(f"선택한 대상 '{target_item}'을(를) {ref_col}년 데이터에서 찾을 수 없습니다.")

    target_row = target_matches.iloc[0]
    target_actual_name = str(target_row[label_col])
    target_value = float(target_row["_val"])

    sorted_df = clean_df.sort_values(by="_val", ascending=False).reset_index(drop=True)
    rank = int(sorted_df[sorted_df[label_col] == target_actual_name].index[0]) + 1
    percentile = round((1 - (rank / used_rows)) * 100, 1)

    mean_val = float(clean_df["_val"].mean())
    median_val = float(clean_df["_val"].median())
    top1_val = float(sorted_df.iloc[0]["_val"])
    top1_name = str(sorted_df.iloc[0][label_col])

    chart_data = {
        "type": "bar",
        "labels": [target_actual_name, "전체 평균", "전체 중앙값", f"전체 1위 ({top1_name})"],
        "datasets": [
            {
                "label": f"{ref_col}년 비교",
                "data": [
                    round(target_value, 2),
                    round(mean_val, 2),
                    round(median_val, 2),
                    round(top1_val, 2)
                ],
                "backgroundColor": ["#ef4444", "#3b82f6", "#60a5fa", "#10b981"]
            }
        ]
    }

    return {
        "tool": "compare_one_vs_all",
        "reference_period": ref_col,
        "used_rows_count": used_rows,
        "excluded_rows_count": total_excluded,
        "exclusion_reason": exclusion_reason,
        "used_columns": [label_col, ref_col],
        "unit": "%",
        "caution": f"비교 대상({target_actual_name})은 전체 {used_rows}개국 중 {rank}위(상위 {100 - percentile}%)에 해당합니다.",
        "result": {
            "target": target_actual_name,
            "target_value": round(target_value, 2),
            "rank": rank,
            "total_entities": used_rows,
            "percentile": percentile,
            "overall_mean": round(mean_val, 2),
            "overall_median": round(median_val, 2),
            "diff_from_mean": round(target_value - mean_val, 2),
            "diff_from_median": round(target_value - median_val, 2)
        },
        "chart_data": chart_data
    }


# ==============================================================================
# 도구 카탈로그 레지스트리 (TOOL_CATALOG)
# ==============================================================================
TOOL_CATALOG = {
    "reshape_wide_to_long": {
        "func": reshape_wide_to_long,
        "name": "와이드-롱 구조 변환",
        "description": "연도 열들을 행으로 접어 시계열 분석 및 차트 시각화에 적합한 형태로 변환합니다.",
        "requires_reference_period": False
    },
    "quality_report": {
        "func": quality_report,
        "name": "데이터 종합 품질 진단",
        "description": "결측치 비율, 중복 행, 이상 열 이름, 데이터 타입을 종합 점검합니다.",
        "requires_reference_period": False
    },
    "year_coverage": {
        "func": year_coverage,
        "name": "연도별 데이터 충실도 분석",
        "description": "각 연도별 데이터 보유 현황을 파악하고 최적의 기준 분석 연도를 추천합니다.",
        "requires_reference_period": False
    },
    "detect_aggregates": {
        "func": detect_aggregates,
        "name": "집계 묶음 행 감지",
        "description": "국가가 아닌 World, 지역/소득 그룹 등 왜곡을 유발하는 집계 행을 식별합니다.",
        "requires_reference_period": False
    },
    "describe_numeric": {
        "func": describe_numeric,
        "name": "기술 통계 요약",
        "description": "특정 연도의 개수, 평균, 중앙값, 표준편차, 사분위수를 산출합니다.",
        "requires_reference_period": False
    },
    "trend_line": {
        "func": trend_line,
        "name": "선택 항목 시계열 추이 분석",
        "description": "주요 비교 국가들의 연도별 변화 추이를 꺾은선 차트로 시각화합니다.",
        "requires_reference_period": False
    },
    "top_bottom_n": {
        "func": top_bottom_n,
        "name": "상위·하위 N 순위 분석",
        "description": "기준 시점 기준 최상위 및 최하위 N개국의 순위를 비교합니다.",
        "requires_reference_period": True
    },
    "group_summary": {
        "func": group_summary,
        "name": "그룹/구간별 요약 집계",
        "description": "보급률 구간 또는 그룹별 국가 수 및 평균값을 집계합니다.",
        "requires_reference_period": True
    },
    "change_rate": {
        "func": change_rate,
        "name": "두 시점 간 변화량·성장폭 분석",
        "description": "과거 시점과 기준 시점 사이 가장 크게 성장한 상위 국가를 도출합니다.",
        "requires_reference_period": True
    },
    "distribution": {
        "func": distribution,
        "name": "구간별 빈도 분포 분석",
        "description": "국가들의 수치가 어느 구간에 집중되어 있는지 히스토그램으로 확인합니다.",
        "requires_reference_period": True
    },
    "correlation_scatter": {
        "func": correlation_scatter,
        "name": "두 시점/변수 상관관계 분석",
        "description": "두 변수 간 피어슨 상관계수를 계산하고 산점도로 분포를 파악합니다.",
        "requires_reference_period": True
    },
    "compare_one_vs_all": {
        "func": compare_one_vs_all,
        "name": "특정 대상 vs 전체 대푯값 비교",
        "description": "한국 등 특정 국가의 수치를 전 세계 평균, 중앙값, 1위와 대조합니다.",
        "requires_reference_period": True
    }
}
