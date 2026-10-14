"use strict";
const form = document.getElementById("pair-form");
const input = document.getElementById("code");
const button = document.getElementById("submit");
const status = document.getElementById("status");

function connectionCode(value) {
  let text = value.trim();
  if (text.startsWith("https://") || text.startsWith("http://")) {
    const url = new URL(text);
    text = new URLSearchParams(url.hash.slice(1)).get("pair") || "";
  }
  const match = text.match(/^([a-f0-9]{32})\.([a-f0-9]{32})$/);
  if (!match) throw new Error("请粘贴电脑生成的完整配对链接或连接码。");
  return { device: match[1], code: match[2] };
}

form.addEventListener("submit", async event => {
  event.preventDefault();
  button.disabled = true;
  status.className = "";
  status.textContent = "正在连接你的电脑…";
  try {
    const body = connectionCode(input.value);
    const response = await fetch("/relay/pair", { method: "POST", credentials: "same-origin",
      headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "配对失败，请重新生成连接码。");
    input.value = "";
    // Existing tokens belonged to a previous computer; the relay uses its own
    // HttpOnly device session and injects the paired computer's local token.
    localStorage.removeItem("orch.token");
    location.replace("/");
  } catch (error) {
    status.textContent = error.message || "网络连接失败，请稍后重试。";
    status.className = "error";
    button.disabled = false;
  }
});

document.getElementById("logout").addEventListener("click", async () => {
  try {
    const response = await fetch("/relay/logout", { method: "POST" });
    if (!response.ok) throw new Error("无法撤销连接，请稍后再试。");
    localStorage.removeItem("orch.token");
    sessionStorage.removeItem("codebee.paired");
    location.replace("/relay/portal");
  } catch (error) { status.textContent = error.message; status.className = "error"; }
});

let pair = "";
function readPairFragment() {
  const next = new URLSearchParams(location.hash.slice(1)).get("pair");
  if (!next) return;
  pair = next;
  input.value = pair;
  history.replaceState(null, "", location.pathname);
  // A visible confirmation keeps accidental QR scans from consuming a code.
  status.textContent = "已读取连接码，点击「连接我的电脑」完成配对。";
}
readPairFragment();
window.addEventListener("hashchange", readPairFragment);
fetch("/relay/session").then(async response => {
  if (!response.ok) return;
  const session = await response.json();
  document.getElementById("paired").hidden = !session.paired;
  if (!pair && session.paired) status.textContent = session.connected ?
    "此手机已配对，可直接进入工作台。" : "你的电脑当前离线，请启动 Codebee 并保持联网。";
}).catch(() => {});
