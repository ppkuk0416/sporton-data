# SPORTON · Multi-Sport AI Intelligence Platform

농구·축구·야구에 각각 전용 분석기와 리그별 독립 모델을 둔 연구 MVP. 실데이터·합성 자료를 구분하고, 시점 누출 방지 → 실제 모델 학습 → 독립 규칙 심사 → 변경 불가 사전 예측 → 결과 감사 → 후보 모델 비교를 연결합니다.

**직접 수집한 NBA 역사 아카이브 59,008경기와 EPL 3시즌 1,140경기를 보관했습니다. EPL에서 PyTorch 학습·시간순 연구 비교도 실행했습니다. 정확한 공개 시각이 없는 아카이브여서 실전 검증 자료와 분리하며, 실제 KBL 일정·최근 NBA 공식 feed는 아직 프록시에서 차단됩니다.**

## 종목별 분석가

종목 | 분석기와 피처 | 독립 모델 | 예측 범위
---|---|---|---
농구 NBA | 팀 효율·pace·휴식·Elo | Elo / Logistic / LightGBM | 연장 포함 승패·점수·마진·총점
축구 | 득실점·무승부율·xG/xGA·PPDA·휴식 | Poisson goal model / form logistic, 리그별 학습 | 90분 승·무·패 확률·득점·총점
야구 MLB/KBO | 득실점·선발 FIP/K-BB%·휴식/투구수·타선 wRC+·불펜·구장/날씨 | starter logistic + Poisson run heads / Random Forest, MLB·KBO 별도 | 연장 포함 승패·득점·마진·총점, KBO 무승부 별도

각 분석가는 서로 다른 피처·알고리즘·설명 규칙을 사용합니다. 독립 심사도 농구 마진, 축구 골 차이·클린시트·90분 시장, 야구 실점·연장·선발 context를 각각 확인합니다. 동일한 LLM에 역할 이름만 붙인 구조가 아닙니다. 현재는 **통계·규칙 기반 분석가**이며 생성형 LLM은 연결하지 않았습니다. 실제 자료가 없으면 학습 모델을 자동 생성하지 않습니다.

축구·야구 사용법과 정확한 입력 계약은 [다종목 분석 가이드](docs/multisport.md)를 참고하세요.

## 다른 환경에서 가져오기

Python 3.12와 시스템 OpenMP(`libgomp1`)가 있는 환경에서 실행합니다.

```bash
git clone https://github.com/ppkuk0416/sporton-data.git
cd sporton-data
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install -r requirements-deep-learning.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
.venv/bin/sporton init
.venv/bin/sporton serve --host 127.0.0.1 --port 8000
```

이 명령은 독립 SQLite 연구 환경을 시작합니다. 현재 클라우드 전용 `tools/runtime.py` 등의 경로는 `/workspace/sporton-data`를 전제로 합니다. PostgreSQL·운영 환경은 [보존 가이드](docs/persistence.md)와 Compose 설정을 참고하세요. GitHub에는 코드·테스트·설정 예시·문서를 저장하며, `var/`의 DB·원본·학습 모델·백업·비밀번호·암호화 키는 포함하지 않습니다. 기존 자료를 다른 호스트로 옮길 때는 별도로 [백업·복구 절차](docs/backup-recovery.md)를 따라야 합니다.

## 설치 / 실행

현재 PostgreSQL 실행·관리자·암호화 백업 절차는 [영구 보존 가이드](docs/persistence.md)를 사용합니다. 아래 명령은 SQLite 호환 연구 환경입니다.

Python 3.12, Linux 권장. LightGBM에는 시스템 OpenMP (`libgomp1`)가 필요합니다.

```bash
cd /workspace/sporton-data
python -m venv /workspace/.venvs/sporton
. /workspace/.venvs/sporton/bin/activate
pip install -r requirements.lock
pip install -r requirements-deep-learning.lock
pip install --no-deps --no-build-isolation -e .
sporton init
sporton serve --host 127.0.0.1 --port 8000
```

브라우저에서 실행 호스트의 포트 8000으로 접속합니다. 클라우드 온보딩 UI용 localhost 미리보기 링크는 제공하지 않습니다. `GET /health`, `GET /api/capabilities`, `GET /api/overview?league=EPL`, `GET /api/predictions/{id}`로 내부 검증 가능합니다. 기본 화면은 자료 없음이며 실제처럼 보이는 예시 데이터를 자동 삽입하지 않습니다. 실제 경기가 들어오면 한국시간 당일 경기·모델·심사·감사·수집 오류가 표시됩니다. 축구 탭은 EPL/La Liga/Bundesliga/Serie A/K리그/UEFA 리그 선택을 제공하고 MLB·KBO 탭은 각 리그 데이터만 조회합니다. KBL·배구·e스포츠는 미구현으로 표시합니다.

현재 클라우드에는 `/workspace/.venvs/sporton`에 의존성을 설치했습니다. 산출물 기본 경로는 `var/`이며 Git에서 제외됩니다. 단순 CLI의 호환 DB는 `var/sporton.db`, 현재 `tools/runtime.py`로 실행한 서비스 DB는 별도 PostgreSQL입니다. `SPORTON_HOME`으로 산출물 경로, `SPORTON_DATABASE_URL`로 DB를 선택합니다. 서로 다른 모드 실행 시 두 값을 모두 분리하십시오. `.env` 파일을 자동 로딩하지 않으므로 shell에서 export하거나 Compose의 environment를 사용합니다. 키가 필요한 향후 공급자는 환경 설정에 안전하게 등록하고 코드·URL·CLI 인자·로그에 넣지 않습니다. 현재 CSV/로컬 학습에는 키가 필요하지 않습니다.

## 수집과 입력

```bash
sporton collect-nba --seasons 2023-24 2024-25 2025-26
sporton ingest /path/to/licensed_verified_nba.csv
```

첫 명령은 실제 공식 API 원본 수집입니다. 정확한 공개·tipoff 시각이 없는 raw는 자동 학습 승격하지 않습니다. 실패는 exit 1과 DB에 투명하게 기록합니다. 정규화 CSV 입력은 [데이터 계약](docs/data-contracts.md)을 따라야 합니다. 권한·라이선스·실제 공개 시각 증거가 확인된 자료만 `verified`로 표시하십시오. 테스트 생성은 `tests/conftest.py`의 TEST/FIXTURE 전용 경로에만 있습니다.

## 학습 / 백테스트

```bash
sporton backtest
sporton train
# 리그별 별도 모델
sporton train --league EPL
sporton train --league MLB
sporton train --league KBO
sporton backtest --league EPL
# train이 출력한 id를 사용 (아래는 자리 표시자)
sporton promote MODEL_ID
```

Elo·Logistic·LightGBM을 비교하고 독립 시간순 교정·검증·테스트 지표와 artifact 해시를 저장합니다. 같은 test window를 반복해 튜닝하는 실행은 차단합니다. 초기 승격은 비교 개선이 아니라 첫 연구 Champion 등록입니다. 이후에는 같은 미래 구간에서 기존 Champion보다 개선되어야 승격됩니다. 현재 verified 사전 시점 자료는 없으므로 운영 학습 명령은 자료 부족 오류를 반환합니다. 별도 `sporton archive-research`는 직접 수집한 EPL 공개 아카이브로 실행한 연구 결과를 반환합니다.

## 경기 등록 / 분석 / 감사

공식 일정에서 확인한 `fixture.json`은 `game_id, league="NBA", season, starts_at, home_team, away_team` 필드를 갖습니다. 입력 예시 수치·배당을 실제 경기처럼 제공하지 않습니다. 모델 학습 후 미래 경기만 분석할 수 있습니다.

```bash
sporton fixture /path/to/fixture.json
# 선택 사항: 실제로 확보한 부상 상태만 등록 (Record 공통 필드 + game_id/player_id/team_id/status)
sporton injury /path/to/injury.json
# 선택 사항: 실제 시장만 등록
sporton market /path/to/market.json
sporton forecast /path/to/fixture.json --model MODEL_ID
# 시장을 사용하려면 --market-id MARKET_SNAPSHOT_ID 추가
sporton ingest /path/to/new_official_finals.csv
sporton audit
sporton daily
sporton daily --retrain
sporton rollback
# 축구 Champion만 롤백: sporton rollback --league EPL
```

종목별 분석기는 통계·규칙 기반 보고서를 생성하고, 독립 심사기는 초안 열람 전에 원시 기록의 별도 근거를 저장합니다. 현재 LLM 기반의 자연어 전문 분석가는 아닙니다. 데이터 시점 오류, 정규시간/연장 시장 불일치, 오래된 시장, 큰 반대 근거를 검증합니다. NO는 게시 차단, PASS는 품질 심사 통과입니다. WATCH/NO BET/RECOMMEND는 별도 판정이며, 공식 추천은 충분한 실제 감사 자료와 검증된 시장이 있을 때만 가능합니다. 실제 선수 부상·라인업 자동 공급자와 세부 구간 모델은 미연결로 표시합니다. 새 축구·야구는 연구용 WATCH/NO BET이며 아직 공식 추천·EV를 계산하지 않습니다.

## 자동화 / 백업

```bash
# 허가된 공급자가 정규화한 CSV를 var/inbox에 넣으면 주기적으로 입력·감사
sporton worker --inbox var/inbox --interval 3600
# 자동 후보 재훈련까지 명시적으로 켜려면 --retrain
sporton worker --inbox var/inbox --once
sporton backup /path/to/new_backup_directory
```

worker는 단일 프로세스로 실행합니다. `--retrain`을 켜도 새 결과 50개와 미래 검증 gate가 필요하며 매일 무조건 교체하지 않습니다. 공식 API 결과 자동 정규화는 시점 자료 확보 후 추가할 작업입니다. 위 `backup` 명령은 SQLite 호환 경로이며 online backup으로 WAL 일관성을 보존하고 모델·체크섬 manifest를 복사합니다. PostgreSQL에서는 `secure-backup`/`restore-test`와 별도 persistence worker를 사용합니다. 복구는 서버·worker를 멈추고 manifest 해시를 확인한 뒤 기록된 원래 DB/모델 절대 경로에 복원합니다. DB만 복원하면 모델 artifact가 누락됩니다.

`compose.yaml`은 PostgreSQL·초기화·대시보드·백업 worker와 선택 `briefing` profile을 제공합니다. 먼저 비밀 파일을 준비하고 [가이드](docs/persistence.md)를 확인하세요. 기존 개발 bind-mount DB와 Compose named volume은 별도이므로 단순 실행만으로 기존 자료가 이관되지는 않습니다. 컨테이너 경로는 `/data`; 실제 PostgreSQL 컨테이너 저장·복원은 검증했지만 전체 앱 이미지 빌드와 운영 배포는 검증하지 않았습니다.

## 테스트

```bash
pytest -q
```

테스트는 임시 DB에서 TEST/FIXTURE만 생성하며 실전 DB에 주입하지 않습니다. 누출, 사후 픽, period/OT, 불변성, 중복, 시장 정확성, 공급자 실패, 별도 심사, NO 게시 차단, 성능 악화 승격 차단, 모델 체크섬, 시계열·walk-forward, 결과 감사, inbox 반복, 백업, HTTP 응답을 검증합니다.

## 프로젝트 구조 / 문서

```text
sporton/
  contracts.py    # 입력 스키마·시각·period·시장 계약
  storage.py      # append-only entity store / SHA256
  data.py         # CSV adapter / official NBA raw transport
  features.py    # NBA point-in-time feature snapshots
  models.py      # Elo / Logistic / LightGBM / calibration / registry
  review.py      # blind evidence / independent rule reviewer
  engine.py      # analyst / publish / audit / drift / learning gate
  sports/        # football/baseball contracts, features, models, analysts, lifecycle
  maintenance.py # inbox worker / consistent backup
  cli.py         # execution commands
  api.py         # read-only HTTP API
  dashboard.html # Korean local dashboard
 tests/          # TEST/FIXTURE integrity & integration tests
 docs/           # architecture / data contracts / evaluation / roadmap
```

- [기술 설계·인터페이스](docs/architecture.md)
- [데이터 계약·현재 확보 범위](docs/data-contracts.md)
- [모델 검증·승격·한계](docs/model-evaluation.md)
- [구현 상태·다음 우선순위](docs/roadmap.md)

최우선 후속 작업은 실제 세 시즌의 사용 권한·시점 증거가 있는 데이터 확보와 시즌 단위 외부표본 검증입니다. KBL·배구·e스포츠, 생성형 LLM 분석·심사, 선수 단위 고급 피처·세부 구간 모델은 아직 구현되지 않았습니다. 축구·야구 전용 통계 분석기는 구현됐지만 실제 공급자 연결과 실전 성능 검증은 미완료입니다.

## 데일리 브리핑

대시보드 상단에서 매일 **06:30·17:00 (한국시간)** 브리핑을 확인합니다. 지난 경기의 사전 예측 검증, 앞으로 24시간 경기, 이전 브리핑 후 변화, 데이터 축적·모델·오류 상태와 등록 주제를 보관합니다.

```bash
sporton briefing-daemon --inbox var/inbox --retrain
# 별도 실행: 조회 화면
sporton serve
```

정시 생성과 매시간 데이터 입력/감사는 daemon이 실행 중일 때 동작합니다. 같은 DB의 기존 worker와 동시에 실행하지 않습니다. 상시 호스트와 실제 데이터 공급자 연결이 필요하며 이 채팅으로 자동 메시지를 보내지 않습니다. [브리핑 운영·보관·제약](docs/briefings.md)을 참고하세요.

## 직접 수집 · 딥러닝 실행

`sporton collect`로 공개 라이선스를 확인한 원본을 직접 수집하고, `sporton archive-research`로 EPL 3시즌의 고정 비교 실험을 실행/조회합니다. 운영용 모델 학습·백테스트에 MLP 후보를 포함하려면 `--deep-learning`을 지정합니다. 실제 실험에서 딥러닝의 최종 테스트 개선은 확인되지 않아 운영 승격하지 않았습니다. [출처·403 원인·딥러닝·실측 결과](docs/collection-and-deep-learning.md)를 참고하세요.

## PostgreSQL · 원본 보존 · 복원 검증 (0.3)

별도 PostgreSQL 개발 DB, 종목별 typed schema, 이유/변경자/증거를 가진 정정 버전, PRE_GAME/LIVE/POST_GAME/AUDIT/RESEARCH 보고서, 실제 원본이 있는 기록만 집계하는 성능 조회, Parquet/DuckDB, 관리자 `/admin`을 추가했습니다. `secure-backup`은 AES-256-GCM 암호화까지 수행하고, 별도 `restore-test`가 실제 복원·내용·스키마·파일 참조를 대조해야 검증 완료가 됩니다.

[개발 시작·DB·정정·관리자 안내](docs/persistence.md) · [백업/복구·RPO/RTO·운영 한계](docs/backup-recovery.md)

현재 PostgreSQL·백업은 로컬 개발 호스트에서 검증했습니다. 외부 운영 DB/독립 원격 백업과 PITR은 아직 검증되지 않았습니다. 기존 SQLite는 보존돼 있으며 단순 `sporton serve`는 개발 호환 경로입니다. PostgreSQL로 실행하려면 문서의 `tools/runtime.py` 또는 secret URL file을 사용합니다. 라이브 NBA 분석 완성을 의미하지 않습니다.

## 매일 NBA Telegram 자동 실행

[자동 수집·보고서·예약·복구 운영 절차](docs/automatic-nba.md).
현재 환경용 시작은 `.venv/bin/python tools/start_nba_dispatch.py`, 채팅과 독립적인
예약 정의는 `.github/workflows/nba-daily.yaml`이다. GitHub에 토큰·수신 채팅 ID 비밀을
등록하고 실제 Actions 실행을 확인해야 외부 자동 발송 활성화가 완료된다.
