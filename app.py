import os
import io
import re
import json
import zipfile
import tempfile
import base64
from datetime import datetime
from urllib.parse import quote_plus

import feedparser
import requests
import streamlit as st
from bs4 import BeautifulSoup
from openai import OpenAI
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageFilter

# =========================================================
# Ocean Science Brief
# 기사 수집 → AI 분석 → 고급 카드뉴스 → 검토 → GitHub 업로드
# =========================================================

APP_TITLE = "🌊 Ocean Science Brief"
TEXT_MODEL = os.getenv("OPENAI_TEXT_MODEL", "gpt-5.6-luna")
IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2")
CARD_W = 1080
CARD_H = 1350  # Instagram portrait 4:5
FONT_PATH = "fonts/NanumGothic.ttf"
LOGO_PATH = "assets/profile_logo.png"

st.set_page_config(page_title="Ocean Science Brief", layout="wide")
st.title(APP_TITLE)
st.caption("해양환경 기사 자동 수집 → AI 분석 → 고급 카드뉴스 생성 → 학생 검토 → GitHub 업로드")


# =========================================================
# Secrets / Clients
# =========================================================
def get_secret(name: str, default: str = "") -> str:
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return os.getenv(name, default)


def get_client():
    api_key = get_secret("OPENAI_API_KEY")
    if not api_key:
        return None
    return OpenAI(api_key=api_key)


def github_ready() -> bool:
    return all(
        bool(get_secret(k))
        for k in ["GITHUB_TOKEN", "GITHUB_OWNER", "GITHUB_REPO"]
    )


# =========================================================
# News collection
# =========================================================
def clean_html(text: str) -> str:
    if not text:
        return ""
    return BeautifulSoup(text, "html.parser").get_text(" ", strip=True)


def fetch_news_from_google(keywords, per_keyword=5):
    results = []
    seen_links = set()

    for kw in keywords:
        rss_url = (
            "https://news.google.com/rss/search?"
            f"q={quote_plus(kw)}&hl=ko&gl=KR&ceid=KR:ko"
        )
        feed = feedparser.parse(rss_url)

        for entry in feed.entries[:per_keyword]:
            link = entry.get("link", "")
            if not link or link in seen_links:
                continue
            seen_links.add(link)

            results.append(
                {
                    "keyword": kw,
                    "title": entry.get("title", "제목 없음"),
                    "summary": clean_html(entry.get("summary", "")),
                    "published": entry.get("published", ""),
                    "link": link,
                }
            )

    return results


# =========================================================
# AI analysis
# =========================================================
def parse_json_response(text: str):
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end >= 0:
        text = text[start : end + 1]

    return json.loads(text)


def analyze_article(article):
    client = get_client()
    if client is None:
        raise ValueError("OPENAI_API_KEY가 설정되지 않았습니다.")

    prompt = f"""
너는 해양환경 전문 뉴스 카드뉴스 편집자다.
아래 기사 정보를 바탕으로 인스타그램용 카드뉴스 5장을 설계하라.

기사 정보
- 검색 키워드: {article['keyword']}
- 기사 제목: {article['title']}
- 게시 시각: {article['published']}
- 기사 링크: {article['link']}
- RSS 요약: {article['summary']}

목표
- 고등학생과 일반 대중이 빠르게 이해할 수 있어야 한다.
- 뉴스 인포그래픽처럼 짧고 강한 문장으로 구성한다.
- 기사에 없는 사실을 만들어내지 않는다.
- 제목을 기사 원문 그대로 복사하지 말고 카드뉴스용으로 짧게 재구성한다.
- 시각적으로 표현할 가치가 있는 장면을 구체적으로 설명한다.

반드시 JSON만 출력한다.
형식은 정확히 아래와 같다.

{{
  "main_title": "전체 게시물 제목, 18자 안팎",
  "headline_summary": "기사 핵심 요약 1~2문장",
  "why_important": "왜 중요한가 1~2문장",
  "ecology_impact": "해양생태계 영향 1~2문장",
  "solution_or_message": "해결 방향 또는 시사점 1~2문장",
  "caption": "인스타그램 캡션 본문",
  "hashtags": ["#해양환경", "#기후변화", "#해양생태계"],
  "source_note": "출처: 언론사명 또는 기사 제목",
  "slides": [
    {{
      "role": "cover",
      "title": "표지 제목, 최대 18자",
      "body": "한 줄 부제, 최대 45자",
      "emphasis": "가장 강조할 짧은 문구 또는 수치",
      "visual_prompt": "표지용 핵심 장면을 시각적으로 설명"
    }},
    {{
      "role": "fact",
      "title": "핵심 사실 제목, 최대 16자",
      "body": "핵심 사실 설명, 최대 90자",
      "emphasis": "핵심 수치 또는 짧은 문구",
      "visual_prompt": "핵심 사실을 보여주는 시각 장면"
    }},
    {{
      "role": "impact",
      "title": "왜 중요한가, 최대 16자",
      "body": "해양생태계 또는 기후 영향 설명, 최대 90자",
      "emphasis": "핵심 영향 한 구절",
      "visual_prompt": "생태계 영향을 보여주는 구체적 장면"
    }},
    {{
      "role": "solution",
      "title": "해결 방향 제목, 최대 16자",
      "body": "현실적인 대응 방향 2~3개를 짧게 설명, 최대 100자",
      "emphasis": "핵심 행동 한 구절",
      "visual_prompt": "해결책을 상징하는 구체적 장면"
    }},
    {{
      "role": "close",
      "title": "마무리 제목, 최대 16자",
      "body": "독자가 기억할 핵심 메시지, 최대 85자",
      "emphasis": "마지막 한 문장",
      "visual_prompt": "희망적이고 절제된 마무리 장면"
    }}
  ]
}}

추가 규칙
- 슬라이드 제목에 '1장', '2장' 같은 표현을 넣지 않는다.
- 본문은 긴 문단이 아니라 카드뉴스에 맞는 짧은 문장으로 쓴다.
- visual_prompt에는 글자, 로고, 숫자를 그려 달라는 지시를 넣지 않는다.
"""

    response = client.responses.create(model=TEXT_MODEL, input=prompt)
    data = parse_json_response(response.output_text)

    slides = data.get("slides", [])
    if not isinstance(slides, list) or len(slides) != 5:
        raise ValueError("AI가 5장 카드뉴스 형식으로 응답하지 않았습니다.")

    return data


# =========================================================
# Font / Text helpers
# =========================================================
def resolve_font_path():
    candidates = [
        FONT_PATH,
        "./fonts/NanumGothic.ttf",
        "/mount/src/ocean-ai-news/fonts/NanumGothic.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    raise FileNotFoundError("fonts/NanumGothic.ttf를 찾을 수 없습니다.")


RESOLVED_FONT = resolve_font_path()


def font(size):
    return ImageFont.truetype(RESOLVED_FONT, size)


def wrap_text(draw, text, used_font, max_width):
    text = str(text or "").strip()
    if not text:
        return []

    lines = []
    for paragraph in text.split("\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            lines.append("")
            continue

        current = ""
        for ch in paragraph:
            test = current + ch
            box = draw.textbbox((0, 0), test, font=used_font)
            if box[2] - box[0] <= max_width:
                current = test
            else:
                if current:
                    lines.append(current)
                current = ch
        if current:
            lines.append(current)

    return lines


def trim_lines(lines, max_lines):
    if len(lines) <= max_lines:
        return lines
    out = lines[:max_lines]
    if out[-1]:
        out[-1] = out[-1][:-1] + "…" if len(out[-1]) > 1 else out[-1] + "…"
    return out


def draw_lines(draw, lines, used_font, x, y, gap, fill):
    cy = y
    for line in lines:
        if not line:
            cy += gap
            continue
        draw.text((x, cy), line, font=used_font, fill=fill)
        box = draw.textbbox((0, 0), line, font=used_font)
        cy += (box[3] - box[1]) + gap
    return cy


def split_sentences(text):
    text = re.sub(r"\s+", " ", str(text or "").strip())
    if not text:
        return []
    parts = re.split(r"(?<=[.!?다요])\s+", text)
    return [p.strip() for p in parts if p.strip()]


# =========================================================
# Brand logo
# =========================================================
def load_logo(size=68):
    if not os.path.exists(LOGO_PATH):
        return None

    logo = Image.open(LOGO_PATH).convert("RGBA")
    logo = ImageOps.fit(logo, (size, size), method=Image.Resampling.LANCZOS)

    # 원형 마스크
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    logo.putalpha(mask)
    return logo


def add_brand_header(card, page_num, total_pages):
    draw = ImageDraw.Draw(card)
    logo = load_logo(68)

    left = 70
    top = 46
    if logo is not None:
        card.alpha_composite(logo, (left, top))
        text_x = left + 84
    else:
        text_x = left

    draw.text(
        (text_x, top + 14),
        "OCEAN SCIENCE BRIEF",
        font=font(29),
        fill=(15, 105, 140, 255),
    )

    badge = f"{page_num} / {total_pages}"
    badge_font = font(24)
    b = draw.textbbox((0, 0), badge, font=badge_font)
    bw = b[2] - b[0]
    bh = b[3] - b[1]
    x2 = CARD_W - 68
    x1 = x2 - bw - 48
    y1 = 52
    y2 = y1 + 50
    draw.rounded_rectangle((x1, y1, x2, y2), radius=24, fill=(235, 247, 250, 255))
    draw.text((x1 + 24, y1 + (50 - bh) / 2 - 2), badge, font=badge_font, fill=(15, 118, 110, 255))


# =========================================================
# AI image generation
# =========================================================
def generate_ai_visual(client, article_title, slide):
    if client is None:
        raise ValueError("OpenAI API가 연결되지 않았습니다.")

    prompt = f"""
Create a premium editorial infographic illustration for a Korean ocean-science news card.

News topic: {article_title}
Slide title: {slide.get('title', '')}
Key message: {slide.get('body', '')}
Visual direction: {slide.get('visual_prompt', '')}

Art direction:
- professional news infographic illustration, not a generic stock image
- sophisticated editorial vector / semi-3D illustration
- visually clear central concept with meaningful environmental symbolism
- ocean science, climate, marine ecosystem visual language
- navy, ocean blue, teal, sea-green palette with restrained warm accents where useful
- balanced composition, strong depth, refined lighting and shading
- suitable for an educational news card made by Ocean Science Brief
- clean background with enough negative space
- no text, no letters, no numbers, no logos, no watermarks
- avoid childish clip-art, random circles, emoji-like graphics, meme aesthetics
- square composition
"""

    result = client.images.generate(
        model=IMAGE_MODEL,
        prompt=prompt,
        size="1024x1024",
    )
    return base64.b64decode(result.data[0].b64_json)


# =========================================================
# Image helpers
# =========================================================
def rounded_mask(size, radius):
    mask = Image.new("L", size, 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    return mask


def place_image(card, image_bytes, box, radius=34, darken=0.0):
    if not image_bytes:
        return False

    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        size = (box[2] - box[0], box[3] - box[1])
        img = ImageOps.fit(img, size, method=Image.Resampling.LANCZOS)
        if darken > 0:
            overlay = Image.new("RGB", size, (0, 0, 0))
            img = Image.blend(img, overlay, darken)
        mask = rounded_mask(size, radius)
        card.paste(img, (box[0], box[1]), mask)
        return True
    except Exception:
        return False


def base_card():
    card = Image.new("RGBA", (CARD_W, CARD_H), (238, 248, 251, 255))
    draw = ImageDraw.Draw(card)

    # subtle ocean gradient
    for y in range(CARD_H):
        t = y / CARD_H
        r = int(235 + 15 * t)
        g = int(247 + 6 * t)
        b = int(251 + 3 * t)
        draw.line((0, y, CARD_W, y), fill=(r, g, b, 255))

    draw.rounded_rectangle((34, 30, 1046, 1320), radius=44, fill=(255, 255, 255, 255))
    return card


# =========================================================
# Slide layouts
# =========================================================
def render_cover(slide, image_bytes, page_num, total_pages):
    card = base_card()
    draw = ImageDraw.Draw(card)
    add_brand_header(card, page_num, total_pages)

    # large hero visual
    hero = (70, 145, 1010, 815)
    has_image = place_image(card, image_bytes, hero, radius=38, darken=0.03)

    if not has_image:
        # image failure: clean editorial panel, not clip-art
        draw.rounded_rectangle(hero, radius=38, fill=(226, 243, 248, 255))
        draw.rectangle((70, 655, 1010, 815), fill=(14, 74, 106, 255))

    # title area
    title_font = font(62)
    body_font = font(32)
    emph_font = font(28)

    title_lines = trim_lines(wrap_text(draw, slide.get("title", ""), title_font, 900), 2)
    y = draw_lines(draw, title_lines, title_font, 78, 860, 12, (16, 27, 45, 255))

    emphasis = slide.get("emphasis", "").strip()
    if emphasis:
        ebox = draw.textbbox((0, 0), emphasis, font=emph_font)
        ew = min(ebox[2] - ebox[0] + 44, 900)
        draw.rounded_rectangle((78, y + 14, 78 + ew, y + 66), radius=18, fill=(225, 246, 246, 255))
        draw.text((100, y + 23), emphasis, font=emph_font, fill=(13, 116, 111, 255))
        y += 84

    body_lines = trim_lines(wrap_text(draw, slide.get("body", ""), body_font, 900), 3)
    draw_lines(draw, body_lines, body_font, 78, y + 14, 12, (63, 73, 88, 255))

    return card.convert("RGB")


def render_fact(slide, image_bytes, page_num, total_pages):
    card = base_card()
    draw = ImageDraw.Draw(card)
    add_brand_header(card, page_num, total_pages)

    # Minimal text-centric news panel, image optional
    title_font = font(54)
    emphasis_font = font(70)
    body_font = font(34)

    title_lines = trim_lines(wrap_text(draw, slide.get("title", ""), title_font, 890), 2)
    y = draw_lines(draw, title_lines, title_font, 78, 170, 12, (18, 29, 47, 255))

    emphasis = slide.get("emphasis", "").strip()
    if emphasis:
        draw.rounded_rectangle((78, y + 30, 1002, y + 190), radius=30, fill=(12, 94, 125, 255))
        emph_lines = trim_lines(wrap_text(draw, emphasis, emphasis_font, 850), 2)
        draw_lines(draw, emph_lines, emphasis_font, 120, y + 64, 8, (255, 255, 255, 255))
        y += 225

    if image_bytes:
        place_image(card, image_bytes, (78, y + 24, 1002, y + 430), radius=34)
        body_y = y + 470
    else:
        body_y = y + 50

    body_lines = trim_lines(wrap_text(draw, slide.get("body", ""), body_font, 850), 7)
    draw.rounded_rectangle((78, body_y, 1002, 1215), radius=30, fill=(246, 249, 251, 255))
    draw_lines(draw, body_lines, body_font, 116, body_y + 38, 15, (56, 68, 84, 255))

    return card.convert("RGB")


def render_impact(slide, image_bytes, page_num, total_pages):
    card = base_card()
    draw = ImageDraw.Draw(card)
    add_brand_header(card, page_num, total_pages)

    title_font = font(54)
    body_font = font(33)
    emphasis_font = font(30)

    title_lines = trim_lines(wrap_text(draw, slide.get("title", ""), title_font, 890), 2)
    y = draw_lines(draw, title_lines, title_font, 78, 170, 12, (18, 29, 47, 255))

    image_top = y + 28
    if image_bytes:
        place_image(card, image_bytes, (78, image_top, 1002, image_top + 510), radius=36)
        content_top = image_top + 545
    else:
        content_top = image_top + 20

    emphasis = slide.get("emphasis", "").strip()
    if emphasis:
        draw.rounded_rectangle((78, content_top, 1002, content_top + 82), radius=22, fill=(225, 246, 244, 255))
        draw.text((110, content_top + 22), emphasis, font=emphasis_font, fill=(12, 115, 108, 255))
        content_top += 105

    body_lines = trim_lines(wrap_text(draw, slide.get("body", ""), body_font, 850), 6)
    draw_lines(draw, body_lines, body_font, 100, content_top + 15, 14, (54, 65, 82, 255))

    return card.convert("RGB")


def render_solution(slide, image_bytes, page_num, total_pages):
    card = base_card()
    draw = ImageDraw.Draw(card)
    add_brand_header(card, page_num, total_pages)

    title_font = font(54)
    body_font = font(32)
    num_font = font(32)

    title_lines = trim_lines(wrap_text(draw, slide.get("title", ""), title_font, 890), 2)
    y = draw_lines(draw, title_lines, title_font, 78, 170, 12, (18, 29, 47, 255))

    if image_bytes:
        place_image(card, image_bytes, (78, y + 25, 1002, y + 380), radius=34)
        y += 415
    else:
        y += 35

    sentences = split_sentences(slide.get("body", ""))
    if not sentences:
        sentences = [slide.get("body", "")]
    sentences = sentences[:3]

    for idx, sentence in enumerate(sentences, start=1):
        top = y + (idx - 1) * 170
        draw.rounded_rectangle((78, top, 1002, top + 142), radius=26, fill=(246, 249, 251, 255))
        draw.ellipse((105, top + 36, 175, top + 106), fill=(14, 129, 150, 255))
        num = str(idx)
        nb = draw.textbbox((0, 0), num, font=num_font)
        draw.text((140 - (nb[2]-nb[0])/2, top + 50), num, font=num_font, fill=(255, 255, 255, 255))
        lines = trim_lines(wrap_text(draw, sentence, body_font, 760), 3)
        draw_lines(draw, lines, body_font, 205, top + 30, 10, (54, 65, 82, 255))

    return card.convert("RGB")


def render_close(slide, image_bytes, page_num, total_pages, source_note):
    card = base_card()
    draw = ImageDraw.Draw(card)
    add_brand_header(card, page_num, total_pages)

    title_font = font(54)
    quote_font = font(46)
    body_font = font(31)
    source_font = font(20)

    title_lines = trim_lines(wrap_text(draw, slide.get("title", ""), title_font, 890), 2)
    y = draw_lines(draw, title_lines, title_font, 78, 175, 12, (18, 29, 47, 255))

    if image_bytes:
        place_image(card, image_bytes, (78, y + 25, 1002, y + 430), radius=36)
        y += 465
    else:
        y += 55

    emphasis = slide.get("emphasis", "").strip() or slide.get("body", "")
    draw.rounded_rectangle((78, y, 1002, y + 245), radius=34, fill=(15, 84, 112, 255))
    qlines = trim_lines(wrap_text(draw, emphasis, quote_font, 820), 3)
    draw_lines(draw, qlines, quote_font, 120, y + 48, 14, (255, 255, 255, 255))

    body_y = y + 285
    body_lines = trim_lines(wrap_text(draw, slide.get("body", ""), body_font, 860), 4)
    draw_lines(draw, body_lines, body_font, 92, body_y, 12, (62, 73, 89, 255))

    if source_note:
        s_lines = trim_lines(wrap_text(draw, source_note, source_font, 850), 2)
        draw_lines(draw, s_lines, source_font, 92, 1250, 4, (119, 132, 148, 255))

    return card.convert("RGB")


def render_slide(slide, image_bytes, idx, total, source_note):
    role = slide.get("role", "").strip().lower()
    if role == "cover":
        return render_cover(slide, image_bytes, idx, total)
    if role == "fact":
        return render_fact(slide, image_bytes, idx, total)
    if role == "impact":
        return render_impact(slide, image_bytes, idx, total)
    if role == "solution":
        return render_solution(slide, image_bytes, idx, total)
    return render_close(slide, image_bytes, idx, total, source_note)


# =========================================================
# Cardnews generation
# =========================================================
def should_generate_image(idx, mode):
    if mode == "표지만":
        return idx == 1
    if mode == "1장+3장":
        return idx in (1, 3)
    if mode == "모든 슬라이드":
        return True
    return False


def create_zip(paths):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in paths:
            zf.write(path, arcname=os.path.basename(path))
    buf.seek(0)
    return buf.getvalue()


def create_cardnews(article, analysis, image_mode):
    client = get_client()
    slides = analysis.get("slides", [])
    source_note = analysis.get("source_note", f"출처: {article.get('title', '기사 원문')}")

    temp_dir = tempfile.mkdtemp()
    image_paths = []

    for idx, slide in enumerate(slides, start=1):
        image_bytes = None
        if should_generate_image(idx, image_mode):
            try:
                image_bytes = generate_ai_visual(client, article.get("title", "해양환경 기사"), slide)
            except Exception as error:
                st.warning(f"{idx}장 AI 이미지 생성 실패: {error}")

        # 이미지가 실패해도 깔끔한 텍스트 레이아웃으로 자동 처리
        rendered = render_slide(slide, image_bytes, idx, len(slides), source_note)
        path = os.path.join(temp_dir, f"slide_{idx}.png")
        rendered.save(path, quality=95)
        image_paths.append(path)

    return image_paths, create_zip(image_paths)


# =========================================================
# GitHub upload
# =========================================================
def upload_images_to_github(image_paths):
    token = get_secret("GITHUB_TOKEN")
    owner = get_secret("GITHUB_OWNER")
    repo = get_secret("GITHUB_REPO")
    branch = get_secret("GITHUB_BRANCH", "main")

    if not all([token, owner, repo]):
        raise ValueError("GitHub Secrets 설정이 부족합니다.")

    folder = datetime.now().strftime("%Y%m%d_%H%M%S")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    urls = []
    for idx, image_path in enumerate(image_paths, start=1):
        filename = f"slide_{idx}.png"
        github_path = f"published/{folder}/{filename}"
        api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{github_path}"

        with open(image_path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("utf-8")

        payload = {
            "message": f"Upload Ocean Science Brief card {idx}",
            "content": encoded,
            "branch": branch,
        }

        response = requests.put(api_url, headers=headers, json=payload, timeout=60)
        if response.status_code not in [200, 201]:
            raise RuntimeError(f"GitHub 업로드 실패: {response.status_code} {response.text}")

        urls.append(
            f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{github_path}"
        )

    return urls


# =========================================================
# Session state
# =========================================================
if "articles" not in st.session_state:
    st.session_state["articles"] = []
if "analysis" not in st.session_state:
    st.session_state["analysis"] = {}
if "cards" not in st.session_state:
    st.session_state["cards"] = {}
if "github_urls" not in st.session_state:
    st.session_state["github_urls"] = {}


# =========================================================
# Sidebar
# =========================================================
with st.sidebar:
    st.subheader("검색 키워드")

    keyword_text = st.text_area(
        "검색 키워드",
        value="해양온난화\n해양열파\n해양생태계\n생물펌프\n블루카본",
        height=150,
    )

    per_keyword = st.slider("키워드당 기사 수", 2, 10, 5)

    st.markdown("### 카드뉴스 이미지")
    image_mode = st.selectbox(
        "AI 이미지 생성 범위",
        ["표지만", "1장+3장", "모든 슬라이드"],
        index=1,
        help="기본값은 1장+3장입니다. 이미지 생성 수가 많을수록 API 비용이 증가합니다.",
    )

    if get_client() is not None:
        st.success("OpenAI API 연결됨")
    else:
        st.warning("OpenAI API 미연결")

    if github_ready():
        st.success("GitHub 업로드 설정됨")
    else:
        st.info("GitHub 업로드 설정을 확인하세요.")

    if os.path.exists(LOGO_PATH):
        st.success("브랜드 로고 연결됨")
    else:
        st.warning("assets/profile_logo.png가 없습니다.")


# =========================================================
# Main
# =========================================================
if st.button("최신 기사 수집"):
    keywords = [x.strip() for x in keyword_text.splitlines() if x.strip()]
    with st.spinner("최신 기사를 수집하는 중입니다..."):
        st.session_state["articles"] = fetch_news_from_google(keywords, per_keyword)
    st.success("기사 수집 완료")


articles = st.session_state["articles"]

if not articles:
    st.info("먼저 '최신 기사 수집' 버튼을 눌러 주세요.")
else:
    for index, article in enumerate(articles):
        article_key = f"article_{index}"

        with st.expander(f"[{article['keyword']}] {article['title']}"):
            st.write(article.get("published", ""))
            if article.get("summary"):
                st.write(article["summary"])
            st.link_button("원문 열기", article["link"])

            if st.button("AI 분석", key=f"analyze_{article_key}"):
                with st.spinner("AI가 기사를 분석하고 카드뉴스 구조를 설계하는 중입니다..."):
                    try:
                        st.session_state["analysis"][article_key] = analyze_article(article)
                        st.success("AI 분석 완료")
                    except Exception as error:
                        st.error(f"AI 분석 오류: {error}")

            if article_key not in st.session_state["analysis"]:
                continue

            result = st.session_state["analysis"][article_key]

            st.markdown("## " + result.get("main_title", "제목 없음"))
            st.markdown("**핵심 요약**")
            st.write(result.get("headline_summary", ""))
            st.markdown("**왜 중요한가**")
            st.write(result.get("why_important", ""))
            st.markdown("**해양생태계와의 연결**")
            st.write(result.get("ecology_impact", ""))
            st.markdown("**해결 방향**")
            st.write(result.get("solution_or_message", ""))

            caption_default = result.get("caption", "")
            hashtags = " ".join(result.get("hashtags", []))
            caption = st.text_area(
                "인스타그램 캡션",
                value=(caption_default + "\n\n" + hashtags).strip(),
                height=180,
                key=f"caption_{article_key}",
            )

            approved = st.checkbox(
                "원문을 직접 확인했고 게시용으로 검토했음",
                key=f"approve_{article_key}",
            )

            if st.button("고급 카드뉴스 생성", key=f"create_{article_key}"):
                with st.spinner("AI 이미지와 카드뉴스를 생성하는 중입니다..."):
                    try:
                        paths, zip_data = create_cardnews(article, result, image_mode)
                        st.session_state["cards"][article_key] = {
                            "paths": paths,
                            "zip": zip_data,
                            "caption": caption,
                        }
                        st.success("카드뉴스 생성 완료")
                    except Exception as error:
                        st.error(f"카드뉴스 생성 오류: {error}")

            if article_key in st.session_state["cards"]:
                card_data = st.session_state["cards"][article_key]
                st.markdown("### 생성된 카드뉴스")

                for path in card_data["paths"]:
                    st.image(path, use_container_width=True)

                st.download_button(
                    "카드뉴스 ZIP 다운로드",
                    data=card_data["zip"],
                    file_name="ocean_science_brief.zip",
                    mime="application/zip",
                    key=f"download_{article_key}",
                )

                if st.button("GitHub에 카드뉴스 업로드", key=f"upload_{article_key}"):
                    with st.spinner("GitHub에 업로드하는 중입니다..."):
                        try:
                            urls = upload_images_to_github(card_data["paths"])
                            st.session_state["github_urls"][article_key] = urls
                            st.success("GitHub 업로드 완료")
                        except Exception as error:
                            st.error(f"GitHub 업로드 오류: {error}")

                if article_key in st.session_state["github_urls"]:
                    st.markdown("### 공개 이미지 URL")
                    for i, url in enumerate(st.session_state["github_urls"][article_key], start=1):
                        st.write(f"{i}장: {url}")

            if approved:
                st.success("게시 승인 완료")
            else:
                st.warning("게시 전 원문 확인을 권장합니다.")
