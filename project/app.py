from flask import Flask, request, jsonify, render_template, send_from_directory
from io import BytesIO
from PIL import Image
import base64
import os
import time
import torch

from transformers import BlipProcessor, BlipForConditionalGeneration
from peft import PeftModel
from gtts import gTTS
from openai import OpenAI

# -----------------------------
#  경로 / 환경 설정
# -----------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

BASE_MODEL = "Salesforce/blip-image-captioning-base"

# LoRA 경로 (app.py 기준)
LORA_DIR = os.path.join(BASE_DIR, "blip_lora_ko")
ADAPTER_DIR = os.path.join(LORA_DIR, "adapter")
PROCESSOR_DIR = os.path.join(LORA_DIR, "processor")

# 🔊 TTS 파일은 static/tts 밑에 저장
TTS_DIR = os.path.join(BASE_DIR, "static", "tts")
os.makedirs(TTS_DIR, exist_ok=True)

DEVICE = torch.device("cpu")

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

# Flask 앱
app = Flask(__name__, static_folder="static", static_url_path="/")

# OpenAI LLM 클라이언트
llm_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


# -----------------------------
#  BLIP + LoRA 로딩
# -----------------------------
def load_model():
    """
    1) BLIP base 모델 로드
    2) LoRA adapter를 merge
    3) processor 로드
    """
    print("🔄 BLIP + LoRA 모델 로딩 중...")

    # processor
    if os.path.isdir(PROCESSOR_DIR):
        processor = BlipProcessor.from_pretrained(PROCESSOR_DIR)
    else:
        processor = BlipProcessor.from_pretrained(BASE_MODEL)

    # base 모델
    base_model = BlipForConditionalGeneration.from_pretrained(BASE_MODEL)

    # LoRA 어댑터 merge
    if os.path.isdir(ADAPTER_DIR):
        print("🔹 LoRA 어댑터 적용...")
        lora_model = PeftModel.from_pretrained(base_model, ADAPTER_DIR)
        model = lora_model.merge_and_unload()
    else:
        print("⚠ adapter 폴더 없음 → base 모델만 사용")
        model = base_model

    model.to(DEVICE)
    model.eval()
    print("✅ BLIP 로딩 완료!")
    return processor, model


processor, blip_model = load_model()


# -----------------------------
#  유틸 함수들
# -----------------------------
def blip_caption_from_base64(image_b64: str) -> str:
    """Base64 이미지에서 BLIP 캡션 뽑기"""
    img_bytes = base64.b64decode(image_b64)
    pil_image = Image.open(BytesIO(img_bytes)).convert("RGB")

    inputs = processor(images=pil_image, return_tensors="pt").to(DEVICE)
    with torch.no_grad():
        output_ids = blip_model.generate(
            **inputs,
            max_length=40,
            num_beams=5,
            no_repeat_ngram_size=2,
        )

    caption = processor.decode(output_ids[0], skip_special_tokens=True).strip()
    print("[BLIP 캡션]", caption)
    return caption


def make_korean_caption(raw_caption: str) -> str:
    """
    BLIP가 뽑은 캡션(raw_caption)을
    시각장애인이 듣기 좋은 자연스러운 한국어 한두 문장으로 정리.
    (OPENAI_API_KEY 없으면 그냥 원문 사용)
    """
    if llm_client is None:
        return raw_caption

    completion = llm_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "너는 시각장애인을 위한 화면 설명 도우미야. "
                    "입력된 문장을 바탕으로, 자연스러운 한국어 한두 문장으로 "
                    "존댓말로 설명해줘. 군더더기 없이 핵심만 말해."
                ),
            },
            {
                "role": "user",
                "content": f"다음 캡션을 한국어로 정리해줘: {raw_caption}",
            },
        ],
    )
    text = completion.choices[0].message.content.strip()
    print("[한국어 캡션]", text)
    return text


def save_tts_korean(text: str, filename: str) -> str:
    """간단 TTS 생성 (gTTS 한국어) -> static/tts/filename.mp3"""
    path = os.path.join(TTS_DIR, filename)
    print(f"[TTS] 저장 경로: {path}")
    tts = gTTS(text=text, lang="ko")
    tts.save(path)
    return path


def stt_korean_file(audio_file) -> str:
    """
    업로드된 오디오 파일(웹m 등)을 Whisper로 한국어 텍스트로 변환.
    """
    if llm_client is None:
        return ""

    tmp_name = f"voice_{int(time.time())}.webm"
    tmp_path = os.path.join(TTS_DIR, tmp_name)
    audio_file.save(tmp_path)
    print(f"[STT] 임시 오디오 저장: {tmp_path}")

    with open(tmp_path, "rb") as f:
        result = llm_client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            language="ko",
        )
    print("[STT 결과]", result.text)
    return result.text.strip()


# -----------------------------
#  라우터
# -----------------------------
@app.route("/")
def index():
    # templates/index.html 렌더링
    return render_template("index.html")


# 🔊 TTS mp3 서빙
@app.route("/tts/<filename>")
def serve_tts(filename):
    print(f"[TTS 서빙 요청] {filename}")
    return send_from_directory(TTS_DIR, filename)


# -----------------------------
# 1) 캡션: 지금 장면 설명 + 한국어 TTS
# -----------------------------
@app.route("/api/caption", methods=["POST"])
def api_caption():
    data = request.get_json()
    image_b64 = data.get("image")

    if not image_b64:
        return jsonify({"error": "image field not found"}), 400

    # dataURL 형식일 경우 앞부분 제거
    if "," in image_b64:
        image_b64 = image_b64.split(",", 1)[1]

    try:
        raw_caption = blip_caption_from_base64(image_b64)
    except Exception as e:
        print("[ERROR] caption error:", e)
        return jsonify({"error": f"caption error: {e}"}), 500

    korean_caption = make_korean_caption(raw_caption)

    # 한국어 설명을 TTS로 읽어주기
    tts_url = None
    try:
        filename = "caption.mp3"
        tts_path = save_tts_korean(korean_caption, filename)
        tts_url = f"/tts/{filename}"
        print("[TTS URL]", tts_url)
    except Exception as e:
        print("[ERROR] TTS 생성 실패:", e)
        tts_url = None

    return jsonify(
        {
            "raw_caption": raw_caption,
            "korean_caption": korean_caption,
            "tts_url": tts_url,
        }
    )


# -----------------------------
# 2) 텍스트 채팅 Q&A
# -----------------------------
@app.route("/api/ask", methods=["POST"])
def api_ask():
    if llm_client is None:
        return jsonify(
            {
                "answer": "LLM API 키가 설정되지 않았습니다. "
                "터미널에서 OPENAI_API_KEY 환경변수를 설정해 주세요.",
                "error": True,
            }
        )

    data = request.get_json()
    question = (data.get("question") or "").strip()
    image_b64 = data.get("image")

    if not question:
        return jsonify({"answer": "질문이 비어 있습니다.", "error": True})

    if not image_b64:
        return jsonify({"answer": "이미지가 전송되지 않았습니다.", "error": True})

    if "," in image_b64:
        image_b64 = image_b64.split(",", 1)[1]

    try:
        response = llm_client.responses.create(
            model="gpt-4o-mini",
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "너는 시각장애인을 위한 장면 설명 도우미야. "
                                "아래 이미지를 보고, 사용자의 질문에 대해 "
                                "한국어로 1~2문장 정도로 짧고 분명하게 대답해 줘.\n\n"
                                f"질문: {question}"
                            ),
                        },
                        {
                            "type": "input_image",
                            "image_url": "data:image/jpeg;base64," + image_b64,
                        },
                    ],
                }
            ],
        )

        answer_text = response.output[0].content[0].text.strip()
        print("[텍스트 Q&A 답변]", answer_text)

    except Exception as e:
        print("[ERROR] LLM 호출 실패:", e)
        return jsonify(
            {"answer": f"LLM 호출 중 오류가 발생했습니다: {e}", "error": True}
        )

    # 답변도 TTS로 읽어주기
    tts_url = None
    try:
        filename = f"answer_{int(time.time())}.mp3"
        save_tts_korean(answer_text, filename)
        tts_url = f"/tts/{filename}"
        print("[Q&A TTS URL]", tts_url)
    except Exception as e:
        print("[ERROR] Q&A TTS 실패:", e)
        tts_url = None

    return jsonify({"answer": answer_text, "error": False, "tts_url": tts_url})


# -----------------------------
# 3) 음성 Q&A: 음성 → STT → Vision Q&A
# -----------------------------
@app.route("/api/voice-ask", methods=["POST"])
def api_voice_ask():
    if llm_client is None:
        return jsonify(
            {
                "answer": "OPENAI_API_KEY가 설정되지 않았습니다.",
                "error": True,
            }
        )

    audio_file = request.files.get("audio")
    image_b64 = request.form.get("image")

    if not audio_file:
        return jsonify({"answer": "오디오가 전송되지 않았습니다.", "error": True})

    if not image_b64:
        return jsonify({"answer": "이미지가 전송되지 않았습니다.", "error": True})

    # 1) STT로 질문 텍스트 얻기
    try:
        question_text = stt_korean_file(audio_file)
        if not question_text:
            return jsonify({"answer": "음성을 인식하지 못했습니다.", "error": True})
    except Exception as e:
        print("[ERROR] STT 오류:", e)
        return jsonify({"answer": f"STT 오류: {e}", "error": True})

    # 2) base64 헤더 제거
    if "," in image_b64:
        image_b64 = image_b64.split(",", 1)[1]

    # 3) Vision Q&A 호출
    try:
        response = llm_client.responses.create(
            model="gpt-4o-mini",
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "너는 시각장애인을 위한 장면 설명 도우미야. "
                                "아래 이미지를 보고, 사용자의 질문에 대해 "
                                "한국어로 1~2문장 정도로 짧고 분명하게 대답해 줘.\n\n"
                                f"질문: {question_text}"
                            ),
                        },
                        {
                            "type": "input_image",
                            "image_url": "data:image/jpeg;base64," + image_b64,
                        },
                    ],
                }
            ],
        )

        answer_text = response.output[0].content[0].text.strip()
        print("[음성 Q&A 답변]", answer_text)

    except Exception as e:
        print("[ERROR] LLM(voice) 호출 실패:", e)
        return jsonify({"answer": f"LLM 오류: {e}", "error": True})

    # 4) 답변도 TTS로 읽어주기
    tts_url = None
    try:
        filename = f"voice_answer_{int(time.time())}.mp3"
        save_tts_korean(answer_text, filename)
        tts_url = f"/tts/{filename}"
        print("[VOICE Q&A TTS URL]", tts_url)
    except Exception as e:
        print("[ERROR] VOICE TTS 실패:", e)
        tts_url = None

    return jsonify(
        {
            "question": question_text,
            "answer": answer_text,
            "tts_url": tts_url,
            "error": False,
        }
    )


# -----------------------------
# 메인
# -----------------------------
if __name__ == "__main__":
    # 개발용 서버 실행
    app.run(host="0.0.0.0", port=5000, debug=True)
