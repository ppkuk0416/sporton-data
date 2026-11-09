# 직접 수집과 딥러닝 · 현재 동작 및 한계

## 403의 의미와 실제 원인 구분

HTTP 403은 접근 거절을 뜻한다. 이번 KBL 요청은 웹사이트 본문을 받기 전 환경의 HTTPS 프록시에서 `ProxyError 403 Forbidden`으로 실패했다. 실행 환경의 적용 상태는 restricted/enforced이고 custom allowed_hosts는 비어 있었다. KBL 도메인은 허용 목록에 없다. KBL 사이트가 크롤러를 차단했다거나 유료 API 키가 필요하다는 사실을 확인한 것이 아니다.

초안에는 KBL·NBA 공식 사이트, Google/Bing/Naver 검색·스포츠, GitHub API 도메인 요구를 보존·추가했다. **초안 저장은 실행 환경 정책 반영이나 게시가 아니다.** 현재 도구는 초안 작성까지만 지원하며 환경 설정에서 변경 저장·게시가 필요하다. 정책을 우회하는 프록시나 TLS 검증 해제는 사용하지 않는다. 등록된 검색 도메인은 현재 접근 가능하거나 검색이 실행됐다는 뜻이 아니다.

자료 수집은 사용자의 책임으로 넘기지 않는다. 공식 경로 확인과 함께 독립적으로 공개된, 재사용 권한이 확인된 자료를 직접 수집한다. 대체 자료를 오늘 KBL의 일정·기록으로 가장하지 않는다.

## 직접 확보한 출처

출처 | 실제 수집 범위 | 권리·신뢰 등급
---|---|---
FiveThirtyEight Historical NBA Elo | NBA로 분류된 59,008경기, 원본 18,173,265 bytes | 저장소 명시 CC-BY-4.0, attribution 보존. 역사 아카이브이며 현대 NBA 3시즌이나 KBL이 아님
OpenFootball EPL | 2023-24 / 2024-25 / 2025-26, 시즌별 380경기, 총 1,140 | 저장소 명시 CC0-1.0, 원본과 license 증거 보존. 공식 리그 feed와 동등하게 간주하지 않음
KBL 공식 페이지 | 접근 실패, 확인된 당일 경기 0건 | discovery_only. 페이지 추출기·학습 이용 조건은 접근 후 검증 필요

- NBA 데이터: https://github.com/fivethirtyeight/data/tree/master/nba-elo
- NBA 라이선스 명시: https://github.com/fivethirtyeight/data/blob/master/README.md
- EPL 데이터: https://github.com/openfootball/football.json
- EPL 라이선스: https://github.com/openfootball/football.json/blob/master/LICENSE.md

자료의 정확한 날짜 범위는 저장된 `archive_datasets.metadata.period`에서 확인한다. 원본은 `var/raw/{sha256}.{format}`에 내용 해시로 보관하며 source URL·수집 시각·라이선스 원문과 해시·버전을 DB에 보존한다. 같은 내용 재수집은 첫 원본을 덮어쓰지 않고 확인 시도만 새 기록으로 남긴다. `published_at`을 경기 종료 시각으로 추정해 만들지 않는다.

## 실행과 자동 축적

```bash
bash tools/install.sh
sporton collect
sporton collect --source epl-2025-26 --force
sporton archive-research
sporton train --league EPL --deep-learning
sporton train --deep-learning
sporton backtest --league MLB --deep-learning
sporton daily --league EPL --retrain --deep-learning
```

`collect`는 고정 출처 registry를 직접 요청한다. 라이선스 문구 확인 → 원본 보존 → 형태 검증 → archive metadata 기록 순서다. 전체 호출 중 일부가 실패하면 성공분을 보존하면서 exit 1과 실패 기록을 반환한다. 확인 주기는 공식 KBL discovery 6시간, EPL archive 24시간, 고정 NBA 역사 archive 7일이다. `--force`는 설정 변경이나 새로운 진단 후 재확인할 때 사용한다.

브리핑 daemon은 매시간 due source를 점검한다. `--retrain`과 PyTorch가 있으면 EPL 3시즌 연구 실험을 한 번 실행하고, 같은 입력의 결과가 있으면 재학습 없이 기존 결과를 반환한다. 검증된 운영 자료가 쌓인 리그의 후보 학습에도 MLP를 포함하며 기존 교정·검증·승격 gate를 그대로 적용한다. 오래된 아카이브를 계속 다운받는 것을 신규 실제 경기 축적이라고 주장하지 않는다. 공식 실시간 일정·로스터·결과 어댑터는 아직 미완료다.

대시보드의 **직접 수집한 원본 · 연구 데이터** 패널과 `GET /api/data-sources`, `GET /api/archive-research`에서 원본 건수·등급·실패 및 비교 실험을 확인한다. 기존 verified 경기/라이브 성능 집계와 분리한다. TEST/FIXTURE에서는 실네트워크 수집을 끄고 mock transport만 사용한다.

## 실제 PyTorch 모델

PyTorch 2.9.0+cpu, CPU 2스레드, seed 2026. 표준화 입력 → 32 → 16 → 결과 logits + 양 팀 득점의 MLP이다. 80 epochs, Adam(lr=.003, weight_decay=.005), Cross Entropy + 0.25 Huber joint loss. 입력과 득점 scaler는 fit 블록에서만 학습한다. 별도 calibration 블록에서 확률을 교정하고 잔차 구간을 구한다. 단순히 역할 이름을 붙인 LLM이 아니며 실제 gradient 학습을 수행한다.

`--deep-learning`은 NBA 및 각 종목·리그의 전용 피처를 입력으로 받아 MLP 후보를 기존 모델들과 비교한다. 모델 가중치·scaler·교정기·체크섬을 저장한다. 딥러닝을 켰다는 이유로 선택하거나 승격하지 않는다. CPU 의존성은 공식 배포판 `requirements-deep-learning.lock`에 고정했으며 TLS 검증을 유지했다.

## EPL 아카이브 연구 결과

정확한 공개 시각·tipoff timezone이 없고 나중의 데이터 정정이 포함됐을 수 있으므로 **date_only_archive, production_eligible=false**다. 기존 `Game`의 verified 시점 규칙을 완화하지 않고 별도 연구 경로를 둔다. 다음 결과를 엄밀히 검증된 역사 as-of 시뮬레이션이나 오늘 KBL 예측 성능으로 표현하지 않는다.

- 2023-24 fit 349경기
- 2024-25 앞 날짜 블록 calibration 198경기 / 뒤 블록 validation 173경기
- 2025-26 final test 374경기
- 각 경기 피처는 엄격히 더 이른 날짜 경기만으로 생성. 같은 날·자기 경기 결과 제외. 최초 팀 이력 3경기 미달 제외.
- xG·PPDA는 없는 수치를 생성하지 않고 결측 flags로 표시. 달력 날짜 차이는 정확한 시간 단위 휴식으로 표현하지 않음.
- 세 후보와 하이퍼파라미터를 미리 고정하고 validation Brier로 선택한 뒤 final test를 한 번 비교. 같은 최종 경기 집합을 source correction만 바꿔 재학습/재튜닝하는 실행은 차단.

모델 | Validation Brier | Final test Brier ↓ | Final test Accuracy | Final test Log Loss
---|---:|---:|---:|---:
Goal Poisson | 0.3137 | 0.3171 | 46.79% | 1.0508
Form Logistic | 0.3162 | 0.3178 | 47.59% | 1.0525
PyTorch MLP | 0.3129 | 0.3264 | 43.85% | 1.0788

MLP training loss는 1.1802 → 0.9477로 내려갔으나, 최종 테스트에서는 기준 모델보다 나빴다. **학습 loss 감소와 실제 개선은 다르며, 이번 실험에서 딥러닝 성능 개선은 확인되지 않았다.** Validation 선택 모델은 MLP였지만 어떤 아카이브 후보도 운영 레지스트리나 추천에 등록하지 않았다. Final test를 보고 다른 모델로 바꿔 같은 테스트에서 개선했다고 주장하지 않는다.

표본·기간·출처·bootstrap 95% CI는 `var/research/<data-hash>/result.json`에 보존했다. bootstrap은 경기 독립을 가정한다. 학습 계획·원본 해시·고정 seed/하이퍼파라미터·모델 파일/해시를 보존한다. 최초 연구 실행에 코드 전체 해시를 별도 기록하지 못한 한계가 있어 정확한 실행 소스 snapshot 검증은 다음 실행부터 강화할 과제다. 이 누락을 과거 기록을 수정해 숨기지 않는다.
