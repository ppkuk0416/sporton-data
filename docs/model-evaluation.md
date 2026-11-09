# 모델과 검증 정책

## NBA 알고리즘

NBA 전용 Elo baseline, 표준화 LogisticRegression, LightGBM classifier를 같은 시간 구간에서 비교한다. 득점 2개 헤드는 baseline/Ridge 또는 LightGBM 회귀. 마진·총점은 두 점수의 일관된 합/차이다. 특정 시장선에 대한 확률, 쿼터·전후반 득점, 선수 결장 효과는 학습하지 않았으므로 반환하지 않는다. NBA 고급 피처 전체를 구현한 상태가 아니다.

날짜가 같은 경기를 하나의 분할에 두고 고유 날짜 기준 50% fit / 15% calibration / 15% validation / 20% test로 나눈다. 다음 구간 cutoff 이후에 공개된 이전 구간 정답은 embargo로 제외한다. 최소 100 usable games, 40 game dates, 각 후행 블록 최소 10경기가 필요하다. 큰 데이터에서는 시즌 단위 고정 홀드아웃으로 이 정책을 대체할 계획이다.

각 후보는 fit에서 학습하고 별도 calibration에서 Platt 확률 교정을 한다. validation Brier가 가장 낮은 후보만 선택한다. 선택 모델의 test는 1회만 평가하고 테스트 해시를 저장한다. 동일 holdout의 재학습·반복 튜닝은 차단한다. `backtest`는 3개 expanding-window로 Elo/Logistic/LightGBM을 미래 블록에서 비교하며 **개발용**으로 표시한다. 이 블록을 sealed test라고 부르지 않는다. 별도 test를 본 뒤 backtest로 모델을 튜닝하고 동일 테스트에서 개선이라고 주장해서는 안 된다.

Accuracy, Log Loss, Brier, calibration bins, 점수·마진·총점 MAE, 점수 RMSE, marginal score interval coverage를 기록한다. 모든 평가에 표본 수·기간·소스가 포함된다. Accuracy는 Wilson 95% CI, Brier는 고정 seed 400회 bootstrap CI. Bootstrap은 경기 간 독립을 가정하므로 시간 상관에 낙관적일 수 있다. 모델 변경을 승인하는 통계적 유의성 증명으로 사용하지 않는다. 점수별 90% split-conformal residual interval은 시간 drift/exchangeability 위반 시 nominal coverage를 보장하지 않는다. 조합 마진/총점 분포는 미구현이다.

## Champion / Challenger

초기 Champion은 실데이터 모델 중 calibration/validation 각각 최소 30경기를 확보한 경우 명시적 `promote`로 등록한다. 이 첫 등록은 기존 대비 성능 개선을 의미하지 않는다. 이후 후보는 기존 Champion의 선택 데이터보다 미래의 validation window가 있어야 한다. 두 모델을 **동일 경기**에서 재평가한다. 사전 고정 기준은 Brier 절대 개선 >=0.005, Log Loss 비악화, 점수 MAE 비악화다. 최종 test는 승격 판단에 사용하지 않는다. 이 간단한 기준은 수익 보장이나 충분한 표본 검정이 아니다.

`rollback`은 이전 Champion 이벤트로 되돌린다. 과거 예측과 심사·모델 이벤트는 수정하지 않는다. 최근 실전 30경기의 Brier가 등록 validation Brier보다 0.05 이상 악화되면 drift 경고. 경고는 추천을 차단하지만, 작은 표본의 일시적 변동만으로 무조건 롤백하지 않는다. 운영자가 근거를 보고 `rollback`한다. 자동 롤백 정책은 추가 검증 후 구현할 예정이다.

## 추천과 심사

PASS는 게시 품질 판정이다. RECOMMEND/WATCH/NO BET와 분리한다. 승인된 연구 예측은 WATCH로 게시해 라이브 결과를 먼저 쌓는다. 검증된 시장이 없어도 임의 배당 없이 방향 분석을 할 수 있다. 공식 추천의 현재 보수적 gate:

- 실제 데이터 모드, 현 Champion, matching independent PASS
- 같은 모델의 실전 감사 >=100경기, Brier <0.25, Log Loss < ln(2), drift 경고 없음
- verified moneyline 시장, 연장 포함, cutoff 이전 15분 이내 수집
- EV = calibrated probability × decimal odds - 1 >=0.05

실전 감사 기준을 충족하기 전에는 EV를 표시하지 않는다. spread/total은 실제 시장을 보존·정산할 수 있지만 확률 분포가 없어 공식 추천하지 않는다. TEST/FIXTURE 모델의 연구 교정 성능으로 실제 가격 우위를 주장하지 않는다. 모델별 통계의 점검과 관리가 필요하며 이 gate가 확률 정확성의 증명은 아니다.

## 감사와 지속 학습

게시 시각이 tipoff 이전이고 matching PASS와 report hash가 있어야 게시된다. 실제 결과는 검증된 동일 fixture/teams/time/league여야 한다. 감사는 종료·공개가 현재 시각 이전일 때만 실행하고 prediction ID당 1번 저장한다. 틀린 예측 또는 마진 오차 >15는 error_memory에 검증되지 않은 가설로 저장하며 정답 라벨로 쓰지 않는다. 시장 정산은 실제 보존된 라인·배당으로 계산하고 WATCH 가상정산과 실제 추천 ROI를 구분한다.

`worker`는 CSV inbox 입력 → 검증·저장 → 감사 → drift를 반복한다. `--retrain`을 명시했을 때 새 결과가 이전 선택 종료 시각 이후 50경기 이상이면 후보를 훈련한다. 새로운 미래 검증 구간과 성능 gate에 실패하면 Champion을 유지한다. 신규 게임만으로 매번 모델을 바꾸지 않는다. 실제 공급자 자동 스케줄/부상/결과 API 통합은 아직 미완료다.

## 현재 성능 주장

NBA 역사 59,008경기와 EPL 1,140경기 아카이브는 확보했으나 공개 시각을 검증한 운영 학습 경기와 실전 모델은 0이다. 실전 라이브 정확도는 **미측정**이다. 자동 테스트의 TEST/FIXTURE 학습·평가 수치는 실행 경로 검증일 뿐이며 제품 정확도로 보고하지 않는다. PyTorch 후보 경로와 별도 EPL 아카이브 연구는 구현했다. 해당 MLP는 최종 연구 테스트에서 Poisson 대비 개선되지 않아 승격하지 않았다. NBA RandomForest/XGBoost 추가 비교, 시즌 고정 외부표본 검증, block bootstrap, 시장 baseline 비교는 후속 과제다.

## 축구·야구의 별도 학습 정책

두 종목은 같은 시간 분할·교정·홀드아웃·승격 기록 절차만 재사용한다. 학습 피처와 통계 모델은 NBA와 공유하지 않는다. 각 리그마다 최소 100 usable games, fit/calibration에 해당 리그의 모든 결과 클래스가 존재해야 한다. 축구 및 KBO의 무승부를 표본 부족 때문에 지우거나 2-way로 바꾸지 않는다. 두 후보를 validation Brier로 비교하고 선택 모델만 별도 test에서 평가한다.

축구: 표준화 PoissonRegressor 두 골 헤드 → 독립 Poisson 차이 분포로 away/draw/home, 또는 form logistic의 3-way 예측. 이후 독립 calibration 구간의 multiclass logistic 교정. 야구: 선발 context와 팀 득실점 피처로 starter logistic + Poisson 득점 헤드, 또는 Random Forest 분류·회귀. MLB 2-way / KBO 3-way로 리그별 모델을 저장한다.

새 모델의 Brier는 `0.5 * sum((p_class - y_class)^2)`이다. Binary일 때 NBA Brier와 같고 3-way 균등 baseline은 1/3이다. Accuracy/Brier/Log Loss/점수·마진·총점 MAE에 표본·기간·소스와 400회 bootstrap CI를 기록한다. Calibration은 클래스별 bin으로 저장한다. CI는 독립 경기 가정으로 시간 상관을 충분히 반영하지 못한다.

`backtest --league ...`는 동일한 3-fold expanding window에서 해당 종목의 후보들만 평가한다. Champion/Challenger 비교도 같은 리그·호환 피처 버전·새 미래 구간이어야 하며 기존 고정 개선 gate를 통과해야 한다. `rollback --league ...`는 지정 리그만 되돌린다.

새 축구·야구의 실제 라이브 교정과 가격 정책은 아직 검증되지 않아 PASS여도 WATCH만 게시한다. 임의 EV나 공식 추천은 생성하지 않는다. 감사는 해당 결과 클래스 및 시장 scope에 맞춰 실행하고, 3-way 무승부와 push를 구분한다. 선수·전술 정보가 부족하면 설명과 심사에 결측/반대 근거를 남긴다.

## v0.3 원본과 정정 성적

`analytics.performance`는 최초 PRE_GAME과 원본 선택의 연결을 검증할 수 있는 기록만 집계한다. OFFICIAL_PRICED/DIRECTION_ONLY/CONDITIONAL_WATCH/PASS 및 종목·시장·모델 버전을 분리하고, 원본 미확인 기록은 제외 목록에 남긴다. 팀 득점·마진·총점 MAE, 승패 정확도, Brier/Log Loss, 표본·기간·출처를 계산한다. 가격/공식 요건이 없는 관찰 기록을 공식 ROI에 포함하지 않는다. 독립 쿼터·이닝·세트 모델이 없는 성적은 null이다.

실제 결과 정정은 새 settlement와 AUDIT를 추가하며 원래 평가를 변경하지 않는다. 최신 성능·브리핑·드리프트·추천 게이트는 정정 평가를 해석한다. 학습은 대상 경기 cutoff 당시에 알려진 버전을 사용해 사후 정정을 과거 피처로 누출시키지 않는다. 공식 가격 추천에는 양 팀의 cutoff 이전 확인된 라인업/가용성 증거도 요구한다. 생성형 독립 LLM 심사는 아직 연결되지 않았고 결정적 검사가 우선이다.
