# 백업·복구 운영

## 기본 원칙

`secure-backup` 성공은 **CREATED_UNVERIFIED**다. 실제 격리 DB에 복원하고 내용·제약·트리거·함수·원본/모델/Parquet 참조를 대조한 `restore-test` 성공만 **RESTORE_VERIFIED**다. 나중의 재검증이 실패하면 현재 검증 상태도 실패로 본다. 이전 성공 이력은 삭제하지 않는다.

```bash
python tools/runtime.py --owner sporton secure-backup
python tools/runtime.py --owner sporton restore-test BACKUP_ID
python tools/runtime.py --owner sporton persistence-worker --once
python tools/runtime.py --owner sporton persistence-worker
```

개발 설정 `config/persistence.dev.json`, 운영 예시 `config/persistence.production.example.json`:

- interval_minutes: 60
- retention_days: 개발 30 / 운영 예시 90
- rpo_minutes: 60
- rto_seconds: 개발 300 / 운영 예시 900
- failure_retry_minutes: 기본 15
- backup_root, key_file, 선택 wal_staging 및 개발용 pg_tools_prefix

이 수치는 구성 목표다. 로컬 복원 측정이 외부 운영 SLA를 보증하지 않는다. 백업/복원 전용 worker를 앱 조회 역할과 분리한다. 브리핑은 이 worker에 백업을 위임하며 운영에서 기존 SQLite 평문 백업으로 대체하지 않는다.

## 백업 내용과 암호화

PostgreSQL은 repeatable-read transaction에서 `pg_export_snapshot`을 얻어 `pg_dump --snapshot`과 내용 manifest가 같은 스냅샷을 보게 한다. SQLite는 online backup 사본에서 manifest를 만든다. 모델·raw·research·lake를 함께 포함하고 DB가 참조하는 파일의 SHA를 검증한다.

임시 stage는 제한된 임시 디렉터리에 만들고 종료 시 정리한다. bundle은 무작위 nonce의 **AES-256-GCM**으로 스트리밍 암호화하고 파일 권한을 0600으로 설정한다. 키는 정확히 32바이트의 별도 파일이며 backup_root 내부에 둘 수 없다. 생산 환경에서는 backup_root가 데이터 home과 별도 마운트여야 한다. key·DB 비밀번호·관리 토큰은 Git이나 백업 bundle에 포함하지 않는다. 개발 키는 같은 호스트의 비공개 파일이므로 원격 키 보관을 대체하지 않는다. 키를 잃으면 복호화할 수 없다. 키 회전 시 이전 키의 별도 보관·선택 정책은 운영에서 마련해야 한다.

## 실제 복원 검사

1. 암호문 checksum과 GCM 인증 확인 후에만 tar를 추출한다.
2. 경로 탈출·심볼릭/하드 링크·device 항목을 거절한다.
3. SQLite는 새 임시 파일, PostgreSQL은 새 `sporton_restore_<uuid>` DB에 실제 복원한다. 원본 DB를 대상으로 restore하지 않는다.
4. 모든 테이블의 행 수와 ID별 내용 hash, 제약·trigger·function 지문, 파일 hash와 DB가 참조하는 복원된 자산을 대조한다.
5. Parquet 파일을 실제 읽어 row count를 확인하고 이벤트 자체의 해시를 재검증한다.
6. 원본 DB는 그대로 두고 임시 복원 DB를 제거한다. 결과·소요 시간·RTO 목표 충족 여부를 append-only로 기록한다.

pg_dump/pg_restore 도구의 exit 0만으로 성공으로 표시하지 않는다. pg_dump가 VARCHAR 배열 CHECK를 동등한 TEXT 캐스트로 재해석하는 차이는 명시적 TEXT CHECK 정의로 안정화했다. 비교 대상 schema checksum을 임의로 바꿔 통과시키지 않는다.

원래 경로로 실제 장애 복구할 때는 앱·수집 writer를 중지하고 검증된 backup/key를 선택해 DB와 모든 asset을 함께 복원해야 한다. 현재 `restore-test`는 격리 훈련용이며 운영 원본을 자동으로 덮어쓰는 명령은 제공하지 않는다. 장애 후 교체·트래픽 전환은 운영자 절차로 남아 있다.

## 재개·보존·경고

백업 worker는 주기를 보고 새 백업·실제 복원 시험을 순차 수행한다. 복원 실패는 실패로 기록하고 정해진 대기 후 새 스냅샷을 시도한다. 실패 백업을 무한히 즉시 재시도하지 않는다. 보존 만료 파일은 checksum과 현재 복원 검증 상태를 확인하고 최소 최근 검증된 두 세트를 보존한 채 정책에 따라 만료한다. 원본 데이터·보고서·실패 기록은 삭제하지 않는다. 관리 API는 최신 파일 생성/복원 검증 여부와 검증된 backup age/RPO 초과·실패를 별도로 보여준다.

## WAL

개발 PostgreSQL은 wal_level=replica, archive_mode=on, archive_timeout=3600으로 실행한다. archive_command는 이미 존재하는 파일을 원본 WAL과 비교하고 불일치 시 실패한다. WAL staging은 제한된 권한의 별도 디렉터리이며 worker가 세그먼트를 AES-GCM 암호화해 보관하고 hash를 기록한다. 실제 pg_stat_archiver와 생성된 WAL 암호화를 확인했다. 초기 권한 설정 중 발생한 실패 카운트도 숨기거나 reset하지 않았다.

**물리 base backup과 recovery_target 시점으로의 실제 PITR 훈련은 아직 하지 않았다.** 논리 dump와 WAL 파일 보관만으로 PITR 가능을 주장하지 않는다. 상태 API의 pitr_restore_tested는 false다. 운영에서 PITR이 필요하면 관리형 DB의 PITR 또는 물리 base backup+WAL 관리 도구를 구성하고 목표 시점 복구를 별도로 검증해야 한다.

## 외부 장애 범위

현재 백업·DB는 같은 클라우드 호스트의 개발 환경에 있다. 프로세스/DB 컨테이너 재시작과 격리 restore는 검증했지만, 별도 호스트·원격 저장소·호스트 전체 손실·실제 운영 credentials는 미연결이다. `compose.production.yaml`은 외부 PostgreSQL TLS URL 파일과 독립 백업 mount를 요구하며 배포 자체는 아직 실행하지 않았다. 이 조건이 충족되기 전에는 데이터 영구 보존 또는 무중단 운영을 보장한다고 표현하지 않는다.
