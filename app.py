import streamlit as st
import os
import json
import glob
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
import PySAM.Pvwattsv8 as pvwatts

st.set_page_config(page_title="AI 태양광 컨설턴트", page_icon="☀️", layout="centered")

class SolarInput(BaseModel):
    location: str = Field(description="사용자가 언급한 설치 지역")
    capacity_kw: float = Field(default=3.0, description="설치 용량(kW)")

def parse_input(client, user_text):
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=f"다음 사용자의 질문에서 태양광 분석에 필요한 정보를 추출해줘: {user_text}",
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=SolarInput,
        ),
    )
    return response.text

def run_simulation(capacity_kw):
    system = pvwatts.new()
    epw_files = glob.glob("*.epw")
    if not epw_files:
        raise FileNotFoundError("기상 파일(.epw)이 없습니다.")
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
    return {"capacity": capacity_kw, "annual_energy_kwh": round(annual_energy, 2)}

def generate_report(client, sim_results, location):
    prompt = f"""
    당신은 친절한 태양광 에너지 컨설턴트입니다.
    사용자의 지역({location})과 아래의 PySAM 시뮬레이션 결과를 바탕으로,
    초보자도 이해하기 쉬운 안내 메시지를 3~4문장으로 자연스럽게 작성해주세요.

    [시뮬레이션 데이터]
    - 설치 용량: {sim_results['capacity']} kW
    - 연간 예상 발전량: {sim_results['annual_energy_kwh']} kWh
    """
    response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
    return response.text

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

                    status_text = st.empty()
                    status_text.text("🤖 1단계: 사용자 조건 추출 중...")
                    parsed_json_str = parse_input(client, user_input)
                    parsed_data = json.loads(parsed_json_str)

                    status_text.text(f"⚙️ 2단계: {parsed_data['location']} 기상 데이터 연동 및 시뮬레이션 중...")
                    sim_data = run_simulation(parsed_data['capacity_kw'])

                    status_text.text("📝 3단계: AI 최종 컨설팅 리포트 작성 중...")
                    final_report = generate_report(client, sim_data, parsed_data['location'])

                    status_text.empty()
                    st.success("✅ 분석 완료!")
                    st.write(final_report)

                    with st.expander("📊 시뮬레이션 수치 자세히 보기"):
                        st.json(sim_data)

                except FileNotFoundError:
                    st.error("⚠️ 기상 파일(.epw)을 찾을 수 없습니다.")
                except Exception as e:
                    st.error(f"⚠️ 에러가 발생했습니다: {e}")
