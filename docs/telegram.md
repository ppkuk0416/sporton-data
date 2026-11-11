# Telegram 연결

텔레그램 BotFather로 만든 봇에 대한 개인 채팅 전송이다. 토큰은 채팅·명령 인자에
넣지 않는다. 다음 명령을 서버 터미널에서 실행하고 숨김 입력 프롬프트에 입력한다.

```bash
cd /workspace/sporton-data
.venv/bin/sporton telegram-setup
.venv/bin/sporton telegram-test
```

setup은 getMe로 봇을 확인하고 getUpdates에서 개인 `/start` 발신자가 정확히
한 명인 경우에만 수신자를 선택한다. 그룹은 제외한다. `/start` 업데이트가
만료됐거나 웹훅/다른 poller와 충돌하면 오류를 반환하며 임의 수신자를 고르거나
기존 웹훅을 삭제하지 않는다. 복수 개인 발신자는 명시적 설정이 필요하다.

설정은 기본 SPORTON_HOME의 `telegram.json`, 토큰과 개인 채팅 ID는 `secrets/`의
0600 파일에 저장한다. 다른 환경에는 SPORTON_TELEGRAM_CONFIG로 설정 파일을
지정할 수 있다. 설정 파일은 token_file과 chat_id_file 절대 경로를 포함한다.
비밀 파일은 Git 제외 경로에 두어야 한다. 기존 파일은 덮어쓰지 않는다.

telegram-test는 연결 확인 메시지 **한 건을 실제 발송**한다. Bot API 허용 도메인은
api.telegram.org이며 TLS 검증을 사용한다. 토큰이 URL에 포함되므로 HTTP 예외
원문이나 응답 본문은 DB·로그에 남기지 않는다.

기존 briefing-daemon이 생성한 실제 브리핑은 Markdown 문서 첨부로 전송된다.
4096자 메시지 제한으로 본문이 잘리는 문제를 피한다. TEST/FIXTURE 보고서는
외부 전송하지 않는다. 성공 메시지 ID를 briefing_events에 남기고 같은 브리핑을
다시 전송하지 않는다. 실패는 최소 5분 후 재시도한다. 원본 브리핑은 수정하지 않는다.

```bash
.venv/bin/sporton briefing-daemon --inbox var/inbox
```

이 프로세스는 **상시 호스트와 프로세스 감독이 필요**하다. 위 명령을 제공한 것만으로
서버 구축이나 예약 발송이 완료된 것은 아니다. 현재 기존 생성 슬롯은 KST
06:30/17:00이다. 06:30은 생성 시작 시각이며 **06:30까지 완료 SLA가 아니다**.
그 목표를 달성하려면 사전 NBA 최신 수집 자동화, 앞선 실행 시작 시각, 마감
감시·경고를 별도로 운영 검증해야 한다. 토큰 등록 전에는 아무것도 전송하지 않는다.

Telegram이 수신한 직후 네트워크 연결이 끊기거나 DB 성공 기록이 실패하면 재시도로
중복될 수 있다. Telegram Bot API는 요청 멱등 키를 제공하지 않으므로 정확히 한 번
전달을 보장하지 않는다. 로컬 성공 기록을 통한 일반 재시작 중복 방지만 구현했다.
자동 전송 실패는 DB 이벤트에 기록하며, 외부의 별도 장애 알림 채널은 미연결이다.

관리형 환경에서는 환경 설정의 비밀 값 `SPORTON_TELEGRAM_BOT_TOKEN`을 등록할 수
있다. 허용 목적지는 api.telegram.org이다. 이 값이 주입되면 telegram-setup은
토큰 입력을 다시 요청하지 않으며, 설정 파일에는 환경 변수 이름만 보관한다.
비밀 값은 디스크에 복사하지 않는다. 비밀 요구사항 초안 저장은 실제 값 등록이나
현재 환경 반영을 뜻하지 않는다. 설정 검토·저장 및 사용자 환경 게시 후 새 연결로
getMe/getUpdates와 실제 시험 전송을 검증해야 한다.
