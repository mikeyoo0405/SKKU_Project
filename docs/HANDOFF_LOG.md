# 이전 작업 인계

### 2026-11-21 (KST) · Codex · Phase 2 구현

- 단계·상태 — 후보 미디어 추출, 검수 큐·타이머, 검수 결정·이력·철회, 수동 초안 분리 구현.
- 변경 — `feat/phase-2` 미커밋. 신규 후보 영상과 WAV는 작업 공간에 두고 채택본만 출력한다.
- 검증 — Windows에서 Python 테스트 53개와 Node 테스트 3개 통과. Chrome에서 합성 입력으로 세션 시작, 검수 큐 진입, 보류 사유 저장을 확인했다.
- 다음 작업 — Ubuntu 복사본을 갱신하고 기존 데이터 경로로 배치를 재실행한다. 실제 한국어 영상의 경계·화자·대사와 작업 시간을 확인한다.

### 2026-11-21 (KST) · Codex · 업로드 전 계획 확인

- 단계·상태 — Ubuntu 사용자 출력으로 세 영상 배치 완료와 캐시 재사용 확인. 후보 미디어 추출·청취, 수동 시간, worker 비교는 대기.
- 변경 — feat/phase-1, PLAN.md의 현황·실행 결과·다음 구현 항목을 갱신했다. 미커밋.
- 검증 — 현재 배치 코드와 문서를 대조하고 사용자가 제공한 출력으로 실행 상태를 확인했다. 문서 변경 검증을 수행했다.
- 다음 작업 — Windows 변경분을 검토해 커밋하고, 다음 구현에서 후보 미디어 추출을 연결한다.

### 2026-11-21 (KST) · Codex · 리뷰 수정

- 단계·상태 — Phase 1 리뷰 지적 3건 수정. WSL2 실측 대기.
- 변경 — `feat/phase-1` 미커밋. ASR 구간 보존, 체크포인트 읽기 오류 구분, 추론 장치·정밀도 캐시 키 반영.
- 검증 — Windows에서 `python -B -m unittest discover` 30개 통과. 실제 음성 추론은 WSL2 파일럿에서 검증한다.
- 다음 작업 — 위 Phase 1 실행 절차로 파일럿 후보의 경계와 대사를 청취 확인한다.

### 2026-11-21 (KST) · Antigravity · Phase 1 코드 리뷰 및 보완

- 단계·상태 — 1단계 코드 리뷰 완료 및 HIGH/MEDIUM 5건 수정 완료 (실측 항목 대기)
- 변경 — `feat/phase-1`, `emolabel-youtube/app/youtube.py`·`tools/run_batch.py`·`test_batch.py`·`test_batch_youtube.py` 수정(미커밋): 로컬 캐시 우선 조회(오프라인/재실행 시 불필요한 YouTube probe 제거), `yt-dlp` 비정상 메타데이터 방어, 손상된 workspace JSON 자동 복구, `process_video` 단계별 헬퍼 분리 및 `*.part*` 정리, `--urls` `#` 주석 무시, 회귀 테스트 4건 추가
- 검증 — Windows 호스트에서 `python -B -m unittest discover -s emolabel-youtube -v` 26개 전체 통과(0.370s), 수정 파일 모두 LF 확인. WSL2 Ubuntu 환경 미접근으로 실제 다운로드·VAD·ASR은 미실행 (WSL2 재현: `cd emolabel-youtube && uv run python -m unittest -v test_portability && uv run python -m unittest discover -v && uv run python -m tools.run_batch --video-workers 1`)
- 다음 작업 — WSL2 Ubuntu에서 `pilot_v1` 수동 기준선 측정 및 배치 첫 실행·재실행(`--video-workers` 1 vs 2) 검증 후 `feat/phase-1` 커밋(`gan-harness/` 제외 주의)

### 2026-11-21 (KST) · Codex · Phase 1 구현

- 단계·상태 — 사용자 합의로 0단계 체크 완료. 1단계 코드를 구현했고 실측 항목은 열어 두었다.
- 변경 — `feat/phase-1`, 후보 선정·VAD·수집·배치 CLI와 회귀 테스트, 검토 기록을 추가했다. 미커밋.
- 검증 — Windows Python 3.14에서 `python -B -m unittest discover` 22개 통과, CLI 도움말 확인. WSL2 조회가 E_ACCESSDENIED로 실패해 실제 다운로드·음성 추론·사람 작업 시간은 측정하지 못했다.
- 다음 작업 — 위 실행 절차로 파일럿의 수동 기준선과 배치 첫 실행·재실행, worker 1·2를 비교한다.

### 2026-11-21 (KST) · Codex

- 단계·상태 — 0단계 기준 정리 중. 한국어 영상만 사용하고 영상당 길이순 상위 3개를 파일럿 기본값으로 기록했다.
- 변경 — `main`, `PLAN.md` 미커밋. 후보 선정 재검토 항목과 단계별 Git 커밋·실행 데이터 보호 범위를 정리했다.
- 검증 — Windows에서 문서와 Git 제외 규칙을 확인했다. 문서 변경으로 Python 테스트는 실행하지 않았다.
- 다음 작업 — 0단계 합의 후 `pilot_v1`의 한국어 발화 여부를 확인하고 WSL2에서 수동 기준선을 측정한다.


### 2026-11-21 (KST) · Antigravity

- 단계·상태 — 0단계 공통 기준 합의 진행 중 (파일럿 목록 `pilot_v1` 3건 등록, 합의 결정표 임시 기본값 설정)
- 변경 — `main`, [AGENTS.md](AGENTS.md)·[PLAN.md](PLAN.md) 수정(미커밋): 협업·교차 환경·인계 복구 규칙 보완, 데이터·상태 계약 신설, 0단계 합의 결정표 임시 기본값 및 `pilot_v1`(`DGwuCn-EDfQ`, `mKwJDo0YTkM`, `6yrnO_6Hl0I`) 기록, 1–4단계 운영 가드레일 반영
- 검증 — 문서 리뷰 및 수정만 수행(코드 변경 없음, `test_portability` 미실행)
- 다음 작업 — 0단계 임시 기본값·변경 예정 파일 최종 확인 후, 1단계 수동 기준선 측정(`pilot_v1`) 및 공통 수집·후보 배치 구현 시작
