from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Optional

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.util import Inches, Pt


@dataclass
class StyleEntry:
    name: str
    font_name: Optional[str]
    font_size_pt: Optional[float]
    bold: Optional[bool]
    italic: Optional[bool]
    alignment: Optional[str]
    outline_level: Optional[int]
    space_before_pt: Optional[float]
    space_after_pt: Optional[float]


@dataclass
class SectionProps:
    page_width_inches: float
    page_height_inches: float
    left_margin_inches: float
    right_margin_inches: float
    top_margin_inches: float
    bottom_margin_inches: float


@dataclass
class TemplateProfile:
    style_catalog: list[StyleEntry]
    tone_sample: str
    section_props: SectionProps
    raw_document: object
    available_style_names: set[str] = field(default_factory=set)


def extract_template_profile(docx_bytes: bytes) -> TemplateProfile:
    stream = io.BytesIO(docx_bytes)
    doc = Document(stream)

    style_catalog = _extract_styles(doc)
    tone_sample = _extract_tone_sample(doc)
    section_props = _extract_section_props(doc)
    available_style_names = {s.name for s in style_catalog}

    if not tone_sample.strip():
        raise ValueError("템플릿에 분석할 본문 텍스트가 없습니다. 본문 단락을 추가해 주세요.")

    return TemplateProfile(
        style_catalog=style_catalog,
        tone_sample=tone_sample,
        section_props=section_props,
        raw_document=doc,
        available_style_names=available_style_names,
    )


def clear_document_body(doc) -> None:
    body = doc.element.body
    sectPr = body.find("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}sectPr")

    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    for child in list(body):
        tag = child.tag.replace("{" + ns + "}", "w:")
        if tag in ("w:p", "w:tbl", "w:sdt"):
            body.remove(child)

    if sectPr is not None and sectPr not in body:
        body.append(sectPr)


def _extract_styles(doc) -> list[StyleEntry]:
    entries = []
    for style in doc.styles:
        if style.type not in (WD_STYLE_TYPE.PARAGRAPH, WD_STYLE_TYPE.CHARACTER):
            continue
        try:
            font = style.font
            font_name = font.name
            font_size_pt = float(font.size.pt) if font.size else None
            bold = font.bold
            italic = font.italic

            pf = style.paragraph_format if style.type == WD_STYLE_TYPE.PARAGRAPH else None
            alignment = None
            outline_level = None
            space_before_pt = None
            space_after_pt = None
            if pf:
                if pf.alignment is not None:
                    alignment = str(pf.alignment)
                if pf.space_before is not None:
                    try:
                        space_before_pt = float(Pt(1).pt) * (pf.space_before.pt if hasattr(pf.space_before, 'pt') else 0)
                    except Exception:
                        pass
                if pf.space_after is not None:
                    try:
                        space_after_pt = float(Pt(1).pt) * (pf.space_after.pt if hasattr(pf.space_after, 'pt') else 0)
                    except Exception:
                        pass
                outline_level = _get_outline_level(style.name)

            entries.append(StyleEntry(
                name=style.name,
                font_name=font_name,
                font_size_pt=font_size_pt,
                bold=bold,
                italic=italic,
                alignment=alignment,
                outline_level=outline_level,
                space_before_pt=space_before_pt,
                space_after_pt=space_after_pt,
            ))
        except Exception:
            entries.append(StyleEntry(
                name=style.name,
                font_name=None,
                font_size_pt=None,
                bold=None,
                italic=None,
                alignment=None,
                outline_level=None,
                space_before_pt=None,
                space_after_pt=None,
            ))
    return entries


def _get_outline_level(style_name: str) -> Optional[int]:
    name_lower = style_name.lower()
    if "heading 1" in name_lower or "제목 1" in name_lower:
        return 1
    if "heading 2" in name_lower or "제목 2" in name_lower:
        return 2
    if "heading 3" in name_lower or "제목 3" in name_lower:
        return 3
    if "heading" in name_lower or "제목" in name_lower:
        return 4
    if "title" in name_lower or "타이틀" in name_lower:
        return 0
    return None


def _extract_tone_sample(doc, max_chars: int = 3000) -> str:
    texts = []
    total = 0
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        texts.append(text)
        total += len(text)
        if total >= max_chars:
            break
    return "\n".join(texts)


def _extract_section_props(doc) -> SectionProps:
    section = doc.sections[0]

    def to_inches(emu_val) -> float:
        try:
            return emu_val.inches
        except Exception:
            return 1.0

    return SectionProps(
        page_width_inches=to_inches(section.page_width),
        page_height_inches=to_inches(section.page_height),
        left_margin_inches=to_inches(section.left_margin),
        right_margin_inches=to_inches(section.right_margin),
        top_margin_inches=to_inches(section.top_margin),
        bottom_margin_inches=to_inches(section.bottom_margin),
    )


def style_catalog_to_text(style_catalog: list[StyleEntry]) -> str:
    lines = []
    for s in style_catalog:
        parts = [f"- {s.name}"]
        if s.font_name:
            parts.append(f"폰트:{s.font_name}")
        if s.font_size_pt:
            parts.append(f"{s.font_size_pt}pt")
        if s.bold:
            parts.append("굵게")
        if s.outline_level is not None:
            parts.append(f"레벨:{s.outline_level}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)
