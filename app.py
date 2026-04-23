from __future__ import annotations

import os

import anthropic
import streamlit as st

from src.excel_reader import read_excel_profile
from src.report_generator import analyze_tone, generate_report_content, parse_tagged_content
from src.word_extractor import extract_template_profile
from src.word_writer import write_report

st.set_page_config(
    page_title="보고서 변환기",
    page_icon="📄",
    layout="wide",
)

st.title("📄 보고서 변환기")
st.caption("워드 템플릿의 서식과 문체를 유지하면서 엑셀 데이터로 새 보고서를 생성합니다.")


with st.sidebar:
    st.header("설정")

    template_file = st.file_uploader(
        "워드 템플릿 (.docx)",
        type=["docx"],
        help="서식과 문체의 기준이 되는 워드 보고서를 업로드하세요.",
    )

    excel_file = st.file_uploader(
        "엑셀 데이터 (.xlsx)",
        type=["xlsx", "xls"],
        help="보고서에 담을 원본 데이터 파일을 업로드하세요.",
    )

    st.divider()

    default_api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    api_key = st.text_input(
        "Anthropic API 키",
        value=default_api_key,
        type="password",
        help="Claude AI API 키를 입력하세요. 환경변수 ANTHROPIC_API_KEY가 있으면 자동 입력됩니다.",
    )

    model = st.selectbox(
        "AI 모델",
        options=["claude-sonnet-4-6", "claude-opus-4-7"],
        help="Sonnet: 빠르고 경제적 / Opus: 최고 품질",
    )

    target_sections = st.number_input(
        "목표 섹션 수",
        min_value=3,
        max_value=10,
        value=5,
        help="보고서에 생성할 주요 섹션의 수",
    )

    include_table = st.checkbox(
        "원본 데이터 테이블 포함",
        value=False,
        help="생성된 보고서에 엑셀 원본 데이터를 표 형태로 삽입합니다.",
    )

    st.divider()
    generate_btn = st.button("보고서 생성", type="primary", use_container_width=True)


if template_file and "template_profile" not in st.session_state:
    try:
        with st.spinner("템플릿 서식 분석 중..."):
            st.session_state["template_profile"] = extract_template_profile(template_file.read())
        st.sidebar.success("템플릿 로드 완료")
    except Exception as e:
        st.sidebar.error(f"템플릿 오류: {e}")

if not template_file and "template_profile" in st.session_state:
    del st.session_state["template_profile"]

if excel_file and "excel_profile" not in st.session_state:
    try:
        with st.spinner("엑셀 데이터 파싱 중..."):
            st.session_state["excel_profile"] = read_excel_profile(excel_file.read())
        st.sidebar.success("엑셀 로드 완료")
    except Exception as e:
        st.sidebar.error(f"엑셀 오류: {e}")

if not excel_file and "excel_profile" in st.session_state:
    del st.session_state["excel_profile"]


tab_preview, tab_log, tab_download = st.tabs(["미리보기", "생성 로그", "다운로드"])

with tab_preview:
    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("워드 템플릿")
        if "template_profile" in st.session_state:
            profile = st.session_state["template_profile"]
            with st.expander("문체 샘플 (처음 3000자)", expanded=True):
                st.text(profile.tone_sample[:3000])
            with st.expander("감지된 스타일 목록"):
                style_names = [s.name for s in profile.style_catalog]
                st.write(style_names)
        else:
            st.info("왼쪽 사이드바에서 워드 템플릿을 업로드하세요.")

    with col_right:
        st.subheader("엑셀 데이터")
        if "excel_profile" in st.session_state:
            ep = st.session_state["excel_profile"]
            sheet_tabs = st.tabs([s.name for s in ep.sheets])
            for i, sheet_tab in enumerate(sheet_tabs):
                with sheet_tab:
                    sheet = ep.sheets[i]
                    st.caption(f"행: {sheet.row_count} | 열: {sheet.col_count}")
                    if sheet.is_pivot:
                        st.warning("피벗 테이블 구조 감지됨 — AI가 추세를 서술합니다.")
                    st.dataframe(sheet.df.head(20), use_container_width=True)
        else:
            st.info("왼쪽 사이드바에서 엑셀 파일을 업로드하세요.")

with tab_log:
    st.subheader("생성 진행 상황")
    log_placeholder = st.empty()
    progress_placeholder = st.empty()
    stream_placeholder = st.empty()

with tab_download:
    st.subheader("결과 다운로드")
    if "last_generated" in st.session_state:
        st.success("보고서가 생성되었습니다. 아래 버튼으로 다운로드하세요.")
        st.download_button(
            label="워드 파일 다운로드 (.docx)",
            data=st.session_state["last_generated"],
            file_name="generated_report.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            type="primary",
        )
    else:
        st.info("보고서를 생성하면 여기서 다운로드할 수 있습니다.")


if generate_btn:
    errors = []
    if "template_profile" not in st.session_state:
        errors.append("워드 템플릿 파일을 업로드해 주세요.")
    if "excel_profile" not in st.session_state:
        errors.append("엑셀 데이터 파일을 업로드해 주세요.")
    if not api_key:
        errors.append("Anthropic API 키를 입력해 주세요.")

    if errors:
        for err in errors:
            st.sidebar.error(err)
    else:
        try:
            template_profile = st.session_state["template_profile"]
            excel_profile = st.session_state["excel_profile"]

            progress_bar = progress_placeholder.progress(0, text="시작 중...")

            with log_placeholder.container():
                st.info("1/4 — 문체 분석 중...")
            progress_bar.progress(10, text="문체 분석 중...")

            tone_profile = analyze_tone(
                template_profile.tone_sample,
                api_key=api_key,
                model=model,
            )

            with log_placeholder.container():
                st.success("1/4 — 문체 분석 완료")
                st.info("2/4 — 보고서 내용 생성 중 (스트리밍)...")
            progress_bar.progress(30, text="보고서 내용 생성 중...")

            stream_buf: list[str] = []

            def on_stream(delta: str) -> None:
                stream_buf.append(delta)
                stream_placeholder.markdown(
                    "**생성 중...**\n\n" + "".join(stream_buf)[-2000:],
                    unsafe_allow_html=False,
                )

            raw_output = generate_report_content(
                tone_profile=tone_profile,
                style_catalog=template_profile.style_catalog,
                excel_profile=excel_profile,
                api_key=api_key,
                model=model,
                target_section_count=int(target_sections),
                streaming_callback=on_stream,
            )

            with log_placeholder.container():
                st.success("2/4 — 보고서 내용 생성 완료")
                st.info("3/4 — 구조 파싱 중...")
            progress_bar.progress(70, text="구조 파싱 중...")

            tagged_content = parse_tagged_content(raw_output)

            with log_placeholder.container():
                st.success("3/4 — 구조 파싱 완료")
                st.info("4/4 — 워드 파일 작성 중...")
            progress_bar.progress(85, text="워드 파일 작성 중...")

            sheet_map = {s.name: s for s in excel_profile.sheets}
            output_bytes = write_report(
                template_profile=template_profile,
                tagged_content=tagged_content,
                sheet_map=sheet_map,
                include_data_table=include_table,
            )

            st.session_state["last_generated"] = output_bytes

            progress_bar.progress(100, text="완료!")
            with log_placeholder.container():
                st.success("4/4 — 워드 파일 생성 완료!")

            stream_placeholder.empty()

            st.toast("보고서가 생성되었습니다! '다운로드' 탭에서 다운로드하세요.", icon="✅")

        except anthropic.AuthenticationError:
            st.sidebar.error("API 키가 올바르지 않습니다. 키를 확인해 주세요.")
        except anthropic.RateLimitError:
            st.sidebar.error("API 요청 한도에 도달했습니다. 잠시 후 다시 시도해 주세요.")
        except anthropic.APIStatusError as e:
            st.sidebar.error(f"Claude API 오류 (상태코드 {e.status_code}): 다시 시도해 주세요.")
        except ValueError as e:
            st.sidebar.error(str(e))
        except Exception as e:
            st.sidebar.error(f"예기치 않은 오류가 발생했습니다: {e}")
            with st.sidebar.expander("오류 상세"):
                st.exception(e)
