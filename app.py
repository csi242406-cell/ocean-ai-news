import os
import io
import re
import json
import zipfile
import tempfile
import base64
import requests
from datetime import datetime
from urllib.parse import quote_plus

import feedparser
import streamlit as st
from openai import OpenAI
from bs4 import BeautifulSoup
import base64
from PIL import Image, ImageDraw, ImageFont


st.set_page_config(page_title="Ocean Science Brief", layout="wide")

st.title("🌊 Ocean Science Brief")
st.caption("해양환경 기사 자동 수집 → AI 요약 → 카드뉴스 생성 → 학생 검토 → SNS 게시")


def get_api_key():
    if "OPENAI_API_KEY" in st.secrets:
        return st.secrets["OPENAI_API_KEY"]
    return os.getenv("OPENAI_API_KEY", "")


def get_client():
    api_key = get_api_key()
    if not api_key:
        return None
    return OpenAI(api_key=api_key)


def clean_html(text):
    if not text:
        return ""
    soup = BeautifulSoup(text, "html.parser")
    return soup.get_text(" ", strip=True)


def fetch_news_from_google(keywords, per_keyword=5):
    results = []
    seen_links = set()

    for kw in keywords:
        rss_url = (
            f"https://news.google.com/rss/search?"
            f"q={quote_plus(kw)}&hl=ko&gl=KR&ceid=KR:ko"
        )
        feed = feedparser.parse(rss_url)

        for entry in feed.entries[:per_keyword]:
            link = entry.get("link", "")
            title = entry.get("title", "제목 없음")
            summary = clean_html(entry.get("summary", ""))
            published = entry.get("published", "")

            if link in seen_links:
                continue

            seen_links.add(link)

            results.append({
                "keyword": kw,
                "title": title,
                "summary": summary,
                "published": published,
                "link": link,
            })

    return results


def parse_json_response(text):
    text = text.strip()

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()

    start = text.find("{")
    end = text.rfind("}")

    if start != -1 and end != -1:
        text = text[start:end + 1]

    return json.loads(text)


def analyze_article(article):
    client = get_client()

    if client is None:
        raise ValueError("OPENAI_API_KEY가 설정되지 않았습니다.")

    prompt = f"""
너는 해양환경 인스타그램 카드뉴스를 만드는 과학 커뮤니케이터다.

아래 기사 정보를 바탕으로
고등학생도 이해할 수 있는 한국어 카드뉴스를 작성하라.

기사 정보
키워드: {article["keyword"]}
제목: {article["title"]}
날짜: {article["published"]}
링크: {article["link"]}
RSS 요약: {article["summary"]}

반드시 JSON만 출력하라.

형식:

{{
  "main_title": "게시물 전체 제목",
  "headline_summary": "핵심 요약 1~2문장",
  "why_important": "왜 중요한가",
  "ecology_impact": "해양생태계와의 연결",
  "solution_or_message": "해결 방향 또는 생각해볼 점",
  "caption": "인스타그램 게시용 캡션",
  "hashtags": ["#해양환경", "#기후변화"],
  "slides": [
    {{
      "title": "1장 제목",
      "body": "1장 본문"
    }},
    {{
      "title": "2장 제목",
      "body": "2장 본문"
    }},
    {{
      "title": "3장 제목",
      "body": "3장 본문"
    }},
    {{
      "title": "4장 제목",
      "body": "4장 본문"
    }},
    {{
      "title": "5장 제목",
      "body": "5장 본문"
    }}
  ],
  "source_note": "출처 표기 문장"
}}

카드뉴스 구성은 반드시 다음 순서를 따른다.

1장: 표지
2장: 핵심 사실
3장: 왜 중요한가
4장: 해양생태계에 미치는 영향
5장: 해결 방향 또는 시사점

주의:
- 기사에 없는 사실을 과장해서 추가하지 않는다.
- 한 장의 본문은 너무 길지 않게 작성한다.
- 정확히 5장의 slides를 출력한다.
"""

    response = client.responses.create(
        model="gpt-5.6-luna",
        input=prompt
    )

    data = parse_json_response(response.output_text)

    if "slides" not in data or len(data["slides"]) != 5:
        raise ValueError("AI가 5장 형식으로 응답하지 않았습니다.")

    return data


def get_font_path():
    candidates = [
        "fonts/NanumGothic.ttf",
        "./fonts/NanumGothic.ttf",
        "/mount/src/ocean-ai-news/fonts/NanumGothic.ttf",
    ]

    for path in candidates:
        if os.path.exists(path):
            return path

    return None


def wrap_text(draw, text, font, max_width):
    lines = []
    current = ""

    for char in text:
        test = current + char
        bbox = draw.textbbox((0, 0), test, font=font)
        width = bbox[2] - bbox[0]

        if width <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = char

    if current:
        lines.append(current)

    return lines


def draw_card(slide_number, title, body, source_note, save_path):
    width = 1080
    height = 1350

    image = Image.new("RGB", (width, height), (244, 249, 252))
    draw = ImageDraw.Draw(image)

    font_path = get_font_path()

    if not font_path:
        raise FileNotFoundError("NanumGothic.ttf를 찾을 수 없습니다.")

    title_font = ImageFont.truetype(font_path, 58)
    body_font = ImageFont.truetype(font_path, 40)
    small_font = ImageFont.truetype(font_path, 28)
    footer_font = ImageFont.truetype(font_path, 24)

    blue = (18, 82, 150)
    teal = (0, 145, 150)
    dark = (30, 41, 59)
    gray = (71, 85, 105)
    white = (255, 255, 255)

    # 상단 헤더
    draw.rounded_rectangle(
        (60, 60, 1020, 190),
        radius=30,
        fill=blue
    )

    draw.text(
        (100, 95),
        "Ocean Science Brief",
        font=title_font,
        fill=white
    )

    draw.text(
        (900, 105),
        f"{slide_number}/5",
        font=small_font,
        fill=white
    )

    # 제목
    draw.rounded_rectangle(
        (80, 240, 1000, 430),
        radius=28,
        fill=white
    )

    title_lines = wrap_text(
        draw,
        title,
        title_font,
        820
    )

    y = 275
    for line in title_lines[:2]:
        draw.text(
            (120, y),
            line,
            font=title_font,
            fill=dark
        )
        y += 72

    # 본문
    draw.rounded_rectangle(
        (80, 470, 1000, 1110),
        radius=28,
        fill=white
    )

    body_lines = []

    for paragraph in body.split("\n"):
        paragraph = paragraph.strip()

        if not paragraph:
            continue

        wrapped = wrap_text(
            draw,
            paragraph,
            body_font,
            790
        )

        body_lines.extend(wrapped)
        body_lines.append("")

    y = 530

    for line in body_lines:
        if y > 1010:
            break

        if line == "":
            y += 25
        else:
            draw.text(
                (120, y),
                line,
                font=body_font,
                fill=gray
            )
            y += 58

    # 하단 강조
    draw.rounded_rectangle(
        (80, 1150, 1000, 1235),
        radius=22,
        fill=(222, 244, 247)
    )

    draw.text(
        (110, 1178),
        "해양환경 이슈를 쉽게 읽는 과학 카드뉴스",
        font=small_font,
        fill=teal
    )

    # 출처
    source_lines = wrap_text(
        draw,
        source_note,
        footer_font,
        900
    )

    y = 1260

    for line in source_lines[:2]:
        draw.text(
            (80, y),
            line,
            font=footer_font,
            fill=(100, 116, 139)
        )
        y += 28

    image.save(save_path)


def create_cardnews(analysis):
    temp_dir = tempfile.mkdtemp()
    image_paths = []

    source_note = analysis.get(
        "source_note",
        "출처: 기사 원문"
    )

    for index, slide in enumerate(
        analysis["slides"],
        start=1
    ):
        path = os.path.join(
            temp_dir,
            f"slide_{index}.png"
        )

        draw_card(
            index,
            slide["title"],
            slide["body"],
            source_note,
            path
        )

        image_paths.append(path)

    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(
        zip_buffer,
        "w",
        zipfile.ZIP_DEFLATED
    ) as zip_file:

        for path in image_paths:
            zip_file.write(
                path,
                arcname=os.path.basename(path)
            )

    zip_buffer.seek(0)

    return image_paths, zip_buffer.getvalue()

def upload_image_to_github(image_path, filename):
    token = st.secrets["GITHUB_TOKEN"]
    owner = st.secrets["GITHUB_OWNER"]
    repo = st.secrets["GITHUB_REPO"]
    branch = st.secrets.get("GITHUB_BRANCH", "main")

    with open(image_path, "rb") as f:
        content = base64.b64encode(f.read()).decode("utf-8")

    folder = datetime.now().strftime("%Y%m%d_%H%M%S")
    github_path = f"published/{folder}/{filename}"

    api_url = (
        f"https://api.github.com/repos/"
        f"{owner}/{repo}/contents/{github_path}"
    )

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }

    data = {
        "message": f"Upload cardnews image: {filename}",
        "content": content,
        "branch": branch
    }

    response = requests.put(
        api_url,
        headers=headers,
        json=data
    )

    if response.status_code not in [200, 201]:
        raise Exception(
            f"GitHub 업로드 실패: {response.status_code} "
            f"{response.text}"
        )

    raw_url = (
        f"https://raw.githubusercontent.com/"
        f"{owner}/{repo}/{branch}/{github_path}"
    )

    return raw_url
    
# =========================
# AI 이미지 포함 카드뉴스 생성 기능
# =========================

FONT_PATH = "fonts/NanumGothic.ttf"

def pick_value(d, keys, default=""):
    for k in keys:
        if k in d and d[k]:
            return d[k]
    return default

def get_font(size):
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except:
        return ImageFont.load_default()

def wrap_text(draw, text, font, max_width):
    text = str(text).strip()
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

def draw_wrapped_text(draw, text, font, x, y, max_width, line_gap=12, max_lines=None, fill=(30, 41, 59)):
    lines = wrap_text(draw, text, font, max_width)

    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        if lines:
            if len(lines[-1]) >= 2:
                lines[-1] = lines[-1][:-1] + "…"
            else:
                lines[-1] = lines[-1] + "…"

    current_y = y
    for line in lines:
        draw.text((x, current_y), line, font=font, fill=fill)
        bbox = draw.textbbox((0, 0), line, font=font)
        line_height = bbox[3] - bbox[1]
        current_y += line_height + line_gap

    return current_y

def generate_ai_visual(client, article_title, slide_title, slide_body):
    if client is None:
        return None

    prompt = f"""
Create a polished editorial illustration for an Instagram card-news slide.

Topic: {article_title}
Slide focus: {slide_title}
Key message: {slide_body}

Requirements:
- Square 1:1 composition
- Ocean science / marine environment theme
- Clean, modern editorial illustration
- Visually rich and attractive, not plain
- Blue, teal, white dominant palette with a small coral accent
- Suitable for Korean educational card news
- No text, no letters, no numbers
- No watermark
- Should look professional and relevant to the topic
"""

    try:
        result = client.images.generate(
            model="gpt-image-1",
            prompt=prompt,
            size="1024x1024"
        )
        image_b64 = result.data[0].b64_json
        return base64.b64decode(image_b64)
    except Exception as e:
        st.warning(f"AI 이미지 생성 실패: {e}")
        return None

def create_card_slide(title, body, slide_no, total_slides, image_bytes=None):
    width, height = 1080, 1080
    card = Image.new("RGB", (width, height), (245, 250, 252))
    draw = ImageDraw.Draw(card)

    # 바깥 카드 배경
    draw.rounded_rectangle((30, 30, 1050, 1050), radius=36, fill=(255, 255, 255))

    # 상단 라벨
    label_font = get_font(28)
    title_font = get_font(58)
    body_font = get_font(38)
    small_font = get_font(24)

    draw.text((70, 60), "OCEAN SCIENCE BRIEF", font=label_font, fill=(14, 116, 144))

    # 이미지 영역
    image_box = (70, 110, 1010, 560)

    if image_bytes:
        try:
            img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            img = ImageOps.fit(img, (image_box[2] - image_box[0], image_box[3] - image_box[1]))
            card.paste(img, (image_box[0], image_box[1]))
        except:
            draw.rounded_rectangle(image_box, radius=28, fill=(207, 234, 245))
    else:
        draw.rounded_rectangle(image_box, radius=28, fill=(207, 234, 245))
        draw.ellipse((120, 180, 250, 310), fill=(125, 211, 252))
        draw.ellipse((210, 240, 360, 390), fill=(103, 232, 249))
        draw.ellipse((760, 180, 940, 360), fill=(165, 243, 252))
        draw.arc((110, 350, 950, 700), 0, 180, fill=(14, 165, 233), width=8)

    # 제목
    title_y = 610
    title_max_width = 940
    title_lines = wrap_text(draw, title, title_font, title_max_width)
    if len(title_lines) > 2:
        title_lines = title_lines[:2]
        title_lines[-1] = title_lines[-1][:-1] + "…"

    current_y = title_y
    for line in title_lines:
        draw.text((70, current_y), line, font=title_font, fill=(15, 23, 42))
        bbox = draw.textbbox((0, 0), line, font=title_font)
        current_y += (bbox[3] - bbox[1]) + 10

    # 본문
    body_y = current_y + 20
    draw_wrapped_text(
        draw=draw,
        text=body,
        font=body_font,
        x=70,
        y=body_y,
        max_width=940,
        line_gap=10,
        max_lines=7,
        fill=(51, 65, 85)
    )

    # 하단 구분선
    draw.line((70, 980, 1010, 980), fill=(226, 232, 240), width=2)

    # 페이지 번호
    footer_text = f"{slide_no} / {total_slides}"
    footer_bbox = draw.textbbox((0, 0), footer_text, font=small_font)
    footer_w = footer_bbox[2] - footer_bbox[0]
    draw.text((1010 - footer_w, 995), footer_text, font=small_font, fill=(100, 116, 139))

    return card

def build_slide_contents(article, analysis):
    article_title = article.get("title", "해양 환경 기사")
    summary = pick_value(analysis, ["summary", "핵심 요약", "core_summary"], "")
    importance = pick_value(analysis, ["importance", "왜 중요한가"], "")
    ecosystem = pick_value(analysis, ["ecosystem", "해양생태계와의 연결"], "")
    solution = pick_value(analysis, ["solution", "해결 방향"], "")
    caption = pick_value(analysis, ["caption", "인스타그램 캡션"], "")

    caption_clean = caption.split("#")[0].strip() if caption else ""
    if not caption_clean:
        caption_clean = "바다의 변화를 이해하고 해양환경 보호 실천에 함께 참여해 봅시다."

    slides = [
        {
            "title": article_title,
            "body": summary if summary else "기사의 핵심 내용을 요약한 카드입니다."
        },
        {
            "title": "왜 중요한가",
            "body": importance if importance else "이 내용이 왜 중요한지 설명합니다."
        },
        {
            "title": "해양생태계와의 연결",
            "body": ecosystem if ecosystem else "해양생태계에 미치는 영향을 정리합니다."
        },
        {
            "title": "해결 방향",
            "body": solution if solution else "문제 해결을 위한 방향을 제시합니다."
        },
        {
            "title": "함께 생각해 보기",
            "body": caption_clean
        }
    ]
    return slides

def create_cardnews_with_ai(article, analysis, client, use_ai_images=True, image_mode="표지만"):
    slides = build_slide_contents(article, analysis)

    temp_dir = tempfile.mkdtemp()
    image_paths = []

    for idx, slide in enumerate(slides, start=1):
        should_generate_image = False

        if use_ai_images:
            if image_mode == "모든 슬라이드":
                should_generate_image = True
            elif image_mode == "표지만" and idx == 1:
                should_generate_image = True

        image_bytes = None
        if should_generate_image:
            image_bytes = generate_ai_visual(
                client=client,
                article_title=article.get("title", "해양 환경 기사"),
                slide_title=slide["title"],
                slide_body=slide["body"]
            )

        card_img = create_card_slide(
            title=slide["title"],
            body=slide["body"],
            slide_no=idx,
            total_slides=len(slides),
            image_bytes=image_bytes
        )

        out_path = os.path.join(temp_dir, f"slide_{idx}.png")
        card_img.save(out_path)
        image_paths.append(out_path)

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in image_paths:
            zf.write(path, arcname=os.path.basename(path))

    zip_buffer.seek(0)
    return image_paths, zip_buffer.getvalue()

if "articles" not in st.session_state:
    st.session_state["articles"] = []

if "analysis" not in st.session_state:
    st.session_state["analysis"] = {}

if "cards" not in st.session_state:
    st.session_state["cards"] = {}


with st.sidebar:

    st.subheader("검색 키워드")

    keyword_text = st.text_area(
        "검색 키워드",
        value=(
            "해양온난화\n"
            "해양열파\n"
            "해양생태계\n"
            "생물펌프\n"
            "블루카본"
        ),
        height=150
    )

    per_keyword = st.slider(
        "키워드당 기사 수",
        2,
        10,
        5
    )
    st.markdown("### 카드뉴스 옵션")

    use_ai_images = st.checkbox(
        "AI 이미지 포함",
        value=True
    )

    image_mode = st.selectbox(
        "이미지 생성 범위",
        ["표지만", "모든 슬라이드"],
        index=0
    )

    if get_api_key():
        st.success("OpenAI API 연결됨")
    else:
        st.warning("OpenAI API 미연결")


if st.button("최신 기사 수집"):

    keywords = [
        keyword.strip()
        for keyword in keyword_text.splitlines()
        if keyword.strip()
    ]

    with st.spinner("기사를 수집하는 중입니다..."):

        st.session_state["articles"] = (
            fetch_news_from_google(
                keywords,
                per_keyword
            )
        )

    st.success("기사 수집 완료")


articles = st.session_state["articles"]


if not articles:

    st.info(
        "먼저 '최신 기사 수집' 버튼을 눌러 주세요."
    )

else:

    for index, article in enumerate(articles):

        article_key = f"article_{index}"

        title = (
            f'[{article["keyword"]}] '
            f'{article["title"]}'
        )

        with st.expander(title):

            st.write(article["published"])

            if article["summary"]:
                st.write(article["summary"])

            st.link_button(
                "원문 열기",
                article["link"]
            )

            if st.button(
                "AI 분석",
                key=f"analyze_{article_key}"
            ):

                with st.spinner(
                    "AI가 기사를 분석하는 중입니다..."
                ):

                    try:

                        result = analyze_article(article)

                        st.session_state["analysis"][
                            article_key
                        ] = result

                        st.success("AI 분석 완료")

                    except Exception as error:

                        st.error(
                            f"AI 분석 오류: {error}"
                        )


            if article_key in st.session_state["analysis"]:

                result = st.session_state[
                    "analysis"
                ][article_key]

                st.markdown(
                    "## "
                    + result.get(
                        "main_title",
                        "제목 없음"
                    )
                )

                st.markdown(
                    "**핵심 요약**"
                )

                st.write(
                    result.get(
                        "headline_summary",
                        ""
                    )
                )

                st.markdown(
                    "**왜 중요한가**"
                )

                st.write(
                    result.get(
                        "why_important",
                        ""
                    )
                )

                st.markdown(
                    "**해양생태계와의 연결**"
                )

                st.write(
                    result.get(
                        "ecology_impact",
                        ""
                    )
                )

                st.markdown(
                    "**해결 방향**"
                )

                st.write(
                    result.get(
                        "solution_or_message",
                        ""
                    )
                )


                caption = (
                    result.get(
                        "caption",
                        ""
                    )
                    + "\n\n"
                    + " ".join(
                        result.get(
                            "hashtags",
                            []
                        )
                    )
                )

                st.markdown(
                    "### 인스타그램 캡션"
                )

                st.text_area(
                    "캡션",
                    value=caption,
                    height=200,
                    key=f"caption_{article_key}"
                )


                approved = st.checkbox(
                    "원문을 직접 확인했고 게시용으로 검토했음",
                    key=f"approve_{article_key}"
                )


                if st.button(
                    "카드뉴스 생성",
                    key=f"create_card_{article_key}"
                ):

                    with st.spinner(
                        "AI 이미지와 카드뉴스를 생성하는 중입니다..."
                    ):

                        try:

                            client = get_client()

                            paths, zip_data = create_cardnews_with_ai(
                                article=article,
                                analysis=result,
                                client=client,
                                use_ai_images=use_ai_images,
                                image_mode=image_mode
                            )

                            st.session_state[
                                "cards"
                            ][article_key] = {
                                "paths": paths,
                                "zip": zip_data,
                                "caption": caption
                            }

                            st.success(
                                "AI 카드뉴스 생성 완료"
                            )

                        except Exception as error:

                            st.error(
                                f"카드뉴스 생성 오류: {error}"
                            )


                if article_key in st.session_state["cards"]:

                    card_data = st.session_state[
                        "cards"
                    ][article_key]

                    st.markdown(
                        "### 생성된 카드뉴스"
                    )

                    for image_path in card_data["paths"]:

                        st.image(
                            image_path,
                            use_container_width=True
                        )


                    st.download_button(
                        "카드뉴스 ZIP 다운로드",
                        data=card_data["zip"],
                        file_name="ocean_cardnews.zip",
                        mime="application/zip",
                        key=f"download_{article_key}"
                    )

                    if st.button(
                        "GitHub에 카드뉴스 업로드",
                        key=f"upload_{article_key}"
                    ):

                        with st.spinner(
                            "카드뉴스 이미지를 GitHub에 업로드하는 중입니다..."
                        ):

                            try:
                                uploaded_urls = []

                                for idx, image_path in enumerate(
                                    card_data["paths"],
                                    start=1
                                ):
                                    filename = f"slide_{idx}.png"

                                    url = upload_image_to_github(
                                        image_path,
                                        filename
                                    )

                                    uploaded_urls.append(url)

                                st.session_state[
                                    f"github_urls_{article_key}"
                                ] = uploaded_urls

                                st.success(
                                    "GitHub 업로드 완료"
                                )

                            except Exception as error:
                                st.error(
                                    f"GitHub 업로드 오류: {error}"
                                )

                    github_urls_key = f"github_urls_{article_key}"

                    if github_urls_key in st.session_state:
                        st.markdown("### GitHub 이미지 URL")

                        for idx, url in enumerate(
                            st.session_state[github_urls_key],
                            start=1
                        ):
                            st.write(f"{idx}장: {url}")
                    
                    if approved:

                        st.success(
                            "게시 승인 완료"
                        )

                        st.info(
                            "다음 단계에서 Instagram 자동 게시 버튼을 연결할 예정입니다."
                        )

                    else:

                        st.warning(
                            "게시 전 원문 확인을 권장합니다."
                        )
