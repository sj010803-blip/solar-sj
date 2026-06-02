import streamlit as st
import os
import json
import glob
import re
import zipfile
from google import genai
import PySAM.Pvwattsv8 as pvwatts

st.set_page_config(page_title="AI 태양광 컨설턴트", page_icon="☀️", layout="centered")

def parse_input(client, user_text):
    prompt = f"""
    사용자의 질문에서 태양광 설치 지역(location)과 설치 용량(capacity_kw)을 추출하세요.
    반드시 아래 JSON 형태만 출력하세요. 다른 설명은 하지 마세요.
    {{
        "location": "지역명",
        "capacity_kw": 3.0
    }}
    질문: {user_text}
    """
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt
    )
    
    match = re.search(r'\{.*\}', response.text, re.DOTALL)
    if match:
        return match.group(0)
    return '{"location": "강릉", "capacity_kw": 3.0}'

def run_simulation(capacity_kw):
    system = pvwatts.new()
    
    zip_files = glob.glob("**/*.zip", recursive=True)
    for z_file in zip_files:
        try:
            with zipfile.ZipFile(z_file, 'r') as zip_ref:
                zip_ref.extractall(".")
        except:
            pass

    epw_files = (
        glob.glob("**/*.epw", recursive=True) + 
        glob.glob("**/*.EPW", recursive=True) + 
        glob.glob("*.epw") + 
        glob.glob("*.EPW")
    )
    
    epw_files = list(set(epw_files))
    
    if not epw_files:
        raise FileNotFoundError("기상 데이터 파일(.epw 또는 .zip)을 저장소 내에서 전혀 찾을 수 없습니다.")
        
    weather_file_name = epw_files[0]

    system.value("solar_resource_file", weather_file_name)
    system.value("system_capacity", capacity_kw)
    system.value("module_type", 0)
    system.value("dc_ac_ratio", 1.2)
    system.value("array_type", 0)
    system.value("tilt", 20)
    system.value("azimuth", 180)
    system.value("gcr", 0.4)
    system.value("losses", 14.07)
    system.value("inv_eff", 96.0)

    system.execute()
    
    annual_energy = system.Outputs.annual_energy
    ac_monthly = system.Outputs.ac_monthly
    
    try:
        dc_monthly = system.Outputs.dc_monthly
    except:
        dc_monthly = ac_monthly 

    try:
        poa_monthly = system.Outputs.poa_monthly
    except:
        poa_monthly = [0]*12 

    capacity_factor = (annual_energy / (capacity_kw * 8760)) * 100 if capacity_kw > 0 else 0
    kwh_per_kw = annual_energy / capacity_kw if capacity_kw > 0 else 0
    
    return {
        "capacity": capacity_kw,
        "annual_energy_kwh": round(annual_energy, 2),
        "capacity_factor": round(capacity_factor, 2),
        "kwh_per_kw": round(kwh_per_kw, 2),
        "ac_monthly_kwh": [round(float(val), 2) for val in ac_monthly],
        "dc_monthly_kwh": [round(float(val), 2) for val in dc_monthly],
        "poa_monthly": [round(float(val), 2) for val in poa_monthly]
    }

def generate_report(client, sim_results, location):
    prompt = f"""
당신은 대한민국 최고의 'AI 태양광 발전 컨설턴트'이자 공학 전문가입니다.
아래 제공된 NREL PySAM 시뮬레이션 데이터를 바탕으로, 자원공학 관점의 깊이가 담긴 [최종 공학 및 경제성 평가 보고서]를 작성해 주세요.
전문 엔지니어링 리포트 수준의 신뢰감 있는 톤앤매너를 유지하며 마크다운(Markdown)을 적극 활용하세요.

[시뮬레이션 팩트 데이터]
- 설치 지역: {location}
- 시스템 용량: {sim_results['capacity']} kW
- 연간 총 발전량 (AC): {sim_results['annual_energy_kwh']} kWh
- 설비 이용률 (Capacity Factor): {sim_results['capacity_factor']} %
- 단위 발전량 (Specific Yield): {sim_results['kwh_per_kw']} kWh/kW
- 1월~12월 월별 직류(DC) 발전량: {sim_results['dc_monthly_kwh']}
- 1월~12월 월별 최종 교류(AC) 발전량: {sim_results['ac_monthly_kwh']}
- 1월~12월 경사면 총 일사량 (POA): {sim_results['poa_monthly']}

[보고서 필수 포함 양식]
## 📊 태양광 발전 시스템 정밀 공학 및 경제성 분석 리포트

### 1. ☀️ 시스템 종합 성능 지표
- (총 발전량, 설비 이용률, 단위 발전량)을 깔끔한 요약 표로 제시하고, 자원공학적 관점에서 이 수치들이 의미하는 바를 전문적으로 해석해 주세요.

### 2. 📈 월별 자원 및 에너지 변환 데이터 (표 형식)
- 제공된 데이터를 활용해 [월 | 경사면 일사량(POA) | DC 발전량 | AC 발전량]으로 구성된 4열 표를 작성하세요. (단위 명시)
- 인버터 변환 손실(DC vs AC 차이)에 대한 공학적 분석을 표 아래에 추가하세요.

### 3. 💰 경제성 및 ROI (투자 수익률) 평가
- 초기 시공비(kW당 150만 원 가정), 전력 판매 단가(160원/kWh 가정)를 기준으로 예상 투자비, 연간 수익, 그리고 자본 회수 기간(Payback Period)을 도출하세요.

### 4. 💡 자원공학 관점 종합 제언
- {location} 지역의 기상 특성을 고려할 때 태양광 자원으로서의 가치를 평가하고 최적화 방안을 제안하세요.
"""
    
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
    )
    return response.text

# --- UI 코드 ---
st.title("☀️ AI 태양광/경제성 평가 툴")
st.markdown("**강원대학교 자원공학 프로젝트** - 자연어로 태양광 발전량을 분석해보세요!")

with st.sidebar:
    st.header("⚙️ 기본 설정")
    api_key_input = st.text_input("Gemini API 키를 입력하세요", type="password")
    st.info("발급받으신 Google Gemini API 키를 입력해야 서비스가 작동합니다.")
    st.divider()
    st.markdown("💡 **사용 예시**\n- 춘천 단독주택에 3kW 태양광 설치하면 어때?\n- 강릉 100kW 발전소 1년 발전량 계산해줘")

user_input = st.chat_input("질문을 입력하세요...")

if user_input:
    st.chat_message("user").write(user_input)
    if not api_key_input:
        st.error("앗! 왼쪽 메뉴에서 Gemini API 키를 먼저 입력해주세요.")
    else:
        with st.chat_message("assistant"):
            with st.spinner("AI가 데이터를 분석하고 시뮬레이션을 돌리고 있습니다..."):
                try:
                    os.environ["GEMINI_API_KEY"] = api_key_input
                    client = genai.Client()

                    parsed_json_str = parse_input(client, user_input)
                    
                    try:
                        parsed_data = json.loads(parsed_json_str)
                    except:
                        parsed_data = {"location": "강릉", "capacity_kw": 3.0}

                    raw_capacity = parsed_data.get('capacity_kw', 3.0)
                    try:
                        numeric_str = re.sub(r'[^\d.]', '', str(raw_capacity))
                        safe_capacity_kw = float(numeric_str) if numeric_str else 3.0
                    except:
                        safe_capacity_kw = 3.0
                        
                    safe_location = parsed_data.get('location', '강릉')

                    sim_data = run_simulation(safe_capacity_kw)
                    final_report = generate_report(client, sim_data, safe_location)

                    st.success("✅ 분석 완료!")
                    st.markdown(final_report)
                    
                    # 💡 [핵심 안전망 그래프] 외부 라이브러리 없이 스트림릿 기본 엔진으로 100% 안전하게 차트 구현
                    try:
                        st.divider()
                        st.subheader("📉 월별 최종 교류(AC) 예상 발전량 추이 (kWh)")
                        
                        # 1월부터 12월까지의 데이터를 딕셔너리로 맵핑하여 가볍게 출력
                        chart_dict = {f"{i}월": sim_data["ac_monthly_kwh"][i-1] for i in range(1, 13)}
                        st.bar_chart(chart_dict)
                    except:
                        pass # 어떤 상황에서도 그래프 때문에 본문 리포트가 멈추지 않도록 차단

                    with st.expander("📊 시뮬레이션 수치 자세히 보기"):
                        st.json(sim_data)

                except FileNotFoundError as e:
                    st.error(f"⚠️ {e}")
                except Exception as e:
                    st.error(f"⚠️ 시스템 오류 발생: {e}")
