"use strict";
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

async function pairCurrentLink(value) {
  status.className = "";
  status.textContent = "正在等待电脑同步配对码…";
  try {
    const body = connectionCode(value);
    let failure = "配对失败，请重新生成连接码。";
    // The desktop shows the QR immediately, while the outbound connector may
    // need a few seconds to write the code into its Durable Object. Retry the
    // transient 401/429 responses so a fast scan does not look like a dead QR.
    for (let attempt = 0; attempt < 8; attempt++) {
      const response = await fetch("/relay/pair", { method: "POST", credentials: "same-origin",
        headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
      const result = await response.json().catch(() => ({}));
      if (response.ok) {
        // Existing tokens belonged to a previous computer; the relay uses its own
        // HttpOnly device session and injects the paired computer's local token.
        localStorage.removeItem("orch.token");
        location.replace("/");
        return;
      }
      failure = result.error || failure;
      if (response.status !== 401 && response.status !== 429) throw new Error(failure);
      if (attempt < 7) {
        status.textContent = "电脑正在同步，马上重试…";
        await new Promise(resolve => setTimeout(resolve, 700 + attempt * 300));
      }
    }
    throw new Error(failure);
  } catch (error) {
    status.textContent = error.message || "网络连接失败，请稍后重试。";
    status.className = "error";
  }
}

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
  history.replaceState(null, "", location.pathname);
  // QR scans and copied pairing links are one-step flows: opening the link
  // consumes the one-time code and redirects into this computer's workbench.
  pairCurrentLink(pair);
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
