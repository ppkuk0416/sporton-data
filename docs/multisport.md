# 농구·축구·야구의 독립 분석가

## 분리 수준

구분 | 농구 NBA | 축구 | 야구
---|---|---|---
분석기 | NBAAnalyst | FootballAnalyst | BaseballAnalyst
피처 | 효율·Pace·Elo·휴식 | 골·무승부율·xG/xGA·PPDA·휴식 | 득실점·선발·타선·불펜·구장·날씨
모델 | Elo/Logistic/LightGBM | Poisson goal / 3-way form logistic | starter logistic + Poisson runs / Random Forest
별도 심사 근거 | 최근 마진 | 최근 골 차이·클린시트 | 최근 실점·연장·독립 선발 context
결과 | 승패, 연장 포함 | 승·무·패, 90분 | MLB 승패, KBO 승·무·패, 연장 포함
모델 저장 | NBA별 artifact | 각 축구 리그별 artifact | MLB와 KBO 별도 artifact

공통 데이터베이스와 lifecycle을 사용하지만 종목 피처, 학습 알고리즘, 분석 설명, 심사 규칙을 분리한다. 같은 역할 프롬프트를 여러 번 부르는 구현이 아니다. **현재는 통계·규칙 기반 분석기**이며 생성형 LLM 연동은 별도 작업이다.

## 지원 리그와 실행

NBA, EPL, LA_LIGA, BUNDESLIGA, SERIE_A, K_LEAGUE, UEFA, MLB, KBO를 계약/플러그인으로 지원한다. 코드에 리그가 등록됐다는 것은 실제 데이터를 확보하거나 해당 리그 성능이 검증됐다는 뜻이 아니다. 학습·Champion·승격/rollback·대시보드가 리그 단위로 격리된다.

```bash
sporton ingest /path/to/licensed_football.csv
sporton train --league EPL
sporton backtest --league EPL
sporton ingest /path/to/licensed_baseball.csv
sporton train --league MLB
sporton train --league KBO
# 출력된 model ID로 미래 fixture 분석
sporton forecast /path/to/fixture.json --model MODEL_ID
sporton audit
sporton daily --league EPL
sporton daily --league MLB --retrain
sporton rollback --league EPL
```

`forecast`는 fixture의 league로 분석기를 선택하며 다른 리그 모델을 주면 오류를 반환한다. 데이터가 없으면 명확히 자료 부족 오류가 난다. 테스트하려면 TEST/FIXTURE 전용 임시 DB를 사용하는 `pytest tests/test_multisport.py -q`를 실행한다. 실제 DB에 샘플 경기를 자동 생성하지 않는다.

웹: `GET /api/capabilities`는 분석기·피처 버전·후보 모델·결과 클래스·범위·자료 수를 반환한다. `GET /api/overview?league=EPL|MLB|KBO`는 선택 리그만 반환한다. UI 축구 탭에서 리그를 변경할 수 있다. KBL·배구·e스포츠는 아직 미구현이다.

## 공통 CSV 필드

`source, source_id, collected_at, published_at, data_version, quality, game_id, league, season, starts_at, ends_at, home_team, away_team, home_score, away_score, home_periods, away_periods`.

메타데이터 계약과 TEST/FIXTURE 격리는 [data-contracts.md](data-contracts.md)와 같다. 경기 ID는 공급자·리그를 포함하는 전역 고유 ID여야 한다. 동일한 팀 이름이나 시즌 문자열이 있어도 학습과 팀/시즌 메타데이터는 리그로 분리된다. 각 종목 CSV는 자기 필드만 포함하고 없는 수치는 빈 셀로 남긴다. 숫자 예시를 실제 기록으로 제공하지 않는다.

### 축구

- `league`: EPL / LA_LIGA / BUNDESLIGA / SERIE_A / K_LEAGUE / UEFA.
- `scope=regulation` (기본값). 득점은 90분+추가시간만, 연장전·승부차기 제외.
- `home_periods`, `away_periods`: 전반/후반 두 정수의 JSON 배열. 합이 해당 팀 90분 득점과 같아야 한다.
- 선택 피처: `home_xg, away_xg, home_ppda, away_ppda`. 같은 경기 결과의 xG는 그 경기 예측에 사용하지 않고 공개 후 이후 경기에만 사용한다.
- 모델은 최근 8경기의 득실점·무승부율·선택 xG/PPDA를 사용한다. 결측 수치의 인코딩 0은 실제 관측 0이 아니며 advanced_known 플래그로 구분한다.
- 전술 포메이션·부상·선발·세트피스 공급자는 미연결이다. 분석가는 해당 정보를 알고 있다고 설명하지 않는다.

### 야구

- `league=MLB|KBO`, `scope=including_extra_innings` (기본값), `regulation_innings` 기본 9, `rules_version` 필수.
- periods는 이닝별 득점 배열. 양 팀 길이와 합을 검사한다. 홈 마지막 공격 미실시는 마지막 값 null로 표현하고 홈 승리일 때만 허용한다.
- 연장 이닝 시작 직전까지 누적 득점이 동점이어야 한다. MLB 최종 무승부는 거절한다. KBO 무승부는 별도 결과로 학습한다.
- `regulation_innings=5..9`로 명시된 단축경기를 입력할 수 있다. 실제 단축·승부치기·최대 연장 규정은 `rules_version`과 공식 공급자 확인이 필요하며 코드가 모든 시즌 규정을 자동 검증하는 것은 아니다.

사전 투수/타선/불펜 정보는 경기 최종 통계와 분리해 JSON으로 입력한다.

```bash
sporton fixture /path/to/mlb_fixture.json
sporton context /path/to/pregame_context.json
```

`BaseballContext` 필드: Record 공통 메타데이터 + `game_id, league, team_id`, 선택 `starter_id, starter_fip, starter_k_bb, starter_last_pitch_count, starter_rest_days, lineup_wrc_plus, bullpen_pitches_3d, park_factor, temperature_c, lineup_confirmed`.

K-BB%는 0..1의 비율 단위다. 팀은 fixture의 홈/원정 중 하나여야 한다. 같은 경기·팀의 여러 버전을 append-only로 보존하며 공개·수집 시각 모두 cutoff 이전인 최신 context만 사용한다. 모델의 각 context 수치는 개별 known flag가 있다. 없으면 실제 수치를 생성하지 않고 결측으로 인코딩한다. 일정 밀도 변수 `bullpen_proxy_3d`는 경기 수이며 실제 불펜 투구 수가 아니다. 자동 선발·로스터·날씨 공급자는 미연결이다.

## 시장과 게시

NBA는 including_overtime, 축구는 regulation, 야구는 including_extra_innings의 scope를 요구한다. 무승부가 있는 축구/KBO 결과 시장은 `market_type=three_way`와 `selection=home|draw|away`를 명시한다. MLB는 moneyline을 사용한다. 2-way 시장의 무승부 정산 규정을 임의로 추정하지 않는다.

모든 분석은 독립 규칙 심사의 PASS가 있어야 게시된다. 축구·야구는 라이브 가격/교정 정책 미검증으로 WATCH/NO BET만 반환하며 EV·공식 추천은 생성하지 않는다. 세부 구간, 5이닝, 전후반, 시장선별 확률 분포는 미구현이다.

## 실제 데이터와 성능

현재 축구·야구 실제 자료는 0경기이며 확보된 실전 정확도도 없다. CSV 어댑터·학습·백테스트·심사·감사 경로를 TEST/FIXTURE로 검증한다. 공급자 연결·라이선스·시점 증거·실제 외부표본 검증이 다음 단계다. 선택 후보가 늘거나 데이터가 누적됐다는 이유만으로 성능이 좋아졌다고 주장하지 않는다.
