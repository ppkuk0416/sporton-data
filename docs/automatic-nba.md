# 매일 NBA 보고서 자동 전송

## 실행 경로

`python -m sporton.dispatch`는 한국시간 당일 NBA 일정을 최신 조회하고 경기별
최근 결과·부상 표기·시장선을 수집한다. NBA 공식 일정과 ESPN을 교차 확인한다.
수집 원본은 SHA256과 함께 DB에 저장하고, 보고서를 변경 불가 RESEARCH로 보관한
뒤 Telegram 문서로 보낸다. 같은 날짜 성공 기록이 있으면 재발송하지 않는다.

최근 최대 5경기 득실점에서 계산한 점수 시나리오는 비검증 탐색 기준선이다.
기존 운영 모델을 대체하지 않으며 자체 확률·EV·적중률을 만들어내지 않는다.
현재 자동 결과 감사와 Champion 모델의 실전 성능 검증은 미완료다.

```bash
.venv/bin/python -m sporton.dispatch --dry-run
.venv/bin/python -m sporton.dispatch
.venv/bin/python tools/start_nba_dispatch.py
```

preview는 보고서·수집 원본만 저장하며 전송·사전 보고서 등록은 하지 않는다.
실전 실행은 날짜별 최초 보고서를 고정하고 전송 실패 시 해당 원본을 재사용한다.
원본은 `professional_reports`, 날짜별 식별자는 `briefings`, 발송·실패는
`briefing_events`, 파일 사본은 `var/daily-reports`에 남는다.

## 현재 클라우드 환경용 프로세스

시작 도구는 중복 프로세스를 막고 06:30 KST에 자동 실행하며, 실패 시 5분마다
재시도한다. 최초 실패는 Telegram 알림을 시도하고 heartbeat/다음 실행 시각을
`var/nba-daemon.json`에 기록한다. `var/nba-daemon.log`는 실행 요약이다.
이미 오후인 최초 설치에서는 다음날부터 시작하고 해당 시작 날짜를 보존한다.

이 프로세스는 **현재 클라우드 실행 환경이 살아 있는 동안만 동작**한다.
노트북 터미널 종료와 분리된 프로세스로 시작하지만, 클라우드 머신 종료·정지·환경
교체 후까지 유지되는 상시 서비스로 간주하지 않는다. PID 1이 systemd가 아닌
현재 환경에는 systemd 상주 서비스를 활성화했다고 주장하지 않는다.

## 채팅과 독립적인 GitHub Actions

`.github/workflows/nba-daily.yaml`은 기본 브랜치에서 매일 UTC 21:30,
21:40, 21:50, 22:00에 복구 실행한다. 기본 실행은 UTC 21:30 = 한국시간 다음날 06:30이다.
현재 사용자 지시대로 06:30 시작과 실제 완료/전송 시각을 분리 기록한다.
GitHub 예약은 지연될 수 있으므로 **06:30 도착의 절대 SLA는 아니다**.
엄격한 SLA에는 전용 상시 호스트와 독립 외부 모니터가 필요하다.

저장소 Settings → Secrets and variables → Actions에 필요한 두 비밀:

- `SPORTON_TELEGRAM_BOT_TOKEN`: 사용자의 실제 BotFather 토큰
- `SPORTON_TELEGRAM_CHAT_ID`: 이미 연결한 개인 수신 채팅 ID

Codex 환경 비밀은 GitHub Actions로 자동 이전되지 않는다. Codex의 네트워크
비밀은 목적지 제한이 있는 프록시 바인딩이므로 값을 추출하거나 GitHub로
복사하지 않는다. 사용자가 GitHub Secret에 실제 값을 등록해야 한다.
토큰을 소스·커밋·워크플로·로그·artifact에 기록하지 않는다.

첫 수동 실행은 Actions → NBA daily Telegram → Run workflow. 성공 로그와
Telegram 수신을 확인한다. 활성화 확인 후 로컬 daemon은 중지해 서로 다른 DB를
사용하는 두 호스트가 중복 전송하지 않도록 한다. 프로세스 중지는 heartbeat의
PID와 프로세스 명령이 `sporton.dispatch --daemon`인지 확인한 뒤 SIGTERM을 보낸다.

Actions는 concurrency로 중복 실행을 직렬화하고 신뢰한 동일 워크플로의 완료된
schedule/workflow_dispatch 실행에서 최신 DB와 전송 영수증을 복원한다.
실패 후에도 artifact를 업로드한다. artifact 보존은 90일이며 누적 DB가 다음 실행에
포함된다. 이는 외부 불변 원격 백업·PostgreSQL·PITR을 대체하지 않는다.
저장소가 public이므로 raw 스포츠 자료와 보고서 artifact에 개인 정보·토큰·chat ID를
넣지 않는다. artifact는 인증된 저장소 읽기 권한 보유자가 다운로드할 수 있다.

## 실패와 남은 한계

- 수집은 요청별 3회 재시도, 전송은 최소 5분 간격 재시도한다.
- 송신 오류는 토큰이 포함된 Telegram URL/응답 본문을 노출하지 않는다.
- Telegram 수신 직후 DB 기록 전에 장애가 나면 중복될 수 있다. Telegram은
  멱등 키가 없어 정확히 한 번 전달을 보장하지 않는다.
- DB/artifact가 유실되면 이전 전송 성공을 알 수 없다.
- runner가 아예 시작하지 않거나 Telegram이 중단되면 같은 채널로 장애를 알릴
  수 없다. GitHub 실패 알림은 계정 알림 설정에 의존한다.
- 작업 활성화·실제 도착은 실행 증거를 확인하기 전에는 완료로 표시하지 않는다.

## 실제 외부 활성화 검증 · 2026-11-11 KST

GitHub repository secrets 등록 후 외부 runner에서 실행했다.

- 최초 외부 실행: https://github.com/ppkuk0416/sporton-data/actions/runs/43037547787
  — success, 로그 `status=sent`, `date_kst=2026-11-11`. 실제 Telegram Bot API 발송 성공과
  DB·보고서 artifact 업로드를 확인했다. 오후 수동 검증이어서 `late=true`를 보존했다.
- 두 번째 외부 실행: https://github.com/ppkuk0416/sporton-data/actions/runs/43037926066
  — success, 로그 `status=already_sent`. 이전 artifact에서 성공 영수증을 복원해
  실제 재발송을 막았다.
- 워크플로는 main의 active 상태이며 KST 06:00/06:15/06:25/06:35 예약을 확인했다.
  다음 기본 실행 대상일은 2026-11-12 KST다. 미래 cron 발동·06:30 도착을 사전에
  성공으로 기록하지 않는다.
- 현재 클라우드의 로컬 daemon은 SIGTERM으로 중지했고 lock 해제를 확인했다.
  `var/nba-daemon-service.json`의 disabled 표시로 시작 도구의 자동 재시작도 막았다.
  외부와 로컬의 서로 다른 DB로 인한 이중 발송을 방지한다.

자동 전송 활성화가 모델 검증·공식 추천 정확도 확보를 뜻하지 않는다. 보고서는
수집한 증거와 탐색 기준선, 결측·오류를 구분하는 연구 보고서다.


## 품질 개선 v2

[원인·수정·A–I 회귀·실데이터·모델·복원 검증](nba-quality-repair.md). v1의 실행 증거는 이력으로 유지한다. 현재 예약은 06:30/06:40/06:50/07:00 KST이며 수동 실행 키는 예약과 별도다.
