# 실행 검증 기록 · 2026-11-04 (Asia/Seoul)

- Python 3.12 가상환경 `/workspace/.venvs/sporton` 설치 완료.
- `bash tools/install.sh` 재실행 성공: frozen 의존성 설치, editable package 재등록, `pip check`, DB 초기화.
- `pytest -q`: **29 passed**, 27.93초. TEST/FIXTURE 전용 임시 DB 사용. 73개 의존성 경고(LightGBM feature-name 경고, Starlette/AnyIO deprecated alias); assertion 실패 없음. 샌드박스의 로컬 소켓 제한을 피하기 위해 지원되는 실행 권한 컨텍스트에서 검증.
- 실제 `sporton serve --host 127.0.0.1 --port 8000` 프로세스 시작. `/health`, `/api/overview`, `/`에 로컬 HTTP GET: 모두 200. 빈 실제 데이터, null 성능, 수집 오류 3개를 확인.
- `node --check`로 dashboard script 문법 확인. 실제 브라우저 레이아웃/상호작용 검증은 수행하지 않음.
- `sporton worker --inbox var/inbox --once`: 신규 입력 0, 감사 0, 후보 훈련 없음. 자동으로 데이터를 만들어내지 않음.
- 테스트에서 실제 ML 학습, 교정, 세 후보 비교, 홀드아웃 평가, 3-fold expanding backtest, 미래 경기 예측·PASS·WATCH 게시, 종료 후 결과 감사 및 불변성을 실행.
- `sporton collect-nba --seasons 2023-24 2024-25 2025-26`: 세 요청 모두 proxy `403 Forbidden`, 실패 저장 및 exit 1. 추가 진단에서도 `ProxyError 403 Forbidden` 확인. 현재 actual/historical games 0, synthetic production games 0, 실전 예측 0.
- `sporton train` 실전 DB 실행: 자료 부족 `need >=100 usable games, found 0`, exit 2. 미학습 모델이나 실전 성능을 생성하지 않음.
- 환경 초안 `install_script`, `start_skill`, network custom domain `stats.nba.com` 저장 성공. `requires_publish=true`. 현재 runtime custom allowed_hosts는 여전히 비어 있으며 도메인 추가가 반영된 상태로 주장하지 않음.
- 컨테이너 빌드, PostgreSQL, 실제 NBA 예측 성능, 게시 후 새 태스크 복원은 검증하지 않음.

## 다종목 확장 후 검증

- 최종 `pytest -q`: **53 passed**, 83.65초. 기존 NBA 테스트에 축구·MLB·KBO의 학습/교정/독립 심사/감사, 3-way 무승부, 이닝·90분 계약, 사전 context 시점, 리그별 데이터·모델·Champion·rollback 격리, 악화 후보 승격 차단을 추가. 의존성 경고 73개는 위와 동일.
- `bash tools/install.sh`: sporton 0.2.0 재설치, pip check, DB 초기화 성공. 새로운 모델은 기존 의존성으로 동작하며 scipy를 직접 의존성으로 명시.
- 실제 서버 `GET /api/capabilities`: 통계 분석가 3종(NBA/football/baseball) 확인. `/api/overview?league=NBA|EPL|MLB|KBO` 모두 HTTP 200, 실제/합성 경기 0건 및 성능 null 확인. `/` HTTP 200 및 리그 선택 요소 확인.
- 대시보드 JavaScript `node --check` 통과. 실제 브라우저 상호작용 검증은 미수행.
- 실제 축구·야구 자료와 라이브 성능은 미확보. TEST/FIXTURE에서 돌아간 결과를 실제 정확도로 해석하지 않는다. 생성형 LLM, 자동 축구·야구 공급자, KBL/배구/e스포츠 모델은 미연결/미구현이다.
- 기존 환경 install_script는 그대로 재검증하고 start_skill을 다종목 실행·53개 테스트·리그별 학습/백테스트/감사·미구현 범위에 맞게 갱신 저장했다. 초안 저장은 게시나 신규 머신 복원 검증과 별개다.

## 데일리 브리핑 검증

- 최종 전체 `pytest -q`: **70 passed**, 92.61초. 의존성 경고 79개. 브리핑 17개 검사는 KST 06:30/17:00·UTC·연말 경계, 지연/누락/재시작, 슬롯 중복 방지·불변 원본·사본 복구, 이전 안내 경기의 결과 추적, 새 정보 재심사, 실제 NBA 테스트 모델을 이용한 자동 분석·심사, 매시간 관리·중복 프로세스 차단, fixture 격리 API를 포함한다.
- 현재 실제 DB에 `2026-11-04-0630` 브리핑 생성. 실제 생성 시각은 2026-11-04 11:18 KST이므로 **지연 생성**을 표시. 실제 경기·일정 미확보와 기존 공급자 403 오류 3건을 정직하게 포함. TEST/FIXTURE 경기/모델은 실제 DB에 없음.
- 실제 브리핑 daemon `--retrain` 실행 확인. API 관측 `running_observed=true`, 다음 예정 `2026-11-04T08:00:00Z` = 당일 17:00 KST. 이 관측은 미래 시각 실행 성공이나 환경 종료 후 지속 실행의 보장은 아님.
- `/api/briefing-status`, `/api/briefings`, 브리핑 상세·Markdown 원문, 대시보드 HTTP 응답 성공. 목록의 원본과 Markdown 응답이 같음을 확인. Dashboard JS 문법 검사 성공. 브라우저 상호작용 실검증은 미수행.
- 실제 `var/backups/2026-11-04-0630` manifest SHA256 검증 및 백업 SQLite의 briefing 1건 확인. 원격 백업과 새 호스트에서의 전체 복구는 미검증.
- 환경 start_skill에 필수 API/브리핑 daemon 재시작·상태 확인·매시간 관리·고정 시간·보관/백업·상시 호스트 제약을 반영 저장. 설치 스크립트/네트워크/저장소 설정은 변경하지 않음. 게시 전 초안 상태임.

## 직접 수집·PyTorch 검증

- 공개 원본과 라이선스를 직접 요청: FiveThirtyEight NBA archive HTTP 200, 18,173,265 bytes, NBA로 분류한 59,008 경기. OpenFootball EPL 2023-24/2024-25/2025-26 각 380 경기, 총 1,140. 원본/라이선스와 SHA256을 보존. 외부 접속이 차단된 KBL 사이트의 우회 relay를 사용하지 않았다.
- 실제 EPL 원본에 score 객체와 score 배열 두 형식이 있어 첫 2025-26 수집이 실패했다. 원본 구조를 검사하고 두 형식을 검증하도록 수정한 뒤 재수집 성공. 실패 기록도 보존.
- 공식 PyTorch CPU 2.9.0+cpu를 TLS 검증 유지로 설치. `bash tools/install.sh`로 고정 CPU 의존성을 포함한 재설치 및 pip check 성공.
- 실제 EPL 날짜순 연구 fit/calibration/validation/test 349/198/173/374. PyTorch MLP loss 1.1802→0.9477. Final test Brier MLP 0.3264, Poisson 0.3171, Logistic 0.3178. 딥러닝 개선은 확인되지 않았으며 어떤 archive 후보도 production model_versions에 등록하지 않음. 상세 한계/CI는 collection-and-deep-learning.md 및 연구 result.json에 기록.
- 전체 자동 테스트 **77 passed**, 95.09초, 의존성 경고 79개. 수집·라이선스 실패·중복·형태 검증·시간 분리·MLP 실제 학습/교정/직렬화·연구/운영 격리·같은 final-test 경기 재사용 차단을 포함. 이후 raw/research 백업 확장과 최신 브리핑 경로의 영향 범위 테스트 **25 passed**, 9.28초.
- 최신 API 실제 HTTP 검증: data-sources의 EPL 1,140/NBA 59,008 확인, archive-research의 MLP test n=374 및 production_eligible=false 확인, 자동 scheduler 실행 상태 확인, 연구 패널과 새로운 브리핑의 비교 실험 표시 확인. verified historical_game_results와 model_versions는 0으로 유지.
- 적용 네트워크 상태 restricted/enforced, custom allowed_hosts=[] 확인. 초안에는 KBL·NBA·검색 도메인이 있으나 실행 정책에 적용됐다는 증거가 없음. KBL 오늘 대진은 여전히 미확인. 초안 install_script/start_skill을 직접 수집·PyTorch·연구 격리·원본 백업에 맞춰 저장했다. 사용자 환경 설정 저장·게시 및 새 태스크 복원 검증은 별도다.

## 영구 보존 기반 v0.3 · 2026-11-06 (Asia/Seoul)

- Python package 0.3.0, psycopg/Parquet/DuckDB/cryptography 고정 의존성 설치·editable 재등록·pip check 성공. 기존 소스·SQLite·원본·실패 기록을 보존했다.
- 공식 digest 고정 PostgreSQL을 별도 Docker 프로세스/보존 bind mount로 실행. 앱 SELECT/INSERT 역할과 owner 작업을 분리. 46개 이벤트+7개 typed/FK 테이블=53개. 전체 앱 Docker 이미지 빌드와 외부 운영 Compose 배포는 미검증이다.
- 실제 SQLite 이벤트 68건을 원본 ID·생성 시각·payload·hash 그대로 PostgreSQL로 이관하고 전수 대조. 이후 연구 보고서와 출처 관측·lake·백업 기록은 새 행으로 추가. 기존 DB를 삭제하거나 빈 DB로 대체하지 않았다.
- 실제 NBA 역사 59,008 + EPL 3시즌 1,140 = 60,148행을 Parquet로 변환하고 DuckDB 건수/원본 hash 검증. 네 개 archive 출처 관측은 공개 시각 증거가 없어 UNVERIFIED. verified 운영 경기·실전 모델·공식 픽은 여전히 0이며 실전 정확도는 미측정이다.
- 최종 전체 테스트 **96 passed**, 100.37초, 의존성 경고 79개. 실제 PostgreSQL 네 통합 테스트 포함. 권한/원본 UPDATE·DELETE·TRUNCATE 차단, 정상 PRE_GAME와 typed projection, 위조·수정된 시작 시각을 이용한 사후 등록 거절, 실제 암호화 pg_dump→격리 pg_restore를 실행했다. SQLite 테스트는 정정 as-of·미확인/충돌·typed schema·보존 등급·멱등 이관/rollback·결과 정정 후 감사·원본 없는 성적 제외·암호문 손상·복원 실패 상태·재시도·관리 인증을 포함한다. TEST/FIXTURE 결과는 실제 정확도가 아니다.
- 최신 실제 backup `b7ee0f11-38eb-4de5-94d6-3e21697e32e2`, restore test `d3f237e4-ed64-450b-be11-dfca2f0f575e`: 53개 테이블 내용, 제약/트리거/함수 128개, 15개 자산을 격리 DB/복원 파일에서 검증. 소요 0.7222초, 이 소규모 로컬 훈련의 RTO 300초 목표 충족. 운영 복원 시간이나 SLA의 증거로 확대 해석하지 않는다. 실패한 검사도 이력으로 보존한다.
- 실제 개발 DB 컨테이너 재시작 전후 53개 테이블 내용 hash가 동일. 증거는 ignored `var/persistence-dev/restart-check.json`. 스키마와 원본을 초기화하지 않았다.
- WAL 실제 archiver 성공과 세그먼트 암호화 보관 확인. 초기 권한 설정 실패 19회도 숨기거나 reset하지 않음. 최근 archiving 성공을 확인했지만 물리 base backup/PITR 목표 시점 복원은 미검증이며 API는 pitr_restore_tested=false다.
- 실제 관리자 HTTP: 비인증 401, 인증 200, PostgreSQL/53개 테이블/미확인 출처 4건/최신 복원 검증 성공/RPO 경고 없음, RESEARCH 원본 추적 200. `/health`와 브리핑 API도 200. 브라우저 레이아웃/상호작용은 미검증이다.
- API·브리핑·persistence worker를 PostgreSQL 역할에 맞춰 실행. 브리핑은 06:30/17:00 Asia/Seoul 대시보드 경로이며 환경 종료 후 상시 실행은 보장하지 않는다. 백업은 owner worker에 위임하며 평문 SQLite 백업으로 대체하지 않는다.
- 재사용 설치/시작 지침 초안 저장 성공(requires_publish=true). 기존 네트워크/저장소/비밀 요구사항은 보존. 초안 저장은 정책 적용·게시·운영 배포·새 태스크 복원과 별개다.
- 남은 운영 위험: DB/백업/키가 동일 호스트에 있고 외부 DB·원격 백업·키 관리가 미연결. 호스트 전체 유실까지 영구 보존한다고 주장하지 않는다. 최신 NBA/KBL 일정·시장·라인업 및 고급 종목별 데이터/실전 모델·생성형 독립 심사도 아직 미확보/미연결이다.

- 추가 실운영 관측: 2026-11-06 17:00 KST 예약 브리핑이 08:00:00.148974 UTC에 late=false로 실제 생성·PostgreSQL 저장·HTTP 조회됨. 당일 최신 경기/시장 미확보는 그대로 표시. 이 브리핑까지 포함한 후속 백업 ecdeb65e-36aa-49cf-b585-b90dee20daa7와 격리 복원 817e6955-924a-4d98-bf23-7eae2f83151d가 53개 테이블/128개 스키마 객체/15개 파일 검증을 통과했다(0.5969초). 이 관측은 향후 상시 실행 성공을 보장하지 않는다.
