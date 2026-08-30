import os, re, json, urllib.parse
import streamlit as st
import feedparser
from bs4 import BeautifulSoup
from openai import OpenAI

st.set_page_config(page_title="Ocean Science Brief", page_icon="🌊", layout="wide")

DEFAULT_KEYWORDS = ["해양온난화","해양열파","해양생태계","생물펌프","블루카본"]

def rss_url(keyword):
    q = urllib.parse.quote(keyword)
    return f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"

def fetch_feed(keyword, limit=5):
    feed = feedparser.parse(rss_url(keyword))
    out = []
    for e in feed.entries[:limit]:
        out.append({
            "keyword": keyword,
            "title": getattr(e, "title", ""),
            "link": getattr(e, "link", ""),
            "published": getattr(e, "published", ""),
            "summary": BeautifulSoup(getattr(e, "summary", ""), "html.parser").get_text(" ", strip=True),
        })
    return out

def get_key():
    try:
        return st.secrets.get("OPENAI_API_KEY", "")
    except Exception:
        return os.getenv("OPENAI_API_KEY", "")

def curate(article, api_key):
    if not api_key:
        return {
            "headline": article["title"],
            "summary": article["summary"][:350],
            "why": "",
            "action": "",
            "caption": f"🌊 오늘의 해양환경 뉴스\n\n{article['title']}\n\n원문: {article['link']}\n\n#해양환경 #기후변화 #월간사이언스"
        }
    client = OpenAI(api_key=api_key)
    prompt = f'''아래 해양환경 뉴스 정보를 과장 없이 한국어로 정리해.
기사에 없는 사실은 추가하지 마.
JSON만 출력해.
형식:
{{"headline":"카드뉴스 제목","summary":"핵심 2~3문장","why":"왜 중요한가 1~2문장","action":"기사에 제시된 해결책 또는 구체적 해결책 없음","caption":"인스타그램용 500자 이내 캡션"}}

제목: {article["title"]}
RSS 요약: {article["summary"]}
링크: {article["link"]}'''
    r = client.responses.create(model="gpt-5.6-luna", input=prompt)
    text = re.sub(r"^```json\s*|\s*```$", "", r.output_text.strip(), flags=re.S)
    try:
        return json.loads(text)
    except:
        return {"headline":article["title"],"summary":text,"why":"","action":"","caption":text}

st.title("🌊 Ocean Science Brief")
st.caption("해양환경 기사 자동 수집 → AI 요약 → 학생 검토 → SNS 게시")

with st.sidebar:
    kws = st.text_area("검색 키워드", "\n".join(DEFAULT_KEYWORDS), height=150)
    n = st.slider("키워드당 기사 수", 2, 10, 5)
    api_key = get_key()
    st.success("OpenAI API 연결됨") if api_key else st.warning("OPENAI_API_KEY 미설정")

keywords = [x.strip() for x in kws.splitlines() if x.strip()]

if st.button("최신 기사 수집", type="primary"):
    items = []
    for kw in keywords:
        items.extend(fetch_feed(kw, n))
    seen = {}
    for x in items:
        seen[x["title"]] = x
    st.session_state["articles"] = list(seen.values())

articles = st.session_state.get("articles", [])
if not articles:
    st.info("'최신 기사 수집'을 눌러 시작하세요.")
else:
    for i, a in enumerate(articles):
        with st.expander(f"[{a['keyword']}] {a['title']}"):
            st.write(a["published"])
            st.write(a["summary"] or "RSS 요약 없음")
            st.link_button("원문 열기", a["link"])
            if st.button("AI 분석", key=f"ai{i}"):
                st.session_state[f"c{i}"] = curate(a, api_key)
            c = st.session_state.get(f"c{i}")
            if c:
                st.markdown(f"### {c.get('headline','')}")
                st.write("**핵심 요약**", c.get("summary",""))
                st.write("**왜 중요한가**", c.get("why",""))
                st.write("**해결 가능성**", c.get("action",""))
                st.text_area("인스타그램 캡션 초안", c.get("caption",""), height=220, key=f"cap{i}")
                ok = st.checkbox("원문을 직접 확인했고 게시해도 됨", key=f"ok{i}")
                if ok:
                    st.success("게시 승인됨 — 현재 버전은 학생 검토 후 수동 게시 방식입니다.")

st.divider()
st.caption("AI 요약은 원문을 대체하지 않습니다. 게시 전 출처·날짜·수치·인용을 반드시 직접 확인하세요.")
