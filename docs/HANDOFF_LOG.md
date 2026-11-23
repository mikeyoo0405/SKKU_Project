# 이전 작업 인계

### 2026-11-23 (KST) · Antigravity · 3.5단계 완료 반영 및 세션 저장

- 단계·상태 — 3.5단계(수동 구간 추가 + 배치 `asr.json` 초안·선택형 `대사 자동 전사` 버튼) 구현·ECC 4단계 점검(`click-path-audit`·`/fastapi-review`·`code-review`·`verification-loop`)·`feat/phase-3` 커밋 및 원격 푸시(`5320846` → `origin/feat/phase-3`) 완료 상태를 `PLAN.md`에 반영하고 세션 스냅샷을 저장했다. Ubuntu HTTP 6개 테스트와 실제 브라우저·Whisper 전사 확인은 실환경 대기 항목으로 유지한다.
- 변경 — `feat/phase-3` (`5320846` 푸시 완료 후 후속 문서 변경 미커밋): [PLAN.md](../PLAN.md) (3.5단계 완료·커밋 `5320846` 상태 반영 및 인계 5건 유지), [docs/HANDOFF_LOG.md](HANDOFF_LOG.md) (이전 인계 1건 이동), [docs/WORKFLOW_GUIDE.md](WORKFLOW_GUIDE.md) (팀원용 프로그램 작동 방식·설치·사용 가이드 신설, 미커밋), 글로벌 세션 기록(`~/.claude/session-data/2026-11-23-phase35-manual-add-session.tmp`). 코드 변경 없음.
- 검증 — `git status --short --branch`·`git log -n 3 --oneline`으로 `origin/feat/phase-3`이 `5320846`에 동기화된 상태 확인, 변경 문서 UTF-8/LF(`CRLF: 0`)·`PLAN.md` 최신 인계 5건 확인. 코드 수정이 없어 직전 `5320846` 로컬 검증(Python 비HTTP 142/142·Node 13/13 PASS)을 그대로 유지한다. Ubuntu 실환경 검증은 미실행이며 재현 명령은 `git fetch origin && git checkout -B feat/phase-3 origin/feat/phase-3 && cd emolabel-youtube && uv run python -m unittest -v test_portability && uv run python -m unittest discover -v && node --test test_review_ui.mjs test_boundary_playback.mjs`이다.
- 다음 작업 — 필요 시 `PLAN.md`·`docs/HANDOFF_LOG.md`·`docs/WORKFLOW_GUIDE.md` 커밋·푸시(및 팀원 가이드 노출 방식 결정) 후, Ubuntu에서 `feat/phase-3` 동기화·전체 148개 테스트·브라우저 실검증을 마치고 4단계 팀원 프로그램 비교로 진입한다.

### 2026-11-22 (KST) · Antigravity · 3.5단계 수동 구간 추가 마무리·보완 및 검증

- 단계·상태 — 3.5단계(수동 구간 추가 + 전사 보조) 구현·보완 및 ECC 점검(`click-path-audit`·`/fastapi-review`·`verification-loop`·`code-review`) 완료. 배치 `asr.json` 즉시 초안 채우기 + 선택형 `대사 자동 전사` 버튼(`POST /api/review/candidates/{clip_id}/transcribe`)까지 반영·회귀 검증했다. Ubuntu HTTP 6개 테스트와 실제 브라우저 추가/전사/저장/재실행 확인 및 GATE 2 커밋·푸시는 대기다.
- 변경 — `feat/phase-3` 미커밋. `emolabel-youtube/app/review.py` (수동 추가·경계 통합 `_normalize_bounds`, 배치 `asr.json` 겹침 초안 `_asr_draft_for_range`, 비파괴 단건 전사 `candidate_audio_path`/`run_candidate_asr`/`transcribe_candidate`, `_manual_source()` 보강, 수동 후보 미이동 `accepted` 유지, `_snapshot()` 복구 락 확장, `_folder()` `parse_clip_id`), `emolabel-youtube/app/schemas.py` (`ReviewDecision` `strict=True`, `source_version` `min_length=1`), `emolabel-youtube/app/review_api.py` (`POST /{clip_id}/transcribe` 503 안전 처리, `_http_errors()` 손상 JSON 500 보존), `emolabel-youtube/static/review.mjs` (`canTranscribeCandidate`·`normalizeBoundsMs`·`canStartAddition` `wsReady`·`restoreReviewFields`·`canUseEmotionShortcut` 등), `emolabel-youtube/static/app.js`·`index.html` (`#transcribe-btn`·`?v=manual-add3`, `wsReady` 레이스 및 소수점 끝단 반올림 수정), `test_manual_candidates.py` (17개)·`test_review_api.py` (6개)·`test_review_ui.mjs` (13개), `README.md`·`emolabel-youtube/README.md`·`PLAN.md`·`docs/HANDOFF_LOG.md` 갱신. 이전 인계 1건을 `docs/HANDOFF_LOG.md`로 이동.
- 검증 — Windows Python 3.14 비HTTP 전체 **142/142 PASS**(`test_manual_candidates` 17개·`test_portability` 7개 포함), Node **13/13 PASS**(`test_review_ui.mjs`·`test_boundary_playback.mjs`), Python 42개 `py_compile`·JS 4개 `node --check`·39개 프로젝트 파일 UTF-8/LF(`CRLF: 0`)·시크릿/개인경로/console.log 0건 확인. Windows `uv`·`ruff`·`pyright` 부재, `test_review_api.py`(HTTP 6개)는 Windows 샌드박스 `TestClient` 제약, 실제 브라우저·Ubuntu Whisper 전사/재실행 확인은 WSL 미실행으로 NOT PERFORMED (WSL2 재현: `cd emolabel-youtube && uv run python -m unittest -v test_portability && uv run python -m unittest discover -v && node --test test_review_ui.mjs test_boundary_playback.mjs`).
- 다음 작업 — 사용자 GATE 2 확인 후 `feat/phase-3` 커밋·푸시(`.claude/`·`gan-harness/` 제외) → Ubuntu 동기화 및 전체 148개 Python 테스트 + Node 13개 + 실제 브라우저 수동 구간 추가/대사 자동 전사/저장/재실행 보존 확인 → 4단계 팀원 워크플로우 비교 진행.

### 2026-11-22 (KST) · Codex · 3.5단계 수동 구간 추가 계획

- 단계·상태 — 사용자가 수동 구간 추가를 3.5단계로 배치하도록 요청해 목표·흐름·데이터 보호·변경 파일·완료 기준을 PLAN에 작성했다. 이번 작업은 계획만 해당하며 기능 구현은 시작하지 않았다. 기존 3단계 결과를 보존하고 커밋·푸시는 3.5단계 구현·검증 후 재개한다.
- 변경 — `feat/phase-3` 미커밋. 이번 변경은 `PLAN.md`와 이전 인계 이동을 위한 `docs/HANDOFF_LOG.md`뿐이다. 새 구간 선택→독립 pending 후보→사람 검수/확정 저장, 직접 추가 출처, 취소/실패 시 입력 보존, 중복 차단·영상별 잠금·재실행 보존, 자동 후보 없음 시 수동 시작, 기존 original_asr 평가 입력 분리를 계획했다. 기존 코드와 다른 도구의 변경은 보존했다.
- 검증 — 현재 후보/검수/스키마/UI 구조를 이전 읽기 결과와 대조하고 문서 단계 순서·완료 기준·UTF-8/LF·diff·인계 최근 5건을 확인했다. 코드 변경이 없어 자동 검사는 재실행하지 않았다. Ubuntu 새 기능 검증은 미구현이므로 미실행이며, 구현 후 `cd emolabel-youtube && uv run python -m unittest -v test_portability && uv run python -m unittest discover -v && node --test test_review_ui.mjs test_boundary_playback.mjs`와 실제 추가/저장/재실행 절차로 확인한다.
- 다음 작업 — 3.5단계 계획에 따라 후보 추가 API·영속 저장부터 구현하고 UI 추가/취소 모드를 연결한다. 로컬 git add는 `.git/index.lock` 권한 오류로 실패했으며 원격 커밋/푸시는 완료되지 않았다. 재개 시 권한 또는 사용 가능한 GitHub 연결 경로를 확인한다.

### 2026-11-22 (KST) · Codex · Phase 3 커밋·푸시 검토

- 단계·상태 — 사용자가 구현 마무리로 판단하고 커밋·푸시를 명시적으로 요청했다. 코드·문서 36개를 검토하고 최종 로컬 검증을 통과했다. 실모델 9건 추론·기존 UI 표시는 사용자 출력/스크린샷으로 확인했으며, 새 표 UI의 실제 저장·확정본 보존과 Ubuntu HTTP 포함 전체 검사는 별도 미확인 항목으로 유지한다.
- 변경 — `feat/phase-3`의 모델 어댑터·평균 결합·보수적 경계 보정·검수 제안 표·오프라인 평가·회귀 검사 및 문서를 함께 마감한다. 저장소 기존 형식에 맞춘 커밋 제목은 `feat: implement phase 3 emotion prelabeling`, 푸시 대상은 `origin`(`mikeyoo0405/SKKU_Project`)의 `feat/phase-3`다. `.claude/`·`gan-harness/`·모델 가중치·미디어·라벨 데이터·비밀 설정은 포함하지 않는다. 최종 커밋/원격 상태는 Git 기록과 푸시 결과로 확인한다.
- 검증 — Windows Python 3.14 비HTTP 전체 125/125 PASS(5.205초), Node UI/재생 12/12 PASS(79ms), JS 구문 2개·대상 Python 구문·36개 UTF-8/LF·비밀 패턴·diff 검사 PASS. Windows uv 부재로 Python 직접 실행. HTTP 5개는 앞서 확인된 Windows TestClient 제약으로 Ubuntu 검증 대기이며 이번에 반복하지 않았다.
- 다음 작업 — 커밋·푸시 후 Ubuntu에서 변경분을 보존하며 최신 브랜치 동기화, `uv run python -m unittest discover -v`와 `node --test test_review_ui.mjs test_boundary_playback.mjs` 및 실제 화면 저장 확인. 이후 합의한 팀원 워크플로우 비교를 진행한다.

### 2026-11-22 (KST) · Codex · 모델 점수 비교 표 UI

- 단계·상태 — 사용자 스크린샷에서 기존 모델 제안 표시를 확인했고 화면 정리 요청에 따라 평균/결합 제안·한국어 보류 사유·감정별 비교 표로 변경했다. 사람 확정 저장은 아직 미확인이다.
- 변경 — `feat/phase-3` 미커밋. `static/review.mjs`(표 view와 textContent 기반 DOM), `static/app.js`(렌더러 연결), `static/index.html`(section·캐시 버전), `static/style.css`(4열 표·제안 행/최고 점수 강조), `test_review_ui.mjs`, PLAN·인계 갱신. 대사/음성/평균 점수와 기타 질량은 그대로 표시하고 실패/누락은 대시로 표시한다. 기존 미커밋 변경과 모델·사람 라벨 데이터는 보존했다.
- 검증 — Windows Node UI/재생 12개 PASS(89ms), Python 3.14 `python -B -m unittest -q test_portability` 7개 PASS(0.062초), JS 2개 구문·변경 UI 5개 UTF-8/LF·diff PASS. DOM 계약 검사로 5행·4열·제안 행 강조·한국어 이유·문자열 안전 렌더링·블라인드/대사/경계 변경 시 숨김·사람 라벨 미선택 유지 확인. 실제 브라우저 새 레이아웃은 미실행(로컬 Playwright 없음, WSL 접근 제약). 화면 변경만 있어 모델 추론은 재실행하지 않았다.
- 다음 작업 — Ubuntu 복사본에 `static/app.js`, `static/review.mjs`, `static/index.html`, `static/style.css` 4개를 반영하고 Ctrl+Shift+R로 새로고침해 실제 좁은 화면과 저장 동선을 확인한다. 기존 추론 결과를 재사용하며 재추론은 필요 없다. Ubuntu에서 `uv run python -m unittest -v test_portability`와 `node --test test_review_ui.mjs test_boundary_playback.mjs`(테스트 파일도 동기화 시)를 실행한다.

### 2026-11-22 (KST) · Codex · Ubuntu 최종 모델 실추론 결과 확인

- 단계·상태 — 사용자 제공 실제 Ubuntu 출력으로 HowRU·emotion2vec+ base의 9건 추론과 평균 점수 생성 성공 확인. 결합 제안 4건·other 보류 5건, 모델 실행 실패 0건. 브라우저·사람 확정 저장과 독립 정답 기반 품질 평가는 대기.
- 변경 — `feat/phase-3` 미커밋. 이번에는 PLAN·PILOT_LOG·HANDOFF_LOG 문서만 갱신했다. 다른 도구의 최신 코드 리뷰 변경과 모든 미커밋 코드를 보존했다.
- 검증 — 사용자 전달 run_id `e7c5793886344b789c686dec307ec207`, input_errors 0, text ok 5/보류 4, audio ok 4/보류 5, combined ok 4/보류 5. 9건 평균 분포와 선택 감정/other 보류가 일치함을 출력으로 확인했다. 원본 결과 파일 직접 열람·추론 재실행은 하지 않았고 정확도로 해석하지 않았다. 문서 변경이므로 코드 검사는 재실행하지 않았다. UTF-8/LF·diff·최신 인계 5건 확인.
- 다음 작업 — 같은 모델 설정을 적용한 Ubuntu 터미널에서 `uv run uvicorn app.main:app --host 127.0.0.1 --port 8000` 실행, 배치 후보 검수에서 제안/점수 표시와 사람 확정 저장·보존 확인. Ubuntu `uv run python -m unittest discover -v`와 `node --test test_review_ui.mjs test_boundary_playback.mjs`도 확인한다.

### 2026-11-22 (KST) · Antigravity · 최종 모델 어댑터·평균 결합 코드 리뷰 및 보완

- 단계·상태 — `feat/phase-3` 최종 추가 기능(HowRU·emotion2vec+ base 어댑터, 50:50 평균 결합, 설정/연결 확인 CLI, UI 점수 표시) 코드 리뷰 완료 및 CRITICAL 2건·HIGH 1건·MEDIUM 2건 수정 완료 (모델 설치 후 Ubuntu 실연결·HTTP 5개 포함 130개 테스트 대기)
- 변경 — `feat/phase-3` 미커밋. `emolabel-youtube/app/emotion_scores.py`·`tools/configure_emotion.py`·`tools/scored_emotion.py`·`tools/check_emotion_connection.py`·`static/app.js`·`static/index.html`·`test_scored_emotion.py`·`emolabel-youtube/README.md`·[PLAN.md](../PLAN.md)·[docs/HANDOFF_LOG.md](HANDOFF_LOG.md) 수정: (1) `emotion2vec_plus_base` 실제 `tokens.txt`의 9번째 토큰 `<unk>`→`unknown` 매핑 추가(누락 시 실모델 전건 실패 방지, `<unk>`+`unknown` 중복 차단), (2) `configure_emotion.py`가 Linux venv `bin/python` 심볼릭 링크를 `resolve()`로 시스템 파이썬으로 풀어버리던 치명적 버그를 `os.path.abspath`로 수정 및 `kind` 검증 추가, (3) FunASR `numpy.ndarray`/`np.float32` 점수 리스트 정규화 및 `bool` 차단, (4) `validate_combined()`에서 `agreement-v1` 위조 `probabilities` 차단, (5) UI 캐시 무효화(`?v=emotion2`), `check_emotion_connection` 추론 전 기존 출력 선검사, README `mean_score`·동기화 파일 목록 보강. 이전 인계 1건을 `docs/HANDOFF_LOG.md`로 이동.
- 검증 — Windows Python 3.14 비HTTP 전체 **125/125 PASS (16.18s)**, Node **10/10 PASS (138ms)**, Python 41개 `py_compile`·JS 4개 `node --check` PASS, 변경·신규 프로젝트 파일 35개 전부 UTF-8/LF(`CRLF: 0`)·시크릿/개인경로 0건 확인. `test_review_api.py`(HTTP 5개)는 Windows 샌드박스 `TestClient` 제약, 실모델 로드·추론은 모델 미설치 및 WSL 미실행으로 NOT PERFORMED (WSL2 재현: `cd emolabel-youtube && uv run python -m unittest discover -v && node --test test_review_ui.mjs test_boundary_playback.mjs`).
- 다음 작업 — `feat/phase-3` 커밋·푸시(`.claude/`·`gan-harness/` 제외) → Ubuntu 동기화 및 전체 130개 테스트 확인 → 두 모델 설치 후 `tools.configure_emotion` → `tools.check_emotion_connection`으로 실모델 연결 검증

### 2026-11-22 (KST) · Codex · 최종 두 모델 어댑터·평균 결합 구현

- 단계·상태 — 합의한 최종 모델의 점수 계약·50:50 평균 결합·검수 UI 연결 구현 완료. C-SCORE(분포/매핑/평균/보류/오류/호환)와 코드 회귀 PASS. C-REAL(실제 모델 로드·추론·브라우저 검수)은 NOT PERFORMED: 사용자가 두 모델 미설치·추후 별도 설치 예정임을 확인했다. 설치·다운로드는 수행하지 않았다.
- 변경 — `feat/phase-3` 미커밋. 신규 `app/emotion_scores.py`, `tools/scored_emotion.py`, `tools/configure_emotion.py`, `tools/check_emotion_connection.py`, `test_scored_emotion.py`. 기존 `app/emotion.py`·`app/review.py`·`static/review.mjs`·`tools/evaluate_models.py`와 관련 Python/Node 검사, `.env.example`, 두 README, PLAN 갱신. 이전 도구의 미커밋 변경은 보존했다. 전용 어댑터는 외부 CPU 런타임·로컬 체크포인트·원격 코드 비허용, 기존 기본 결합은 agreement-v1 호환, 새 설정은 명시적 mean-v1이다. 원래 점수·model_revision·매핑 버전·other 질량을 보존하고 실패한 모델을 0점으로 대체하지 않는다. 설정 생성·연결 보고서 CLI는 기존 파일을 덮어쓰지 않는다.
- 검증 — Windows Python 3.14 비HTTP 전체 125개 PASS(5.217초), Node UI/재생 10개 PASS(99ms), Python 구문 41개·JS 구문 2개 PASS, 변경 파일 UTF-8/LF·자격 증명 패턴·diff 검사 PASS. 추가 revision 불일치 검증 후 `python -B -m unittest -q test_scored_emotion test_portability` 19개 재통과. HTTP 5개는 `test_review_api`를 자식 프로세스에서 실행했으나 Windows TestClient 대기로 15초 후 종료, Ubuntu 미실행. Windows에 uv·ruff·pyright가 없어 uv 명령 대신 Python으로 검사했고 lint·정적 타입·커버리지 수치는 미산출. WSL 조회는 E_ACCESSDENIED. 공식 FunASR 소스에서 출력이 softmax 점수임을 확인했고 이중 softmax를 적용하지 않았다. 모의 런타임 검사로 실제 모델 성능이나 연결 성공을 주장하지 않는다.
- 다음 작업 — 모델 설치 후 최신 코드를 Ubuntu에 동기화하고 README의 `tools.configure_emotion` → `tools.check_emotion_connection`(실제 mono 16kHz WAV와 같은 대사) → 고정 후보 배치/UI를 실행한다. Ubuntu `cd emolabel-youtube && uv run python -m unittest -v test_portability && uv run python -m unittest discover -v && node --test test_review_ui.mjs test_boundary_playback.mjs`로 HTTP 포함 전체 130개를 확인한다. 연결 보고서 VERIFIED와 실제 브라우저 표시/사람 확정본 보존 확인 후 기존 요청 범위에서 커밋·푸시한다.
### 2026-11-22 (KST) · Codex · 최종 모델 2개와 평균 결합 계획 확정

- 단계·상태 — 사용자가 최종 모델 선정을 위임했다. 대사 HowRU-KoELECTRA 분류 모델과 음성 emotion2vec+ base를 연결 대상으로 확정하고, 50:50 평균 사전 제안의 구현 범위를 기록했다. 어댑터 연결과 실추론은 아직 미완료다.
- 변경 — `feat/phase-3` 미커밋. 이번 변경은 `PLAN.md`와 `docs/HANDOFF_LOG.md`만 해당한다. 두 모델의 역할·공통 감정 매핑·other 보류·실패 처리·변경 예정 파일·완료 기준을 작성하고, 4단계 목표를 팀원 프로그램과의 워크플로우 비교로 바로잡았다. 기존 코드 변경은 보존했다.
- 검증 — 공식 모델 카드와 emotion2vec 공식 저장소에서 중립 포함 클래스와 전체 점수 출력을 확인했다. 문서 변경만 있어 코드 테스트는 재실행하지 않았다. UTF-8/LF·diff·최신 인계 5건 유지 검사. Ubuntu 실추론과 새 모델 CPU 지연/정확도는 미검증이며, 이전 세션의 WSL E_ACCESSDENIED가 해소되었는지는 이번에 재확인하지 않았다. 구현 후 Ubuntu `cd emolabel-youtube && uv run python -m unittest -v test_portability && uv run python -m unittest discover -v && node --test test_review_ui.mjs test_boundary_playback.mjs` 및 README에 추가할 실모델 실행 절차로 검증한다.
- 다음 작업 — 두 모델 전용 로컬 어댑터부터 구현하고 평균 결합·UI를 연결한다. 실제 연결 확인 후 커밋·푸시하며, 모델 파일과 라벨링 데이터는 Git에서 제외한다.
### 2026-11-22 (KST) · Antigravity · 검증 루프 점검 및 Living Docs 문서 정비

- 단계·상태 — `feat/phase-3` 커밋 전 `verification-loop` 6단계 점검 완료(개인 경로 4건 제거) 및 `living-docs-governance` 4역할(Constitution/Map/Status/History) 문서 정비 완료.
- 변경 — `feat/phase-3` 미커밋. [AGENTS.md](../AGENTS.md)(문서 역할·읽기 순서 추가), [README.md](../README.md)(루트 프로젝트 Map·모듈 점프 테이블 신설), [PLAN.md](../PLAN.md)(Status 역할로 경량화, 기각·보류 Delete-Zone 추가), [docs/PILOT_LOG.md](PILOT_LOG.md)(1–3차 파일럿 및 R0–R3 상세 근거 이관), [docs/HANDOFF_LOG.md](HANDOFF_LOG.md)(이전 인계 1건 이동), [emolabel-youtube/README.md](../emolabel-youtube/README.md)(Windows 개인 경로 플레이스홀더화). 코드 로직 변경 없음.
- 검증 — `verification-loop` 실행: Python 36개 `py_compile`·JS 4개 `node --check` PASS, Windows 비HTTP Python 111/111 PASS(14.08s), Node 9/9 PASS(73.6ms), 변경·신규 프로젝트 파일 전부 시크릿/개인경로 0건·UTF-8/LF(`CRLF: 0`) 확인. `test_review_api.py`(HTTP 5개) 및 Ubuntu 실환경 검증은 미실행(WSL2 재현: `cd emolabel-youtube && uv run python -m unittest discover -v && node --test test_review_ui.mjs test_boundary_playback.mjs`).
- 다음 작업 — `conventional-commit-review`로 `feat/phase-3` 변경분(`.claude/`·`gan-harness/` 제외)을 커밋·푸시한 뒤 Ubuntu에서 동기화 및 116개 전체 테스트 실행, 이후 `pilot-v3.json` 고정 및 독립 정답 기반 R3 평가 진행
### 2026-11-22 (KST) · Antigravity · R0–R3 코드 리뷰 및 보완

- 단계·상태 — `feat/phase-3`의 R0–R3 변경분 코드 리뷰 완료 및 HIGH 2건·MEDIUM 3건 수정·회귀 테스트 6건 추가 완료 (Ubuntu 동기화 및 전체 테스트·R3 독립 정답 평가 대기)
- 변경 — `feat/phase-3` 미커밋. `emolabel-youtube/app/candidates.py`·`app/emotion.py`·`tools/local_emotion.py`·`tools/reproduce_boundaries.py`·`tools/evaluate_models.py`, 관련 테스트 5개(`test_candidates.py`·`test_emotion.py`·`test_local_emotion.py`·`test_boundary_reproduction.py`·`test_evaluate_models.py`), [PLAN.md](../PLAN.md)·[docs/HANDOFF_LOG.md](HANDOFF_LOG.md) 수정: (1) 소수점 밀리초 원본 길이에서 `end_ms`가 `duration`을 초과 반올림하는 경계 오류(`math.floor(duration*1000+1e-9)`) 및 R0 `audit()`의 ms 반올림·`duration_s` 타입/유한성 검증 보완, (2) 0.08초 여백이 비겹침 인접 ASR이나 `MAX_CLIP_S`에 걸려 정상 꼬리 확장을 취소하던 문제 수정, (3) `local_emotion.py`·`emotion.py`의 비문자열 `wav_path` 및 `bool` `temperature` 입력 검증 오분류 수정, (4) `reproduce_boundaries.py` `crossing()`의 `BOUNDARY_EPSILON_S` 일치·WAV `framerate<=0` 방어, (5) R0·R3 보고서 `json.dumps` 선직렬화(실패 시 빈 파일 차단)·`FileExistsError` 안내 통일 및 `evaluate_models.py` `bool` 스키마 버전·빈 예측 방어. 이전 인계 1건을 `docs/HANDOFF_LOG.md`로 이동.
- 검증 — Windows Python 3.14 비HTTP 전체 검사 **111개 통과**(`test_batch_youtube` 6개 + 나머지 12개 모듈 105개), `node --test emolabel-youtube/test_review_ui.mjs emolabel-youtube/test_boundary_playback.mjs` **9개 통과 (71.5ms)**, 수정 파일 12개 모두 UTF-8/LF(`CRLF: 0`) 확인. `test_review_api.py`(HTTP 5개)는 Windows 샌드박스 FastAPI `TestClient` 제약으로 미실행, Ubuntu 실미디어·청취·모델 추론은 미실행 (WSL2 재현: README 「R1 오류 진단·R2 경계 보정 적용」 동기화 후 `cd emolabel-youtube && uv run python -m unittest discover -v && node --test test_review_ui.mjs test_boundary_playback.mjs`).
- 다음 작업 — `feat/phase-3` 커밋·푸시(`.claude/`·`gan-harness/` 제외) 후 Ubuntu 동기화 및 전체 테스트(`test_review_api.py` 포함 116개) 실행, 새 `pilot-v3.json` 고정 및 독립 정답 기반 R3 평가 진행
### 2026-11-22 (KST) · Codex · R1 오류 진단·R2 제한적 경계 보정

- 단계·상태 — 사용자 요청한 R1·R2 구현 및 Windows 검증 완료. 선정 v3의 짧은 VAD 경계 확장, 인접 ASR 침범 제한, 긴 경계 검수 경고 및 오류 단계·코드 기록을 추가했다. 후속 사용자 실제 청취에서 이전 대비 잘림 감소를 확인했다. 개별 사례 전후 결과·정량 감소율·작업 시간 및 Ubuntu 자동 검사 결과는 대기다.
- 변경 — `feat/phase-3` 미커밋. `app/emotion.py`, `tools/local_emotion.py`, `app/candidates.py`, `tools/run_batch.py`, `tools/reproduce_boundaries.py`, 관련 Python 검사 6개와 README·PLAN·인계 로그 변경. 선정 버전 갱신 전에 이전 state를 `selection-history/`에 보존하고 원래 ASR ID·경계·보정량을 기록한다. 기존 사람 확정 라벨·추론 기록·미디어와 감정 결합 규칙은 유지했다.
- 검증 — Windows Python 3.14 비HTTP 전체 검사 105개 통과(3.876초), 보정 이력 진단 표시 추가 후 `python -B -m unittest -v test_boundary_reproduction` 6개 재검증, `node --test test_review_ui.mjs test_boundary_playback.mjs` 9개 통과. 잘못된 입력·로딩/의존성·전처리·추론·출력·프로세스 종료/타임아웃과 민감 문자열 미노출을 확인했다. 기존 Windows TestClient 초기화 제약으로 HTTP 5개 미실행, WSL 직접 접근 제한 때문에 Ubuntu 실미디어·모델/청취 검증 미실행. UTF-8/LF·diff 검사 확인.
- 다음 작업 — README 「R1 오류 진단·R2 경계 보정 적용」의 파일 동기화 후 Ubuntu에서 `uv run python -m unittest discover -v`, `node --test test_review_ui.mjs test_boundary_playback.mjs`, `uv run python -m tools.run_batch --video-workers 1`, `uv run python -m tools.reproduce_boundaries --project . --output data/evaluations/r0-v3.json` 실행. 실패 원본 ASR ID와 정상 대조를 비교 청취한다. 새 manifest는 `pilot-v3.json`으로 내보내고 기존 실험과 분리한다. 경고만 추가되거나 후보가 제외된 사례는 잘림 해결로 집계하지 않는다.
- 추가 확인 — 사용자가 “예전에 비해 잘림 현상이 눈에 띄게 줄어들었어”라고 보고했다. 정성적 개선만 완료 처리하고 개별 사례 해결·모델 정확도·시간 절감으로 확대 해석하지 않았다. 다음 실험은 새 경계의 후보 목록을 고정한 뒤 모델 제안을 가린 사람 감정 정답을 작성하는 R3 평가다.

### 2026-11-22 (KST) · Codex · R0 재현·R3 모델 비교 도구 및 사용자 Ubuntu 결과 확인

- 단계·상태 — 요청된 R0·R3 범위의 진단/평가 도구와 합성 재현 검사 완료. 사용자 제공 기존 Ubuntu 보고서에서 실제 후보 9건(의심 6·대조 3), 모델 세 조합의 커버리지·보류율 확인. 추가 사용자 청취로 1번 뒤 잘림·2번 정상을 확인했다. 원인 단계 확정과 독립 정답 기반 성능 비교는 대기다.
- 변경 — `feat/phase-3` 미커밋. 신규 `tools/reproduce_boundaries.py`, `tools/evaluate_models.py`, `test_boundary_reproduction.py`, `test_boundary_playback.mjs`, `test_evaluate_models.py`; README 실행/정답 형식 안내, PLAN 진행 결과 및 인계 로그 갱신. 앱 경계·추론·결합 로직과 기존 사람 라벨은 변경하지 않았다. 연구 자료 요약과 기준선 해시는 Git 제외 `data/evaluations/`에 기록했다.
- 검증 — Windows Python 3.14 관련 검사 50개 통과, Node UI/경계 검사 9개 통과. UTF-8/LF 및 diff 검사 확인. 직접 WSL 조회는 E_ACCESSDENIED, 공유 경로 읽기도 실패했다. 사용자의 재실행은 보고서 파일 존재로 저장을 거부했으며 기존 보고서 cat 결과를 근거로 집계했다(최신성 직접 검증 아님). 새 파일명으로 Ubuntu에서 `PYTHONPATH=/mnt/c/Users/<windows-user>/Desktop/산학/emolabel-youtube uv run python -m tools.reproduce_boundaries --project "$PWD" --output data/evaluations/r0-v2.json`, `PYTHONPATH=/mnt/c/Users/<windows-user>/Desktop/산학/emolabel-youtube uv run python -m tools.evaluate_models --manifest data/workspace/pilot.json --runs data/workspace/emotion-runs/* --output data/evaluations/r3-coverage-v2.json`로 재현 가능. Ubuntu 전체 검사 및 실제 브라우저 검증은 미실행이다.
- 다음 작업 — 실패 `6yrnO_6Hl0I`의 50.78초 이후 원본 발화 종료 위치와 추출본/브라우저 동작을 비교해 원인을 확정한다. 정상 대조 `DGwuCn-EDfQ` 11–16초를 보존한다. 독립 정답 확보 후 README의 `--gold` 평가를 실행한다. 커버리지로 모델 정확도를 판단하지 않는다.
- 추가 확인 — 사용자 응답으로 뒤 잘림/정상 기준을 갱신했다.
### 2026-11-22 (KST) · Codex · 구현 전 실패 평가·수정 계획 초안

- 단계·상태 — 파일럿 후 수정 계획 검토 초안 작성 완료, 구현 승인 대기. B01 대사 잘림, B02 긴 무음, M01 높은 결합 보류율, D01 오류 진단 부족의 근거와 미검증 항목을 구분했다.
- 변경 — `feat/phase-3` 미커밋. 이번 작업은 `PLAN.md`의 현재 단계·수정 계획·인계 기록과 `docs/HANDOFF_LOG.md`의 이전 인계 이동만 변경했다. 기존 코드·미커밋 변경은 보존했다.
- 검증 — Windows에서 계획을 현재 후보 선정·로컬 실행기 코드 및 기존 파일럿 기록과 대조하고 문서 diff·UTF-8/LF·인계 최근 5건 유지를 확인했다. 문서만 수정하여 자동 테스트는 재실행하지 않았다. WSL2 실미디어·모델 검증은 이번 계획 작성 범위 밖으로 미실행; 구현 후 Ubuntu `emolabel-youtube/`에서 `uv run python -m unittest -v test_portability`, `uv run python -m unittest discover -v`, `node --test test_review_ui.mjs`와 README 파일럿 절차로 검증한다.
- 다음 작업 — 사용자가 「구현 전 검토할 결정」의 수정 순서·범위와 평가 기준을 검토한 뒤 R0 재현부터 착수한다. 결합 규칙 교체는 독립 정답 비교 결과 검토 후 별도 승인한다.
### 2026-11-22 (KST) · Antigravity · 한국어 음성 모델 연결 및 1–3차 실모델 파일럿 정리

- 단계·상태 — `feat/phase-3`에서 `Wav2Vec2ForSpeechClassification` 커스텀 헤드 로더 구현 및 Ubuntu 1–3차 실모델 파일럿(동일 `pilot.json` 9건) 완료. 오프더셸프 음성 모델 3종의 쇼츠 도메인 편향 확인, 독립 정답 작성 및 결합 규칙 재설계 대기.
- 변경 — `feat/phase-3`, `emolabel-youtube/tools/local_emotion.py`·`test_local_emotion.py`·[PLAN.md](../PLAN.md)·[docs/HANDOFF_LOG.md](HANDOFF_LOG.md) 수정(미커밋): `Wav2Vec2ForSpeechClassification` 오프라인 로더 및 누락 가중치 검증 추가(Ubuntu 동기화 완료), 1–3차 파일럿 결과(`b693b114...`, `c3eea505...`, `680de205...`) 기록. Ubuntu `.env`는 현재 3차 조합(`EXAONE-3.5-7.8B` + `wav2vec2-xlsr-korean-ser-rebalance`) 연결 상태.
- 검증 — Windows `test_local_emotion` 5개 통과(LF 확인), WSL2 Ubuntu `uv run python -m unittest discover -v` **82개 전체 통과 (0.739s)**, 1–3차 `--candidates` 모두 `input_errors: 0` 확인.
- 다음 작업 — 파일럿 9건(또는 개발 표본)에 독립 정답을 작성한 뒤, 4단계 평가 스크립트(`tools/evaluate_models.py`) 또는 표본 비교로 `EXAONE-7.8B` 대사 단독 vs 음성 3종 단독 vs 결합 규칙(보류율 ≤ 20%)을 결정하고 `feat/phase-3` 커밋(`.claude/`·`gan-harness/` 제외)

### 2026-11-22 (KST) · Antigravity · 3단계 감정 사전 분류 코드 리뷰 및 보완

- 단계·상태 — 3단계 코드 리뷰 완료 및 HIGH 1건·MEDIUM 2건 수정 완료 (Ubuntu 실모델 파일럿 대기)
- 변경 — `feat/phase-2`, `tools/local_emotion.py`·`app/emotion.py`·`test_local_emotion.py` 수정(미커밋): 음성 체크포인트 `id2label` 문자열 키(`"0"`)·누락 방어 및 `getattr(extractor, "sampling_rate", None)` 안전 조회, LLM 빈 `content: None` 응답 방어, POSIX `os.killpg` `PermissionError` 시 `process.kill()` 폴백 및 타임아웃 후 `reader.join(timeout=1)` 파이프 정리, `combined.elapsed_s` 소수 6자리 반올림. 이전 인계 1건을 `docs/HANDOFF_LOG.md`로 이동.
- 검증 — Windows 호스트에서 Python 비-HTTP 단위 테스트 76개(수정 후 3단계·portability 28개 재실행 포함) 통과, `node --test emolabel-youtube/test_review_ui.mjs` 8개 통과(70ms), 수정 파일 모두 LF 확인. `test_review_api.py`(5개, FastAPI `TestClient`)는 Windows 샌드박스 제약, 실모델 추론은 WSL2 Ubuntu 미접근으로 미실행 (WSL2 재현: `cd emolabel-youtube && uv run python -m unittest discover -v && node --test test_review_ui.mjs`).
- 다음 작업 — Ubuntu에서 전체 테스트 통과 확인 후 `README.md`의 `--export-candidates` / `--candidates` 절차로 실모델 파일럿을 실행하고, Phase 3 커밋 전 브랜치 정리(`feat/phase-2` 유지 또는 `feat/phase-3` 분리, `.claude/`·`gan-harness/` 제외)

### 2026-11-21 (KST) · Codex · 3단계 감정 사전 분류

- 단계·상태 — 고정 후보에 대해 대사·음성 모델의 유효 라벨이 일치할 때만 결합한다. 모델 대역 검증 완료, Ubuntu 실모델 비교 대기.
- 변경 — `feat/phase-2` 미커밋. 배치 CLI, 모델 실행기·설정 예시, 검수 API·화면, 실행 안내와 회귀 검사 갱신. 수정 대사 실험은 별도 보관한다.
- 검증 — Windows Python 비HTTP 테스트 76개와 Node UI 테스트 8개 통과. 합성 입력으로 추론→검수→사람 확정→재추론 후 확정본 보존 확인. Windows TestClient는 이벤트 루프의 로컬 소켓 초기화에서 대기해 HTTP 테스트 5건 미확인. WSL 접근 제한(E_ACCESSDENIED)으로 실제 가중치·메모리·품질 미측정.
- 다음 작업 — Ubuntu의 `emolabel-youtube/`에서 `uv run python -m unittest discover -v`와 `node --test test_review_ui.mjs` 실행 후 README의 고정 후보 내보내기·재사용 명령으로 실모델 파일럿을 진행한다.

### 2026-11-21 (KST) · Codex · 원본 이어듣기 종료 수정

- 단계·상태 — 선택 구간 자동 정지는 Ubuntu 실제 후보에서 사용자 확인 완료. 일부 대사가 잘리는 현상은 후속 검수에서 관찰한다.
- 변경 — feat/phase-2 미커밋. static/app.js, static/review.mjs, static/index.html, test_review_ui.mjs에 종료 처리 및 회귀 검사를 반영했다.
- 검증 — Windows에서 Node UI 7개와 Python test_portability 7개 통과. pause → finish에서 원본 재생은 반복 0회, 선택 구간 반복은 1회 확인. WSL 접근 제한(E_ACCESSDENIED)으로 Ubuntu 실영상 미확인.
- 다음 작업 — 3단계에서 Ubuntu의 로컬 감정 모델과 실행 환경을 확인한다.

### 2026-11-21 (KST) · Antigravity · 선택 구간 자동 정지 코드 리뷰 및 보완

- 단계·상태 — 2단계 보완(선택 구간 자동 정지) 코드 리뷰 및 엣지 케이스 수정 완료 (Ubuntu 실청취 대기)
- 변경 — `feat/phase-2`, `static/review.mjs`·`static/app.js`·`test_review_ui.mjs`·`tools/run_batch.py` 수정(미커밋): `<video>` 서브프레임 양자화 오차(±20ms 허용)로 인한 시작점 반복 탐색·끝점 재재생 멈춤 방지(`boundaryEpsilon`), `pause`/`finish` 시 `playingOriginal` 자동 초기화, `playRegionBtn` 비활성·`state.ws` 가드, `no_candidates` 캐시 재사용에도 `selection_version` 일치 검사 추가, JS 회귀 테스트 1건 추가. 이전 인계 1건을 `docs/HANDOFF_LOG.md`로 이동.
- 검증 — Windows 호스트에서 `node --test emolabel-youtube/test_review_ui.mjs` 6개 통과(64ms), `app.js` ESM 구문 정상, Python 비-HTTP 단위 테스트 55개 통과(1.365s), 수정 파일 모두 LF 확인. `test_review_api.py`(5개, FastAPI `TestClient`)는 Windows 샌드박스 제약, WSL2 Ubuntu 실청취는 환경 미접근으로 미실행 (WSL2 재현: `cd emolabel-youtube && uv run python -m unittest discover -v && node --test test_review_ui.mjs && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000`).
- 다음 작업 — Ubuntu 복사본을 갱신해 전체 테스트(Python 60개·JS 6개) 실행 후 실제 파일럿 후보의 끝말 정지·원본 이어듣기·검수 작업 시간을 확인하고 `feat/phase-2` 커밋(`.claude/`, `gan-harness/` 제외)

### 2026-11-21 (KST) · Codex · 선택 구간 자동 정지

- 단계·상태 — 선택 구간 정지·재재생 및 원본 이어듣기 구현. 실제 청취 대기.
- 변경 — `feat/phase-2` 미커밋. 검수 화면 재생 제어, 버튼 및 회귀 테스트 추가.
- 검증 — Windows에서 `node --test test_review_ui.mjs` 5개, `python -B -m unittest -v test_portability` 7개 통과. Chrome 합성 음원 테스트는 응답 대기로 중단. WSL 접근 오류(E_ACCESSDENIED)로 Ubuntu 미실행.
- 다음 작업 — Ubuntu 복사본을 갱신한 뒤 `emolabel-youtube/`에서 `uv run python -m unittest -v test_portability` 및 `node --test test_review_ui.mjs` 실행. 기존 후보를 열어 끝점 정지와 원본 이어듣기를 청취한다.

### 2026-11-21 (KST) · Codex · Phase 2 최종 리뷰 수정

- 단계·상태 — 완료 캐시의 원본 메타데이터 복구 및 SQLite 잠금 오류 분류를 수정했다.
- 변경 — `feat/phase-2` 미커밋. 배치, 수동·배치 검수 오류 처리, 회귀 테스트를 갱신했다.
- 검증 — Windows에서 Python 58개 및 JavaScript 4개 통과. 메타데이터 누락·손상 후 재실행과 채택, 잠금·읽기 전용·I/O 오류 응답을 확인했다.
- 다음 작업 — Ubuntu에서 배치를 재실행해 파일럿 영상의 경계·대사와 검수 시간을 확인한다.

### 2026-11-21 (KST) · Antigravity · Phase 2 코드 리뷰 및 보완

- 단계·상태 — 2단계 코드 리뷰 완료 및 HIGH 2건·MEDIUM 4건·LOW 1건 수정 완료 (실측 완료 기준 대기)
- 변경 — `feat/phase-2`, `app/review.py`·`app/review_api.py`·`app/store.py`·`app/main.py`·`tools/run_batch.py`·`static/review.mjs`·`static/app.js` 및 테스트 3건 수정(미커밋): 파형/영상 오류 시 보류·제외 저장 허용 및 타이머 초기화, 체크박스·라디오 포커스 시 단축키 유지(`isShortcutBlocked`), `rejected` 시 경계 이동·불필요한 FFmpeg 추출 차단, `/static/review.mjs` 캐시 무효화, SQLite 락 경합 409 변환·`source.json` 누락 방어·`fps` 전달, `seconds` 누락 방어. 이전 인계 1건을 `docs/HANDOFF_LOG.md`로 이동.
- 검증 — Windows 호스트에서 Python 비-HTTP 단위 테스트 52개 통과(1.495s), `node --test emolabel-youtube/test_review_ui.mjs` 4개 통과(97ms), 수정 파일 모두 LF 확인. `test_review_api.py`(FastAPI `TestClient`)는 Windows 샌드박스 제약으로 미완료, WSL2 Ubuntu 실측 미실행 (WSL2 재현: `cd emolabel-youtube && uv run python -m unittest discover -v && node --test test_review_ui.mjs && uv run python -m tools.run_batch --video-workers 1`).
- 다음 작업 — WSL2 Ubuntu에서 전체 테스트(`test_review_api.py` 포함) 통과 확인 후 파일럿 실측(재생·경계 수정·네 상태 저장/철회·기본값 확인·작업 시간)을 마치고 `feat/phase-2` 커밋(`gan-harness/` 제외 주의)

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
- 변경 — `main`, [AGENTS.md](../AGENTS.md)·[PLAN.md](../PLAN.md) 수정(미커밋): 협업·교차 환경·인계 복구 규칙 보완, 데이터·상태 계약 신설, 0단계 합의 결정표 임시 기본값 및 `pilot_v1`(`DGwuCn-EDfQ`, `mKwJDo0YTkM`, `6yrnO_6Hl0I`) 기록, 1–4단계 운영 가드레일 반영
- 검증 — 문서 리뷰 및 수정만 수행(코드 변경 없음, `test_portability` 미실행)
- 다음 작업 — 0단계 임시 기본값·변경 예정 파일 최종 확인 후, 1단계 수동 기준선 측정(`pilot_v1`) 및 공통 수집·후보 배치 구현 시작
