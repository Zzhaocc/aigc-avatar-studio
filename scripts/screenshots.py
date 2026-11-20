"""给控制台截图（开发辅助）。

    python scripts/screenshots.py

需要本机已安装 playwright 与 chromium：
    python -m playwright install chromium
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

BASE = "http://127.0.0.1:8000"
OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
CHROME = os.getenv("CHROME_PATH") or str(
    Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright" / "chromium-1252" / "chrome-win64" / "chrome.exe"
)


def main() -> int:
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME if Path(CHROME).exists() else None)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: errors.append(f"console: {m.text}") if m.type == "error" else None)

        page.goto(BASE, wait_until="networkidle")
        page.wait_for_timeout(2200)
        # 让工作台左侧看起来是"有人正在用"：填入示例脚本并触发字数统计
        page.evaluate(
            """async () => {
                const cat = await fetch('/api/catalog').then(r => r.json());
                const el = document.querySelector('#script');
                if (el && !el.value && cat.demo_scripts && cat.demo_scripts.length) {
                    el.value = cat.demo_scripts[0].text;
                    el.dispatchEvent(new Event('input'));
                }
            }"""
        )
        page.wait_for_timeout(600)
        page.screenshot(path=str(OUT / "console-studio.jpg"), type="jpeg", quality=88)
        print("  ✓ 工作台 → console-studio.jpg")

        for view, name in [("models", "模型服务"), ("voices", "音色"), ("jobs", "任务"), ("tools", "工具箱"), ("metrics", "指标")]:
            page.click(f'.nav-item[data-view="{view}"]')
            page.wait_for_timeout(1200)
            page.screenshot(path=str(OUT / f"console-{view}.jpg"), type="jpeg", quality=88)
            print(f"  ✓ {name} → console-{view}.jpg")

        browser.close()

    if errors:
        print("\n浏览器错误：")
        for err in errors[:8]:
            print("  -", err)
        return 1
    print("\n截图完成，无浏览器错误")
    return 0


if __name__ == "__main__":
    sys.exit(main())
