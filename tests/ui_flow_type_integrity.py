# -*- coding: utf-8 -*-
"""任务类型身份贯通 UI 回归；自起随机端口临时服务，不碰真实数据。"""
from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def check(name, condition, detail=""):
    if not condition:
        raise AssertionError("%s：%s" % (name, detail))
    print("[PASS] " + name)


def main():
    port = free_port()
    base = "http://127.0.0.1:%d" % port
    with tempfile.TemporaryDirectory(prefix="codebee-flow-") as data_dir:
        env = dict(os.environ, TUTTI_DATA=data_dir, PYTHONPATH=str(ROOT),
                   PYTHONIOENCODING="utf-8")
        service = subprocess.Popen(
            [sys.executable, "app/main.py", "--port", str(port), "--no-browser",
             "--no-public-tunnel"], cwd=str(ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            for _ in range(60):
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        break
                except OSError:
                    time.sleep(0.25)
            else:
                service.terminate()
                raw = service.communicate(timeout=5)[0]
                try:
                    output = raw.decode("utf-8")
                except UnicodeDecodeError:
                    output = raw.decode("gbk", errors="replace")
                raise RuntimeError("临时服务启动超时（exit=%s）：%s" %
                                   (service.returncode, output[-2000:]))

            run_browser_checks(base)
        finally:
            service.terminate()
            try:
                service.wait(timeout=8)
            except subprocess.TimeoutExpired:
                service.kill()
                service.wait(timeout=5)


def run_browser_checks(base):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 950})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base, wait_until="domcontentloaded")
        page.wait_for_function("typeof S !== 'undefined' && S.flows && S.flows.length >= 15")

        page.evaluate("pickType('novel')")
        novel_rubric = page.locator("#f-rubric").input_value()
        page.evaluate("pickType('translation')")
        translation_rubric = page.locator("#f-rubric").input_value()
        check("小说维度正确", "情节" in novel_rubric, novel_rubric)
        check("切到翻译后维度不串值", "忠实度" in translation_rubric and "情节" not in translation_rubric,
              translation_rubric)

        page.evaluate("document.getElementById('f-rubric').value = '忠实度, 法律术语'")
        page.evaluate("pickType('novel')")
        page.evaluate("pickType('translation')")
        check("同类型用户草稿可恢复", page.locator("#f-rubric").input_value() == "忠实度, 法律术语")

        before = page.evaluate("fetch('/api/flows').then(r => r.json())")
        page.evaluate("flowForm('translation')")
        check("编辑态保留原流程 ID", page.locator("#fl-engine").get_attribute("data-fid") == "translation")
        page.locator("#fl-threshold").fill("8.5")
        page.evaluate("saveFlow()")
        page.wait_for_timeout(500)
        after = page.evaluate("fetch('/api/flows').then(r => r.json())")
        flows = after.get("flows", [])
        translation = next(item for item in flows if item.get("id") == "translation")
        check("编辑预置流程不新增副本", len(flows) == len(before.get("flows", [])), len(flows))
        check("编辑仍落到 translation", translation.get("edited") is True and translation.get("threshold") == 8.5,
              translation)
        check("没有误建 flow-*", not any(item.get("id", "").startswith("flow-") for item in flows))
        check("保存后 rubric 刷新为流程定义", "法律术语" not in page.locator("#f-rubric").input_value(),
              page.locator("#f-rubric").input_value())

        calls = page.evaluate("""async () => {
      const oldApi = api;
      const seen = [];
      api = async function(url, options) {
        if (url.startsWith('/api/usage/estimate?')) seen.push(url);
        return oldApi(url, options);
      };
      await fetchFlowEstimates();
      api = oldApi;
      return seen;
    }""")
        check("成本预估覆盖全部类型", len(calls) == len(flows), "%d/%d" % (len(calls), len(flows)))

        page.evaluate("openFlowsManager()")
        direct_label = page.evaluate("""() => {
      const row = [...document.querySelectorAll('#modal .item')]
        .find(x => x.textContent.includes('direct'));
      return row ? [...row.querySelectorAll('.tag')].map(x => x.textContent.trim()) : [];
    }""")
        check("直接执行标为直连引擎", "直连引擎" in direct_label, direct_label)
        check("页面无 JS 异常", not errors, errors)
        browser.close()


if __name__ == "__main__":
    main()
