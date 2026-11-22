# EmoLabel 자동화 구축 계획 (Status)

- 작성일 2026-11-21 (KST) · 문서 역할: **Status** (현황·합의 기준·열린 체크리스트·기각 목록·최신 인계 5건)
- 현재 단계 · `feat/phase-3`의 모델 연결·50:50 평균 결합·점수 비교 표 및 **3.5단계 · 수동 구간 추가(전사 보조 포함)** 구현과 ECC 3단계 점검·코드 리뷰(Windows 비HTTP Python 142개·Node 13개 PASS) 완료. 사용자 제공 Ubuntu 출력으로 두 모델의 실제 후보 9건 추론·평균 점수 생성과 기존 화면 표시를 확인했다(독립 정답 기반 정확도·새 표 UI 및 수동 추가/전사 보조의 실제 Ubuntu 브라우저 저장·HTTP 6개 테스트는 미확인). 커밋·푸시는 사용자 GATE 2 확인 후 진행한다. 4단계의 팀원 프로그램 비교 목적은 유지한다.
- 관련 정본 문서 · 규칙 [AGENTS.md](AGENTS.md), 코드 지도 [README.md](README.md), 실행 안내 [emolabel-youtube/README.md](emolabel-youtube/README.md), 상세 이력 [docs/PILOT_LOG.md](docs/PILOT_LOG.md)·[docs/HANDOFF_LOG.md](docs/HANDOFF_LOG.md), [라벨링 가이드](emolabel-youtube/docs/ANNOTATION_GUIDE.md)

## 목표와 구현 원칙

팀은 한국어 발화 영상만 입력 목록에 등록하고, 영상 수집, 후보 선정, 검수, 저장을 하나의 파이프라인으로 사용한다. 입력 목록을 준비할 때 언어를 확인한다. 팀원별 모델은 감정 사전 분류에만 연결한다. 사람은 영상·원음을 확인하고 발화 경계, 발화자, 대사, 감정, 채택 여부를 확정한다. 모델의 제안은 `auto_accept: false`로 저장한다.

분류 품질이 팀 합의 기준을 충족한 모델 중 **최종 채택 클립 1개당 사람 작업 시간**을 가장 많이 줄인 구성을 우선한다. 첫 파일럿은 갤럭시북 6의 WSL2 Ubuntu, Python 3.12, FFmpeg, CPU에서 진행한다. 새 감정 모델은 Ubuntu에서 별도로 준비한 로컬 모델과 런타임을 설정으로 연결한다. 신규 Hugging Face 런타임 의존성이나 토큰 요구는 추가하지 않는다. 기존 Transformers 및 토큰이 필요한 화자 분리 경로는 준비된 환경의 참조 실험으로만 기록한다.

## 현재 코드와 구축 범위

모듈별 소유 파일과 테스트 매핑은 [README.md](README.md)를 기준으로 확인한다.

| 단계 | 현재 확인한 구현 | 남은 검증 / 추가 작업 |
| --- | --- | --- |
| 영상 수집 | URL 목록 배치, 영상 ID 중복 제거, 180초 상한, 진행·실패 기록과 재시도 구현 | 수동 기준선 측정, worker 1·2 실측 비교 |
| 후보 선정 | 선정 v3: 원래 ASR 경계를 보존하고 짧은 VAD 경계 확장(≤ 0.5초, 0.08초 여백 클램프)·긴 발화 경고, 발화 비율·여백·미해결 경계 위험으로 최대 3개 선정 | Ubuntu 개별 사례 전후 청취·감소율 확인 |
| 감정 사전 분류 | `prelabels.json` 초안, R1 진단, 기존 엄격 일치 호환, 확정한 두 모델 어댑터·50:50 평균 결합·점수 표시 구현 | 모델 설치 후 Ubuntu 실추론·연결 보고서·브라우저 확인 |
| 사람 검수 | 후보별 파형·재생·선택 구간 자동 정지, 검수 큐, 사유, 단축키, 독립 검수, 유휴 제외 타이머 | 파일럿 4개 상태 저장·철회 및 작업 시간 실측 |
| 수동 구간 추가 (3.5단계) | 원본 새 구간 선택·독립 `pending` 후보 추가(`reviews.json` 영속)·출처/초안 보호·무후보 URL 경로·기존 검수/저장 연결 구현 및 자동 검사 통과 | Ubuntu 실제 브라우저 추가→재생→저장→재실행 보존 및 HTTP 테스트 확인 |
| 확정 저장·평가 | 작업 공간/확정 출력 분리, 경계 수정 이력·철회 격리·재실행 보호, R0·R3 오프라인 진단·평가 도구 구현 | 기존 자료와 함께 Ubuntu 검증, 독립 정답 기반 최종 비교 |

## 데이터·상태 계약 (구현 공통 기준)

- **디렉터리 경계**
  - 작업 공간(`data/workspace/`): 배치 실행 상태, 후보 목록, `selection-history/`, 모델별 사전 분류 결과(`emotion-runs/`), 보류·제외·대체(`superseded`) 이력, 임시 파일
  - 최종 확정 출력(`data/output/{clip_id}/`): 사람이 채택한 클립만 저장 (`clip.mp4`, `audio_16k.wav`, `prelabels.json`, `annotation.json`). 기존 확정본 경로와 형식은 그대로 읽을 수 있게 유지한다.
- **후보 상태 전이**
  - 상태값: `pending` → `accepted` | `accepted_edited` | `hold` | `rejected` | `superseded`
  - 경계를 수정하면 기존 ID 규칙으로 새 `clip_id`를 만들고 원래 후보 ID를 연결한다. 원래 후보는 `superseded`로 전환해 후보 수·채택률 중복 집계를 막는다.
  - 이미 확정(`accepted` / `accepted_edited`)되어 `data/output/`에 들어간 클립을 추후 `hold`나 `rejected`로 변경하면, 확정 출력에서 격리·제거하고 변경 이력을 작업 공간에 남긴다.
- **실행 데이터 보호**
  - 확정본은 `clip.mp4`·`audio_16k.wav` 작성을 완료한 뒤 `annotation.json`을 마지막에 임시 파일에서 원자적으로 교체한다. 필요한 미디어와 `annotation.json`이 모두 있는 경우에만 완료로 집계한다.
  - `.part`·`.tmp` 등 부분 파일은 완료로 집계하지 않으며 재실행 시 정리 후 다시 시도한다. 기계 재실행은 사람 확정본(`annotation.json`)과 검수 이력을 덮어쓰지 않는다.
  - 선정 버전 변경 시 기존 `state.json`을 `selection-history/{sha256}.json`에 보존하고 새 manifest(`pilot-v3.json` 등)로 분리한다. 입력 지문이 다른 실행은 같은 모델 비교에 섞지 않는다.

## 0단계 · 공통 기준 합의

- [x] 워크플로우 원본과 현재 코드의 수집·추출·검수·저장 진입점을 확인했다.
- [x] 테스트 숏츠 3개를 선정해 아래 `pilot_v1`에 기록했다 (독백·화자 전환·BGM 유형은 1단계 실행 시 확인, 필요 시 1–2개 보강).
- [x] 라벨링 가이드를 기준으로 채택 / 수정 후 채택 / 보류 / 제외 조건, 품질 사유, 결과 기록 양식, 아래 합의 결정표의 임시 기본값을 파일럿 기준으로 합의했다. 모델 최소 품질 기준은 최종 비교 전에 팀에서 확정한다.
- [x] 구현 단계와 범위를 검토·합의했다.

### 0단계 합의 결정표

| 항목 | 합의값 (임시 기본값 · 파일럿 후 조정 가능) | 비고 |
| --- | --- | --- |
| 파일럿 URL 목록 버전 | `pilot_v1` (3개 숏츠) | 필요 시 1–2개 추가해 `pilot_v2`로 갱신 |
| 입력 영상 길이 상한 | `180초` | 장편 유입 시 CPU ASR 정지 방지 |
| 원본 영상당 최대 후보 수 | `3개` | VAD 발화 비율 ≥ 50% · 양 끝 여백 ≤ 1.5초 우선 → 미해결 경계 위험 없음 → 3–12초 우선 → 발화 비율↓, 끝 여백↑, 길이↓, 시작 시각↑ |
| 검수 타이머 유휴(idle) 컷오프 | `90초` | 무입력·탭 비활성 시 일시정지 |
| 모델 최소 품질 기준 | `Macro-F1 ≥ 0.65`, 감정별 재현율 `≥ 0.55`, 최대 보류율 `≤ 20%` | 실패·누락 포함 미제안율 ≤ 20% 적용 여부 포함 팀 최종 확정 대기 |
| 모델 건당 타임아웃 | `60초` | WSL2 CPU 기준, 모델 특성에 따라 조정 (`EXAONE 7.8B`는 `90초` 사용) |
| 최종 비교 평가 세트 최소 규모 | 감정당 `25개` 이상(총 `100개` 이상), 교차 라벨링 `10개` 이상 | 개발 표본과 원본 영상·화자 분리 |

### 파일럿 테스트 목록 (`pilot_v1`)

| # | 원본 ID | URL | 사례 유형 (1단계 확인) |
| --- | --- | --- | --- |
| 1 | `DGwuCn-EDfQ` | https://www.youtube.com/shorts/DGwuCn-EDfQ | 확인 예정 (대조 정상 사례 11–16초 확인) |
| 2 | `mKwJDo0YTkM` | https://www.youtube.com/shorts/mKwJDo0YTkM | 확인 예정 |
| 3 | `6yrnO_6Hl0I` | https://www.youtube.com/shorts/6yrnO_6Hl0I | 확인 예정 (v2 뒤 잘림 사례 47.78–50.78초 확인) |

## 1단계 · 기준선 측정과 공통 수집·후보 배치

- [ ] 기존 UI로 파일럿 URL을 수동 처리하고 후보 탐색·재생·경계 조정·라벨링 사람 시간과 경계 오류를 기록한다.
- [x] 공통 URL 목록 입력, 영상 ID 중복 제거, 길이 상한(180초), 실패 사유 기록 및 계속 진행 구현.
- [ ] `video_workers=1` vs `2` 동일 조건 처리 시간·병목을 Ubuntu에서 비교한다(별도 `EMOLABEL_DATA_DIR`, ASR 사전 다운로드 후 측정).
- [x] 원래 ASR 경계 보존 + Silero VAD 기반 선정 v3(짧은 경계 확장 ≤ 0.5초·경고 플래그·최대 3개) 구현.
- [x] 무음·BGM 전용 `no_candidates` 처리, 품질 플래그 기록, 동일 입력 재실행 시 완료 구간·확정본 보존 구현.
- [x] Ubuntu에서 `pilot_v1` 3개 영상 배치 완료 및 캐시 재사용 확인.
- [ ] 완료 기준 · 파일럿 두 번 실행의 첫 실행/캐시 시간 기록, 후보 경계·대사 실청취 확인, 수동 기준선 대비 비교.

## 2단계 · 검수 동선과 확정 저장

- [x] 후보 미디어 추출·재생 연결, 검수 큐(일반/품질/이력), 보류·제외 사유, 단축키, 기본값 오염 방지, 독립 검수 모드, 유휴 제외 타이머(90초), 원자적 확정 저장·`superseded`·철회 격리 구현.
- [x] 2단계 보완(선택 구간 자동 정지·원본 이어듣기 종료) 구현 및 Ubuntu 실제 후보 청취 확인 완료.
- [ ] 완료 기준 · 파일럿에서 재생, 경계 수정, 네 검수 상태의 저장·재조회·철회, 이력 연결, 기본값 확인, 재실행 보호와 수동 대비 작업 시간을 확인한다. (반복 켠 원본 이어듣기의 영상 끝 정지 포함)

## 3단계 · 로컬 감정 모델 연결

- [x] 공통 분류 인터페이스, 고정 후보(`--export-candidates` / `--candidates`), 수정 대사 분리(`--edited-transcripts`), 4감정+보류 정규화, 프로세스 분리·타임아웃, R1 구조화 오류 진단(`diagnostic.stage`/`code`), 검수 UI 제안 표시·독립 검수 숨김 구현.
- [x] 완료 기준(기계 계약) · 동일 후보 기준 추론과 모델 미설치·타임아웃·오류 시 수동 검수 지속 가능 확인. Ubuntu 1–3차 실모델 파일럿 완료(상세 표는 [docs/PILOT_LOG.md](docs/PILOT_LOG.md) 참조).
- [x] 최종 연결 모델 2개 선정: 대사 `LimYeri/HowRU-KoELECTRA-Emotion-Classifier`, 음성 `emotion2vec/emotion2vec_plus_base`. 사용자 요청은 감정별 평균 점수 기반 사전 라벨링이며, 자체 모델 후보 비교를 반복하는 것이 아니다.
- [x] 아래 구현 계획에 따라 두 모델의 점수 출력·평균 결합·UI 표시 코드를 연결했다. 실제 모델 설치·추론은 별도 완료 기준이다.
- [x] 사용자 제공 Ubuntu 실제 후보 9개 출력에서 두 모델 실패 0·평균 점수 저장 확인.
- [ ] 실제 브라우저 제안 표시·사람 확정 저장·추론 재실행 후 확정본 보존을 확인한다. 정확도는 독립 사람 정답으로 별도 평가한다.

### 최종 모델과 평균 결합 구현 계획 (2026-11-22)

**목표**: 대사와 음성이 각 감정에 부여한 점수를 동일 비중으로 평균내고, 가장 높은 감정을 사람 검수용 초안으로 제시한다. 구간 선정 v3는 유지한다.

| 역할 | 확정 모델 | 선정 근거와 한계 |
| --- | --- | --- |
| 대사 | [LimYeri/HowRU-KoELECTRA-Emotion-Classifier](https://huggingface.co/LimYeri/HowRU-KoELECTRA-Emotion-Classifier) | 한국어 8감정 단일 분류, 평범함(Neutral) 포함, 공식 예제에 전체 softmax 분포 제공. 일기·상담 중심 학습이므로 숏츠 대사 성능은 미검증. Regression 모델과 구분한다. |
| 음성 | [emotion2vec/emotion2vec_plus_base](https://huggingface.co/emotion2vec/emotion2vec_plus_base) | 약 90M 규모의 음성 감정 분류 모델. 중립 포함 9감정 점수 출력, 16 kHz 입력. 한국어 숏츠 품질·CPU 지연은 실측 전이며, 임베딩 전용 emotion2vec_base와 구분한다. |

출력 계약은 원래 점수와 감정 이름을 보존하고, `neutral`, `angry`, `sad`, `happy` 및 내부 `other`로 정렬한다. 대사의 평범함/분노/슬픔/기쁨과 음성의 neutral/angry/sad/happy를 각각 직접 대응시킨다. 대사의 설렘·놀라움·불쾌함·두려움과 음성의 나머지 감정은 `other`에 합산한다. 임의로 중립에 넣거나 남은 네 감정만 재정규화하지 않는다. 매핑 버전과 다운로드한 모델 revision을 기록한다.

결합은 각 범주에 `(대사 점수 + 음성 점수) / 2`를 적용한다. 네 감정 중 최고가 전체 최고이면 제안하고, `other`가 최고이거나 공동 최고·모델 실패·점수 누락/유효성 오류이면 보류한다. 한 모델의 실패를 0점으로 대체하지 않는다. 모델별 분포가 보정된 확률이라는 보장은 없으므로 평균은 사전 제안 점수로 표시한다. `auto_accept: false`와 사람 확정본·이력 보존은 유지한다.

**변경 예정 파일**:

- `tools/local_emotion.py` 및 필요한 전용 어댑터: 로컬 Transformers 텍스트 분류와 FunASR 음성 분류, 전체 점수 검증·라벨 매핑. 모델은 외부 런타임에서 준비하며 앱의 필수 의존성/토큰 요구를 늘리지 않는다.
- `app/emotion.py`, `app/config.py`, `.env.example`, `tools/emotion_batch.py`: 점수 계약·평균 결합 버전·실행 설정/기록. 기존 엄격 일치 실행 결과도 읽을 수 있게 한다.
- `app/review.py`, `static/review.mjs`, 필요한 UI 파일: 기존 엄격 일치 전용 검증을 버전별로 분리하고 모델별·평균 점수 표시, 독립 검수 시 제안 숨김 유지.
- `tools/evaluate_models.py`와 관련 Python/Node 테스트, `emolabel-youtube/README.md`: 새 결합 결과 읽기·회귀 검증·Ubuntu 설치/실행 안내. 추가 후보 모델 순위 실험을 필수 단계로 만들지 않는다.

**완료 기준·검증**:

- [x] 두 어댑터의 전체 클래스 점수 출력·유한성·범위·합계·라벨 대응 검증을 구현했다. 모의 런타임으로 호출 계약을 확인했으며 실제 체크포인트 출력 확인은 아래 Ubuntu 항목에 남긴다.
- [x] 검증용 점수로 평균·순위·other 보류·동점·실패·이전 형식 호환을 검사했다. UI 문자열의 같은 점수 표시, 블라인드/입력 수정 시 숨김과 변조 결과 차단을 확인했다.
- [ ] 기본 검사 `uv run python -m unittest -v test_portability`, 전체 Python 검사, `node --test test_review_ui.mjs test_boundary_playback.mjs`를 Ubuntu에서 실행한다. Windows에는 uv가 없어 Python 3.14로 portability 포함 비HTTP 125개와 Node 10개 통과. HTTP 5개는 TestClient 대기로 15초 후 중단했고 Ubuntu에 남긴다.
- [ ] Ubuntu에서 정상 후보와 기존 잘림 후보를 포함해 실제 추론→점수 저장→검수 UI→사람 확정 저장을 확인한다. 이 확인 후 사용자 요청 범위대로 커밋·푸시하고 최종 워크플로우 비교로 넘어간다.

**실모델 검증 상태**: 2026-11-22 사용자 제공 Ubuntu 실행 결과로 `howru-emotion2vec-mean-v1` 실행(`e7c5793886344b789c686dec307ec207`, original_asr, 후보 9개, input_errors 0)의 실추론·평균 점수 저장 성공을 확인했다. text는 ok 5/abstained 4, audio는 ok 4/abstained 5, combined는 ok 4/abstained 5이며 모델 실패는 모두 0이다. 보류는 모두 unsupported_emotion(other 최고)이다. 직접 파일 검증·독립 정답·정확도·실제 브라우저 검수/확정본 보존은 미확인이다. 전용 연결 확인 CLI의 VERIFIED 보고서를 받았다는 의미는 아니다. 상세 결과는 [docs/PILOT_LOG.md](docs/PILOT_LOG.md)에 기록한다.

근거 확인일은 2026-11-22이며, 모델 카드와 [emotion2vec 공식 저장소](https://github.com/ddlBoJack/emotion2vec)의 점수 출력 계약을 확인했다. 모델 카드 성능 수치를 이 프로젝트의 정확도로 취급하지 않는다.

## 3.5단계 · 수동 구간 추가

**목표**: 자동 선정에서 빠진 좋은 발화를 사람이 원본 영상에서 찾아 독립적인 검수 후보로 추가한다. 기존 후보의 경계 수정과 새 후보 추가를 구분한다. 기존 수동 영상 로딩·추출 기능을 검토해 배치 검수에서도 같은 흐름을 사용할 수 있게 연결한다.

### 사용자 흐름과 범위

1. 현재 원본 영상에서 **새 구간 추가**를 누른다. 자동 후보가 없는 영상도 기존 URL 불러오기 경로에서 수동 구간을 만들 수 있게 한다.
2. 원본 파형 드래그 또는 시작/끝 조정으로 원하는 구간을 선택하고 재생한다. 이 상태는 새 구간 초안이며 기존 후보 경계를 바꾸지 않는다. 취소하면 기존 검수로 돌아가고, 작성 중인 검수 내용이 조용히 사라지지 않도록 처리한다.
3. **검수 후보로 추가**를 누르면 미디어 추출을 완료한 새 `pending` 후보를 목록에 넣고 선택한다. 이 단계에서는 최종 채택이나 확정 출력 저장을 하지 않는다.
4. 사람은 대사·감정·필수 검수 항목을 직접 입력해 기존 채택/수정 후 채택/보류/제외 흐름으로 처리한다. 모델 설치·감정 추론·자동 전사 성공을 추가의 필수 조건으로 만들지 않는다.

### 데이터와 보호 기준

- 사람이 추가했다는 출처와 추가자·추가 시각을 기록하고 UI에 **직접 추가**로 표시한다. 다른 후보의 대사·감정·모델 제안을 복사하거나 사람 라벨을 미리 선택하지 않는다. 배치 `asr.json`이 있으면 겹치는 구간 대사를 즉시 초안으로 채우고(없거나 독립 검수 모드면 빈 대사), 검수 화면의 선택형 **대사 자동 전사** 버튼(`POST /api/review/candidates/{clip_id}/transcribe`)으로 장부·확정본을 덮어쓰지 않고 단건 Whisper 초안을 요청할 수 있다. 전사 실패·미설치는 추가나 수동 입력을 막지 않는다.
- 기존 `video_id + 시작/끝 ms` 기반 `clip_id`와 원본 버전 확인·영상별 잠금·미디어 캐시를 재사용한다. 원본 존재 여부, 유한한 시각, `0 ≤ 시작 < 끝 ≤ 원본 길이`, 현재 최소/최대 길이 및 ms 정규화 후 유효성을 서버에서 검증한다.
- 동일 경계의 자동/수동 후보, 검수 이력의 후보, 기존 확정 출력과 충돌하면 덮어쓰지 않고 기존 구간 확인을 안내한다. 경계가 다른 일부 중첩은 허용한다.
- 수동 후보는 기계가 다시 생성하는 `state.json`의 선정 목록과 분리해 영속 보존한다. 배치 재실행·선정 버전 변경·새로고침 후에도 후보·출처·검수 이력·확정본이 유지돼야 한다. 실제 저장은 기존 영상별 검수 장부 `data/workspace/{video_id}/reviews.json`을 재사용하며(`origin: "manual"`, `manual_creation: {annotator_id, at, active_seconds}`), 확정 출력 `data/output/{clip_id}/annotation.json`에는 `candidate_origin: "manual"`과 `manual_creation`을 기록한다. 경계를 변경하지 않고 대사를 입력해 `채택`으로 저장한 수동 후보는 `accepted` 상태를 유지한다.
- 미디어 추출/저장 실패 시 성공한 후보처럼 노출하지 않고 재시도할 수 있게 한다. 기존 후보·확정본은 보존한다. 취소된 초안은 후보 수에 포함하지 않는다.
- 수동 후보는 자동 ASR 후보인 것처럼 기존 `original_asr` 평가 manifest에 섞지 않는다. 고정 ASR 평가 입력은 유지하고, 전체 워크플로우 비교에서는 수동 추가 건수·추가에 든 사람 시간을 구분해 기록한다. 수동 후보의 모델 사전 제안·별도 추론 배치 확장은 이번 단계 필수 범위에 넣지 않는다.

### 변경 예정 파일

| 영역 | 대상 | 변경 목적 |
| --- | --- | --- |
| 후보 추가·보존 | `app/review.py`, `app/review_api.py`, `app/schemas.py` | 새 후보 생성 요청·입력/중복 검증·잠금·출처 기록·영속 저장과 기존 검수 연결 |
| 미디어 | `app/candidate_media.py`, 필요 시 `app/main.py` | 기존 추출/영상 로딩 재사용, 자동 후보가 없는 원본 지원 경로 확인 |
| 검수 화면 | `static/app.js`, `static/review.mjs`, `static/index.html`, `static/style.css` | 추가/취소/선택 모드·출처 표시·목록 갱신·사람 입력 보호 |
| 회귀·안내 | `test_review.py`, `test_review_api.py`, `test_review_ui.mjs`, 필요 시 `test_batch.py`·`test_emotion_batch.py`, 두 README | 수동 추가·확정 저장·재실행/기존 평가 입력 보호 검사와 사용법 |

### 완료 기준·검증

- [x] 자동 후보 밖의 새 구간을 원본에서 선택해 별도 미검수 후보로 추가하고 영상/WAV를 재생할 수 있다. 자동 후보가 없는 영상에서도 수동 시작 경로가 있다. (단위/API/UI 계약 확인; 실제 브라우저 재생은 아래 Ubuntu 항목에서 확인)
- [x] 추가 모드 진입·취소·실패가 기존 후보 경계와 작성 중인 검수 내용을 바꾸지 않는다. 새 후보는 감정 미선택·모델 제안 없음 상태로 시작한다. 독립 검수 숨김 규칙과 추가 중 감정 단축키(1–4) 차단을 유지한다.
- [x] 동일 경계 중복·원본 변경/누락·NaN/무한대·범위/길이 오류를 서버에서 거부하고, 추출·저장 실패 후 기존 자료가 보존된다. 동시 추가 시 같은 후보가 중복 생성되지 않는다.
- [x] 직접 추가 후보의 새로고침·채택/보류/제외·경계 수정·재조회·철회가 기존 검수 규칙대로 동작한다. 배치/모델 재실행 후 수동 후보·사람 확정 라벨·이력을 보존한다.
- [x] 자동 후보 고정 manifest와 기존 모델 결과를 바꾸지 않는다. 수동 후보 출처를 표시하고 자동/수동 건수와 사람 시간의 구분을 확인한다.
- [ ] Python 후보 추가/저장·API·회귀 검사와 Node 추가/취소/입력 보호 검사를 통과한다. Windows에서 비HTTP Python 142개(수동 추가·전사 보조 17개·portability 7개 포함)·Node 13개 통과. Windows 샌드박스 제약으로 미실행한 HTTP 6개(`test_review_api.py`) 포함 전체 `uv run python -m unittest discover -v`는 Ubuntu에서 실행한다.
- [ ] Ubuntu 실제 원본에서 새 구간 추가 → 재생 → 대사/감정 입력 → 확정 저장 → 새로고침/재실행 후 보존을 확인한다. 기존 자동 후보 1건을 대조해 새 손상이 없음을 확인한다.
- [ ] 3.5단계 구현·검증 결과를 기록한 후 보류한 커밋·푸시를 재개하고 4단계 팀원 워크플로우 비교로 넘어간다. 이전 커밋 시도는 로컬 `.git/index.lock` 쓰기 권한 오류로 실패했고, GitHub 도구 확인은 사용자 중단으로 완료되지 않았으므로 커밋·푸시 성공으로 기록하지 않는다.

## 4단계 · 팀원 프로그램과 동일 조건 워크플로우 비교

- [x] 오프라인 비교 평가 도구(`tools/evaluate_models.py`, `test_evaluate_models.py`) 구현 및 합성 정답표 검증 완료.
- [ ] 0단계 합의 규모(감정당 ≥ 25건, 총 ≥ 100건, 교차 라벨링 ≥ 10건, 개발/평가 원본·화자 분리)의 독립 사람 정답 세트를 확보한다. 기존 101개 자료는 품질 메모·정답 독립성·중복 여부 확인 후 편입 범위를 정한다.
- [ ] C-EVAL의 전체 분모 계산과 미제안율(`≤ 20%`) 기준을 최종 평가 전에 팀과 확정한다(결과를 본 뒤 완화하지 않음).
- [ ] 본인 프로그램(확정한 두 모델의 평균 사전 제안)과 팀원 프로그램을 동일 자료·검수 기준에서 비교한다. 공통 후보 기준 감정 제안 품질과 원본 영상 기준 전체 작업 시간을 구분해 기록한다. 자체 모델 후보를 계속 교체하는 실험은 필수 범위가 아니다.

| 지표 | 기록 방법 |
| --- | --- |
| 채택 클립당 사람 작업 시간 | 활성 총시간(유휴 제외) ÷ 최종 저장 완료 채택 건수 (0건이면 산출 불가) |
| 기계 처리 시간 | 다운로드·VAD·ASR·추출·추론·저장의 영상별 소요 시간 (첫 실행/캐시 구분) |
| 후보 품질 | 후보 수, 검수율, 채택률(채택 ÷ 검수), 경계 수정률, 거절 사유, 최종 유효 클립 수 |
| 감정 분류 | 전체 분모 및 유효 예측 분모 각각의 정확도, Macro-F1, 감정별 재현율, 보류율, 실패·누락률, 제안 노출 시 라벨 수정률 |
| 수동 개입 | 단계별 자동 처리 범위, 사람 수정·재시도 횟수와 사유 |

## 파일럿 후 열린 수정·검증 항목

완료된 R0–R3 구현·합성 검증·1–3차 파일럿 상세 기록과 완료 기준(C-DIAG/C-BOUND/C-EVAL/C-PILOT/G1–G3)은 [docs/PILOT_LOG.md](docs/PILOT_LOG.md)에 보관되어 있다.

- [x] R0 경계 재현 도구·R1 오류 진단·R2 선정 v3 경계 보정·R3 오프라인 평가 도구 구현 및 Windows 회귀 검사(Python 비HTTP 111개·Node 9개) 통과.
- [x] 사용자 실제 청취에서 선정 v3의 잘림 현상이 눈에 띄게 줄었다는 정성적 개선 확인(정량 감소율·작업 시간 미산출).
- [ ] **Ubuntu 전체 자동 검증**: `feat/phase-3` 동기화 후 Ubuntu Python 3.12에서 `uv run python -m unittest discover -v`(HTTP 6개 포함 148개) 및 `node --test test_review_ui.mjs test_boundary_playback.mjs` 실행.
- [ ] **R2 개별 사례 전후 확인**: `6yrnO_6Hl0I` 47.78–50.78초(실패 기준)와 `DGwuCn-EDfQ` 11–16초(정상 대조)를 Ubuntu에서 새 `r0-v3.json`과 함께 재청취해 실제 경계 수정률·사람 시간 감소 확인. (후보 제외나 경고만 추가된 사례는 해결로 집계하지 않음)
- [ ] **확정 모델 연결 후 팀원 워크플로우 비교**: 새 고정 manifest(`pilot-v3.json` 등)에 모델 제안을 가린 독립 정답을 작성한다. 두 프로그램의 감정 제안 품질·보류율과 원본 영상부터 최종 채택까지의 사람 작업 시간을 비교한다. 기존 R3 파일럿은 이전 구성의 진단 이력으로 보존한다.
- [ ] **실제 숏츠 종단 간 검증**: 수집부터 확정 저장까지 실행 후 `uv run python tools/dataset_status.py --root data/output`으로 영상·WAV·`annotation.json` 대조.

## 기각·보류 목록 (Delete-Zone)

재시도 낭비와 중복 논의를 막기 위해 기각되었거나 조건부 보류 중인 접근을 기록한다.

| 대상 / 접근 | 상태·사유 | 대안 / 현재 기준 | 재검토 조건 |
| --- | --- | --- | --- |
| `byc3230/hubert-large-korean-finetuned-korspeech-ser` | **기각** · `config.json`/README에 `LABEL_0..3` 감정 매핑이 미문서화 | `SenseVoiceSmall`, `emotion2vec_plus_large` 등 라벨 정의 명확한 모델 | 공식 라벨 매핑이 검증된 경우에만 |
| 스튜디오 SER 3종 단독 + 엄격 일치 결합(`hubert-large-superb-er`, `wavlm-emotion-v1-crosslingual`, `jungjongho/wav2vec2-korean-ser`) | **현 규칙 한계 확인** · 파일럿 9건 보류율 77.8%–100%(임시 상한 20% 초과) 및 쇼츠 도메인 편향 | HowRU-KoELECTRA + emotion2vec+ base의 50:50 평균 사전 제안으로 전환 예정 | 실제 연결·사람 검수 결과에 문제가 있는 경우 |
| `EXAONE-4.0 32B` 등 30B급 로컬 LLM | **기각 (현 장비)** · WSL2 CPU 가용 RAM ~14 GiB 초과 | 7–8B급 `Q4_K_M`(`EXAONE-3.5-7.8B`, `Qwen3-8B`, `Kanana` 등) | 24 GiB+ RAM/GPU 장비 전환 시 |
| ASR 구간 자동 병합 / LLM 기반 경계 보정 | **보류** · 화자 혼합·지연·복잡도 위험으로 R2 범위에서 제외 | 선정 v3의 제한적 VAD 확장(≤ 0.5초) + 검수 경고 플래그 | 파일럿에서 경계 수동 조정 시간이 반복 병목으로 확인될 때 별도 합의 |
| 원시 logit 기반 모델 간 신뢰도 가중 결합 | **보류** · 보정(calibration) 검증 없이 모델 간 logit 직접 비교 불가 | 각 모델의 softmax 분포를 공통 범주로 매핑한 뒤 동일 비중 평균. 평균 점수를 보정된 확률로 부르지 않음 | 개발 표본에서 확률 보정 검증 후 |
| Windows 샌드박스에서 `test_review_api.py` 실행 | **환경 제약** · AnyIO 로컬 소켓페어 대기로 중단됨 | WSL2 Ubuntu에서 `uv run python -m unittest discover -v`로 HTTP 6개 검증 | 환경 제약 해소 시 |
| 보류된 확장 실험 (채널 단위 수집, 원본 타임라인 검수, PySceneDetect/AutoClip/KMSAV, 라벨 300–500건 후 LightGBM/MLP) | **보류** · 현재 1–4단계 핵심 흐름 완료 우선 | 현재 공통 파이프라인 및 단순 결합 기준선 | 핵심 파일럿 완료 및 충분한 표본(300–500건 등) 확보 후 |

## 검수 화면 점수 정리 (2026-11-22)

- 사용자 요청: 한 문단에 나열된 모델 점수를 감정별 비교 표로 정리한다.
- 변경 예정: `static/review.mjs`(표 데이터·안전한 DOM 렌더링), `static/app.js`(표 연결), `static/index.html`·`static/style.css`(제안 카드·반응형 표·캐시 버전), `test_review_ui.mjs`.
- 완료 기준: 평균/결합 제안과 한국어 보류 사유가 명확하고, 5감정 × 대사/음성/평균 점수를 비교할 수 있다. 실패·누락 점수는 0 대신 대시로 표시한다. 블라인드·대사/경계 수정 시 숨김과 사람 감정 미선택 상태를 유지한다.
- 검증: Node UI/재생 검사, JS 구문 검사, Python portability, UTF-8/LF·diff 확인. Ubuntu 파일 교체 후 강력 새로고침으로 실제 브라우저 확인은 별도 기록한다.
- [x] 구현·자동 검증 완료: Node UI/재생 12개·Python portability 7개, JS 구문·UTF-8/LF·diff 검사 통과. 실제 Ubuntu 새 화면 확인은 대기.
- [ ] Ubuntu 실제 화면 적용·저장 확인.

## 작업 인계

최신 항목을 맨 위에 기록한다. 최근 5건만 유지하고 이전 기록은 [docs/HANDOFF_LOG.md](docs/HANDOFF_LOG.md)로 이동한다.

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
