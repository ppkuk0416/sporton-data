# 영구 보존 기반 · PostgreSQL / Parquet / DuckDB

## 현재 구현 범위와 보장 경계

PostgreSQL을 애플리케이션과 별도 프로세스로 실행하고, 데이터 디렉터리·원본·모델·암호화 백업을 보존한다. SQLite는 기존 개발 자료와 호환 입력으로 유지하며 삭제하거나 초기화하지 않았다. 실제 SQLite 기록을 ID·생성 시각·payload·SHA256 그대로 PostgreSQL에 이관해 대조했다.

**현재 검증 환경은 이 클라우드 호스트의 개발용 PostgreSQL이다.** 외부 관리형 운영 DB, 다른 호스트의 백업 저장소, 키 보관 서비스는 아직 연결되지 않았다. 컨테이너/프로그램 재시작 보존과 격리 복원은 검증했지만, 호스트 디스크 전체 유실·환경 삭제까지 생존한다는 보장은 하지 않는다. 운영 전 외부 DB·독립 백업 마운트·별도 키 보관이 필요하다.

## 저장 구조

- 기존 33개 원본 이벤트 테이블과 신규 정정·출처·관측·보고서·선택·감사·백업·복원·lake 이벤트를 포함해 **46개 append-only 이벤트 테이블**.
- `report_index`: 보고서 유형·경기·원본 예측·모델·심사 FK, 시각·모드·원본 해시.
- `observation_index`: 제공처/URL/수집·공개·유효 시각, 리그/경기/엔티티, 검증 모듈·상태.
- `sport_basketball_statistics`, `sport_baseball_statistics`, `sport_football_statistics`, `sport_volleyball_statistics`, `sport_esports_statistics`: 각각 독립 열과 공통 provenance FK.
- 합계 53개 테이블. 세부 엔티티는 `schema.py` / `storage.py`에 정의한다.

운영 조회·추가는 `sporton_app` 역할, 스키마 초기화·백업·복원은 별도 owner/관리 작업으로 분리한다. 앱 역할에는 SELECT/INSERT만 주며 CREATE/UPDATE/DELETE/TRUNCATE를 부여하지 않는다. DB 트리거는 owner가 일반 SQL로 원본 UPDATE/DELETE/TRUNCATE하는 것도 차단한다. 최상위 DB 관리자가 트리거를 해제하는 행위까지 막는 WORM/하드웨어 보장은 아니다.

## 개발 시작

```bash
cd /workspace/sporton-data
bash tools/install.sh
/workspace/.venvs/sporton/bin/python tools/prepare_persistence.py
/workspace/.venvs/sporton/bin/python tools/start_postgres.py
/workspace/.venvs/sporton/bin/python tools/postgres_init.py
# 기존 SQLite는 그대로 두고 이관·검증·실제 archive export
/workspace/.venvs/sporton/bin/python tools/upgrade_storage.py
# 제한된 앱 역할로 실행
/workspace/.venvs/sporton/bin/python tools/runtime.py sporton serve
# 백업/실제 복원 전용 작업은 owner 역할로 별도 실행
/workspace/.venvs/sporton/bin/python tools/runtime.py --owner sporton persistence-worker
```

`prepare_persistence.py`는 무시 경로 `var/persistence-dev`에 개발용 암호·AES 키·관리 토큰을 최초 한 번 생성하고 기존 값을 보존한다. 값을 로그나 Git에 저장하지 않는다. 이 파일들을 운영 비밀키 관리의 대체재로 간주하지 않는다. PostgreSQL 이미지는 공식 이미지 digest에 고정했고 localhost 55432만 공개한다. 기존 컨테이너의 이미지·데이터 마운트가 다르면 자동 교체하지 않는다.

`SPORTON_DATABASE_URL` 또는 `SPORTON_DATABASE_URL_FILE`을 지원한다. 운영 모드 `SPORTON_ENV=production`은 PostgreSQL과 `sslmode=verify-full`을 강제한다. SQLite로 조용히 대체하지 않는다. `compose.yaml`은 PostgreSQL·초기화·앱·백업 작업을 분리하고, `compose.production.yaml`은 외부 DB URL 파일·독립 데이터/백업 마운트를 요구한다. 현재 Compose 구문은 검증했지만 전체 앱 이미지 빌드·외부 운영 배포는 검증하지 않았다.

## 정정과 시점

`record_revisions`에는 대상 테이블/ID, 원본 해시, replacement 전체, 이유·변경자·증거·기록 시각을 새 행으로 저장한다. 현재 정정 대상은 fixture와 게임 결과다. 예측·심사·공식 선택의 원본은 정정 API로도 바꿀 수 없으며 POST_GAME/AUDIT 후속 보고로 설명해야 한다.

```bash
sporton revise /path/to/correction.json
```

JSON 필드: `table, key, replacement, reason, actor, evidence`. 결과의 경기·리그·팀·시작 시각 식별자는 조용히 바꾸지 않는다. 다른 경기로 재매칭할 필요가 있으면 별도 canonical fixture가 필요하다. fixture 일정 정정은 가능하지만 새 사전 게시 게이트는 현재 정정 시각을 사용해 소급 PRE_GAME을 차단한다.

`effective_get(..., as_of)` / `games_as_of(...)`는 그 시점까지 기록된 정정만 적용한다. 학습 피처를 만들 때도 당시 버전을 선택한다. 정정 후 원본 평가를 덮어쓰지 않고 `settlement_events`와 새로운 AUDIT에 재계산 결과를 보존한다. 브리핑·드리프트·선택 게이트·성능 조회는 최신 정정 평가를 사용하고 원본 평가도 추적 가능하다.

## 출처·품질·종목별 스키마

`sporton observe file.json`은 `Observation` 계약을 검증한다. source/provider, HTTPS URL, source_record_id, data_version, collected_at, nullable published_at, effective_at, league, season, entity_id, field/value, optional fixture/raw SHA가 필요하다. 공개 시각이 없으면 만들지 않는다.

- `MISSING`: 값 없음.
- `UNVERIFIED`: 공개 시각 또는 실제 원본 증거 부족.
- `CONFLICTED`: 동일 리그/엔티티/필드/유효 시각에 상충 값. 어느 쪽도 조용히 우선하지 않음.
- `VERIFIED`: 코드의 source/clock/hash 검사가 통과. 이 표시는 원본 제공처의 주장 자체가 객관적 진실임을 보증하지 않음.
- `TEST/FIXTURE`: 테스트 전용.

현재 원본 hash/URL 증거 확인은 수집된 archive metadata와 연결한다. URL 존재만으로 semantic truth를 인증하지 않는다. 완전한 공급자별 필드 검증·공식 교차 확정·리그별 정정 승인 워크플로는 후속 작업이다. `register_alias`는 리그·제공처별 명시적 canonical mapping과 증거를 저장하고 충돌을 거절한다. 자동 퍼지 팀명 매칭을 하지 않는다.

`sporton statistics file.json`은 `sport, observation_payload, measurements`를 받는다. 농구 효율/비율/포제션·Usage/OnOff·라인업/period, 야구 FIP/xFIP/SIERA/K-BB/xwOBA/wRC+/구종/이닝/불펜/구장, 축구 xG/PPDA/포메이션/위치/전환/시간대, 배구 서브/리시브/블록/세트/로테이션, e스포츠 패치/맵/진영/로스터/오브젝트/BO 스키마가 분리되어 있다. 농구 NetRtg 산식·비율 범위·종목/리그 혼합을 검사하고 e스포츠에는 게임/패치가 필요하다. **스키마가 있다는 이유로 해당 자료를 확보했거나 전문 예측 모델을 완성했다고 주장하지 않는다.**

## 보고서와 선택 분리

보고서 종류: PRE_GAME / LIVE / POST_GAME / AUDIT / RESEARCH. `register_report`를 사용한다. PRE_GAME은 원본 예측·일치하는 피처·모델·독립 사전 근거·matching PASS가 필요하다. PostgreSQL도 서버 현재 시각·정정된 시작 시각·원본 해시·모델/리그·근거/심사/피처 일치를 검사한다. 보고서와 공식 선택은 같은 트랜잭션으로 확정한다. LIVE/POST_GAME은 해당 경기 단계가 확인돼야 한다.

선택 분류는 `OFFICIAL_PRICED`, `DIRECTION_ONLY`, `CONDITIONAL_WATCH`, `PASS`이며 심사 PASS/NO와 다르다. 공식 가격 선택은 실제 시장·확률 교정·실전 표본/드리프트 기준에 더해 양 팀의 확인된 lineup/availability evidence가 필요하다. 원본 PRE_GAME을 찾을 수 없는 수입 기록은 성능에서 제외하고 excluded 목록에 보존한다. 새 세부 구간 모델/배당이 없는 항목을 예측했다고 채워 넣지 않는다.

## Parquet와 DuckDB

`sporton lake-export --dataset DATASET_ID`는 검증한 원본 bytes에서 Parquet를 만들고 source SHA·행 수·등급·출처를 유지한다. `sporton lake-export --entity professional_reports`는 ID·원본 JSON·시각·해시를 포함한 이벤트 스냅샷을 만든다. `sporton lake-summary`는 checksum을 확인한 뒤 DuckDB의 고정 집계로 조회한다. 과거 버전은 보존하고 기본 조회는 출처별 최신 export만 사용한다.

실제 NBA 역사 59,008 + EPL 3시즌 1,140 = **60,148행**을 Parquet로 변환·DuckDB에서 대조했다. 공개 시각이 없어 모두 date_only_archive이며 verified 사전 데이터로 승격하지 않았다. 실제 NBA 당일 일정/부상/시장과 운영 모델은 아직 확보·검증되지 않았다.

## 관리자 화면

`/admin`과 `/api/admin/*`는 별도 관리 경로다. HTML은 토큰 입력 폼만 제공하고 데이터 API는 Bearer 토큰을 요구한다. `SPORTON_ADMIN_TOKEN_FILE`로 별도 비공개 파일을 지정한다. 토큰은 URL/브라우저 저장소에 넣지 않는다. 운영에서는 HTTPS·망/역할 분리 및 인증 서비스 연결이 필요하다.

데이터 건수, 누락/충돌·정정, 보고서 타입, PASS/NO, 모델·학습, 원본이 검증된 성능 그룹, 수집 오류, 백업·실제 restore drill·RPO, WAL 통계를 확인한다. 보고서 원본을 열면 원본 prediction/feature/model/market/review/result, 정정 결과와 평가·가설을 추적할 수 있다. 지원하지 않는 세부 구간 성능은 null이다.
