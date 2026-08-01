// 通用工具：JSON 请求、删除确认、任务轮询

async function api(method, url, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const resp = await fetch(url, opts);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.detail || `请求失败（HTTP ${resp.status}）`);
  return data;
}

async function apiAction(method, url, body, { confirmMsg, reload = true } = {}) {
  if (confirmMsg && !window.confirm(confirmMsg)) return;
  try {
    const data = await api(method, url, body);
    if (data.task_id) {
      window.location.href = `/tasks/${data.task_id}/wait`;
      return;
    }
    if (data.redirect_url) {
      window.location.href = data.redirect_url;
      return;
    }
    if (reload) window.location.reload();
  } catch (e) {
    alert(e.message);
  }
}

// 任务等待页轮询
function pollTask(taskId) {
  const bar = document.getElementById("task-progress");
  const msg = document.getElementById("task-msg");
  const errBox = document.getElementById("task-error");

  const timer = setInterval(async () => {
    let data;
    try {
      data = await api("GET", `/api/tasks/${taskId}`);
    } catch (e) {
      return; // 网络抖动，下轮再试
    }
    bar.value = data.progress;
    msg.textContent = data.progress_msg || "";
    if (data.status === "success") {
      clearInterval(timer);
      msg.textContent = "完成，正在跳转…";
      window.location.href = data.redirect_url || "/";
    } else if (data.status === "failed") {
      clearInterval(timer);
      errBox.style.display = "block";
      errBox.querySelector("p").textContent = data.error || "未知错误";
    }
  }, 2000);
}

// 多文件上传表单（条款/健康材料共用）
function bindUploadForm(formId, url) {
  const form = document.getElementById(formId);
  if (!form) return;
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const btn = form.querySelector("button[type=submit]");
    btn.setAttribute("aria-busy", "true");
    btn.disabled = true;
    try {
      const resp = await fetch(url, { method: "POST", body: new FormData(form) });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data.detail || `上传失败（HTTP ${resp.status}）`);
      if (data.task_id) {
        window.location.href = `/tasks/${data.task_id}/wait`;
      } else if (data.redirect_url) {
        if (data.message) alert(data.message);
        window.location.href = data.redirect_url;
      } else {
        window.location.reload();
      }
    } catch (e) {
      alert(e.message);
      btn.removeAttribute("aria-busy");
      btn.disabled = false;
    }
  });
}
