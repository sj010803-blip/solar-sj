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
    
    # 💡 [치트키 패치 1] 혹시 깃허브에 .zip 파일로 올렸어도 서버가 알아서 압축을 풉니다.
    zip_files = glob.glob("**/*.zip", recursive=True)
    for z_file in zip_files:
        try:
            with zipfile.ZipFile(z_file, 'r') as zip_ref:
                zip_ref.extractall(".")
        except:
            pass

    # 💡 [치트키 패치 2] 폴더 안속 깊숙이 있든, 대소문자(.EPW)가 다르든 샅샅이 찾아냅니다.
    epw_files = (
        glob.glob("**/*.epw", recursive=True) + 
        glob.glob("**/*.EPW", recursive=True) + 
        glob.glob("*.epw") + 
        glob.glob("*.EPW")
    )
    
    # 중복된 경로 제거
    epw_files = list(set(epw_files))
    
    if not epw_files:
        raise FileNotFoundError("기상 데이터 파일(.epw 또는 .zip)을 저장소 내에서 전혀 찾을 수 없습니다.")
        
    # 가장 먼저 찾아낸 기상 파일 선택
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
    
    return {
        "capacity": capacity_kw,
        "annual_energy_kwh": round(annual_energy, 2),
        "ac_monthly_kwh": [round(float(val), 2) for val in ac_monthly]
    }

def generate_report(client, sim_results, location):
    prompt = f"""
당신은 대한민국 최고의 'AI 태양광 발전 컨설턴트'입니다.
아래 제공된 NREL PySAM 시뮬레이션 데이터를 바탕으로, 깔끔하고 가독성 높은 [최종 컨설팅 보고서]를 작성해 주세요.
반드시 마크다운(Markdown) 문법을 사용하여 표와 굵은 글씨를 적극 활용하세요.

[시뮬레이션 데이터]
- 설치 지역: {location}
- 시스템 용량: {sim_results['capacity']} kW
- 연간 총 예상 발전량: {sim_results['annual_energy_kwh']} kWh
- 1월~12월 실제 월별 발전량 리스트: {sim_results['ac_monthly_kwh']}

[보고서 필수 포함 양식]
## 📊 AI 태양광 컨설팅 종합 보고서

### 1. ☀️ 월별 및 연간 발전량 예측 (표 형식)
- 제공된 '1월~12월 실제 월별 발전량 리스트' 수치를 그대로 활용하여 12달의 발전량 표를 그려주세요. (절대 수치를 마음대로 지어내지 마세요!)
- 연간 총 예상 발전량({sim_results['annual_energy_kwh']} kWh)을 표 하단에 크게 강조해 주세요.

### 2. 💰 경제성 및 투자비용 회수 분석 (ROI)
- 시장 평균 단가(시공비 kW당 150만 원, 전기요금 160원/kWh 가정)를 기준으로 다음 항목을 계산해서 보여주세요.
  * 예상 초기 투자 비용 (원)
  * 연간 예상 수익 (원)
  * 투자금 회수 기간 (약 O.O년)

### 3. 🌱 환경 보호 기여도
- 이 시스템을 통해 얻을 수 있는 연간 CO2 감축량(kg)과 소나무 식재 효과(그루 수)를 계산해서 포함해 주세요.

### 4. 💡 컨설턴트 종합 의견
- {location} 지역의 특성을 고려한 간단한 발전 효율 평가와 유지보수 팁을 2~3줄로 요약해 주세요.
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

                    with st.expander("📊 시뮬레이션 수치 자세히 보기"):
                        st.json(sim_data)

                except FileNotFoundError as e:
                    st.error(f"⚠️ {e}")
                except Exception as e:
                    st.error(f"⚠️ 시스템 오류 발생: {e}")
