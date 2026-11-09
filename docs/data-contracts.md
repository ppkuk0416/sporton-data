# 데이터 계약

## 정규화 CSV v1

UTF-8 헤더:

```text
source,source_id,collected_at,published_at,data_version,quality,game_id,league,season,starts_at,ends_at,home_team,away_team,home_score,away_score,home_periods,away_periods,home_possessions,away_possessions
```

- `league`: 이 절의 NBA 스키마는 NBA만 허용. 축구·야구 스키마는 [다종목 계약](multisport.md)을 참조. 모든 팀·경기 ID는 공급자 간 안정적인 정규화 ID여야 한다.
- 모든 시각: timezone이 있는 ISO8601, 내부 UTC 정규화. `starts_at < ends_at <= published_at <= collected_at`. 실제 데이터의 수집 시각은 미래일 수 없다.
- `published_at`: 그 버전의 최종 결과가 실제 공개된 시각을 입증하는 공급자 메타데이터. 오늘 내려받은 과거 기록의 공개 시각을 임의로 경기 종료 시각으로 만들지 않는다. 수정된 데이터는 수정 공개 시각을 사용한다.
- 품질 `verified`는 운영자가 출처와 라이선스·시점 증거를 검증한 입력이다. 자동적인 출처 인증을 의미하지 않는다. 근거가 없으면 `unverified`로 저장하고 학습에서 제외한다. 공개 시각 자체가 없으면 CSV 정규화를 하지 말고 raw에 남긴다.
- 점수: 음이 아닌 정수, NBA 최종 무승부 금지. period 배열은 CSV 안에서 JSON 문자열. 첫 4개는 정규시간, 이후는 연장. 모든 기간 합이 최종 점수와 같고 각 연장 직전에는 동점이어야 한다.
- possessions: 실제로 확보된 경우만 수치, 없으면 빈 값. 결측을 실제 0으로 주장하지 않는다. 모델 인코딩은 0+결측 플래그를 사용하고 보고서에 결측을 표시한다.
- `source`, `source_id`: 공급자와 원본 URL/식별자. 토큰이나 API 키가 들어간 URL은 저장하지 않는다.
- 같은 ID·동일 payload의 재입력은 멱등. 같은 ID·다른 payload는 거절. 결과 정정은 `record_revisions`에 이유·변경자·시각·증거를 갖는 새 이벤트로 등록한다. 원본을 덮어쓰지 않으며 학습 cutoff 이전에 알려진 버전만 사용한다.
- 원본 CSV는 검증 전에 `raw_data`에 그대로 보존한다. 검증 실패는 `source_failures`에 기록한다.

## 실제 수집 범위

`collect-nba --seasons 2023-24 2024-25 2025-26`는 공식 `https://stats.nba.com/stats/leaguegamelog`를 실제 호출하는 어댑터다. 성공 시 원본 응답과 수집 시각·시즌을 보존한다. 이 응답만으로 정확한 tipoff/최종 공개 시각/쿼터 기록이 확보되지 않아 **자동으로 verified 학습 데이터로 승격하지 않는다**. 허가된 boxscore·공개 시각 증거와 결합해 위 CSV로 제공해야 한다. 응답 해시와 원본은 재현에 사용한다.

현재 환경에서 세 시즌 요청은 모두 프록시의 `403 Forbidden`으로 실패했다. 이 API를 통해 확보한 검증 경기 수는 0이다. 별도 NBA 역사/EPL 아카이브 60,148행은 공개 시각이 없는 date_only_archive로 보존한다. 네트워크 허용 변경 후에도 공급자 이용 조건 및 접근 가능성을 별도로 확인해야 한다. 접근 차단 우회나 무단 스크래핑은 구현하지 않는다. 유료 API는 미연결이다. 별도 `lake.py`가 원본 아카이브와 이벤트를 Parquet로 내보내고 DuckDB에서 검증한다.

## As-of 계약

`feature_cutoff_timestamp <= as_of_timestamp < starts_at`. 현재는 두 timestamp를 동일하게 저장한다. 해당 경기 자신의 결과는 항상 제외한다. 과거 결과의 `published_at <= cutoff`와 `ends_at < cutoff`를 둘 다 만족해야 사용한다. 팀 최근 10경기 평균, 최근 휴식, Elo, 가능한 경우 possessions·ORtg를 생성한다. 각 팀 최소 3경기 필요. Elo의 모든 선행 경기 해시와 rolling source ID를 저장한다.

역사 연구에서는 원본의 실제 공개 시각을 사용한다. 과거 당시 이 서비스가 해당 공급자를 구독·수집했다는 주장은 하지 않는다. 라이브 예측은 모델 생성 시각, 모델 선택 근거의 공개 시각, 피처 cutoff가 모두 실제 분석 시점 이전이어야 한다.

## 시장 스냅샷 JSON

`Market` Pydantic 스키마를 따른다. 공통 출처·시각·품질 필드 외에 `game_id`, `market_type` (`moneyline|three_way|spread|total`), `scope` (`including_overtime|including_extra_innings|regulation`), `selection` (`home|away|draw|over|under`), `line`, `decimal_odds`, `bookmaker` 필수. Moneyline/three_way의 line만 null. 무승부가 있는 결과 시장은 three_way를 명시해야 한다. 홈 spread -3.5는 `home - away - 3.5 > 0`이면 적중. away spread는 원정 기준 line이다. 총점 기준점 일치는 push로 기록하고 이익 0이다.

시장은 실제 입력만 저장하며 임의 배당을 채우지 않는다. NBA MVP 예측 헤드는 연장 포함 전체 경기만 지원한다. 정규시간 시장과 비교하면 NO. 최신 시장도 cutoff보다 나중이면 NO, 15분보다 오래되면 NO. 학습된 분포가 없는 spread/total EV는 계산하지 않는다. 시장 종가 비교·세부 구간별 성적은 데이터와 모델 확보 전 null/미구현이다.

## 테스트 격리

테스트 자료는 fictitious `FIXTURE-*` 팀, `TEST-*` 경기, `quality=TEST/FIXTURE`, 전용 임시 DB를 사용한다. `--test-mode`와 분리된 `SPORTON_HOME`, `SPORTON_DATABASE_URL`을 모두 사용한다. 테스트 모델은 production Champion 승격 불가, 실제 대시보드 지표에 포함 불가. API 기본 화면은 테스트 자료를 자동 생성하지 않는다.

## 부상 상태 입력

`injury` 명령은 Record 공통 필드 외에 `game_id, player_id, team_id, status`를 검증한다. status는 out/doubtful/questionable/probable/available 중 하나다. 독립 심사 근거에는 published_at과 collected_at이 cutoff 이전인 선수별 최신 상태만 포함한다. 경기 후 갱신된 부상 정보는 과거 심사에 유입되지 않는다. 부상 영향의 정량 모델은 미구현이다.

## v0.3 관측·정정·전문 통계 계약

`governance.Observation`은 제공처·HTTPS 원본 URL·원본 ID·버전·수집/공개/유효 시각·리그/시즌·고유 엔티티·검증 모듈·원본 SHA를 보존한다. 공개 시각 누락은 UNVERIFIED, 값 없음은 MISSING, 같은 키의 상충 값은 CONFLICTED로 남긴다. 알려지지 않은 값을 추정해서 채우지 않는다. 소스 해시/URL 증거 검사는 원본 내용의 의미가 참임을 보증하지 않는다.

종목별 전문 통계는 공통 provenance와 독립 열 스키마를 갖는다. 리그/종목 혼합·비율·농구 NetRtg 산식을 검증하며 e스포츠는 게임/패치 정보가 필수다. 제공처별 명시적 canonical alias만 등록하고 충돌을 거절한다. 상세 CLI와 필드 범위는 [persistence.md](persistence.md)에 있다.

최초 예측/심사/공식 선택은 정정 대상으로 허용하지 않는다. 결과 정정은 새 settlement/AUDIT를 생성하며 최초 평가도 남긴다. 보고서 종류는 PRE_GAME/LIVE/POST_GAME/AUDIT/RESEARCH다. 사후 PRE_GAME 등록은 서비스와 PostgreSQL 서버 게이트에서 차단한다.
