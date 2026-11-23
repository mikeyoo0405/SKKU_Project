# 오픈소스·모델 라이선스 및 데이터 권한 안내

- 작성일: 2026-11-23 (KST)
- 적용 대상: `emolabel-youtube/` 및 외부 로컬 감정 모델 연결 환경
- 목적: 프로젝트에서 사용하는 오픈소스 라이브러리·AI 모델·수집 미디어의 라이선스와 연구 데이터 취급 원칙을 한곳에 정리합니다. 이 문서는 법률 자문이 아니며, **외부 공개·상업적 이용·재배포 전에는 반드시 각 공식 저장소·모델 카드의 원문 라이선스를 직접 확인**해야 합니다.

---

## 1. 핵심 앱 의존성 및 동봉 자산 (`emolabel-youtube/`)

| 구성 요소 | 역할 | 공식 출처 / 확인 기준 | 주의사항 |
| --- | --- | --- | --- |
| FastAPI / Uvicorn / Pydantic | 로컬 웹 검수 서버 및 API | `pyproject.toml` (`fastapi`, `uvicorn`) | 로컬 바인딩(`127.0.0.1`) 전용. 인증이 없으므로 `--host 0.0.0.0` 외부 공개 금지 |
| `yt-dlp` | YouTube 영상 메타데이터·미디어 로딩 | `pyproject.toml` (`yt-dlp`) | 도구 사용은 플랫폼 이용약관·저작권·연구 이용 권한 확인을 전제로 함 |
| FFmpeg / ffprobe | 클립 자르기·16 kHz WAV 추출 | 시스템 설치 패키지 | 배포 시 사용 빌드(LGPL/GPL 옵션) 확인 필요 |
| `faster-whisper` / `mlx-whisper` | 한국어 음성 전사(ASR) | `pyproject.toml` · Whisper `turbo` 가중치 | Linux CPU `int8` / Apple Silicon MLX 분기. 전사 모델 가중치 조건 원문 확인 |
| `silero-vad` | 발화 구간(VAD) 검출 | `pyproject.toml` (`silero-vad`) | 오픈소스 VAD 모델(MIT 계열, 사용 버전 원문 확인) |
| `opencv-python` + YuNet (`app/models/face_detection_yunet_2023mar.onnx`) | 얼굴 수 QC | `pyproject.toml` + 동봉 ONNX 자산 | **저장소에 동봉된 서드파티 ONNX 파일** — 공개 저장소 전환·재배포 전 OpenCV Zoo 원문 라이선스 확인 필수 |
| `WaveSurfer.js` + Regions (`static/vendor/`) | 웹 파형 표시·드래그 선택 | `static/vendor/wavesurfer.esm.js`, `regions.esm.js` | 동봉 프런트엔드 라이브러리 — 재배포 시 원문 라이선스(BSD-3-Clause 등) 고지 확인 |
| `speechbrain` / `pyannote-audio` | 선택형 화자 수 QC (`app/qc.py`) | `pyproject.toml` | `pyannote` 게이트 모델은 Hugging Face 이용 약관 동의 및 개인 토큰 필요(토큰 미설정 시 기본 대체 경로 사용) |
| `audeering/wav2vec2-large-robust-24-ft-age-gender` | 수동 추출 시 화자 성별 보조 (`app/gender.py`) | Hugging Face 모델 카드 | 보조 참고용. 배포·상업적 활용 전 모델 카드 원문 라이선스(CC-BY-NC-SA 등 비상업 조건 여부) 확인 필수 |

---

## 2. 선택 설치 도구 및 외부 감정 모델 (Git 제외)

아래 구성 요소는 `emolabel-youtube/pyproject.toml` 필수 의존성에 넣지 않고 Ubuntu 로컬 환경에 별도 설치·연결합니다. 모델 가중치는 Git에 포함하지 않습니다.

| 구성 요소 | 역할 | 모델 / 패키지 ID | 주의사항 |
| --- | --- | --- | --- |
| PySceneDetect (`AdaptiveDetector`) | 선택형 장면 전환 경고(S1, `EMOLABEL_SCENE_MODE=warn`) | `scenedetect` (`uv pip install scenedetect`) | BSD-3-Clause 계열. 미설치 환경에서는 `unavailable` 상태로 안전 동작 |
| 대사 감정 모델 | 한국어 대사 8감정 → 4감정+`other` 사전 점수 | `LimYeri/HowRU-KoELECTRA-Emotion-Classifier` | 외부 로컬 폴더에 커밋 SHA 고정 저장. **공개·상업적 이용 전 Hugging Face 모델 카드의 라이선스·데이터셋 조건 원문 확인 필수** |
| 음성 감정 모델 | 16 kHz 음성 9감정 → 4감정+`other` 사전 점수 | `emotion2vec/emotion2vec_plus_base` (`funasr`) | 외부 로컬 폴더에 커밋 SHA 고정 저장. **FunASR/emotion2vec 원문 라이선스 및 연구/상업 제한 조건 확인 필수** |

---

## 3. 수집 영상·라벨 데이터·비밀 정보 취급 원칙

1. **원본 영상과 저작권·이용 권한**
   - 이 저장소는 **코드와 문서만** 관리하며, YouTube 원본 영상(`data/downloads/`), 추출 클립(`data/workspace/`, `data/output/`), 모델 가중치, 라벨 데이터는 Git에 올리지 않습니다.
   - 연구실 내부 검토·학술 연구 목적 외 외부 공개나 재배포 전에는 원본 영상의 저작권·초상권·플랫폼 약관과 별도 허가 여부를 반드시 확인해야 합니다.
   - 기존 파일럿 101개 데이터셋의 공유 범위·한계는 [DATASET_CARD.md](../emolabel-youtube/docs/DATASET_CARD.md)를 따릅니다.
2. **사람 최종 확정 원칙 (`auto_accept: false`)**
   - 모든 AI 모델(ASR·장면 전환·대사/음성 감정 모델)의 출력은 **참고용 사전 제안**이며, 최종 대사와 감정 라벨은 사람이 원음·영상을 확인한 뒤 확정합니다.
   - 모델 카드의 벤치마크 수치를 본 프로젝트의 실제 정확도로 인용하지 않습니다.
3. **비밀 정보 및 개인 경로 보호**
   - `.env`, `data/local-emotion.env`, Hugging Face 토큰, 쿠키, 개인 로컬 경로는 코드·문서·인계 기록·제출 ZIP에 포함하지 않습니다.
   - 모델 연결 보고서(`data/evaluations/*.json`)에는 연구 대사와 실행 경로가 포함될 수 있으므로 Git 제외 폴더(`data/`)에만 보관합니다.
