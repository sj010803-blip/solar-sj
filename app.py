import streamlit as st
import os
import json
import re
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import seaborn as sns
from google import genai
import PySAM.Pvwattsv8 as pvwatts


# ============================================================
# 1. 페이지 기본 설정
# ============================================================

st.set_page_config(
    page_title="AI 태양광 컨설턴트",
    page_icon="☀️",
    layout="wide"
)

plt.rcParams["font.family"] = "DejaVu Sans"


# ============================================================
# 2. 지역별 EPW 파일 매핑
# ============================================================

WEATHER_FILES = {
    "서울": "KOR_Seoul.epw",
    "춘천": "KOR_Chuncheon.epw",
    "강릉": "KOR_Kangnung.epw",
    "부산": "KOR_Busan.epw",
    "대전": "KOR_Daejeon.epw",
    "광주": "KOR_Gwangju.epw",
    "제주": "KOR_Jeju.epw",
}


# ============================================================
# 3. 경제성 평가 기준 데이터
# ============================================================

KEPCO_RESIDENTIAL_LOW_VOLTAGE = {
    "source": "KEPCO 주택용 전력(저압) 전기요금표",
    "basic_charge": [
        (100, 400),
        (200, 890),
        (300, 1560),
        (400, 3750),
        (500, 7110),
        (float("inf"), 12600),
    ],
    "energy_rate": [
        (100, 59.10),
        (200, 122.60),
        (300, 183.00),
        (400, 273.20),
        (500, 406.70),
        (float("inf"), 690.80),
    ],
}

KPX_SMP_2025_LAND = {
    "source": "KPX 월별 SMP, 2025년 육지 SMP",
    "unit": "원/kWh",
    "monthly": {
        1: 117.11,
        2: 116.39,
        3: 113.12,
        4: 124.63,
        5: 125.50,
        6: 118.02,
        7: 120.39,
        8: 117.39,
        9: 112.90,
        10: 101.53,
        11: 94.80,
        12: 90.43,
    },
}

KPX_SMP_2025_LAND["annual_average"] = (
    sum(KPX_SMP_2025_LAND["monthly"].values()) / 12
)

REC_PRICE_REFERENCE = {
    "source": "KPX 오늘의 REC 현물시장",
    "unit": "원/REC",
    "price": 71409,
    "note": "2026-06-04 KPX 오늘의 REC 평균가 기준. 1REC = 1MWh, 가중치 적용 전 기준.",
}

KEA_HOME_SUBSIDY_2025 = {
    "source": "한국에너지공단 신재생에너지보급 주택지원사업 보조금 단가 예시",
    "unit": "원/kW",
    "none": 0,
    "apartment_pv_fixed_low_carbon": 517000,
    "island_area": 594000,
    "note": "보조금은 총 설치비가 아니며, 사업연도, 대상, 설비형태, 지역에 따라 달라질 수 있음.",
}

EPW_SOURCE_TEXT = "한국건축친환경설비학회(KIAEBS) 대한민국 표준기상데이터 EPW"


# ============================================================
# 4. Gemini 안정화 설정
# ============================================================

GEMINI_MODELS = [
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.5-flash",
]


def generate_content_with_fallback(client, prompt):
    """
    Gemini 모델이 503 오류 등으로 실패할 경우,
    여러 모델을 순차적으로 재시도하는 함수.
    """
    last_error = None

    for model_name in GEMINI_MODELS:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt
            )
            return response.text
        except Exception as e:
            last_error = e
            continue

    raise last_error


# ============================================================
# 5. 유틸리티 함수
# ============================================================

def normalize_location(location_text):
    """
    사용자가 입력한 지역명에서 지원 가능한 대표 지역명을 찾는 함수
    """
    if not location_text:
        return "춘천"

    text = str(location_text).replace(" ", "")

    location_keywords = {
        "서울": ["서울", "서울시", "서울특별시"],
        "춘천": ["춘천", "춘천시", "강원도춘천"],
        "강릉": ["강릉", "강릉시", "강원도강릉"],
        "부산": ["부산", "부산시", "부산광역시"],
        "대전": ["대전", "대전시", "대전광역시"],
        "광주": ["광주", "광주시", "광주광역시"],
        "제주": ["제주", "제주시", "제주도", "제주특별자치도"],
    }

    for standard_name, keywords in location_keywords.items():
        for keyword in keywords:
            if keyword in text:
                return standard_name

    return "춘천"


def get_weather_file(location_name):
    """
    지역명에 맞는 EPW 파일 경로를 반환하는 함수
    """
    standard_location = normalize_location(location_name)
    file_name = WEATHER_FILES.get(standard_location, WEATHER_FILES["춘천"])
    epw_path = os.path.join(os.path.dirname(__file__), file_name)

    if not os.path.exists(epw_path):
        raise FileNotFoundError(
            f"{standard_location} 지역의 EPW 파일을 찾을 수 없습니다: {file_name}"
        )

    return standard_location, epw_path, file_name


def calculate_kepco_residential_bill(monthly_usage_kwh):
    """
    KEPCO 주택용 저압 누진요금 단순 계산 함수.
    기본요금 + 전력량요금만 계산한다.
    부가세, 전력산업기반기금, 기후환경요금, 연료비조정요금은 제외한다.
    """
    usage = max(float(monthly_usage_kwh), 0)

    basic_charge = 0
    for limit, charge in KEPCO_RESIDENTIAL_LOW_VOLTAGE["basic_charge"]:
        if usage <= limit:
            basic_charge = charge
            break

    energy_charge = 0
    previous_limit = 0

    for limit, rate in KEPCO_RESIDENTIAL_LOW_VOLTAGE["energy_rate"]:
        if usage > previous_limit:
            tier_usage = min(usage, limit) - previous_limit
            energy_charge += tier_usage * rate
            previous_limit = limit

        if usage <= limit:
            break

    return basic_charge + energy_charge


def calculate_self_consumption_economics(
    monthly_generation_kwh,
    monthly_usage_kwh,
    self_consumption_rate,
    install_cost_per_kw,
    capacity_kw,
    subsidy_per_kw
):
    """
    자가소비형 태양광 경제성 계산.
    월별 발전량 중 자가소비율만큼 전기사용량을 차감한다고 가정한다.
    """
    total_before_bill = 0
    total_after_bill = 0
    total_self_consumed = 0

    for gen in monthly_generation_kwh:
        before_usage = monthly_usage_kwh

        usable_generation = gen * (self_consumption_rate / 100)
        self_consumed = min(usable_generation, before_usage)
        after_usage = max(before_usage - self_consumed, 0)

        before_bill = calculate_kepco_residential_bill(before_usage)
        after_bill = calculate_kepco_residential_bill(after_usage)

        total_before_bill += before_bill
        total_after_bill += after_bill
        total_self_consumed += self_consumed

    annual_saving = total_before_bill - total_after_bill
    gross_install_cost = install_cost_per_kw * capacity_kw
    total_subsidy = subsidy_per_kw * capacity_kw
    net_install_cost = max(gross_install_cost - total_subsidy, 0)

    payback_year = net_install_cost / annual_saving if annual_saving > 0 else 0

    return {
        "annual_bill_before": total_before_bill,
        "annual_bill_after": total_after_bill,
        "annual_saving": annual_saving,
        "self_consumed_kwh": total_self_consumed,
        "gross_install_cost": gross_install_cost,
        "total_subsidy": total_subsidy,
        "net_install_cost": net_install_cost,
        "payback_year": payback_year,
    }


def calculate_power_business_economics(
    annual_energy_kwh,
    smp_price,
    rec_price,
    rec_weight,
    install_cost_per_kw,
    capacity_kw
):
    """
    발전사업형 수익 계산.
    SMP 수익 + REC 수익을 단순 합산한다.
    """
    smp_revenue = annual_energy_kwh * smp_price
    rec_revenue = (annual_energy_kwh / 1000) * rec_price * rec_weight
    total_revenue = smp_revenue + rec_revenue

    gross_install_cost = install_cost_per_kw * capacity_kw
    payback_year = gross_install_cost / total_revenue if total_revenue > 0 else 0

    return {
        "smp_revenue": smp_revenue,
        "rec_revenue": rec_revenue,
        "total_revenue": total_revenue,
        "gross_install_cost": gross_install_cost,
        "payback_year": payback_year,
    }


# ============================================================
# 6. PySAM 시뮬레이션 함수
# ============================================================

def run_simulation(capacity, tilt, azimuth, location_name):
    """
    PySAM PVWatts를 사용해 지역별 EPW 기상데이터 기반 태양광 발전량을 계산하는 함수
    """
    standard_location, epw_path, epw_file_name = get_weather_file(location_name)

    system = pvwatts.default("PVWattsNone")

    system.SolarResource.assign({
        "solar_resource_file": epw_path
    })

    system.SystemDesign.assign({
        "system_capacity": capacity,
        "module_type": 0,
        "array_type": 0,
        "tilt": tilt,
        "azimuth": azimuth,
        "dc_ac_ratio": 1.1,
        "inv_eff": 96,
        "losses": 14.0757,
        "gcr": 0.4
    })

    system.execute()

    annual = system.Outputs.ac_annual
    monthly = list(system.Outputs.ac_monthly)

    return {
        "location": standard_location,
        "epw_file": epw_file_name,
        "tilt": tilt,
        "azimuth": azimuth,
        "capacity": capacity,
        "annual_energy_kwh": annual,
        "ac_monthly_kwh": monthly
    }


def optimize_design(capacity, location_name):
    """
    기준 설계와 여러 경사각 후보를 비교하여 최적 설계를 찾는 함수
    """
    results = []

    baseline = run_simulation(capacity, 90, 180, location_name)
    baseline["Type"] = "Case 1: Baseline Vertical"
    results.append(baseline)

    for t in [15, 20, 25, 30, 35, 40]:
        sim = run_simulation(capacity, t, 180, location_name)
        sim["Type"] = "Optimization Search"
        results.append(sim)

    return results


# ============================================================
# 7. 사용자 입력 파싱 및 AI 보고서
# ============================================================

def parse_user_input(client, user_input):
    """
    사용자 질문에서 지역명과 용량을 추출.
    1차: 규칙 기반 추출
    2차: Gemini 사용
    Gemini가 실패해도 기본값으로 작동한다.
    """
    parsed_location = "춘천"
    capacity_kw = 5.0

    # 1. 지역명 규칙 기반 추출
    for loc in WEATHER_FILES.keys():
        if loc in user_input:
            parsed_location = loc
            break

    # 2. 용량 정규식 추출
    capacity_match = re.search(r"(\d+\.?\d*)\s*kW", user_input, re.IGNORECASE)
    if capacity_match:
        capacity_kw = float(capacity_match.group(1))

    # 3. 규칙 기반으로 지역과 용량을 충분히 찾았다면 Gemini 호출 생략
    if parsed_location != "춘천" or capacity_match:
        return normalize_location(parsed_location), capacity_kw

    # 4. Gemini가 있을 때만 추가 파싱 시도
    if client is None:
        return normalize_location(parsed_location), capacity_kw

    try:
        parse_prompt = f"""
다음 사용자 문장에서 지역명과 태양광 설비 용량(kW)을 추출해 주세요.

사용자 문장:
{user_input}

반드시 아래 JSON 형식으로만 답하세요.
설명문은 쓰지 마세요.

{{
  "location": "지역명",
  "capacity_kw": 숫자
}}
"""

        parse_text = generate_content_with_fallback(client, parse_prompt)

        match = re.search(r"\{.*\}", parse_text, re.DOTALL)

        if match:
            data = json.loads(match.group(0))

            loc = data.get("location")
            if loc:
                parsed_location = loc

            cap = data.get("capacity_kw")
            if cap:
                capacity_kw = float(cap)

    except Exception:
        pass

    parsed_location = normalize_location(parsed_location)

    return parsed_location, capacity_kw


def get_ai_analysis(
    client,
    parsed_location,
    capacity,
    results,
    analysis_mode,
    self_economics,
    business_economics,
    economic_inputs
):
    """
    PySAM과 경제성 계산 결과를 바탕으로 LLM 보고서를 생성한다.
    """
    baseline = next((r for r in results if r["Type"].startswith("Case 1")), results[0])
    optimized = max(results, key=lambda x: x["annual_energy_kwh"])

    improvement = (
        (optimized["annual_energy_kwh"] - baseline["annual_energy_kwh"])
        / baseline["annual_energy_kwh"]
        * 100
        if baseline["annual_energy_kwh"] > 0
        else 0
    )

    if analysis_mode == "자가소비형":
        economics_text = f"""
[자가소비형 경제성 결과]
- 월평균 전기사용량: {economic_inputs["monthly_usage_kwh"]:.1f} kWh/month
- 자가소비율: {economic_inputs["self_consumption_rate"]:.1f}%
- 연간 자가소비 발전량: {self_economics["self_consumed_kwh"]:.2f} kWh/year
- 설치 전 연간 전기요금: {self_economics["annual_bill_before"]:,.0f}원/year
- 설치 후 연간 전기요금: {self_economics["annual_bill_after"]:,.0f}원/year
- 연간 전기요금 절감액: {self_economics["annual_saving"]:,.0f}원/year
- 총 설치비: {self_economics["gross_install_cost"]:,.0f}원
- 보조금 반영액: {self_economics["total_subsidy"]:,.0f}원
- 순 설치비: {self_economics["net_install_cost"]:,.0f}원
- 단순 회수기간: {self_economics["payback_year"]:.1f}년
"""
    else:
        economics_text = f"""
[발전사업형 경제성 결과]
- 적용 SMP: {economic_inputs["smp_price"]:.2f}원/kWh
- 적용 REC 가격: {economic_inputs["rec_price"]:,.0f}원/REC
- REC 가중치: {economic_inputs["rec_weight"]:.2f}
- SMP 연간 수익: {business_economics["smp_revenue"]:,.0f}원/year
- REC 연간 수익: {business_economics["rec_revenue"]:,.0f}원/year
- 연간 총수익: {business_economics["total_revenue"]:,.0f}원/year
- 총 설치비: {business_economics["gross_install_cost"]:,.0f}원
- 단순 회수기간: {business_economics["payback_year"]:.1f}년
"""

    prompt = f"""
당신은 대한민국 최고의 'AI 태양광 발전 컨설턴트'입니다.
초보자도 쉽게 이해할 수 있으면서도, 공학적 깊이가 담긴 태양광 경제성 평가 보고서를 작성해 주세요.

[시뮬레이션 조건]
- 지역: {parsed_location}
- 사용 EPW 파일: {optimized["epw_file"]}
- EPW 출처: {EPW_SOURCE_TEXT}
- 시스템 용량: {capacity} kW
- Baseline 조건: 수직 설치, 남향
- Optimized 조건: 후보 경사각 중 최적 설계
- 경제성 평가 모드: {analysis_mode}

[PySAM PVWatts 시뮬레이션 결과]
- Baseline 기준 발전량: {baseline["annual_energy_kwh"]:.2f} kWh/year
- Optimized 최적 발전량: {optimized["annual_energy_kwh"]:.2f} kWh/year
- 최적 경사각: {optimized["tilt"]}도
- 최적 방위각: {optimized["azimuth"]}도
- 발전량 개선율: {improvement:.2f}%

{economics_text}

[사용한 경제성 데이터 출처]
- 전기요금: {KEPCO_RESIDENTIAL_LOW_VOLTAGE["source"]}
- SMP: {KPX_SMP_2025_LAND["source"]}
- REC: {REC_PRICE_REFERENCE["source"]}
- 보조금: {KEA_HOME_SUBSIDY_2025["source"]}

[보고서 양식]
### 1. ☀️ 시스템 종합 성능
### 2. 📐 최적 설계 조건
### 3. 💰 경제성 평가
### 4. 🌱 환경 보호 기여도
### 5. 📚 데이터 출처 및 한계
### 6. 💡 컨설턴트 종합 의견

주의:
- 발전량은 PySAM PVWatts와 EPW 기상데이터 기반의 시뮬레이션 결과입니다.
- 경제성은 공식자료 기반 입력값을 사용했지만, 실제 설치비, 보조금, 전기요금, SMP, REC는 시점과 조건에 따라 달라질 수 있음을 설명해 주세요.
- LLM이 발전량을 직접 계산한 것이 아니라, PySAM과 경제성 계산 결과를 해석한 것임을 명확히 설명해 주세요.
- 과장하지 말고, 산정 조건과 한계를 함께 설명해 주세요.
"""

    return generate_content_with_fallback(client, prompt)


# ============================================================
# 8. UI 구성
# ============================================================

with st.sidebar:
    st.header("⚙️ 기본 설정")

    api_key_input = st.text_input(
        "Gemini API 키를 입력하세요",
        type="password"
    )

    if api_key_input:
        st.success("Gemini API 키가 입력되었습니다.")
    else:
        st.warning("API 키가 없어도 PySAM 계산은 가능하지만, AI 보고서는 생성되지 않습니다.")

    st.divider()

    st.subheader("📍 지원 지역")
    st.write(", ".join(WEATHER_FILES.keys()))
    st.caption("사용자가 입력한 지역명에 따라 해당 EPW 기상데이터를 자동 선택합니다.")

    st.divider()

    st.subheader("💰 경제성 평가 설정")

    analysis_mode = st.radio(
        "경제성 평가 모드",
        ["자가소비형", "발전사업형"],
        index=0
    )

    install_cost_per_kw = st.number_input(
        "설치비 단가(원/kW)",
        min_value=500_000,
        max_value=5_000_000,
        value=1_500_000,
        step=100_000,
        help="실제 논문 분석에서는 업체 견적서 또는 공공자료 기반 기준값을 적용하는 것이 좋습니다."
    )

    if analysis_mode == "자가소비형":
        monthly_usage_kwh = st.number_input(
            "월평균 전기사용량(kWh/month)",
            min_value=50.0,
            max_value=2000.0,
            value=350.0,
            step=10.0
        )

        self_consumption_rate = st.slider(
            "자가소비율(%)",
            min_value=0,
            max_value=100,
            value=80,
            step=5
        )

        subsidy_option = st.selectbox(
            "보조금 적용",
            [
                "보조금 없음",
                "공동주택 태양광 고정식 저탄소모듈 예시",
                "도서지역 예시"
            ]
        )

        if subsidy_option == "공동주택 태양광 고정식 저탄소모듈 예시":
            subsidy_per_kw = KEA_HOME_SUBSIDY_2025["apartment_pv_fixed_low_carbon"]
        elif subsidy_option == "도서지역 예시":
            subsidy_per_kw = KEA_HOME_SUBSIDY_2025["island_area"]
        else:
            subsidy_per_kw = 0

    else:
        smp_price = st.number_input(
            "SMP 단가(원/kWh)",
            min_value=0.0,
            max_value=500.0,
            value=float(KPX_SMP_2025_LAND["annual_average"]),
            step=1.0
        )

        rec_price = st.number_input(
            "REC 가격(원/REC)",
            min_value=0,
            max_value=300_000,
            value=REC_PRICE_REFERENCE["price"],
            step=1000
        )

        rec_weight = st.number_input(
            "REC 가중치",
            min_value=0.0,
            max_value=2.0,
            value=1.0,
            step=0.1
        )

    st.divider()

    with st.expander("📚 적용 데이터 출처"):
        st.write(f"- EPW: {EPW_SOURCE_TEXT}")
        st.write(f"- 전기요금: {KEPCO_RESIDENTIAL_LOW_VOLTAGE['source']}")
        st.write(f"- SMP: {KPX_SMP_2025_LAND['source']}")
        st.write(f"- REC: {REC_PRICE_REFERENCE['source']}")
        st.write(f"- 보조금: {KEA_HOME_SUBSIDY_2025['source']}")
        st.caption("전기요금, SMP, REC, 보조금 단가는 변동 가능하므로 논문에는 기준일과 접속일을 함께 명시하세요.")


st.title("☀️ SAM-Copilot 태양광 경제성 분석기")
st.markdown(
    "**PySAM PVWatts, 지역별 EPW 기상데이터, 공식 경제성 자료, LLM을 결합하여 "
    "태양광 발전량과 경제성을 분석합니다.**"
)

user_input = st.chat_input("질문을 입력하세요... (예: 춘천에 5kW 태양광 설치하면 어때?)")


# ============================================================
# 9. 메인 실행
# ============================================================

if user_input:
    st.chat_message("user").write(user_input)

    with st.chat_message("assistant"):
        with st.spinner("지역 EPW 선택, PySAM 시뮬레이션, 경제성 평가를 수행 중입니다..."):
            try:
                client = None
                if api_key_input:
                    client = genai.Client(api_key=api_key_input)

                # 1. 사용자 입력 파싱
                parsed_location, capacity_kw = parse_user_input(client, user_input)

                # 2. PySAM 시뮬레이션
                sim_results = optimize_design(capacity_kw, parsed_location)

                baseline = next(
                    (r for r in sim_results if r["Type"].startswith("Case 1")),
                    sim_results[0]
                )

                optimized = max(
                    sim_results,
                    key=lambda x: x["annual_energy_kwh"]
                )

                used_location = optimized["location"]
                used_epw_file = optimized["epw_file"]

                improvement = (
                    (optimized["annual_energy_kwh"] - baseline["annual_energy_kwh"])
                    / baseline["annual_energy_kwh"]
                    * 100
                    if baseline["annual_energy_kwh"] > 0
                    else 0
                )

                # 3. 경제성 계산
                self_economics = None
                business_economics = None

                if analysis_mode == "자가소비형":
                    self_economics = calculate_self_consumption_economics(
                        monthly_generation_kwh=optimized["ac_monthly_kwh"],
                        monthly_usage_kwh=monthly_usage_kwh,
                        self_consumption_rate=self_consumption_rate,
                        install_cost_per_kw=install_cost_per_kw,
                        capacity_kw=capacity_kw,
                        subsidy_per_kw=subsidy_per_kw
                    )

                    economic_inputs = {
                        "monthly_usage_kwh": monthly_usage_kwh,
                        "self_consumption_rate": self_consumption_rate,
                        "install_cost_per_kw": install_cost_per_kw,
                        "subsidy_per_kw": subsidy_per_kw,
                        "smp_price": KPX_SMP_2025_LAND["annual_average"],
                        "rec_price": REC_PRICE_REFERENCE["price"],
                        "rec_weight": 1.0,
                    }

                else:
                    business_economics = calculate_power_business_economics(
                        annual_energy_kwh=optimized["annual_energy_kwh"],
                        smp_price=smp_price,
                        rec_price=rec_price,
                        rec_weight=rec_weight,
                        install_cost_per_kw=install_cost_per_kw,
                        capacity_kw=capacity_kw
                    )

                    economic_inputs = {
                        "monthly_usage_kwh": 0,
                        "self_consumption_rate": 0,
                        "install_cost_per_kw": install_cost_per_kw,
                        "subsidy_per_kw": 0,
                        "smp_price": smp_price,
                        "rec_price": rec_price,
                        "rec_weight": rec_weight,
                    }

                # 4. AI 보고서 생성
                if client is not None:
                    try:
                        final_report = get_ai_analysis(
                            client=client,
                            parsed_location=used_location,
                            capacity=capacity_kw,
                            results=sim_results,
                            analysis_mode=analysis_mode,
                            self_economics=self_economics,
                            business_economics=business_economics,
                            economic_inputs=economic_inputs
                        )
                        st.markdown(final_report)
                    except Exception as ai_error:
                        st.warning(
                            "Gemini API가 일시적으로 응답하지 않아 AI 보고서는 생략했습니다. "
                            "아래 PySAM 시뮬레이션 및 경제성 계산 결과는 정상적으로 표시됩니다."
                        )
                        st.caption(f"AI 오류 내용: {ai_error}")
                else:
                    st.warning(
                        "Gemini API 키가 입력되지 않아 AI 보고서는 생략했습니다. "
                        "아래 PySAM 시뮬레이션 및 경제성 계산 결과만 표시합니다."
                    )

                st.divider()

                # 5. 핵심 지표 출력
                if analysis_mode == "자가소비형":
                    col1, col2, col3, col4 = st.columns(4)

                    with col1:
                        st.metric(
                            "Optimized 발전량",
                            f"{optimized['annual_energy_kwh']:,.0f} kWh/year"
                        )

                    with col2:
                        st.metric(
                            "발전량 개선율",
                            f"{improvement:.2f}%"
                        )

                    with col3:
                        st.metric(
                            "연간 절감액",
                            f"{self_economics['annual_saving']:,.0f} 원/year"
                        )

                    with col4:
                        st.metric(
                            "단순 회수기간",
                            f"{self_economics['payback_year']:.1f} 년"
                        )

                else:
                    col1, col2, col3, col4 = st.columns(4)

                    with col1:
                        st.metric(
                            "Optimized 발전량",
                            f"{optimized['annual_energy_kwh']:,.0f} kWh/year"
                        )

                    with col2:
                        st.metric(
                            "SMP 수익",
                            f"{business_economics['smp_revenue']:,.0f} 원/year"
                        )

                    with col3:
                        st.metric(
                            "REC 수익",
                            f"{business_economics['rec_revenue']:,.0f} 원/year"
                        )

                    with col4:
                        st.metric(
                            "단순 회수기간",
                            f"{business_economics['payback_year']:.1f} 년"
                        )

                st.info(f"선택된 지역: {used_location} / 사용 EPW 파일: {used_epw_file}")

                # 6. 월별 발전량 그래프
                st.subheader(f"📉 {used_location} ({capacity_kw}kW) 월별 발전량 비교")

                df_monthly = pd.DataFrame({
                    "Month": [f"{i}" for i in range(1, 13)],
                    "Base Vertical": baseline["ac_monthly_kwh"],
                    "AI Optimized": optimized["ac_monthly_kwh"]
                })

                df_long = pd.melt(
                    df_monthly,
                    id_vars=["Month"],
                    value_vars=["Base Vertical", "AI Optimized"],
                    var_name="System Type",
                    value_name="Energy Generation (kWh)"
                )

                fig, ax = plt.subplots(figsize=(10, 5))

                sns.barplot(
                    x="Month",
                    y="Energy Generation (kWh)",
                    hue="System Type",
                    data=df_long,
                    palette=["#4A90E2", "#E74C3C"],
                    ax=ax
                )

                ax.set_xlabel("Month")
                ax.set_ylabel("Monthly AC Energy (kWh)")
                ax.set_title("Monthly PV Energy Generation Comparison")
                ax.yaxis.set_major_formatter(ticker.StrMethodFormatter("{x:,.0f}"))

                st.pyplot(fig)

                # 7. 경사각별 발전량 그래프
                st.subheader("📐 경사각별 연간 발전량 비교")

                df_tilt = pd.DataFrame([
                    {
                        "Tilt": r["tilt"],
                        "Annual Energy (kWh/year)": r["annual_energy_kwh"],
                        "Type": r["Type"]
                    }
                    for r in sim_results
                ])

                fig2, ax2 = plt.subplots(figsize=(10, 5))

                sns.barplot(
                    x="Tilt",
                    y="Annual Energy (kWh/year)",
                    data=df_tilt,
                    ax=ax2
                )

                ax2.set_xlabel("Tilt Angle (degree)")
                ax2.set_ylabel("Annual AC Energy (kWh/year)")
                ax2.set_title("Annual Energy by Tilt Angle")
                ax2.yaxis.set_major_formatter(ticker.StrMethodFormatter("{x:,.0f}"))

                st.pyplot(fig2)

                # 8. 경제성 상세 표
                st.subheader("💰 경제성 계산 상세")

                if analysis_mode == "자가소비형":
                    econ_df = pd.DataFrame({
                        "항목": [
                            "총 설치비",
                            "보조금 반영액",
                            "순 설치비",
                            "설치 전 연간 전기요금",
                            "설치 후 연간 전기요금",
                            "연간 절감액",
                            "연간 자가소비 발전량",
                            "단순 회수기간"
                        ],
                        "값": [
                            f"{self_economics['gross_install_cost']:,.0f} 원",
                            f"{self_economics['total_subsidy']:,.0f} 원",
                            f"{self_economics['net_install_cost']:,.0f} 원",
                            f"{self_economics['annual_bill_before']:,.0f} 원/year",
                            f"{self_economics['annual_bill_after']:,.0f} 원/year",
                            f"{self_economics['annual_saving']:,.0f} 원/year",
                            f"{self_economics['self_consumed_kwh']:,.0f} kWh/year",
                            f"{self_economics['payback_year']:.1f} 년"
                        ]
                    })

                else:
                    econ_df = pd.DataFrame({
                        "항목": [
                            "총 설치비",
                            "적용 SMP",
                            "적용 REC 가격",
                            "REC 가중치",
                            "SMP 연간 수익",
                            "REC 연간 수익",
                            "연간 총수익",
                            "단순 회수기간"
                        ],
                        "값": [
                            f"{business_economics['gross_install_cost']:,.0f} 원",
                            f"{smp_price:.2f} 원/kWh",
                            f"{rec_price:,.0f} 원/REC",
                            f"{rec_weight:.2f}",
                            f"{business_economics['smp_revenue']:,.0f} 원/year",
                            f"{business_economics['rec_revenue']:,.0f} 원/year",
                            f"{business_economics['total_revenue']:,.0f} 원/year",
                            f"{business_economics['payback_year']:.1f} 년"
                        ]
                    })

                st.dataframe(econ_df, use_container_width=True)

                # 9. 데이터 출처 및 한계
                st.subheader("📚 데이터 출처 및 한계")

                st.markdown(f"""
- **EPW 기상데이터**: {EPW_SOURCE_TEXT}
- **전기요금**: {KEPCO_RESIDENTIAL_LOW_VOLTAGE["source"]}
- **SMP**: {KPX_SMP_2025_LAND["source"]}, 코드 내 연평균 {KPX_SMP_2025_LAND["annual_average"]:.2f}원/kWh 적용
- **REC**: {REC_PRICE_REFERENCE["source"]}, 코드 내 {REC_PRICE_REFERENCE["price"]:,.0f}원/REC 적용
- **보조금**: {KEA_HOME_SUBSIDY_2025["source"]}

주의: 본 결과는 PySAM PVWatts 모델과 EPW 기상데이터 기반 예측값입니다. 실제 발전량과 경제성은 음영, 모듈 성능, 인버터, 시공 품질, 유지관리, 계통 조건, 전기요금, SMP, REC 가격, 보조금 정책에 따라 달라질 수 있습니다.
""")

            except Exception as e:
                st.error(f"⚠️ 처리 중 오류가 발생했습니다: {e}")
