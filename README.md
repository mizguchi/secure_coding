# 중고마켓 (Tiny Second-hand Shopping Platform)

Flask + SQLite로 만든 간단한 중고거래 플랫폼입니다.

## 구현된 기능

- 회원가입 / 로그인 / 로그아웃
- 상품 등록 / 목록 조회 / 상세 페이지
- 상품 검색
- 판매완료 상태 변경 / 상품 삭제
- 마이페이지 (소개글 수정, 비밀번호 변경, 내 상품 목록)
- 유저 프로필 조회

## 환경 설정 및 실행 방법

### 1. 의존성 설치

```bash
pip install -r requirements.txt
```

### 2. 실행

```bash
python app.py
```

### 3. 접속

브라우저에서 `http://127.0.0.1:5000` 접속

## 파일 구조

```
market/
├── app.py               # Flask 앱 + 라우트 + DB 초기화
├── market.db            # SQLite DB (실행 시 자동 생성)
├── requirements.txt
└── templates/
    ├── base.html        # 공통 레이아웃 (네비바, 플래시 메시지)
    ├── index.html       # 상품 목록 + 검색
    ├── register.html    # 회원가입
    ├── login.html       # 로그인
    ├── add_product.html # 상품 등록
    ├── product_detail.html  # 상품 상세
    ├── mypage.html      # 마이페이지
    └── profile.html     # 유저 프로필
```

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py