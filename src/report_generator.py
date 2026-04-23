from __future__ import annotations

import re
from typing import Callable, Optional

import anthropic

from .excel_reader import ExcelProfile, SheetData
from .word_extractor import StyleEntry, style_catalog_to_text


def analyze_tone(
    tone_sample: str,
    api_key: str,
    model: str = "claude-sonnet-4-6",
) -> str:
    client = anthropic.Anthropic(api_key=api_key)

    prompt = f"""다음은 전문 보고서 템플릿에서 발췌한 텍스트입니다.
다른 작성자가 동일한 문체를 재현할 수 있도록 아래 항목별로 문체를 분석해 주세요:

1. 격식 수준 (공식적/반공식적/비공식적) — 근거 예시 포함
2. 문장 구조 (평균 길이, 종속절 활용 정도)
3. 능동태/수동태 비율
4. 시제 패턴 (과거형 사실 서술 vs 현재형 결론)
5. 인칭 사용 (1인칭/3인칭/무인칭)
6. 헤징 표현 사용 여부 (예: "~로 판단됨", "~이 시사됨") vs 단정적 서술
7. 데이터 인용 패턴 (예: "표 X에 따르면...", "그림 Y에서 확인되듯...")
8. 단락 구조 (주제문 + 근거 + 결론 패턴 여부)

[템플릿 발췌]
{tone_sample}
"""

    message = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def generate_report_content(
    tone_profile: str,
    style_catalog: list[StyleEntry],
    excel_profile: ExcelProfile,
    api_key: str,
    model: str = "claude-sonnet-4-6",
    target_section_count: int = 5,
    selected_sheets: Optional[list[str]] = None,
    streaming_callback: Optional[Callable[[str], None]] = None,
) -> str:
    client = anthropic.Anthropic(api_key=api_key)

    sheets = excel_profile.sheets
    if selected_sheets:
        sheets = [s for s in sheets if s.name in selected_sheets]
    if not sheets:
        sheets = excel_profile.sheets

    style_text = style_catalog_to_text(style_catalog)
    data_text = _build_data_text(sheets)

    prompt = f"""당신은 전문 보고서 작성자입니다. 아래 지침에 따라 완성도 높은 보고서를 작성하세요.

═══════════════════════════════
[중요] 문체 프로파일 — 반드시 이 문체를 정확히 따라야 합니다
═══════════════════════════════
{tone_profile}

═══════════════════════════════
사용 가능한 문서 스타일 (반드시 아래 이름만 사용)
═══════════════════════════════
{style_text}

═══════════════════════════════
분석 대상 데이터
═══════════════════════════════
{data_text}

═══════════════════════════════
작성 지침
═══════════════════════════════
1. 위 데이터를 기반으로 {target_section_count}개 이상의 주요 섹션을 포함한 보고서를 작성하세요.
2. 데이터에 없는 내용을 임의로 만들지 마세요.
3. 각 단락/제목의 앞에 반드시 [STYLE: 스타일명] 태그를 붙이세요.
4. 스타일명은 위 "사용 가능한 문서 스타일" 목록에 있는 것만 사용하세요.
5. 표를 삽입할 위치에는 [TABLE: 시트명] 태그를 사용하세요.

출력 형식 예시:
[STYLE: Heading 1] 요약
[STYLE: Normal] 본문 내용...
[STYLE: Heading 2] 세부 분석
[STYLE: Normal] 세부 내용...
[TABLE: Sheet1]
"""

    if streaming_callback:
        full_text = []
        with client.messages.stream(
            model=model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            for delta in stream.text_stream:
                full_text.append(delta)
                streaming_callback(delta)
        return "".join(full_text)
    else:
        message = client.messages.create(
            model=model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text


def parse_tagged_content(raw_output: str) -> list[tuple[str, str]]:
    """Returns list of (tag_type, style_or_sheet_name, text) tuples.

    tag_type: 'paragraph' or 'table'
    """
    results = []
    pattern = re.compile(
        r'\[(STYLE|TABLE):\s*([^\]]+)\]\s*(.*?)(?=\[(?:STYLE|TABLE):|$)',
        re.DOTALL,
    )
    for match in pattern.finditer(raw_output):
        tag_type = match.group(1).strip()
        name = match.group(2).strip()
        text = match.group(3).strip()
        results.append((tag_type, name, text))

    if not results:
        for line in raw_output.split("\n"):
            line = line.strip()
            if line:
                results.append(("STYLE", "Normal", line))

    return results


def _build_data_text(sheets: list[SheetData]) -> str:
    parts = []
    for sheet in sheets:
        pivot_note = ""
        if sheet.is_pivot:
            pivot_note = "\n※ 이 시트는 피벗 테이블 형태입니다. 개별 수치 나열보다 추세와 패턴을 서술하세요."

        parts.append(f"""--- 시트: {sheet.name} ---
행 수: {sheet.row_count}, 열 수: {sheet.col_count}{pivot_note}

[컬럼 유형]
{_format_column_types(sheet.column_types)}

[통계 요약]
{sheet.summary_stats}

[데이터 샘플]
{sheet.sample_markdown}
""")
    return "\n".join(parts)


def _format_column_types(column_types: dict[str, str]) -> str:
    return "\n".join(f"  {col}: {ctype}" for col, ctype in column_types.items())
