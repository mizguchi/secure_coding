# 중고마켓 (Tiny Second-hand Shopping Platform)

Flask + SQLite로 만든 중고거래 플랫폼

## 구현된 기능

- 회원가입 / 로그인 / 로그아웃 / 계정 삭제
- 상품 등록 / 목록 조회 / 검색 / 상세 페이지
- 판매완료 상태 변경 / 상품 삭제 / 찜하기
- 마이페이지 (소개글·비밀번호 수정, 잔액, 내 상품, 찜 목록, 송금 내역)
- 유저 프로필 조회
- 실시간 전체 채팅 / 1대1 DM
- 유저, 상품 신고 (3회 누적 자동 차단)
- 유저 간 송금
- 관리자 패널 (유저 정지·활성화, 상품 차단·해제, 신고 내역 삭제)

## 환경 설정 및 실행 방법

### 요구 환경

- Python 3.10 이상
- pip

### 1. 가상환경 생성 및 활성화

```bash
python3 -m venv venv
```

**Windows:**
```bash
venv\Scripts\activate
```

**macOS / Linux / WSL:**
```bash
source venv/bin/activate
```

### 2. 의존성 설치

```bash
pip install -r requirements.txt
```

requirements.txt 포함 패키지:
- flask==3.0.3
- werkzeug==3.0.3
- flask-socketio==5.3.6
- eventlet==0.35.2
- flask-wtf==1.2.1

### 3. 실행

```bash
python app.py
```

최초 실행 시 자동으로 처리되는 항목:
- `market.db` SQLite DB 파일 생성
- `secret.key` 랜덤 Secret Key 파일 생성
- 관리자 계정 자동 생성

### 4. 접속

브라우저에서 `http://127.0.0.1:5000` 접속

### 5. 관리자 계정

| 아이디 | 비밀번호 |
|---|---|
| admin | Admin@secure99! |

## 파일 구조

```
market/
├── app.py                  # Flask 앱 + 라우트 + SocketIO + DB 초기화
├── market.db               # SQLite DB (실행 시 자동 생성)
├── secret.key              # Secret Key 파일 (실행 시 자동 생성)
├── requirements.txt
└── templates/
    ├── base.html           # 공통 레이아웃 (네비바, CSRF 토큰 자동 주입)
    ├── index.html          # 상품 목록 + 검색
    ├── register.html       # 회원가입
    ├── login.html          # 로그인
    ├── add_product.html    # 상품 등록
    ├── product_detail.html # 상품 상세 + 찜하기
    ├── mypage.html         # 마이페이지
    ├── profile.html        # 유저 프로필
    ├── chat.html           # 전체 채팅
    ├── dm.html             # 1대1 채팅
    ├── report.html         # 신고
    ├── transfer.html       # 송금
    └── admin.html          # 관리자 패널
```
