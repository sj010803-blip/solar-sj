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


# --- 페이지 기본 설정 ---
st.set_page_config(page_title="AI 태양광 컨설턴트", page_icon="☀️", layout="wide")


# --- 그래프 폰트 설정 ---
# Streamlit Cloud에는 한글 폰트가 없을 수 있으므로 그래프 내부 라벨은 영어로 표시
plt.rcParams["font.family"] = "DejaVu Sans"


# --- PySAM 시뮬레이션 함수 ---
def run_simulation(capacity, tilt, azimuth):
    """
    실제 PySAM PVWatts를 사용해 태양광 발전량을 계산하는 함수
    현재는 저장소에 업로드된 강릉 EPW 파일을 사용함
    """
    system = pvwatts.default("PVWattsNone")

    epw_path = os.path.join(os.path.dirname(__file__), "KOR_Kangnung.471050_IWEC.epw")

    if not os.path.exists(epw_path):
        raise FileNotFoundError(f"EPW 파일을 찾을 수 없습니다: {epw_path}")

    # 기상 데이터 설정
    system.SolarResource.assign({
        "solar_resource_file": epw_path
    })

    # 태양광 시스템 설계 조건 설정
    system.SystemDesign.assign({
        "system_capacity": capacity,   # kW
        "module_type": 0,              # Standard module
        "array_type": 0,               # Fixed open rack
        "tilt": tilt,                  # 경사각
        "azimuth": azimuth,            # 방위각, 180 = 남향
        "dc_ac_ratio": 1.1,
        "inv_eff": 96,
        "losses": 14.0757,
        "gcr": 0.4
    })

    # 시뮬레이션 실행
    system.execute()

    annual = system.Outputs.ac_annual
    monthly = list(system.Outputs.ac_monthly)

    return {
        "tilt": tilt,
        "azimuth": azimuth,
        "capacity": capacity,
        "annual_energy_kwh": annual,
        "ac_monthly_kwh": monthly
    }


def optimize_design(capacity):
    """
    여러 경사각을 비교하여 최적 설계를 찾는 함수
    """
    results = []

    # 기준선: 수직 설치
    baseline = run_simulation(capacity, 90, 180)
    baseline["Type"] = "Case 1: Baseline Vertical"
    results.append(baseline)

    # 최적화 후보
    for t in [20, 30, 35]:
        sim = run_simulation(capacity, t, 180)
        sim["Type"] = "Optimization Search"
        results.append(sim)

    return results


def get_ai_analysis(client, parsed_location, capacity, results):
    baseline = next((r for r in results if r["Type"].startswith("Case 1")), results[0])
    optimized = max(results, key=lambda x: x["annual_energy_kwh"])

    improvement = (
        (optimized["annual_energy_kwh"] - baseline["annual_energy_kwh"])
        / baseline["annual_energy_kwh"]
        * 100
        if baseline["annual_energy_kwh"] > 0
        else 0
    )

    prompt = f"""
당신은 대한민국 최고의 'AI 태양광 발전 컨설턴트'입니다.
초보자도 쉽게 이해할 수 있으면서도, 공학적 깊이가 담긴 태양광 경제성 평가 보고서를 작성해 주세요.

[시뮬레이션 조건]
- 지역: {parsed_location}
- 사용 기상 데이터: 강릉 EPW 기상 데이터
- 시스템 용량: {capacity} kW
- Baseline 조건: 수직 설치, 남향
- Optimized 조건: 후보 경사각 중 최적 설계

[시뮬레이션 결과]
- Baseline 기준 발전량: {baseline["annual_energy_kwh"]:.2f} kWh/year
- Optimized 최적 발전량: {optimized["annual_energy_kwh"]:.2f} kWh/year
- 최적 경사각: {optimized["tilt"]}도
- 최적 방위각: {optimized["azimuth"]}도
- 발전량 개선율: {improvement:.2f}%

[경제성 가정]
- 설치비: kW당 150만원
- 전기요금 절감 단가: 160원/kWh

[보고서 양식]
### 1. ☀️ 시스템 종합 성능
### 2. 📐 최적 설계 조건
### 3. 💰 경제성 및 회수기간
### 4. 🌱 환경 보호 기여도
### 5. 💡 컨설턴트 종합 의견

주의:
- 현재 기상 데이터는 강릉 EPW 기준이므로, 사용자가 춘천/서울 등 다른 지역을 입력해도 실제 계산은 강릉 기상 조건임을 설명해 주세요.
- 과장하지 말고, 추정값이라는 점을 명확히 말해 주세요.
"""

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )

    return response.text


# --- UI 구성 ---
with st.sidebar:
    st.header("⚙️ 기본 설정")
    api_key_input = st.text_input("Gemini API 키를 입력하세요", type="password")
    st.info("발급받으신 Google Gemini API 키를 입력해야 서비스가 작동합니다.")
    st.warning("현재 시뮬레이션은 저장소에 포함된 강릉 EPW 기상 데이터를 기준으로 계산됩니다.")

st.title("☀️ SAM-Copilot 태양광 경제성 분석기")
st.markdown("**PySAM PVWatts와 LLM을 결합하여 태양광 발전량과 경제성을 분석해 보세요.**")

user_input = st.chat_input("질문을 입력하세요... (예: 춘천에 5kW 태양광 설치하면 어때?)")


if user_input:
    st.chat_message("user").write(user_input)

    if not api_key_input:
        st.error("왼쪽 메뉴에서 Gemini API 키를 먼저 입력해주세요.")
    else:
        with st.chat_message("assistant"):
            with st.spinner("PySAM 시뮬레이션 및 AI 분석 중입니다..."):
                try:
                    client = genai.Client(api_key=api_key_input)

                    # 기본값
                    parsed_location = "강원도 춘천"
                    capacity_kw = 5.0

                    # 1. 사용자 입력에서 지역/용량 추출
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

                        parse_res = client.models.generate_content(
                            model="gemini-2.5-flash",
                            contents=parse_prompt
                        )

                        match = re.search(r"\{.*\}", parse_res.text, re.DOTALL)

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

                    # 2. PySAM 시뮬레이션
                    sim_results = optimize_design(capacity_kw)
                    baseline = next((r for r in sim_results if r["Type"].startswith("Case 1")), sim_results[0])
                    optimized = max(sim_results, key=lambda x: x["annual_energy_kwh"])

                    # 3. AI 분석 보고서
                    final_report = get_ai_analysis(client, parsed_location, capacity_kw, sim_results)

                    # 4. 결과 출력
                    st.markdown(final_report)
                    st.divider()

                    # 5. 수치 요약
                    col1, col2, col3 = st.columns(3)

                    with col1:
                        st.metric(
                            "Baseline 발전량",
                            f"{baseline['annual_energy_kwh']:,.0f} kWh/year"
                        )

                    with col2:
                        st.metric(
                            "Optimized 발전량",
                            f"{optimized['annual_energy_kwh']:,.0f} kWh/year"
                        )

                    with col3:
                        improvement = (
                            (optimized["annual_energy_kwh"] - baseline["annual_energy_kwh"])
                            / baseline["annual_energy_kwh"]
                            * 100
                            if baseline["annual_energy_kwh"] > 0
                            else 0
                        )
                        st.metric(
                            "발전량 개선율",
                            f"{improvement:.2f}%"
                        )

                    # 6. 월별 발전량 그래프
                    st.subheader(f"📉 {parsed_location} ({capacity_kw}kW) 월별 발전량 비교")

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

                    # 7. 현재 한계 안내
                    st.info(
                        "현재 버전은 저장소에 포함된 강릉 EPW 기상 데이터를 기준으로 PySAM PVWatts 시뮬레이션을 수행합니다. "
                        "정확한 지역별 분석을 위해서는 춘천, 서울, 부산 등 지역별 EPW 파일을 추가하고 지역 선택 로직을 확장해야 합니다."
                    )

                except Exception as e:
                    st.error(f"⚠️ 처리 중 오류가 발생했습니다: {e}")
