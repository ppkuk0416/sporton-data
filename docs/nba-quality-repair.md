# NBA 자동 분석 품질 점검 및 수정 결과

검증 대상: KST 2026-11-11 정규리그 6경기. 기존 데이터·보고서와 새 원본을 삭제하거나 덮어쓰지 않았다.

## 실제 원인

1. 문제의 23:22 생성은 `workflow_dispatch` 수동 검증 실행이었다. GitHub 실행 43037547787의 event와 생성 로그를 확인했다. 이 기록 자체가 06:30 예약 실패의 증거는 아니다.
2. 기존 자동 경로가 `summary.lastFiveGames`만 읽었다. 경기 종료 후 이 필드가 없는 응답이 정상적으로 존재해 전 팀 기록을 누락으로 표시했다.
3. NBA 요청에 공식 사이트 클라이언트 헤더가 없었고, 실패·스키마 변경·일정 불일치를 한 상태로 합쳤다. 팀 전체 표시 이름의 정확한 문자열 비교에도 의존했다.
4. 보고서 조회는 `nba-daily-v1`과 이전 날짜만 세었고, 로컬 DB와 Actions artifact DB도 별개였다. 조회 0개가 과거 원본 전체 부재를 뜻하지 않았다.
5. 자동 발송 경로가 기존 Champion 분석·독립 심사·감사·학습 gate와 분리돼 있었다. 단순 평균 문서 생성과 전송만 성공하면 정상 완료로 표시했다.
6. 수동 검증과 예약 실행이 같은 날짜 키를 사용해 수동 전송이 본 예약을 소모할 수 있었다.

## 핵심 코드 변경

파일 | 변경
---|---
`dispatch.py` | 구조 검증·지수 재시도·신선한 캐시, 공식 우선 수집, 실행 단계/상태 기록, 원본·예측 원자 저장, 수동/예약 키 분리, 재시도·무경기 알림 억제, 새 보고서 형식
`nba_quality.py` | 별도 ESPN 어댑터, 팀 시즌 이력, 5/10/시즌·홈원정, 파생 효율, 별도 분석/추천 상태, 독립 검사, 시장 스냅샷, 연구 예측 감사와 피드백, 기존 학습/승격 gate 연결
`nba_benchmark.py` | 실제 3시즌 수집·컵 결승 제외·날짜별 누출 차단, 동일 walk-forward fold로 평균/Elo/Logistic/LightGBM/PyTorch 비교
`storage.py` | 불변 `job_executions`, `data_quality_logs`, `learning_feedback`, 원본 URL 조회 인덱스와 해시 검증 조회
`admin.py` | 인증된 관리자 조회에 작업/품질/학습 피드백 노출
`.github/workflows/nba-daily.yaml` | KST 06:30 기본 실행, 06:40·06:50·07:00 복구. 기존 상태 artifact 및 중복 방지 유지

기존 `model_predictions`·`professional_reports`·`critical_reviews`·`forecast_components`·`settlement_events` 등을 재사용한다. 같은 의미의 테이블을 무조건 중복 생성하지 않았다. 실제 report/model 기반 PRE_GAME gate는 그대로 유지한다.

## 일정·시각 검증

공식 NBA scoreboardv3와 scheduleLeagueV2 CDN에 NBA 클라이언트 헤더를 적용한 실제 요청에서 모두 HTTP 200을 확인했다. 공식 팀 코드와 ESPN 별칭 NY/NYK, UTAH/UTA, GS/GSW, SA/SAS, NO/NOP, WSH/WAS를 정규화한다. NBA game ID와 ESPN event ID의 매핑 근거를 불변 저장한다.

실제 6경기 모두 공식/ESPN의 팀·UTC 시작 시각이 일치했다.

- Milwaukee @ New York
- Boston @ Philadelphia
- LA Clippers @ Phoenix
- Miami @ Utah
- Orlando @ Sacramento
- Oklahoma City @ Portland

06:30 KST 시점에 6경기의 시작 시각이 모두 미래이고, 23:22에는 모두 지난 시각임을 회귀 검사했다. 이는 시간 판정 검사이며, 현재 수집한 사후 자료로 과거 사전 예측을 재생성했다는 뜻이 아니다. 늦은 실데이터 검증에서 사전 점수 생성은 0건이었다.

`VERIFIED`, `PARTIALLY_VERIFIED`, `SOURCE_UNAVAILABLE`, `SOURCE_CONFLICT`, `INVALID_EVENT`를 구분한다. NBA 공급원 장애에 ESPN·검증 캐시로 전환하며, 공식에 경기가 있는데 대체 공급원이 빈 결과를 주면 무경기로 조용히 처리하지 않는다.

예약과 수동 실행의 origin·예정 시각·실제 시작·수집 시작/완료·분석 완료·DB commit·전송 완료를 append-only 작업 이벤트로 기록한다. 성공한 재시도를 MISSED_SCHEDULE로 잘못 표시하지 않는다. GitHub runner 자체가 전혀 시작하지 않는 장애를 검출할 독립 외부 모니터와 정확한 06:30 SLA는 아직 없다.

## 최근 이력·효율 검증

11월 11일 12팀 모두 시즌 일정 API에서 종료된 기록을 복구했다. 팀별 최근 5/10경기와 시즌·홈/원정을 저장하며 현재/미래 경기는 제외한다. 시즌 자료는 공급원 공개 시각이 없어 `SOURCE_OBSERVED_PUBLICATION_UNKNOWN`으로 표시한다.

12/12팀에서 박스스코어 기반 파생 ORtg/DRtg/Net/Pace/eFG/TS/3PAr/FTr/OREB/DREB/TOV를 확보했다. 포제션은 양 팀 `FGA-OREB+TOV+0.44*FTA` 평균의 **추정치**이며 공식 트래킹 포제션과 구분한다. 박스스코어 점수와 별도 header 최종 점수를 대조하고, 연장 Pace는 48분 기준으로 정규화한다.

양 팀 최소 3경기 효율을 확보하면 포제션·공격/상대 수비 효율 Baseline을 사용한다. 단순 평균은 별도 비교값으로 유지한다. 검증된 Champion과 필요한 입력이 존재하면 기존 전문 엔진·심사·공식 추천 gate를 재사용한다. 없는 모델이나 공개 시각을 만들어 채우지 않는다.

## 독립 심사·분석과 추천 분리

독립 중앙값 반대 근거를 분석 전에 저장하고, 별도 모듈이 경기 식별·시각·미래 결과 혼입·숫자·마진/총점·구간 합계를 검사한다. 오류는 NO와 사유를 저장한다. 출전 불확실 선수, 연속 경기, 가격 시각 문제도 반대 근거로 저장한다.

이는 **결정적 코드 심사**다. 전문가 생성형 LLM 심사는 미연결이며 동작했다고 주장하지 않는다. 현재 효율·선수 역할·확정 라인업 전체를 검증한 상태가 아니므로 FULL_ANALYSIS를 부여하지 않는다. PARTIAL_ANALYSIS/INSUFFICIENT_DATA와 WATCH/CONDITIONAL/NO_BET를 분리한다. 공식 가격 추천 자격은 기존 gate를 유지한다.

쿼터·전반 예상은 독립 모델이 없어 생성하지 않는다. 향후 저장된 구간 예측과 실제 구간 결과가 있으면 구간 오차 채점이 가능하다. 총점을 4등분한 수치를 예측으로 꾸미지 않는다. TOP 관찰 후보는 신선한 시장과 시나리오 차이가 2점을 초과할 때만 최대 3개를 표시하며, 통계적으로 확인한 가격 우위나 공식 추천으로 취급하지 않는다.

## DB·감사·복원 실측

- 새 실데이터 수동 통합 실행: 2026-11-12 10경기 일정 10/10 일치, 20/20팀 이력·효율, 독립 규칙 PASS 10건, 불변 연구 예측 10건 저장, Telegram 문서 전송 성공. 상태는 `COMPLETED_PARTIAL`, origin은 `manual_quality_validation`이다.
- 수동 검증은 본 예약 키와 분리돼 06:30 예약을 소모하지 않는다.
- 보고서와 예측은 같은 트랜잭션으로 저장한다. DB 실패 시 저장 성공 이벤트나 전송을 발생시키지 않는다.
- 시작 전 실제 생성 시각, 원본 내용 해시, 심사 PASS, 게임 ID가 일치하는 예측만 감사한다. 관찰·수동·예약 성적을 구분하고 공식 가격픽과 혼합하지 않는다. 검증 가격이 없으면 ROI는 null이다.
- 과거 원본이 없는 픽은 복원해 성적으로 인정하지 않는다. 실제 로컬과 Actions의 DB 경로 차이를 보고하고, 원본 보고서를 버전과 무관하게 조회한다.
- 로컬 SQLite → 별도 PostgreSQL 개발 DB로 원래 ID·원본 payload·SHA256·created_at을 보존한 이관 검증 성공: 보고서 4건, 연구 예측 10건, 당시 원본 스냅샷 381건.
- 실제 자료의 AES-256-GCM 백업·격리 복원: SQLite 56테이블 성공. PostgreSQL도 **56테이블·스키마 객체 134개** 대조 후 RESTORE_VERIFIED.
- 실제 PostgreSQL에서 UPDATE/DELETE/TRUNCATE 금지, app role 권한, 사후 PRE_GAME 위조 차단, 암호화 복원을 검사했다.

원본 결과에 정확한 종료·공개 시각이 없으면 엄격한 Game 입력으로 자동 승격하지 않는다. 연구 감사는 ESPN 관측 결과이며 공식 결과 교차 확정 전에는 실전 검증 정확도로 부르지 않는다. 독립 외부 운영 PostgreSQL·원격 불변 백업·실제 PITR은 아직 미구성이다. Actions는 여전히 SQLite 상태 artifact를 사용한다.

## 실제 모델 비교

2023-24/2024-25/2025-26 정규리그 3,690경기, 사용 가능 3,609샘플. NBA 컵 **정규리그** 경기는 포함하고 정규리그 성적에 포함되지 않는 컵 결승 3개는 제외했다. UTC 같은 날짜 경기의 결과를 해당 날짜 feature에 넣지 않는다.

평균/Elo/Logistic/LightGBM/PyTorch 모두 같은 fit·독립 calibration·test 구간을 사용했다. walk-forward 두 구간을 실행했다. 마지막 외부 구간 635경기의 점수 오차:

모델 | 팀 득점 MAE | 마진 MAE | 총점 MAE
---|---:|---:|---:
최근 평균 | 9.74 | 12.42 | 15.07
Elo/선형 득점 회귀 | 9.58 | 11.74 | 15.12
LightGBM | 9.83 | 12.13 | 15.49
PyTorch MLP | 9.71 | 11.81 | 15.53

딥러닝이 더 복잡하다는 이유로 채택하지 않았다. 마진 오차 개선이 총점 개선을 뜻하지 않는다. 자료는 현재 조회한 역사 아카이브여서 최초 공개 시각을 증명하지 못한다. 따라서 이는 **회고 연구 외부표본 결과**이며 운영 Champion 승격·실전 정확도·수익 개선을 인정한 결과가 아니다. [전체 비교 원본](validation/nba-three-season-research.json)을 보존했다.

정상 검증 자료가 100개 이상일 때 기존 `daily(retrain=True)`의 신규 결과 50개·시간순 검증·Champion/Challenger gate를 연결했다. 현재 입력 자격은 부족하므로 BLOCKED를 기록한다. 미검증 자료로 재훈련·승격을 강행하지 않는다.

## A–I 검사와 실행

검사 | 확인
---|---
A | 실제 기록한 공식·ESPN 6경기, 팀/시각/별칭 일치와 중복 제거
B | 06:30 6경기 사전 가능, 23:22 전부 사전 제외; 수동 지연을 예약 실패로 오인하지 않음
C | summary 최근 이력 부재에도 시즌 일정에서 5/10/시즌 복구, 실자료 12/12팀
D | 구조 변경·공급원 장애·캐시 fallback·정보 충돌 구분
E | 원본 저장 후 재실행 재사용, DB 실패 시 원자 rollback
F | 실제 마진·총점 W/L/Push, 시작 후 생성 제외, 원본 해시/심사 연결
G | 동일한 시계열 구간·대상에서 모델 비교, 현재 경기 변경이 자기 피처에 영향 없음
H | 수치 오류·일정 충돌 NO와 반려 사유 저장
I | 보고서·예측이 있는 암호화 실제 복원 및 PostgreSQL 권한/불변성 검사

```bash
SPORTON_TEST_POSTGRES_ADMIN_URL_FILE=/workspace/sporton-data/var/persistence-dev/test-admin.url .venv/bin/python -m pytest -q
.venv/bin/python -m sporton.nba_benchmark --deep-learning
.venv/bin/python -m sporton.dispatch --date 2026-11-11 --dry-run
```

전체 132개 테스트 통과 후 추가한 예약 재시도 오판 방지 회귀도 별도 통과했다. 최종 실행 수와 외부 배포 검증 결과는 아래 실행 기록에 추가한다.

## 남은 완료 조건

전문가 LLM 비판 심사, 공식 확정 라인업·선수 Usage/On-Off/대체 역할·추적 이동, 독립 쿼터 모델, 정확한 공개 시각이 있는 학습 자료, 운영 모델 승격과 실전 개선 증명, 외부 운영 PostgreSQL/키 관리/원격 백업/PITR, runner 미기동을 감지하는 독립 외부 감시가 남아 있다. 이 범위를 완료하거나 검증됐다고 표시하지 않는다.

## 외부 실행과 추가 복구 검증

- `cf46515` 외부 수동 실행 [43045730712](https://github.com/ppkuk0416/sporton-data/actions/runs/43045730712)은 1분 20초에 실제 저장·전송을 완료했다.
- 내려받은 실제 상태 DB에서 10경기, 20/20팀 이력, 20/20팀 파생 효율, PASS 심사 10건, 연구 예측 10건, 이전 보고서 조회 1건을 확인했다. 상태는 `COMPLETED_PARTIAL`, origin은 `workflow_dispatch`다. 예약 전송 키는 소모하지 않았다.
- **환경 차이:** Codex 환경의 공식 NBA 요청은 200이지만 해당 GitHub runner의 공식 API는 타임아웃, CDN은 HTTP 403이었다. 이를 실제 일정 충돌이나 실시간 공식 검증 성공으로 표시하지 않았다.
- 이에 검증된 공식 시즌 일정 projection 캐시를 추가했다. 출처 URL·실제 수집 시각·원본 참조·내용 SHA256을 포함하고 24시간 이내만 사용한다. 캐시와 실시간 ESPN의 팀·시각이 맞아도 `PARTIALLY_VERIFIED`로 기록한다. 캐시가 만료되면 공식 확인을 주장하지 않는다. 원 제공처가 다시 정상 응답하면 런타임 캐시를 갱신하고 artifact에 보존한다.
- CDN의 실제 시각 필드 `gameDateTimeUTC`도 정상화했다. 이 필드 차이를 네트워크 장애로 오인하지 않도록 회귀 검사를 추가했다.
- 예약/수동 연구 감사는 별도 집계하며 전체 경기 지표는 예약 원본 우선, 게임별 최초 원본으로 계산한다. 원본·심사가 없는 캐시 성적도 제외한다.
- 전체 133개 테스트가 PostgreSQL·PyTorch 포함 환경에서 통과했다. 이후 캐시 만료/해시 및 감사 집계 회귀를 추가해 별도로 재검사했다.

## 최종 검증 기록

- PostgreSQL와 PyTorch를 포함한 최종 전체 테스트: **135 passed**, 건너뜀 없음. Starlette/LightGBM 의존성 경고 91개, assertion 실패 없음. 마지막 메타데이터 보존 보완 후 관련 25개 검사도 통과했다.
- 공식 캐시 복구를 포함한 외부 preview 실행 [43047885918](https://github.com/ppkuk0416/sporton-data/actions/runs/43047885918) 성공. **10경기 공식 캐시·실시간 ESPN 팀/시각 일치**, 사전 시나리오 10/10, 최근 이력·효율 20/20팀. `COMPLETED_PARTIAL`을 유지했다.
- 이 검증은 Telegram 추가 전송 없이 수행했고 예약 키를 소모하지 않았음을 실제 DB에서 확인했다. 앞선 외부 수동 전송은 성공했다.
- 헤더도 ‘공식 실시간/공식 캐시/실제 충돌’을 나눠 표시해, 실시간 공급원 장애를 전 경기 불일치로 읽지 않도록 했다.
- 검증 원본: [실데이터/DB/복원](validation/nba-quality-real-validation.json), [실제 외부 전송](validation/nba-quality-external-run.json), [공식 캐시 외부 검증](validation/nba-quality-cache-external-run.json).
