# SKKU_Project (EmoLabel)

한국어 YouTube 숏츠에서 발화 후보를 추출하고 로컬 모델 사전 제안과 사람 검수로 감정 라벨(`neutral`, `angry`, `sad`, `happy`)을 확정하는 프로젝트입니다. 실제 애플리케이션 구현은 `emolabel-youtube/`에 있으며, 첫 파일럿 환경은 **WSL2 Ubuntu · Python 3.12 · FFmpeg · CPU**입니다.

---

## 문서 역할 맵 (Living Docs Governance)

각 사실은 하나의 정본 문서에만 기록하고 다른 문서에서는 링크로 참조합니다.

| 역할 | 정본 문서 | 담당 내용 |
| --- | --- | --- |
| **Constitution (규칙)** | [AGENTS.md](AGENTS.md) | Codex·Antigravity 협업 규칙, 편집(UTF-8/LF)·검증·인계·비밀 보호 원칙 |
| **Map (지도·런북)** | [README.md](README.md) (본 문서), [emolabel-youtube/README.md](emolabel-youtube/README.md) | 저장소 구조·모듈 소유 파일·점프 테이블, Ubuntu/Mac 설치·배치·평가 실행 안내 |
| **Status (현황)** | [PLAN.md](PLAN.md) | 현재 단계, 합의 임계값, 데이터·상태 계약, 열린 체크리스트, 기각·보류 목록(Delete-Zone), 최신 인계 5건 |
| **History (이력)** | [docs/HANDOFF_LOG.md](docs/HANDOFF_LOG.md), [docs/PILOT_LOG.md](docs/PILOT_LOG.md) | 과거 세션 인계 이력, 1–3차 파일럿 및 R0–R3 상세 실행·평가 기록 |
| **Domain Guides** | [ANNOTATION_GUIDE.md](emolabel-youtube/docs/ANNOTATION_GUIDE.md), [DATASET_CARD.md](emolabel-youtube/docs/DATASET_CARD.md), [GPT_ASSISTANCE.md](emolabel-youtube/docs/GPT_ASSISTANCE.md) | 감정 라벨링 기준, 데이터셋 설명, 선택적 GPT 보조 안내 |

---

## 코드 모듈 점프 테이블 (`emolabel-youtube/`)

| 하려는 작업 | 소유 파일 | 관련 테스트 |
| --- | --- | --- |
| 설정·환경 변수·로컬 모델 스펙 | `app/config.py`, `.env.example` | `test_portability.py`, `test_emotion.py` |
| YouTube 다운로드·길이 상한·ID 파싱 | `app/youtube.py` | `test_batch_youtube.py` |
| ASR(Linux faster-whisper / Mac MLX)·VAD·QC·성별 | `app/asr.py`, `app/asr_faster.py`, `app/qc.py`, `app/gender.py` | `test_portability.py`, `test_candidates.py` |
| 미디어 자르기·16 kHz WAV 추출·후보 미디어 캐시 | `app/media.py`, `app/candidate_media.py` | `test_batch.py`, `test_boundary_reproduction.py` |
| 후보 선정(선정 v3, 경계 보정·플래그·순위) | `app/candidates.py` | `test_candidates.py`, `test_boundary_reproduction.py` |
| URL 배치 실행·락·캐시·선정 이력 보존 | `tools/run_batch.py` | `test_batch.py`, `test_emotion_batch.py` |
| 로컬 감정 모델·오류 진단(R1)·평균 결합·연결 확인 | `app/emotion.py`, `app/emotion_scores.py`, `tools/local_emotion.py`, `tools/scored_emotion.py`, `tools/configure_emotion.py`, `tools/check_emotion_connection.py`, `tools/emotion_batch.py` | `test_emotion.py`, `test_local_emotion.py`, `test_scored_emotion.py`, `test_emotion_batch.py` |
| 검수 큐·수동 구간 추가·원자적 확정 저장·철회 격리·API | `app/review.py`, `app/review_api.py`, `app/store.py`, `app/schemas.py`, `app/main.py` | `test_review.py`, `test_manual_candidates.py`, `test_review_api.py`, `test_emotion_review.py`, `test_manual.py` |
| 웹 검수 UI·파형·선택 구간 자동 정지·타이머 | `static/index.html`, `static/app.js`, `static/review.mjs`, `static/style.css` | `test_review_ui.mjs`, `test_boundary_playback.mjs` |
| R0 경계 진단 · R3 오프라인 모델 평가 · 데이터셋 집계 | `tools/reproduce_boundaries.py`, `tools/evaluate_models.py`, `tools/dataset_status.py` | `test_boundary_reproduction.py`, `test_evaluate_models.py`, `test_dataset_status.py` |

---

## 기본 검증 명령

설치·실행 상세 절차는 [emolabel-youtube/README.md](emolabel-youtube/README.md)를 따릅니다.

```bash
# WSL2 Ubuntu (emolabel-youtube/ 기준 — HTTP 테스트 포함 전체 실행)
uv run python -m unittest -v test_portability
uv run python -m unittest discover -v
node --test test_review_ui.mjs test_boundary_playback.mjs
```

- **Git·교차 환경 원칙**: 편집·커밋·푸시는 Windows 저장소에서 진행하고, Ubuntu 실행 복사본은 `git fetch origin && git checkout -B <branch> origin/<branch>`로 동기화합니다(`.env`, `data/`, `.venv/`, 외부 모델 디렉터리는 Git 제외).
- **커밋 제외 주의**: 루트의 로컬 에이전트 작업 폴더(`.claude/`, `gan-harness/`)와 연구 데이터·모델 가중치는 Git에 올리지 않습니다.
