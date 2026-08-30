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
from PIL import Image, ImageDraw, ImageFont, ImageOps

APP_TITLE = "🌊 Ocean Science Brief"
TEXT_MODEL = os.getenv("OPENAI_TEXT_MODEL", "gpt-5.6-luna")
IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1")
CARD_WIDTH = 1080
CARD_HEIGHT = 1350  # 4:5 portrait

st.set_page_config(page_title="Ocean Science Brief", layout="wide")
st.title(APP_TITLE)
st.caption("해양환경 기사 자동 수집 → AI 요약 → 카드뉴스 생성 → 학생 검토 → GitHub 업로드")


# =========================
# 기본 유틸
# =========================
def get_secret(name: str, default: str = "") -> str:
    if name in st.secrets:
        return st.secrets[name]
    return os.getenv(name, default)


def get_api_key() -> str:
    return get_secret("OPENAI_API_KEY", "")


def get_client():
    api_key = get_api_key()
    if not api_key:
        return None
    return OpenAI(api_key=api_key)


def github_ready() -> bool:
    needed = ["GITHUB_TOKEN", "GITHUB_OWNER", "GITHUB_REPO"]
    return all(bool(get_secret(k, "")) for k in needed)


def clean_html(text: str) -> str:
    if not text:
        return ""
    return BeautifulSoup(text, "html.parser").get_text(" ", strip=True)


def fetch_news_from_google(keywords, per_keyword=5):
    results = []
    seen = set()

    for kw in keywords:
        rss_url = (
            "https://news.google.com/rss/search?"
            f"q={quote_plus(kw)}&hl=ko&gl=KR&ceid=KR:ko"
        )
        feed = feedparser.parse(rss_url)

        for entry in feed.entries[:per_keyword]:
            link = entry.get("link", "")
            if not link or link in seen:
                continue
            seen.add(link)

            results.append({
                "keyword": kw,
                "title": entry.get("title", "제목 없음"),
                "summary": clean_html(entry.get("summary", "")),
                "published": entry.get("published", ""),
                "link": link,
            })

    return results


def parse_json_response(text: str):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        text = text[start:end + 1]

    return json.loads(text)


# =========================
# 기사 분석
# =========================
def analyze_article(article):
    client = get_client()
    if client is None:
        raise ValueError("OPENAI_API_KEY가 설정되지 않았습니다.")

    prompt = f"""
너는 해양환경 카드뉴스를 만드는 과학 커뮤니케이터다.
아래 기사 정보를 바탕으로, 고등학생이 이해하기 쉬운 한국어 카드뉴스 초안을 작성하라.

기사 정보
- 키워드: {article['keyword']}
- 제목: {article['title']}
- 날짜: {article['published']}
- 링크: {article['link']}
- RSS 요약: {article['summary']}

출력은 반드시 JSON만 하라.

형식:
{{
  "main_title": "게시물 전체 제목",
  "headline_summary": "핵심 요약 1~2문장",
  "why_important": "왜 중요한가",
  "ecology_impact": "해양생태계와의 연결",
  "solution_or_message": "해결 방향 또는 생각해볼 점",
  "caption": "인스타그램 게시용 캡션 본문",
  "hashtags": ["#해양환경", "#기후변화"],
  "slides": [
    {{"title": "1장 제목", "body": "1장 본문"}},
    {{"title": "2장 제목", "body": "2장 본문"}},
    {{"title": "3장 제목", "body": "3장 본문"}},
    {{"title": "4장 제목", "body": "4장 본문"}},
    {{"title": "5장 제목", "body": "5장 본문"}}
  ],
  "source_note": "출처 표기 문장"
}}

규칙:
- 카드뉴스는 정확히 5장이다.
- 슬라이드 본문은 2~4문장 이내로 간결하게 작성한다.
- 기사에 없는 사실은 과장하여 추가하지 않는다.
- 너무 어려운 전문용어는 쉬운 말로 풀어 쓴다.
- 1장은 표지, 2장은 핵심 사실, 3장은 왜 중요한가, 4장은 생태계 영향, 5장은 해결 방향 또는 시사점으로 구성한다.
"""

    response = client.responses.create(
        model=TEXT_MODEL,
        input=prompt,
    )

    data = parse_json_response(response.output_text)
    slides = data.get("slides", [])
    if not isinstance(slides, list) or len(slides) != 5:
        raise ValueError("AI 응답의 slides 형식이 올바르지 않습니다.")
    return data


# =========================
# 폰트 / 텍스트 유틸
# =========================
def get_font_path():
    candidates = [
        "fonts/NanumGothic.ttf",
        "./fonts/NanumGothic.ttf",
        "/mount/src/ocean-ai-news/fonts/NanumGothic.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    raise FileNotFoundError("fonts/NanumGothic.ttf 파일을 찾을 수 없습니다.")


FONT_PATH = get_font_path()


def get_font(size: int):
    return ImageFont.truetype(FONT_PATH, size)


def wrap_text(draw, text, font, max_width):
    text = str(text or "").strip()
    if not text:
        return []

    paragraphs = text.split("\n")
    lines = []

    for para in paragraphs:
        para = para.strip()
        if not para:
            lines.append("")
            continue

        current = ""
        for ch in para:
            test = current + ch
            bbox = draw.textbbox((0, 0), test, font=font)
            width = bbox[2] - bbox[0]
            if width <= max_width:
                current = test
            else:
                if current:
                    lines.append(current)
                current = ch
        if current:
            lines.append(current)

    return lines


def limit_lines(lines, max_lines):
    if len(lines) <= max_lines:
        return lines

    trimmed = lines[:max_lines]
    last = trimmed[-1].rstrip()
    if len(last) >= 2:
        last = last[:-1] + "…"
    else:
        last = last + "…"
    trimmed[-1] = last
    return trimmed


def draw_text_lines(draw, lines, font, x, y, line_gap, fill):
    current_y = y
    for line in lines:
        if line == "":
            current_y += line_gap
            continue
        draw.text((x, current_y), line, font=font, fill=fill)
        bbox = draw.textbbox((0, 0), line, font=font)
        current_y += (bbox[3] - bbox[1]) + line_gap
    return current_y


# =========================
# 카드뉴스용 슬라이드 내용 구성
# =========================
def build_slide_contents(article, analysis):
    ai_slides = analysis.get("slides", [])
    if isinstance(ai_slides, list) and len(ai_slides) == 5:
        normalized = []
        for slide in ai_slides:
            normalized.append({
                "title": str(slide.get("title", "")).strip() or "제목 없음",
                "body": str(slide.get("body", "")).strip() or "내용 없음",
            })
        return normalized

    caption = analysis.get("caption", "")
    caption_clean = caption.split("#")[0].strip() if caption else ""

    return [
        {
            "title": article.get("title", "해양 환경 기사"),
            "body": analysis.get("headline_summary", "기사의 핵심 내용을 요약한 카드입니다."),
        },
        {
            "title": "왜 중요한가",
            "body": analysis.get("why_important", "이 내용이 왜 중요한지 설명합니다."),
        },
        {
            "title": "해양생태계와의 연결",
            "body": analysis.get("ecology_impact", "해양생태계에 미치는 영향을 정리합니다."),
        },
        {
            "title": "해결 방향",
            "body": analysis.get("solution_or_message", "문제 해결을 위한 방향을 제시합니다."),
        },
        {
            "title": "함께 생각해 보기",
            "body": caption_clean or "바다의 변화를 이해하고 해양환경 보호 실천에 함께 참여해 봅시다.",
        },
    ]


# =========================
# AI 이미지 생성
# =========================
def generate_ai_visual(client, article_title, slide_title, slide_body):
    if client is None:
        return None

    prompt = f"""
Instagram 카드뉴스용 세련된 일러스트 이미지를 생성하라.

주제: {article_title}
슬라이드 제목: {slide_title}
핵심 내용: {slide_body}

조건:
- 해양과학, 바다, 기후, 생태계를 연상시키는 현대적 editorial illustration
- 교육용 카드뉴스에 어울리는 깔끔하고 전문적인 분위기
- 파랑, 청록, 흰색 중심 색감, 소량의 포인트 컬러 허용
- 사람보다 주제 요소가 중심이 되도록 구성
- 텍스트, 숫자, 문자, 로고, 워터마크는 절대 넣지 않는다
- 과장된 밈 스타일, 저품질 클립아트 느낌 금지
- 정사각형 비주얼
"""

    result = client.images.generate(
        model=IMAGE_MODEL,
        prompt=prompt,
        size="1024x1024",
    )
    return base64.b64decode(result.data[0].b64_json)


# =========================
# 카드 이미지 렌더링
# =========================
def make_background():
    card = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), (240, 248, 251))
    draw = ImageDraw.Draw(card)

    for i in range(420):
        ratio = i / 420
        r = int(217 - ratio * 25)
        g = int(239 - ratio * 10)
        b = int(248 - ratio * 3)
        draw.line((0, i, CARD_WIDTH, i), fill=(r, g, b))

    draw.rounded_rectangle((40, 40, 1040, 1310), radius=42, fill=(255, 255, 255))
    draw.rounded_rectangle((74, 116, 200, 124), radius=4, fill=(14, 165, 233))
    return card


def draw_placeholder_visual(draw, box):
    x1, y1, x2, y2 = box
    draw.rounded_rectangle(box, radius=32, fill=(222, 242, 248))

    draw.ellipse((x1 + 40, y1 + 80, x1 + 220, y1 + 260), fill=(115, 198, 238))
    draw.ellipse((x1 + 170, y1 + 130, x1 + 370, y1 + 330), fill=(93, 211, 230))
    draw.ellipse((x2 - 270, y1 + 90, x2 - 110, y1 + 250), fill=(143, 227, 235))

    wave_y = y2 - 110
    draw.pieslice((x1 + 30, wave_y - 50, x1 + 380, wave_y + 140), 0, 180, fill=(71, 184, 230))
    draw.pieslice((x1 + 290, wave_y - 40, x1 + 670, wave_y + 160), 0, 180, fill=(33, 160, 223))
    draw.pieslice((x1 + 590, wave_y - 55, x2 - 30, wave_y + 150), 0, 180, fill=(14, 116, 193))


def paste_visual(card, image_bytes, box):
    draw = ImageDraw.Draw(card)
    if not image_bytes:
        draw_placeholder_visual(draw, box)
        return

    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        fitted = ImageOps.fit(image, (box[2] - box[0], box[3] - box[1]))
        mask = Image.new("L", (box[2] - box[0], box[3] - box[1]), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, box[2] - box[0], box[3] - box[1]), radius=32, fill=255)
        card.paste(fitted, (box[0], box[1]), mask)
    except Exception:
        draw_placeholder_visual(draw, box)


def create_card_slide(title, body, slide_no, total_slides, image_bytes=None, source_note=""):
    card = make_background()
    draw = ImageDraw.Draw(card)

    label_font = get_font(28)
    title_font = get_font(56)
    body_font = get_font(34)
    meta_font = get_font(22)
    page_font = get_font(24)

    navy = (17, 24, 39)
    slate = (55, 65, 81)
    muted = (100, 116, 139)
    teal = (15, 118, 110)
    light_panel = (245, 249, 251)

    draw.text((78, 72), "OCEAN SCIENCE BRIEF", font=label_font, fill=(8, 145, 178))

    page_badge = f"{slide_no} / {total_slides}"
    badge_box = (884, 66, 996, 116)
    draw.rounded_rectangle(badge_box, radius=22, fill=(235, 247, 250))
    badge_bbox = draw.textbbox((0, 0), page_badge, font=page_font)
    badge_w = badge_bbox[2] - badge_bbox[0]
    badge_h = badge_bbox[3] - badge_bbox[1]
    draw.text(
        (
            badge_box[0] + (badge_box[2] - badge_box[0] - badge_w) / 2,
            badge_box[1] + (badge_box[3] - badge_box[1] - badge_h) / 2 - 2
        ),
        page_badge,
        font=page_font,
        fill=teal
    )

    image_box = (78, 150, 1002, 630)
    paste_visual(card, image_bytes, image_box)

    title_box_y = 680
    title_lines = wrap_text(draw, title, title_font, 890)
    title_lines = limit_lines(title_lines, 2)
    title_end_y = draw_text_lines(draw, title_lines, title_font, 78, title_box_y, 10, navy)

    body_panel_top = max(title_end_y + 26, 820)
    draw.rounded_rectangle((78, body_panel_top, 1002, 1215), radius=28, fill=light_panel)

    body_lines = wrap_text(draw, body, body_font, 850)
    body_lines = limit_lines(body_lines, 8)
    draw_text_lines(draw, body_lines, body_font, 110, body_panel_top + 36, 14, slate)

    if source_note:
        source_lines = wrap_text(draw, source_note, meta_font, 850)
        source_lines = limit_lines(source_lines, 2)
        draw_text_lines(draw, source_lines, meta_font, 90, 1240, 6, muted)

    return card


def create_zip(image_paths):
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in image_paths:
            zf.write(path, arcname=os.path.basename(path))
    zip_buffer.seek(0)
    return zip_buffer.getvalue()


def create_cardnews_with_ai(article, analysis, client, use_ai_images=True, image_mode="표지만"):
    slides = build_slide_contents(article, analysis)
    source_note = analysis.get("source_note", f"출처: {article.get('title', '기사 원문')}")

    temp_dir = tempfile.mkdtemp()
    image_paths = []

    for idx, slide in enumerate(slides, start=1):
        should_generate = False
        if use_ai_images:
            if image_mode == "모든 슬라이드":
                should_generate = True
            elif image_mode == "표지만" and idx == 1:
                should_generate = True

        image_bytes = None
        if should_generate:
            try:
                image_bytes = generate_ai_visual(
                    client=client,
                    article_title=article.get("title", "해양 환경 기사"),
                    slide_title=slide["title"],
                    slide_body=slide["body"],
                )
            except Exception as error:
                st.warning(f"{idx}장 AI 이미지 생성 실패: {error}")

        slide_image = create_card_slide(
            title=slide["title"],
            body=slide["body"],
            slide_no=idx,
            total_slides=len(slides),
            image_bytes=image_bytes,
            source_note=source_note if idx == len(slides) else "",
        )

        save_path = os.path.join(temp_dir, f"slide_{idx}.png")
        slide_image.save(save_path)
        image_paths.append(save_path)

    return image_paths, create_zip(image_paths)


# =========================
# GitHub 업로드
# =========================
def upload_images_to_github(image_paths):
    token = get_secret("GITHUB_TOKEN")
    owner = get_secret("GITHUB_OWNER")
    repo = get_secret("GITHUB_REPO")
    branch = get_secret("GITHUB_BRANCH", "main")

    if not all([token, owner, repo]):
        raise ValueError("GITHUB_TOKEN / GITHUB_OWNER / GITHUB_REPO 설정이 필요합니다.")

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
            content = base64.b64encode(f.read()).decode("utf-8")

        payload = {
            "message": f"Upload cardnews image: {filename}",
            "content": content,
            "branch": branch,
        }

        response = requests.put(api_url, headers=headers, json=payload, timeout=60)
        if response.status_code not in (200, 201):
            raise RuntimeError(f"GitHub 업로드 실패: {response.status_code} {response.text}")

        urls.append(f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{github_path}")

    return urls


# =========================
# 세션 상태
# =========================
for key, default in {
    "articles": [],
    "analysis": {},
    "cards": {},
    "github_urls": {},
}.items():
    if key not in st.session_state:
        st.session_state[key] = default


# =========================
# 사이드바
# =========================
with st.sidebar:
    st.subheader("검색 키워드")

    keyword_text = st.text_area(
        "검색 키워드",
        value="해양온난화\n해양열파\n해양생태계\n생물펌프\n블루카본",
        height=150,
    )

    per_keyword = st.slider("키워드당 기사 수", 2, 10, 5)

    st.markdown("### 카드뉴스 옵션")
    use_ai_images = st.checkbox("AI 이미지 포함", value=True)
    image_mode = st.selectbox("이미지 생성 범위", ["표지만", "모든 슬라이드"], index=0)

    if get_api_key():
        st.success("OpenAI API 연결됨")
    else:
        st.warning("OpenAI API 미연결")

    if github_ready():
        st.success("GitHub 업로드 설정됨")
    else:
        st.info("GitHub 업로드를 쓰려면 GITHUB_TOKEN / OWNER / REPO를 설정하세요.")


# =========================
# 기사 수집 버튼
# =========================
if st.button("최신 기사 수집"):
    keywords = [line.strip() for line in keyword_text.splitlines() if line.strip()]
    with st.spinner("기사를 수집하는 중입니다..."):
        st.session_state["articles"] = fetch_news_from_google(keywords, per_keyword)
    st.success("기사 수집 완료")


# =========================
# 메인 UI
# =========================
articles = st.session_state["articles"]

if not articles:
    st.info("먼저 '최신 기사 수집' 버튼을 눌러 주세요.")
else:
    for index, article in enumerate(articles):
        article_key = f"article_{index}"
        expander_title = f"[{article['keyword']}] {article['title']}"

        with st.expander(expander_title):
            st.write(article.get("published", ""))
            if article.get("summary"):
                st.write(article["summary"])
            st.link_button("원문 열기", article["link"])

            if st.button("AI 분석", key=f"analyze_{article_key}"):
                with st.spinner("AI가 기사를 분석하는 중입니다..."):
                    try:
                        st.session_state["analysis"][article_key] = analyze_article(article)
                        st.success("AI 분석 완료")
                    except Exception as error:
                        st.error(f"AI 분석 오류: {error}")

            if article_key in st.session_state["analysis"]:
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

                default_caption = result.get("caption", "")
                hashtags = " ".join(result.get("hashtags", []))
                caption_value = st.text_area(
                    "인스타그램 캡션",
                    value=(default_caption + "\n\n" + hashtags).strip(),
                    height=200,
                    key=f"caption_{article_key}",
                )

                approved = st.checkbox(
                    "원문을 직접 확인했고 게시용으로 검토했음",
                    key=f"approve_{article_key}",
                )

                if st.button("카드뉴스 생성", key=f"create_card_{article_key}"):
                    with st.spinner("카드뉴스를 생성하는 중입니다..."):
                        try:
                            result["caption"] = caption_value
                            client = get_client()
                            paths, zip_data = create_cardnews_with_ai(
                                article=article,
                                analysis=result,
                                client=client,
                                use_ai_images=use_ai_images,
                                image_mode=image_mode,
                            )
                            st.session_state["cards"][article_key] = {
                                "paths": paths,
                                "zip": zip_data,
                                "caption": caption_value,
                            }
                            st.success("카드뉴스 생성 완료")
                        except Exception as error:
                            st.error(f"카드뉴스 생성 오류: {error}")

                if article_key in st.session_state["cards"]:
                    card_data = st.session_state["cards"][article_key]
                    st.markdown("### 생성된 카드뉴스")
                    for image_path in card_data["paths"]:
                        st.image(image_path, use_container_width=True)

                    st.download_button(
                        "카드뉴스 ZIP 다운로드",
                        data=card_data["zip"],
                        file_name="ocean_cardnews.zip",
                        mime="application/zip",
                        key=f"download_{article_key}",
                    )

                    if st.button("GitHub에 카드뉴스 업로드", key=f"upload_{article_key}"):
                        with st.spinner("GitHub에 업로드하는 중입니다..."):
                            try:
                                uploaded_urls = upload_images_to_github(card_data["paths"])
                                st.session_state["github_urls"][article_key] = uploaded_urls
                                st.success("GitHub 업로드 완료")
                            except Exception as error:
                                st.error(f"GitHub 업로드 오류: {error}")

                    if article_key in st.session_state["github_urls"]:
                        st.markdown("### GitHub 이미지 URL")
                        for idx, url in enumerate(st.session_state["github_urls"][article_key], start=1):
                            st.write(f"{idx}장: {url}")

                if approved:
                    st.success("게시 승인 완료")
                else:
                    st.warning("게시 전 원문 확인을 권장합니다.")
