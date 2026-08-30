# Ocean Science Brief

AI 기반 해양환경 뉴스 큐레이션 프로젝트의 첫 버전입니다.

## 현재 기능
- 해양환경 키워드별 최신 기사 수집
- AI 핵심 요약
- '왜 중요한가 / 해결 가능성' 정리
- 인스타그램 캡션 초안 생성
- 학생 원문 검토 후 게시 승인

## 실행
```bash
pip install -r requirements.txt
streamlit run app.py
```

## OpenAI API 키
Streamlit Cloud의 Settings → Secrets에:
```toml
OPENAI_API_KEY="본인_API_KEY"
```
API 키는 GitHub에 직접 올리지 마세요.

## 권장 운영
기사 자동수집 → AI 요약 → 학생 원문 검토 → 승인 → Instagram 직접 게시

완전 자동 게시보다 이 반자동 구조를 먼저 권장합니다. 다음 단계에서 카드뉴스 이미지 생성과 Instagram 공식 API 게시 기능을 붙일 수 있습니다.
