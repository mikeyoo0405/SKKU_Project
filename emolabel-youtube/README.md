# EmoLabel — 연구실 라벨링 도구

YouTube 숏츠에서 한 화자의 발화를 추출하고, 대사를 수정해 neutral / angry / sad / happy를 기록하는 로컬 웹 도구입니다. 중앙 서버 없이 각자 작업할 수 있습니다.

이번 배포본은 기존 Mac 앱의 복사본에 Linux ASR 경로를 추가했습니다. 원본 앱과 기존 데이터는 수정하지 않았습니다. 대상은 **Linux x86_64 또는 Apple Silicon Mac, Python 3.12**입니다. Linux ARM·Windows·Intel Mac은 이 배포본의 검증 대상이 아닙니다.

## 코드 가져오기

이 저장소는 비공개입니다. 저장소 접근 권한이 있는 GitHub 계정으로 인증한 뒤 복제하세요.

```bash
git clone https://github.com/yoonji4024/emolabel-youtube.git
cd emolabel-youtube
```

처음 작업하기 전에 [라벨링 가이드](docs/ANNOTATION_GUIDE.md)를 읽어주세요. [GPT 활용 가이드](docs/GPT_ASSISTANCE.md)는 선택 사항이며 GPT API 연결 없이도 앱을 실행할 수 있습니다.

수집한 101개 샘플과 모델 비교 결과는 이 저장소에 넣지 않았습니다. 데이터는 연구실의 접근 권한이 설정된 Google Drive로 별도 공유합니다. 다운로드 위치는 담당자에게 확인하세요. [데이터 설명](docs/DATASET_CARD.md)

## Linux 설치

Ubuntu/Debian에서 시스템 준비 (다른 배포판은 해당 패키지 관리자를 사용):

```bash
sudo apt update
sudo apt install ffmpeg git unzip
```

[uv 공식 설치 안내](https://docs.astral.sh/uv/getting-started/installation/)에 따라 uv를 설치한 뒤, 이 폴더를 터미널에서 열고 실행합니다.

```bash
uv sync --locked --python 3.12
uv run python -m app.asr
uv run python -m app.gender
uv run python -m app.qc
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

같은 PC의 브라우저에서 http://127.0.0.1:8000 을 엽니다. 처음 모델을 받는 동안 시간이 걸리고 인터넷이 필요합니다. 다운로드 공간을 여유 있게 확보하세요. 실행 시간·용량은 PC 및 라이브러리 구성에 따라 달라지며, 기존 Mac의 속도를 Linux에 보장하지 않습니다.

Linux ASR은 `faster-whisper`, 기본 모델 `turbo`, CPU `int8`입니다. Apple Silicon에서는 기존 `mlx-community/whisper-large-v3-turbo`를 사용합니다. GPU는 기본적으로 요구하지 않습니다. [faster-whisper 공식 사용법](https://github.com/SYSTRAN/faster-whisper)

GPU를 쓰려면 먼저 CUDA/cuDNN 호환성을 확인한 후 별도로 설정하세요. CPU가 너무 느리면 `EMOLABEL_ASR_MODEL=small`로 시험할 수 있지만 전사 모델이 달라지므로 기록하고, 대사는 반드시 사람이 수정합니다. Mac의 `.env`에 있던 MLX 모델명을 Linux 설정에 그대로 복사하지 마세요.

## Mac 설치

Homebrew의 ffmpeg와 uv가 필요합니다. 설치되어 있다면 위의 `uv sync` 이후 명령을 동일하게 사용합니다. ffmpeg/ffprobe는 PATH에서 찾습니다. 필요한 경우 `.env.example`의 경로 설정을 사용하세요.

## 실행 확인 — 배포 전 마지막 단계

이 배포본은 분기/전사 형식 단위 테스트와 의존성 해석을 확인했습니다. **재훈님 Linux PC의 실제 영상 다운로드 → ASR → QC → 저장 전체 흐름은 아직 실행하지 않았습니다.**

먼저 짧은 샘플 하나로 다음을 확인한 뒤 연구실 인원에게 배포합니다.

1. `ffmpeg -version`, `ffprobe -version`이 실행됨.
2. 위의 모델 준비 명령이 성공함.
3. 영상 불러오기와 발화 추출이 됨.
4. 대사를 직접 수정하고 감정·본인 ID를 입력해 저장함.
5. `data/output/{clip_id}/` 안의 영상·WAV·annotation.json과 수정된 대사를 확인함.

단위 테스트 (모델 다운로드 없음):

```bash
uv run python -m unittest -v test_portability
```

8000 포트 사용 중이면 실행 중인 서버의 작업을 먼저 확인하고, 별도 시험은 `--port 8001`로 실행해 http://127.0.0.1:8001 에 접속하세요. 다른 사람이 쓰는 프로세스를 무작정 종료하지 않습니다. 인증이 없는 로컬 도구이므로 외부 공개를 위해 `--host 0.0.0.0`으로 바꾸지 마세요.

## 배치 후보 검수

기존 Phase 1 데이터를 사용하던 Ubuntu의 같은 폴더에서 실행합니다.

```bash
uv run python -m tools.run_batch --video-workers 1
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

브라우저에서 검수자 ID를 입력하고 **세션 시작 → 배치 후보 검수**를 선택합니다. 일반 후보와 품질 플래그가 있는 후보를 각각 확인하세요. 원본 파형에서 경계를 조정하고 추출된 영상과 16 kHz 음성을 재생할 수 있습니다. 채택할 때 대사·감정·품질을 확인하고, 보류·제외할 때 사유를 입력합니다. 처리한 후보는 검수 이력에서 다시 열 수 있습니다.

후보 파일은 `data/workspace/{video_id}/clips/{clip_id}/`, 검수 이력은 같은 영상 폴더의 `reviews.json`에 있습니다. 채택 후 철회한 출력은 해당 작업 공간의 `quarantine/`에 보관됩니다.

## 고정 후보로 감정 사전 제안 실행하기

먼저 위 배치로 후보와 미디어를 준비합니다. 다음 명령은 `emolabel-youtube/`에서 실행합니다. 첫 명령은 완료된 후보의 ID·원본 ASR 대사·미디어 식별값을 고정합니다. 같은 장비에서는 팀원별로 동일 목록을 재사용합니다. 다른 장비로 옮길 때는 목록, `data/downloads/`의 해당 원본, `data/workspace/{video_id}/`의 대응하는 배치 산출물을 함께 복사하고 원본 파일의 수정 시각을 보존하세요(Ubuntu 간 `cp -a` 등). 배치는 저장된 원본의 크기·수정 시각을 확인하고 실험 실행기는 콘텐츠 해시도 대조합니다.

```bash
uv run python -m tools.run_batch --export-candidates data/workspace/pilot.json
uv run python -m tools.run_batch --candidates data/workspace/pilot.json
```

두 번째 명령을 실행하기 전에 `.env.example`의 `EMOLABEL_EMOTION_TEXT`, `EMOLABEL_EMOTION_AUDIO` 예시를 참고해 Ubuntu의 `.env`에 각 모델의 실행 명령과 실제 버전·설정을 입력하세요. 설치된 모델이 없어도 실행 및 수동 검수가 가능하며, 이 경우 결과에 실패 사유가 남습니다. 모델 파일과 실행 환경은 Ubuntu에 별도로 준비합니다.

| 입력 | 제공하는 로컬 실행기 | 준비할 파일 또는 환경 |
| --- | --- | --- |
| 대사 | `python -m tools.local_emotion text --model /absolute/model.gguf` | 한국어 지시를 따르는 GGUF 대화 모델 및 llama-cpp-python 환경. 프롬프트 버전은 `ko-emotion-v1` |
| 음성 | `python -m tools.local_emotion audio --model /absolute/classifier` | Transformers가 로컬에서 읽을 수 있는 음성 분류 체크포인트와 전처리 설정. 출력 이름이 `LABEL_0` 형식이면 모델의 라벨 정의에 맞춰 `--label-map` 지정 |

실행 명령의 첫 항목에 해당 환경의 Python 절대 경로를 지정할 수 있습니다. 팀원 모델도 별도 실행기로 연결할 수 있습니다. 실행기는 표준 입력에서 UTF-8 JSON 요청 하나를 읽고, 표준 출력에 `{"label":"sad"}` 같은 JSON 하나를 씁니다. 텍스트 모델은 `transcript`, 음성 모델은 `wav_path`를 받습니다. 원점수가 있으면 `scores`에 기록하고, 판단을 보류할 때는 `{"label":"abstain","reason":"uncertain"}`을 반환합니다.

일반 검수에서는 두 모델의 개별 결과를 확인할 수 있습니다. 기존 설정의 기본 결합은 `agreement-v1`이며 두 모델이 같은 유효 라벨을 제안했을 때만 결합 라벨을 표시합니다. 아래 최종 모델 설정은 `mean-v1`으로 감정별 점수를 평균냅니다. 최종 감정은 사람이 선택하고, 독립 검수에서는 모든 제안을 숨깁니다.

### 최종 두 모델: HowRU + emotion2vec+ base

대사는 `LimYeri/HowRU-KoELECTRA-Emotion-Classifier`, 음성은 `emotion2vec/emotion2vec_plus_base`를 사용합니다. 구현은 `tools/scored_emotion.py`이며 기존 GGUF/음성 어댑터는 이전 실행 호환용으로 유지합니다. 모델과 런타임 설치 후 아래 절차로 실제 연결을 확인합니다. 현재 검증 상태는 루트 [PLAN.md](../PLAN.md)에 기록합니다.

Ubuntu Python 3.12의 별도 모델 환경에 CPU용 `torch`·`torchaudio`(서로 호환되는 버전), `transformers`, `safetensors`, `soundfile`, `funasr`, `setuptools`, `huggingface_hub` 및 이들의 의존성을 준비합니다. 앱의 기본 의존성은 바꾸지 않습니다. 모델은 Hugging Face에서 커밋 SHA를 고정해 외부 폴더로 내려받고 두 SHA를 기록합니다. HowRU는 `model.safetensors`와 tokenizer/config 파일, emotion2vec+는 `model.pt`, `config.yaml`, `tokens.txt`를 포함한 전체 스냅샷이 필요합니다. 런타임은 로컬 파일만 읽으며 원격 코드를 허용하지 않습니다. 다운로드와 설치는 별도로 수행합니다.

설치 후 **최신 코드 전체를 Ubuntu 복사본에 동기화**하고 프로젝트 루트에서 실행합니다. 아래 `/absolute/...` 경로와 두 40자리 SHA 자리표시자를 실제 값으로 바꾸세요. 설정 생성기는 기존 `.env`와 출력 파일을 덮어쓰지 않습니다.

```bash
uv run python -m tools.configure_emotion \
  --python /absolute/model-env/bin/python \
  --text-model /absolute/models/howru --text-revision '<TEXT_COMMIT_SHA>' \
  --audio-model /absolute/models/emotion2vec-plus-base --audio-revision '<AUDIO_COMMIT_SHA>' \
  --output data/local-emotion.env

# 기존 .env의 다른 설정은 유지하고, 이 셸에서 감정 모델 설정만 우선 적용합니다.
set -a
source data/local-emotion.env
set +a

# 같은 클립의 실제 WAV와 대사를 사용합니다. 보고서는 새 파일명이어야 합니다.
uv run python -m tools.check_emotion_connection \
  --wav /absolute/path/to/audio_16k.wav \
  --transcript '해당 클립의 실제 대사' \
  --output data/evaluations/model-connection-01.json
```

`connection: VERIFIED`는 두 모델이 로드·추론되고 유효한 전체 점수와 평균 결합 결과가 생성됐다는 뜻입니다. `other`가 높아 결합이 보류되더라도 연결은 성공할 수 있습니다. 정확도·UI·사람 확정 저장 성공까지 뜻하지는 않습니다. `FAILED`면 보고서의 모델별 `diagnostic.stage`·`code`를 확인합니다. 보고서에는 연구 대사와 실행 경로가 포함될 수 있으므로 Git에 올리지 않습니다. 설치 환경의 버전은 `uv pip freeze --python /absolute/model-env/bin/python > data/model-runtime.txt`로 함께 기록하세요.

연결 확인 후 같은 셸에서 고정 후보를 실행하고 UI를 시작합니다.

```bash
uv run python -m tools.run_batch --export-candidates data/workspace/pilot-v3.json
uv run python -m tools.run_batch --candidates data/workspace/pilot-v3.json --run-tag howru-emotion2vec-mean-v1
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

이미 같은 입력으로 만든 `pilot-v3.json`이 있으면 내보내기를 생략하고 재사용합니다. 선정 변경으로 입력이 달라졌다면 새 manifest 이름을 사용합니다. UI에서 대사·음성·결합의 감정별 점수가 표시되는지 확인하고, 기존 정상/잘림 후보를 검수한 뒤 사람이 확정 저장합니다. 확정 결과와 검수 이력은 모델 재실행이 덮어쓰지 않습니다.

점수는 `neutral`, `angry`, `sad`, `happy`, 내부 `other`로 정렬합니다. 네 감정 밖의 원래 클래스는 `other`로 합산하며 중립으로 바꾸거나 제외 후 재정규화하지 않습니다. 모델별 softmax 분포를 50:50 평균내고 `other` 최고·동점·모델 실패·점수 오류는 보류합니다. 개별 모델이 `other`로 보류했어도 전체 분포가 유효하면 평균 계산에 사용합니다. 평균은 보정된 정확도/신뢰 확률을 보장하지 않는 **사전 제안 점수**입니다. 원래 클래스 점수·매핑 버전·모델 revision과 이전 실행은 보존합니다.

관련 검증: `uv run python -m unittest -v test_scored_emotion test_emotion_review test_evaluate_models test_portability` 및 `node --test test_review_ui.mjs test_boundary_playback.mjs`. 실모델 연결 보고서와 실제 브라우저 저장 확인은 자동 단위 검사와 별도로 수행합니다.

실행별 결과는 `data/workspace/emotion-runs/`에 보관합니다. 사람이 수정한 대사로 별도 실험하려면 고정 목록의 모든 후보 ID를 수정 대사와 짝지은 JSON 객체를 준비하고, `--candidates` 명령에 `--edited-transcripts data/workspace/edited.json`을 추가하세요. 수정 대사 실험은 원본 대사를 사용한 검수 제안과 분리합니다.

## R0 경계 진단과 R3 오프라인 평가

진단·평가 도구는 기존 후보·모델 결과·확정 라벨을 읽기만 합니다. 추론을 다시 실행하거나 앱의 경계·결합 규칙을 변경하지 않습니다. 출력 경로에는 새 파일명을 지정하세요. 기존 보고서는 덮어쓰지 않습니다.

Ubuntu의 프로젝트 루트에서 실행합니다.

```bash
uv run python -m tools.reproduce_boundaries --project . --output data/evaluations/r0.json
uv run python -m tools.evaluate_models --manifest data/workspace/pilot.json --runs data/workspace/emotion-runs/* --output data/evaluations/r3-coverage.json
```

R0는 후보와 ASR 경계의 일치, VAD 발화 도중의 시작·끝, WAV/MP4 길이 오차, 입력 파일 해시를 기록합니다. VAD 경계와 겹치는 후보는 `boundary_suspect`, 겹치지 않는 후보는 `control_candidate_unconfirmed`입니다. 둘 다 실제 잘림/정상 확정이 아니므로 원본과 추출본을 청취하고 브라우저에서 원본 이어듣기와 선택 재생을 비교해야 합니다. WAV 길이는 Python 표준 라이브러리로 확인하며 MP4 길이는 FFprobe가 있어야 검사합니다. 자료가 없으면 NOT RUN과 종료 코드 2, 일부 파일 오류가 있으면 오류 목록과 종료 코드 2를 반환합니다.

R3의 `--runs`에는 같은 manifest·원본 ASR 입력으로 완료한 실행 폴더만 지정합니다. 다른 후보 목록이나 수정 대사를 사용한 실행은 별도 비교로 분리하세요. 명령의 `*`가 다른 실험까지 포함하면 비교할 폴더를 직접 나열합니다. 동일 후보 ID라도 대사·원본/미디어 해시가 다르면 평가 오류입니다.

독립 정답이 없으면 정확도·Macro-F1·재현율을 계산하지 않습니다. `text`, `audio`, `agreement`(또는 `mean-v1` 실행의 `mean_score`), `text_priority_warning`별 건수·커버리지·보류율·실패율·누락률·실행 시간을 보고합니다. 대사 우선 대안은 대사 예측을 유지하고 음성과 불일치하면 경고만 집계합니다. 음성 결과로 대사를 대체하지 않으며, 경고에 필요한 음성 추론 시간도 포함합니다.

독립 정답은 아래 형태로 `data/evaluations/gold.json`에 작성하고 `--gold`로 전달합니다. 각 후보의 실제 ID와 지문은 고정 manifest에서 복사하고, 감정은 모델 제안을 보지 않은 사람이 작성합니다. 아래 값은 형식 예시이며 평가 데이터가 아닙니다. 모든 고정 후보를 빠짐없이 포함해야 합니다. 기존 annotation에 있는 라벨을 독립성 확인 없이 자동 정답으로 사용하지 않습니다.

```json
{
  "schema_version": 1,
  "independent": true,
  "annotator_id": "reviewer-example",
  "candidates": [
    {
      "clip_id": "pilot_00001_0002000_0005000",
      "emotion": "neutral",
      "source_sha256": "manifest의 실제 source_sha256",
      "media_sha256": {"wav": "manifest의 실제 wav 해시", "video": "manifest의 실제 video 해시"}
    }
  ]
}
```

```bash
uv run python -m tools.evaluate_models --manifest data/workspace/pilot.json --runs data/workspace/emotion-runs/* --gold data/evaluations/gold.json --output data/evaluations/r3-gold.json
uv run python -m unittest -v test_boundary_reproduction test_evaluate_models test_portability
node --test test_review_ui.mjs test_boundary_playback.mjs
```

전체 기준 지표는 보류·실패·누락을 실제 감정의 미검출로 계산합니다. 유효 예측만의 지표는 별도 분모로 표시합니다. 정답 없는 감정의 재현율은 null이고, 네 감정 Macro-F1에는 해당 감정 F1을 0으로 포함합니다. 이 보고서는 개발 비교이며, 최종 채택에는 별도 평가 표본·교차 라벨링·사람 작업 시간 확인이 필요합니다.

Windows에서만 새 평가 도구를 작성했고 WSL 공유 경로를 열 수 없을 때는, Ubuntu 프로젝트 폴더에서 Windows 체크아웃의 도구를 직접 읽어 실행할 수 있습니다. 아래 명령은 앱 코드나 연구 데이터를 복사·덮어쓰지 않습니다. `/mnt/c`에 Windows 디렉터리가 마운트되어 있어야 합니다.

```bash
cd ~/projects/산학/emolabel-youtube
PYTHONPATH=/mnt/c/Users/<windows-user>/Desktop/산학/emolabel-youtube uv run python -m tools.reproduce_boundaries --project "$PWD" --output data/evaluations/r0.json
PYTHONPATH=/mnt/c/Users/<windows-user>/Desktop/산학/emolabel-youtube uv run python -m tools.evaluate_models --manifest data/workspace/pilot.json --runs data/workspace/emotion-runs/* --output data/evaluations/r3-coverage.json
```

## R1 오류 진단·R2 경계 보정 적용

선정 버전 3은 ASR 시작·끝이 VAD 발화 내부인지 검사합니다. 가까운 VAD 경계까지의 차이가 0.5초 이내이면 0.08초 여백을 더해 확장을 시도합니다. 원본·최대 클립 길이를 넘거나 인접 ASR 대사를 침범하면 확장하지 않습니다. 여백이 다른 VAD 발화에 닿으면 그 앞에서 제한합니다. 다음 발화가 바로 이어지는 구간은 자동 병합하지 않고 검수자가 원본을 이어 들어 경계를 조정합니다.

| 후보의 품질 플래그 | 의미·검수 방법 |
| --- | --- |
| `speech_continues_before` | 앞쪽 발화가 이어질 가능성. 원본을 앞쪽으로 이어 듣고 시작점 조정 |
| `speech_continues_after` | 뒤쪽 발화가 이어질 가능성. 원본을 뒤쪽으로 이어 듣고 끝점 조정 |
| `boundary_adjusted_check_transcript` | 짧은 경계를 자동 확장했으므로 추가 음성과 대사 대응을 확인 |

이 후보는 기존 UI의 **품질 확인 큐**에서 확인할 수 있습니다. 보정 전 경계는 `asr_start_s`·`asr_end_s`, 원래 후보 ID는 `source_asr_clip_id`, 보정량은 `boundary_adjustment_ms`로 기록합니다. 선정 버전이 바뀌면 이전 `state.json`은 해당 영상의 `selection-history/{해시}.json`에 보존합니다. 기존 미디어·사람 검수 이력·확정본은 삭제하지 않습니다. 새 경계 실험에는 `pilot-v3.json` 등 새 manifest를 사용하세요. 기존 `pilot.json`의 모델 재실행에는 이전 데이터 스냅샷을 사용하고 새 후보와 섞지 않습니다.

로컬 모델 오류는 기존 `error`/`reason`에 더해 `diagnostic.stage`·`diagnostic.code`로 기록합니다. 진단은 `data/workspace/emotion-runs/{run_id}/{clip_id}.json`의 `results.text`·`results.audio` 및 `emotion-latest.json`에 저장됩니다.

| 진단 단계 | 코드 예시 |
| --- | --- |
| `configuration` | `model_not_configured`, `invalid_configuration` |
| `input_validation` | `invalid_request`, `missing_input`, `invalid_audio`, `invalid_settings` |
| `model_load` | `model_missing`, `dependency_missing`, `checkpoint_load_failed` |
| `preprocessing` | `feature_extraction_failed` |
| `inference` | `inference_exception`, `timeout` |
| `output_validation` | `invalid_prediction`, `invalid_protocol_output` |
| `process_launch` / `process_exit` | `executable_missing`, `spawn_failed` / `nonzero_exit` (종료 코드 포함) |

기존 실행기의 진단 없는 오류도 계속 읽을 수 있으며 `runner/legacy_runner_error`로 구분합니다. 진단에 예외 원문·대사·토큰·개인 경로·stderr를 넣지 않습니다. 허용되지 않은 진단 필드는 출력 오류로 처리합니다. 외부 종료 코드는 기록하지만, 종료 코드만으로 OOM 등 원인을 확정하지 않습니다.

현재 Windows 체크아웃의 R1·R2 코드를 Ubuntu에 적용할 때는 아래 파일을 동기화한 후 실행하세요. 이전 R0·R3 도구와 달리 앱 모듈도 바뀌므로 `PYTHONPATH`만 지정하는 방법으로는 Ubuntu의 기존 앱 코드가 그대로 실행될 수 있습니다. Ubuntu 코드에 별도 수정이 있다면 먼저 diff를 확인해 병합하세요.

```bash
cd ~/projects/산학/emolabel-youtube
emolabel_source=/mnt/c/Users/<windows-user>/Desktop/산학/emolabel-youtube
for file in app/candidates.py app/emotion.py app/emotion_scores.py app/review.py app/review_api.py app/schemas.py static/app.js static/index.html static/review.mjs static/style.css tools/run_batch.py tools/local_emotion.py tools/scored_emotion.py tools/configure_emotion.py tools/check_emotion_connection.py tools/reproduce_boundaries.py tools/evaluate_models.py test_candidates.py test_batch.py test_boundary_reproduction.py test_boundary_playback.mjs test_emotion.py test_local_emotion.py test_scored_emotion.py test_emotion_batch.py test_emotion_review.py test_evaluate_models.py test_manual_candidates.py test_review_api.py test_review_ui.mjs; do
  cp "$emolabel_source/$file" "$file"
done
uv run python -m unittest discover -v
node --test test_review_ui.mjs test_boundary_playback.mjs
uv run python -m tools.run_batch --video-workers 1
uv run python -m tools.reproduce_boundaries --project . --output data/evaluations/r0-v3.json
uv run python -m tools.run_batch --export-candidates data/workspace/pilot-v3.json
```

출력 파일이 이미 있으면 새 이름을 지정합니다. 실제 잘림 개선 여부는 새 후보와 원본·보존된 이전 클립을 청취해 확인합니다. 경고만 추가된 사례를 잘림 해결로 집계하지 않습니다. R1 진단 정보는 이후 추론을 실행할 때 생성되며 기존 추론 기록은 소급해서 변경하지 않습니다.

## 수동 구간 추가 (3.5단계)

자동 후보에서 빠진 발화는 검수 화면의 **＋ 새 구간 추가**로 원본 파형에서 직접 선택해 독립적인 `pending` 후보로 추가할 수 있습니다. 자동 후보가 없는 영상도 상단 URL **수동 불러오기** 후 같은 버튼으로 검수 큐에 구간을 추가할 수 있습니다.

- 추가 모드에 들어가거나 취소해도 기존 후보 경계와 작성 중인 검수 입력은 바뀌지 않으며, 새 후보를 추가하면 기존 후보의 작성 초안은 세션 내 보관됩니다.
- 수동 후보는 `data/workspace/{video_id}/reviews.json`에 `origin: "manual"`과 `manual_creation`(`annotator_id`, `at`, `active_seconds`)으로 영속 저장되며, 기계가 재생성하는 `state.json` 및 `original_asr` 고정 평가 manifest에는 섞이지 않습니다.
- 전사 보조(선택): 배치 실행으로 `data/workspace/{video_id}/asr.json`이 이미 있으면 겹치는 구간 대사를 즉시 초안으로 채우고(없거나 독립 검수 모드면 빈 대사), 검수 화면의 **대사 자동 전사** 버튼(`POST /api/review/candidates/{clip_id}/transcribe`)으로 필요 시 해당 구간 WAV를 단건 Whisper 전사할 수 있습니다. 전사 실패·미설치 시에도 수동 추가와 직접 입력을 막지 않습니다.
- 확정 저장 시 `data/output/{clip_id}/annotation.json`에 `candidate_origin: "manual"`과 `manual_creation`이 기록됩니다. 경계를 바꾸지 않고 대사를 입력해 `채택`으로 저장하면 `accepted` 상태를 유지합니다.
- 관련 검증: `uv run python -m unittest -v test_manual_candidates test_review test_review_api test_portability` 및 `node --test test_review_ui.mjs test_boundary_playback.mjs`.

## 작업 및 저장

URL 불러오기 → 파형에서 발화 선택 → 구간 추출 → 원음과 전사 확인 → 감정·품질 메모·본인 ID → 저장 순서입니다. 전사와 QC는 보조 정보이며 감정을 자동 확정하지 않습니다.

```text
data/output/{clip_id}/
  clip.mp4
  audio_16k.wav
  prelabels.json      # 기계 초안: 생성된 경우
  annotation.json     # 사람 저장 완료 후 생성
```

정답은 `labels.emotion`과 `labels.transcript`입니다. `prelabels`는 기계 초안입니다. valence/arousal은 이번 단계에서 평가하지 않으며, 저장된 기본값 4를 평정값으로 분석하면 안 됩니다. 새 검수에서는 CONF·QUALITY·AMBIG를 직접 선택합니다.

진행상황·누락 점검:

```bash
uv run python tools/dataset_status.py --root data/output
```

## 공용 PC와 제출

한 번에 한 사람씩 작업합니다. 교대 시 새 세션을 시작하고 본인의 검수자 ID를 입력하세요. 각자의 별도 복사본에서 작업하거나 `.env`의 `EMOLABEL_DATA_DIR`에 서로 다른 **절대 경로**를 지정하세요. 모델 캐시만 공유하려면 `EMOLABEL_HF_HOME`을 공용 경로로 설정할 수 있지만, data/output은 분리합니다.

작업 종료 후 각자의 output을 `날짜_annotatorID` 폴더 아래 압축해 전달합니다. 병합 담당자는 같은 clip_id가 있으면 파일을 덮어쓰지 말고 대사·라벨 차이를 확인합니다. 서버로 자동 수집되지는 않습니다.

## 저장소에 포함되는 파일

이 비공개 저장소에는 앱 코드, 설치 의존성 잠금 파일, 실행 안내, 라벨링/GPT 활용 가이드만 포함합니다. 얼굴 수 QC에 필요한 기존 소형 YuNet 자산은 `app/models/`에 포함되어 있습니다. 공개 저장소로 바꾸기 전에는 코드·동봉된 서드파티 자산의 배포 조건을 확인하세요.

`.gitignore`는 `.env` 및 비밀 설정, data/, .venv/, 캐시, 영상·음성·ZIP, 다운로드한 모델 가중치를 제외합니다. 토큰·쿠키·수집 영상을 Git에 넣지 마세요. `.env.example`의 토큰은 비어 있으며, 토큰 없이도 기존 화자 수 QC 대체 경로를 사용합니다. 게이트 모델은 해당 모델의 이용 조건을 별도로 확인해야 합니다.

## 가이드

- [어노테이션 가이드](docs/ANNOTATION_GUIDE.md)
- [GPT 활용 가이드](docs/GPT_ASSISTANCE.md)
- [파일럿 데이터 설명](docs/DATASET_CARD.md)
