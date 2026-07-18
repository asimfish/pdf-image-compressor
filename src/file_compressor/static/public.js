(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const fileInput = $("fileInput");
  const dropzone = $("dropzone");
  const fileCard = $("fileCard");
  const clearFileButton = $("clearFile");
  const submitButton = $("submitButton");
  const progress = $("progress");
  const progressFill = $("progressFill");
  const progressText = $("progressText");
  const elapsed = $("elapsed");
  const result = $("result");
  const error = $("error");
  const download = $("download");

  let selectedFile = null;
  let maxUploadBytes = 50 * 1024 * 1024;
  let downloadUrl = null;
  let timer = null;
  let isBusy = false;

  const formatBytes = (bytes) => {
    if (!Number.isFinite(bytes) || bytes < 0) return "—";
    if (bytes < 1024) return `${bytes} B`;
    const units = ["KB", "MB", "GB"];
    let value = bytes / 1024;
    let index = 0;
    while (value >= 1024 && index < units.length - 1) {
      value /= 1024;
      index += 1;
    }
    return `${value.toFixed(value >= 10 ? 1 : 2)} ${units[index]}`;
  };

  const showError = (message) => {
    error.textContent = message;
    error.classList.add("visible");
    result.classList.remove("visible");
  };

  const clearFeedback = () => {
    error.classList.remove("visible");
    result.classList.remove("visible");
  };

  const chooseFile = (file) => {
    if (isBusy) return;
    clearFeedback();
    if (!file || !file.name.toLowerCase().endsWith(".pdf")) {
      showError("请选择 PDF 文件。");
      return;
    }
    if (file.size > maxUploadBytes) {
      showError(`文件为 ${formatBytes(file.size)}，超过 ${formatBytes(maxUploadBytes)} 的上传限制。`);
      return;
    }
    selectedFile = file;
    $("fileName").textContent = file.name;
    $("fileSize").textContent = formatBytes(file.size);
    fileCard.classList.add("visible");
    submitButton.disabled = false;
  };

  const resetFile = () => {
    if (isBusy) return;
    selectedFile = null;
    fileInput.value = "";
    fileCard.classList.remove("visible");
    submitButton.disabled = true;
    clearFeedback();
  };

  fileInput.addEventListener("change", () => chooseFile(fileInput.files[0]));
  clearFileButton.addEventListener("click", resetFile);

  const setBusy = (busy) => {
    isBusy = busy;
    fileInput.disabled = busy;
    clearFileButton.disabled = busy;
    submitButton.disabled = busy || !selectedFile;
    dropzone.setAttribute("aria-busy", String(busy));
  };

  ["dragenter", "dragover"].forEach((name) => {
    dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      dropzone.classList.add("dragging");
    });
  });
  ["dragleave", "drop"].forEach((name) => {
    dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      dropzone.classList.remove("dragging");
    });
  });
  dropzone.addEventListener("drop", (event) => chooseFile(event.dataTransfer.files[0]));

  const parseError = (text, status) => {
    try {
      const body = JSON.parse(text);
      return body.detail || "压缩失败，请稍后重试。";
    } catch (_) {
      return status === 429 ? "服务正在处理另一份文件，请稍后重试。" : "压缩失败，请检查文件后重试。";
    }
  };

  const startTimer = () => {
    const started = Date.now();
    clearInterval(timer);
    timer = setInterval(() => {
      elapsed.textContent = `${Math.round((Date.now() - started) / 1000)} 秒`;
    }, 1000);
  };

  const stopTimer = () => {
    clearInterval(timer);
    timer = null;
  };

  $("compressForm").addEventListener("submit", (event) => {
    event.preventDefault();
    if (!selectedFile) return;
    const inputFile = selectedFile;
    clearFeedback();

    const target = Number($("targetValue").value);
    const unit = $("targetUnit").value;
    if (!Number.isFinite(target) || target <= 0) {
      showError("请输入有效的目标大小。");
      return;
    }

    const form = new FormData();
    form.append("files", inputFile, inputFile.name);
    form.append("target_size", `${target}${unit}`);
    form.append("pdf_mode", document.querySelector('input[name="pdfMode"]:checked').value);
    form.append("quality", "82");
    form.append("pdf_dpi", "120");
    form.append("compression_level", "2");
    form.append("strip_metadata", "true");

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/compress");
    xhr.responseType = "blob";
    xhr.timeout = 15 * 60 * 1000;
    setBusy(true);
    progress.classList.add("visible");
    progressFill.style.width = "6%";
    progressText.textContent = "上传中";
    elapsed.textContent = "0 秒";
    startTimer();

    xhr.upload.onprogress = (uploadEvent) => {
      if (!uploadEvent.lengthComputable) return;
      const percent = Math.min(72, Math.max(6, Math.round(uploadEvent.loaded / uploadEvent.total * 72)));
      progressFill.style.width = `${percent}%`;
      if (uploadEvent.loaded >= uploadEvent.total) {
        progressText.textContent = "智能压缩中";
        progressFill.style.width = "82%";
      }
    };

    xhr.onload = async () => {
      stopTimer();
      setBusy(false);
      progress.classList.remove("visible");
      if (xhr.status < 200 || xhr.status >= 300) {
        const text = await xhr.response.text();
        showError(parseError(text, xhr.status));
        return;
      }

      if (downloadUrl) URL.revokeObjectURL(downloadUrl);
      downloadUrl = URL.createObjectURL(xhr.response);
      const compressed = xhr.response.size;
      const saved = Math.max(0, inputFile.size - compressed);
      const ratio = inputFile.size ? saved / inputFile.size * 100 : 0;
      const stem = inputFile.name.replace(/\.pdf$/i, "");
      download.href = downloadUrl;
      download.download = `${stem}-compressed.pdf`;
      $("saving").textContent = `−${ratio.toFixed(1)}%`;
      $("resultMeta").textContent = `${formatBytes(inputFile.size)} → ${formatBytes(compressed)}，节省 ${formatBytes(saved)}`;
      result.classList.add("visible");
      download.click();
    };

    xhr.onerror = () => {
      stopTimer();
      setBusy(false);
      progress.classList.remove("visible");
      showError("网络连接中断，请重新提交。");
    };

    xhr.ontimeout = () => {
      stopTimer();
      setBusy(false);
      progress.classList.remove("visible");
      showError("处理超时，请尝试更小的 PDF 或稍后重试。");
    };

    xhr.send(form);
  });

  fetch("/api/config", { cache: "no-store" })
    .then((response) => response.json())
    .then((config) => {
      if (Number.isFinite(config.max_upload_bytes)) maxUploadBytes = config.max_upload_bytes;
      const pages = config.max_pages ? `，最多 ${config.max_pages} 页` : "";
      $("limitCopy").textContent = `最大 ${formatBytes(maxUploadBytes)}${pages}`;
    })
    .catch(() => {
      $("limitCopy").textContent = `最大 ${formatBytes(maxUploadBytes)}`;
    });
})();
