> 실제 저장된 2026-11-13 분석 스냅샷의 화면 비교용 재렌더링입니다. 새 추천을 발행하거나 과거 사전 추천 원본을 변경한 것이 아닙니다. 새 형식의 TOP은 동일 스냅샷을 사용한 미발행 예시이며 성적에 합산하지 않습니다.

# SPORTON NBA 분석 · 2026-11-13 (금요일)
## 1. 운영 상태
예정: 2026-11-13T06:30:00+09:00 / 실제 생성: 2026-11-13T07:05:38.890132+09:00
실행 구분: workflow_dispatch / 전체 4경기, 시작 전 4, 사전 수치 생성 4/4
최근 전력 이력 8/8팀; 효율 기록 8/8팀; 일정 대조: 공식 실시간 0경기 / 공식 캐시 0경기 / 실제 충돌 0경기
분석 상태: COMPLETED_PARTIAL · 연구 기준선과 검증 모델을 구분함

## 2. 전일 감사
현재 DB: 보고서 3개 / 브리핑 3개 / 이전 날짜 보고서 3개
사전 시각·원본 해시·경기 ID가 있는 연구 예측만 감사. 관찰 성적과 공식 가격픽을 분리함. 미등록 과거 채팅은 성적에 합산하지 않음.
관찰 감사: {'n': 0, 'direction_wins': 0, 'direction_n': 0, 'accuracy': None, 'team_mae': None, 'margin_mae': None, 'total_mae': None}
공식 가격픽 0개; ROI None; 확률 교정·구간 성적은 원본이 없으면 미산출.
큰 마진 오차 3건: []

## 3. 오류와 절차 개선
종료 후 사라지는 summary 최근 5경기를 팀 시즌 일정으로 복구. 최근 5/10/시즌 및 홈·원정을 분리. 공급원 장애와 일정 충돌을 구분.
관측 기록의 공개 시각이 없어 자동 학습 승격하지 않음. 학습 가중치 임의 변경 없음. 개선 효과는 외부표본 검증 전까지 미입증.

## 4. 전 경기 분석
### 09:00 Toronto Raptors @ Philadelphia 76ers
장소: Xfinity Mobile Arena / 시작 전 / 일정: SOURCE_UNAVAILABLE
분석: PARTIAL_ANALYSIS / 시장 판단: WATCH / 모델: efficiency-possession-baseline-v1
시장: PHI -7.5, U/O 223.5 · Draft Kings · 조회 2026-11-12T22:05:32.473750+00:00 (원 공개 시각 미확인)
조회 배당(십진): moneyline_away 3.21, moneyline_home 1.3704, pointSpread_away 1.8475, pointSpread_home 1.9709, total_over 1.8929, total_under 1.9259
예상 시나리오(원정–홈): 112.6–113.2; 홈 마진 +0.6; 총점 225.8; 방향 Philadelphia 76ers
핸디 비교: 홈 -7.5, 시나리오 잔차 -6.9 → 원정 방향
총점 비교: OVER (차이 +2.3)
원정 최근 5경기 2승 / 득 110.0, 실 113.8; 최근10 10경기 득 112.4/실 113.9; 시즌 11경기 득 114.5/실 111.9; 홈 5경기 득 114.6/실 105.6; 원정 6경기 득 114.3/실 117.2
파생 효율(9경기): ORtg 111.7, DRtg 113.2, Net -1.6, Pace 102.7, eFG 0.524, TS 0.556, 3PAr 0.387, FTr 0.284, TOV 0.126; 공식 포제션·추적 수치 아님
원정 직전 시작 간격 24.0시간 (연속 경기)
홈 최근 5경기 3승 / 득 112.2, 실 115.2; 최근10 10경기 득 110.2/실 110.4; 시즌 12경기 득 110.2/실 109.2; 홈 6경기 득 109.8/실 108.5; 원정 6경기 득 110.5/실 109.8
파생 효율(10경기): ORtg 109.5, DRtg 109.8, Net -0.3, Pace 100.7, eFG 0.514, TS 0.557, 3PAr 0.393, FTr 0.293, TOV 0.114; 공식 포제션·추적 수치 아님
홈 직전 시작 간격 47.0시간
선수 변수(ESPN 표기): PHI LeBron James Out; PHI Dominick Barlow Out; PHI Adem Bona Out; PHI Joel Embiid Day-To-Day; PHI Ariel Hukporti Out; TOR Trayce Jackson-Davis Out; TOR Scottie Barnes Out; TOR Kawhi Leonard Out; TOR Nate Bittle Out
주요 반대 근거: 출전 불확실: Joel Embiid / away 연속 경기의 출전시간/피로 보정 미완료 / Market publication time unavailable; no official price eligibility.
https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event=401909976

### 09:00 Detroit Pistons @ Washington Wizards
장소: Capital One Arena / 시작 전 / 일정: SOURCE_UNAVAILABLE
분석: PARTIAL_ANALYSIS / 시장 판단: WATCH / 모델: efficiency-possession-baseline-v1
시장: DET -10.5, U/O 221.5 · Draft Kings · 조회 2026-11-12T22:05:32.467125+00:00 (원 공개 시각 미확인)
조회 배당(십진): moneyline_away 1.2283, moneyline_home 4.41, pointSpread_away 1.9709, pointSpread_home 1.8547, total_over 1.9346, total_under 1.885
예상 시나리오(원정–홈): 112.8–105.3; 홈 마진 -7.5; 총점 218.1; 방향 Detroit Pistons
핸디 비교: 홈 +10.5, 시나리오 잔차 +3.0 → 홈 방향
총점 비교: UNDER (차이 -3.4)
원정 최근 5경기 3승 / 득 115.6, 실 105.8; 최근10 10경기 득 112.7/실 104.6; 시즌 10경기 득 112.7/실 104.6; 홈 5경기 득 112.4/실 109.2; 원정 5경기 득 113.0/실 100.0
파생 효율(10경기): ORtg 111.2, DRtg 103.3, Net +7.9, Pace 101.3, eFG 0.546, TS 0.582, 3PAr 0.431, FTr 0.275, TOV 0.130; 공식 포제션·추적 수치 아님
원정 직전 시작 간격 72.0시간
홈 최근 5경기 0승 / 득 104.8, 실 121.8; 최근10 10경기 득 106.0/실 113.3; 시즌 12경기 득 107.5/실 113.4; 홈 4경기 득 107.5/실 106.8; 원정 8경기 득 107.5/실 116.8
파생 효율(10경기): ORtg 105.1, DRtg 112.2, Net -7.1, Pace 100.8, eFG 0.513, TS 0.553, 3PAr 0.370, FTr 0.262, TOV 0.129; 공식 포제션·추적 수치 아님
홈 직전 시작 간격 24.0시간 (연속 경기)
선수 변수(ESPN 표기): WSH Tristan Vukcevic Out; WSH Reece Beekman Out; WSH Justin Champagnie Out; WSH Jamir Watkins Out; DET Ugonna Onyenso Day-To-Day; DET Isaiah Joe Out; DET Jalen Duren Out
주요 반대 근거: 출전 불확실: Ugonna Onyenso / home 연속 경기의 출전시간/피로 보정 미완료 / Market publication time unavailable; no official price eligibility.
https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event=401909977

### 10:00 Dallas Mavericks @ Minnesota Timberwolves
장소: Target Center / 시작 전 / 일정: SOURCE_UNAVAILABLE
분석: PARTIAL_ANALYSIS / 시장 판단: WATCH / 모델: efficiency-possession-baseline-v1
시장: MIN -5.5, U/O 225.5 · Draft Kings · 조회 2026-11-12T22:05:32.434044+00:00 (원 공개 시각 미확인)
조회 배당(십진): moneyline_away 2.85, moneyline_home 1.4484, pointSpread_away 1.9434, pointSpread_home 1.8772, total_over 1.8929, total_under 1.9259
예상 시나리오(원정–홈): 111.2–112.7; 홈 마진 +1.5; 총점 223.9; 방향 Minnesota Timberwolves
핸디 비교: 홈 -5.5, 시나리오 잔차 -4.0 → 원정 방향
총점 비교: UNDER (차이 -1.6)
원정 최근 5경기 3승 / 득 115.6, 실 108.4; 최근10 10경기 득 115.4/실 108.5; 시즌 11경기 득 114.9/실 110.3; 홈 7경기 득 115.6/실 108.4; 원정 4경기 득 113.8/실 113.5
파생 효율(10경기): ORtg 112.8, DRtg 106.0, Net +6.8, Pace 102.3, eFG 0.547, TS 0.585, 3PAr 0.403, FTr 0.270, TOV 0.138; 공식 포제션·추적 수치 아님
원정 직전 시작 간격 72.0시간
홈 최근 5경기 4승 / 득 114.0, 실 107.4; 최근10 10경기 득 116.8/실 107.1; 시즌 11경기 득 118.1/실 107.0; 홈 5경기 득 117.0/실 102.8; 원정 6경기 득 119.0/실 110.5
파생 효율(10경기): ORtg 113.3, DRtg 103.7, Net +9.7, Pace 103.2, eFG 0.553, TS 0.578, 3PAr 0.420, FTr 0.288, TOV 0.136; 공식 포제션·추적 수치 아님
홈 직전 시작 간격 97.0시간
선수 변수(ESPN 표기): MIN Anthony Edwards Out; MIN Donte DiVincenzo Out; DAL Dereck Lively II Out; DAL Tarik Biberovic Out; DAL Sergio de Larrea Out; DAL Morez Johnson Jr. Day-To-Day
주요 반대 근거: 출전 불확실: Morez Johnson Jr. / Market publication time unavailable; no official price eligibility.
https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event=401909978

### 11:00 Charlotte Hornets @ Phoenix Suns
장소: Mortgage Matchup Center / 시작 전 / 일정: SOURCE_UNAVAILABLE
분석: PARTIAL_ANALYSIS / 시장 판단: WATCH / 모델: efficiency-possession-baseline-v1
시장: PHX -6.5, U/O 214.5 · Draft Kings · 조회 2026-11-12T22:05:32.481235+00:00 (원 공개 시각 미확인)
조회 배당(십진): moneyline_away 2.9, moneyline_home 1.4367, pointSpread_away 1.8929, pointSpread_home 1.9259, total_over 1.9259, total_under 1.8929
예상 시나리오(원정–홈): 107.9–111.8; 홈 마진 +3.9; 총점 219.7; 방향 Phoenix Suns
핸디 비교: 홈 -6.5, 시나리오 잔차 -2.6 → 원정 방향
총점 비교: OVER (차이 +5.2)
원정 최근 5경기 1승 / 득 100.6, 실 115.6; 최근10 10경기 득 97.7/실 111.1; 시즌 11경기 득 98.8/실 110.4; 홈 7경기 득 100.7/실 115.0; 원정 4경기 득 95.5/실 102.2
파생 효율(10경기): ORtg 99.7, DRtg 113.2, Net -13.5, Pace 98.1, eFG 0.478, TS 0.518, 3PAr 0.512, FTr 0.224, TOV 0.167; 공식 포제션·추적 수치 아님
원정 직전 시작 간격 74.0시간
홈 최근 5경기 3승 / 득 113.4, 실 113.4; 최근10 10경기 득 112.8/실 118.1; 시즌 10경기 득 112.8/실 118.1; 홈 7경기 득 116.9/실 116.6; 원정 3경기 득 103.3/실 121.7
파생 효율(10경기): ORtg 110.9, DRtg 116.5, Net -5.5, Pace 101.5, eFG 0.550, TS 0.581, 3PAr 0.412, FTr 0.233, TOV 0.130; 공식 포제션·추적 수치 아님
홈 직전 시작 간격 48.0시간
선수 변수(ESPN 표기): PHX Mark Williams Out; CHA Brandon Miller Out; CHA Kon Knueppel Out; CHA Dennis Schroder Out
주요 반대 근거: Market publication time unavailable; no official price eligibility.
https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event=401909979

### 회고 연구 모델 비교
3시즌 3690경기, 3609샘플. 시간순 학습·교정·검증 분리, 실전 검증/자동 승격 아님.
recent_average: 외부 구간 635경기, 팀 득점 MAE 9.74, 마진 MAE 12.42, 총점 MAE 15.07, Brier 0.221
elo: 외부 구간 635경기, 팀 득점 MAE 9.58, 마진 MAE 11.74, 총점 MAE 15.12, Brier 0.212
logistic: 외부 구간 635경기, 팀 득점 MAE 9.58, 마진 MAE 11.74, 총점 MAE 15.12, Brier 0.210
lightgbm: 외부 구간 635경기, 팀 득점 MAE 9.83, 마진 MAE 12.13, 총점 MAE 15.49, Brier 0.214
pytorch_mlp: 외부 구간 635경기, 팀 득점 MAE 9.71, 마진 MAE 11.81, 총점 MAE 15.53, Brier 0.210
공개 아카이브의 최초 정보 공개 시각 미확보. 연구 개선을 실전 성능 개선으로 인정하지 않음. 현재 학습 게이트: BLOCKED
## 5. 독립 심사
코드 기반 독립 원자료/시각/수치 검사를 실행. 전문가 LLM 심사는 미연결이며 AI 검토 완료로 표시하지 않음.
Toronto Raptors @ Philadelphia 76ers: PASS — Identity/time/numeric checks passed for disclosed PARTIAL research scope.
Detroit Pistons @ Washington Wizards: PASS — Identity/time/numeric checks passed for disclosed PARTIAL research scope.
Dallas Mavericks @ Minnesota Timberwolves: PASS — Identity/time/numeric checks passed for disclosed PARTIAL research scope.
Charlotte Hornets @ Phoenix Suns: PASS — Identity/time/numeric checks passed for disclosed PARTIAL research scope.

## 6. TOP 관찰 후보
공식 가격픽은 별도 자격 미충족으로 0개. 아래는 검증되지 않은 시나리오와 시장선 차이가 2점을 초과한 WATCH이며 수익 우위가 아니다.
1. Toronto Raptors @ Philadelphia 76ers: away spread, 홈 기준 -7.5, 시나리오 차이 6.9점 / WATCH / 조회 2026-11-12T22:05:32.473750+00:00
2. Dallas Mavericks @ Minnesota Timberwolves: away spread, 홈 기준 -5.5, 시나리오 차이 4.0점 / WATCH / 조회 2026-11-12T22:05:32.434044+00:00
3. Detroit Pistons @ Washington Wizards: home spread, 홈 기준 +10.5, 시나리오 차이 3.0점 / WATCH / 조회 2026-11-12T22:05:32.467125+00:00

## 7. DB·다음 감사
원본 저장/예측 ID/심사 기록은 보고서 저장 트랜잭션과 연결된다. 시작 후 기록은 사전 성적에서 제외.
공식 포제션·Usage·On/Off·확정 선발·선수별 이동/출전시간 및 구간 모델이 미확보이므로 FULL_ANALYSIS로 표시하지 않음.
점수는 포제션 기반 파생 효율 Baseline(양 팀 3경기 이상) 또는 최근 득실점 비교 Baseline을 명시한다. 공식 추적 데이터와 구분한다. 모든 점수 시나리오는 비교용 Baseline이다. 검증된 Champion이 있을 때만 기존 정량 엔진을 사용한다.
수집 이슈: SOURCE_UNAVAILABLE: stats.nba.com; SOURCE_UNAVAILABLE: cdn.nba.com; SOURCE_UNAVAILABLE: Toronto Raptors @ Philadelphia 76ers; SOURCE_UNAVAILABLE: Detroit Pistons @ Washington Wizards; SOURCE_UNAVAILABLE: Dallas Mavericks @ Minnesota Timberwolves; SOURCE_UNAVAILABLE: Charlotte Hornets @ Phoenix Suns

보고서 실행 ID: 98733b4f-902e-401b-a296-5f0de0806c9c / 원본 ID: nba-daily-v2-2026-11-13-manual-43316374354 / 저장 시 검증할 SHA256: 8fbc796071d70c2131e4dcfe1bb6f7a924e3162494b1ecbd538a13a8632b4c19