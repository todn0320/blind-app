// =======================
// 0) 카메라 초기화
// =======================
const video = document.getElementById("video");
const canvas = document.getElementById("captureCanvas");
const ctx = canvas.getContext("2d");

let lastImageDataURL = null; // 마지막으로 캡쳐한 프레임 저장

async function initCamera() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      video: true,
      audio: false,
    });
    video.srcObject = stream;
  } catch (err) {
    console.error("카메라 접근 오류:", err);
    alert("카메라에 접근할 수 없습니다. 권한을 확인해 주세요.");
  }
}

initCamera();

function captureFrameAsDataURL() {
  if (!video.videoWidth || !video.videoHeight) {
    alert("카메라가 아직 준비되지 않았습니다.");
    return null;
  }
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
  const dataURL = canvas.toDataURL("image/jpeg");
  lastImageDataURL = dataURL;
  return dataURL;
}

function appendLog(prefix, text, type = "me") {
  const qaLog = document.getElementById("qaLog");
  const line = document.createElement("div");

  let cls = "log-me";
  if (type === "ai") cls = "log-ai";
  else if (type === "system") cls = "log-system";

  line.className = cls;
  line.textContent = `${prefix} ${text}`;
  qaLog.appendChild(line);

  qaLog.scrollTop = qaLog.scrollHeight;
}

// =======================
// 1) 지금 장면 설명 듣기 (/api/caption)
// =======================
const captionBtn = document.getElementById("captionBtn");
const captionTextInner = document.getElementById("captionTextInner");

captionBtn.addEventListener("click", async () => {
  const imageData = captureFrameAsDataURL();
  if (!imageData) return;

  captionBtn.disabled = true;
  captionBtn.textContent = "분석 중...";
  captionTextInner.textContent = "장면을 분석하는 중입니다...";

  try {
    const res = await fetch("/api/caption", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ image: imageData }),
    });

    const data = await res.json();

    if (data.error) {
      captionTextInner.textContent = "오류: " + data.error;
      return;
    }

    captionTextInner.innerText =
      "BLIP 캡션: " +
      data.raw_caption +
      "\n\n한국어 설명: " +
      data.korean_caption;

    if (data.tts_url) {
      console.log("caption tts_url:", data.tts_url);
      const audio = new Audio(data.tts_url);
      audio.play().catch((e) => {
        console.error("캡션 오디오 자동 재생 실패:", e);
      });

      // 혹시 브라우저 자동재생이 막혀도 직접 눌러볼 수 있게 링크 표시
      captionTextInner.innerText +=
        `\n\n(▶ 음성 파일: ${window.location.origin}${data.tts_url})`;
    } else {
      console.log("caption tts_url 없음");
    }
  } catch (err) {
    console.error(err);
    captionTextInner.textContent = "요청 중 오류가 발생했습니다.";
  } finally {
    captionBtn.disabled = false;
    captionBtn.textContent = "▷ 지금 장면 설명 듣기";
  }
});

// =======================
// 2) 텍스트 질문 (/api/ask)
// =======================
const questionInput = document.getElementById("questionInput");
const askBtn = document.getElementById("askBtn");

askBtn.addEventListener("click", async () => {
  const question = questionInput.value.trim();
  if (!question) {
    alert("질문을 입력해 주세요.");
    return;
  }

  // 아직 캡쳐된 이미지가 없다면 한 번 캡쳐
  if (!lastImageDataURL) {
    const img = captureFrameAsDataURL();
    if (!img) return;
  }

  appendLog("[나 - 텍스트]", question, "me");
  questionInput.value = "";
  askBtn.disabled = true;

  try {
    const res = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question: question,
        image: lastImageDataURL,
      }),
    });

    const data = await res.json();

    if (data.error) {
      appendLog("[시스템 오류]", data.answer, "system");
      return;
    }

    appendLog("[AI]", data.answer, "ai");

    if (data.tts_url) {
      console.log("ask tts_url:", data.tts_url);
      const audio = new Audio(data.tts_url);
      audio.play().catch((e) =>
        console.error("텍스트 질문 오디오 실패:", e)
      );
    } else {
      console.log("ask tts_url 없음");
    }
  } catch (err) {
    console.error(err);
    appendLog(
      "[시스템 오류]",
      "서버 통신 중 오류가 발생했습니다.",
      "system"
    );
  } finally {
    askBtn.disabled = false;
  }
});

// 👉 Enter 키로도 질문 전송
questionInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    askBtn.click();
  }
});

// =======================
// 3) 음성 질문 (/api/voice-ask)
// =======================
const voiceBtn = document.getElementById("voiceBtn");

let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;

voiceBtn.addEventListener("click", async () => {
  if (!isRecording) {
    await startVoiceRecording();
  } else {
    stopVoiceRecording();
  }
});

async function startVoiceRecording() {
  isRecording = true;
  voiceBtn.textContent = "🎙 녹음 중... (다시 누르면 전송)";
  voiceBtn.classList.add("btn-primary");

  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  mediaRecorder = new MediaRecorder(stream);
  audioChunks = [];

  mediaRecorder.ondataavailable = (e) => {
    audioChunks.push(e.data);
  };

  mediaRecorder.onstop = async () => {
    const blob = new Blob(audioChunks, { type: "audio/webm" });

    // 아직 캡쳐한 이미지가 없다면 한 번 캡쳐
    if (!lastImageDataURL) {
      const img = captureFrameAsDataURL();
      if (!img) return;
    }

    const formData = new FormData();
    formData.append("audio", blob, "voice.webm");
    formData.append("image", lastImageDataURL);

    appendLog("[나 - 음성]", "(질문 전송 중...)", "me");

    try {
      const res = await fetch("/api/voice-ask", {
        method: "POST",
        body: formData,
      });

      const data = await res.json();

      if (data.error) {
        appendLog("[시스템 오류]", data.answer, "system");
        return;
      }

      appendLog("[나 - 음성 텍스트]", data.question, "me");
      appendLog("[AI]", data.answer, "ai");

      if (data.tts_url) {
        console.log("voice tts_url:", data.tts_url);
        const audio = new Audio(data.tts_url);
        audio.play().catch((e) =>
          console.error("음성 질문 오디오 실패:", e)
        );
      } else {
        console.log("voice tts_url 없음");
      }
    } catch (err) {
      console.error(err);
      appendLog(
        "[시스템 오류]",
        "음성 질문 전송 중 오류가 발생했습니다.",
        "system"
      );
    } finally {
      voiceBtn.disabled = false;
      voiceBtn.textContent = "🎙 음성 질문 시작";
      voiceBtn.classList.remove("btn-primary");
    }
  };

  mediaRecorder.start();
}

function stopVoiceRecording() {
  if (!mediaRecorder) return;
  isRecording = false;
  voiceBtn.disabled = true; // 응답 올 때까지 잠깐 비활성화
  mediaRecorder.stop();
}
