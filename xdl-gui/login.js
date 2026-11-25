"use strict";

(() => {
  const form = document.getElementById("login-form");
  const userInput = document.getElementById("login-username");
  const passInput = document.getElementById("login-password");
  const rememberInput = document.getElementById("login-remember");
  const submitBtn = document.getElementById("btn-login-submit");
  const toggleBtn = document.getElementById("btn-toggle-pw");
  const errorBox = document.getElementById("login-error");
  const noticeBox = document.getElementById("login-notice");
  const modeTag = document.getElementById("login-mode-tag");

  let lockTimer = null;
  let busy = false;

  const EYE_OPEN = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
  const EYE_OFF = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17.94 17.94A10.94 10.94 0 0 1 12 20c-7 0-11-8-11-8a21.77 21.77 0 0 1 5.06-6.94"/><path d="M9.9 4.24A10.94 10.94 0 0 1 12 4c7 0 11 8 11 8a21.78 21.78 0 0 1-3.17 4.73"/><line x1="1" y1="1" x2="23" y2="23"/></svg>';

  function showError(msg) {
    if (!msg) {
      errorBox.classList.add("hidden");
      errorBox.textContent = "";
      return;
    }
    errorBox.textContent = msg;
    errorBox.classList.remove("hidden");
  }

  function showNotice(msg, kind = "info") {
    if (!msg) {
      noticeBox.classList.add("hidden");
      noticeBox.textContent = "";
      return;
    }
    noticeBox.textContent = msg;
    noticeBox.className = `login-banner ${kind === "warn" ? "is-warn" : "is-info"}`;
  }

  function setBusy(nextBusy) {
    busy = nextBusy;
    submitBtn.disabled = nextBusy;
    submitBtn.querySelector("span").textContent = nextBusy ? "正在验证…" : "登录工作台";
  }

  function startLockCountdown(seconds) {
    if (lockTimer) clearInterval(lockTimer);
    let left = Math.max(1, Math.round(Number(seconds) || 0));
    const render = () => {
      const m = Math.floor(left / 60);
      const s = left % 60;
      const clock = `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
      submitBtn.disabled = true;
      submitBtn.querySelector("span").textContent = `已锁定 · ${clock}`;
      showError(`连续输错密码次数过多，请在 ${clock} 后重试`);
    };
    render();
    lockTimer = setInterval(() => {
      left -= 1;
      if (left <= 0) {
        clearInterval(lockTimer);
        lockTimer = null;
        submitBtn.disabled = false;
        submitBtn.querySelector("span").textContent = "登录工作台";
        showError("");
        return;
      }
      render();
    }, 1000);
  }

  toggleBtn.addEventListener("click", () => {
    const show = passInput.type === "password";
    passInput.type = show ? "text" : "password";
    toggleBtn.setAttribute("aria-pressed", show ? "true" : "false");
    toggleBtn.setAttribute("aria-label", show ? "隐藏密码" : "显示密码");
    toggleBtn.innerHTML = show ? EYE_OFF : EYE_OPEN;
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy || lockTimer) return;
    const username = userInput.value.trim();
    const password = passInput.value;
    if (!username || !password) {
      showError("请输入用户名和密码");
      return;
    }

    showError("");
    setBusy(true);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({
          username,
          password,
          remember: Boolean(rememberInput.checked),
        }),
      });
      let data = {};
      try { data = await res.json(); } catch {}

      if (res.ok && data.ok) {
        window.location.replace("/");
        return;
      }

      setBusy(false);
      if (res.status === 429 && data.retryAfter) {
        startLockCountdown(data.retryAfter);
      } else {
        showError(data.error || `登录失败 (${res.status})`);
        passInput.select();
      }
    } catch {
      setBusy(false);
      showError("无法连接服务器，请检查网络或服务状态");
    }
  });

  async function bootstrap() {
    const params = new URLSearchParams(window.location.search);
    if (params.get("expired") === "1") {
      showNotice("登录状态已过期或已在其他设备注销，请重新登录", "warn");
    } else if (params.get("logged_out") === "1") {
      showNotice("已安全退出当前会话", "info");
    }

    try {
      const res = await fetch("/api/auth/status", { credentials: "same-origin" });
      const data = await res.json();
      if (data.authenticated) {
        window.location.replace("/");
        return;
      }
      if (data.configured === false) {
        modeTag.textContent = "待初始化";
        modeTag.className = "tag tag-warn mono";
        showNotice("管理员账号尚未初始化，请运行 /opt/X_Download/setup_auth.sh 设置用户名与密码。", "warn");
        submitBtn.disabled = true;
      } else {
        userInput.focus();
      }
    } catch {
      showNotice("正在连接工作台服务…", "info");
    }
  }

  bootstrap();
})();
