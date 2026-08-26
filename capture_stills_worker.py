"""후보 사진 한 장을 별도 프로세스로 캡처하는 워커.

capture_live.py가 후보 사진 개수(5장)만큼 이 스크립트를 매번 새로
subprocess.run(timeout=...)으로 실행한다. 브라우저 하나를 재사용하며 여러 번
무거운 유튜브 페이지를 오가면, 이 실행 환경(GitHub Actions)에서 몇 번째
navigation부터 Chromium이 완전히 멈추는 문제가 실제로 확인됨(2026-08-25~26,
--disable-dev-shm-usage로도 해결 안 됨). 매 장마다 완전히 새 프로세스+새
브라우저로 격리하면, 하나가 죽어도(외부에서 강제종료됨) 나머지 사진 캡처는
영향받지 않는다.

사용법: python capture_stills_worker.py <video_id> <timestamp_seconds> <out_path>
성공하면 out_path에 스크린샷을 남기고 종료 코드 0.
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from capture_live import dismiss_consent


def capture_one(video_id, t, out_path):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        page = browser.new_page(viewport={"width": 1600, "height": 900})
        page.goto(f"https://www.youtube.com/watch?v={video_id}&t={t}s", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)
        dismiss_consent(page)
        try:
            page.evaluate("document.querySelector('video')?.play()")
        except Exception:
            pass
        page.wait_for_timeout(2000)
        page.mouse.move(2, 2)
        page.wait_for_timeout(3500)
        video = page.locator("video").first
        try:
            video.screenshot(path=str(out_path), type="jpeg", quality=95)
        except Exception:
            page.screenshot(path=str(out_path), type="jpeg", quality=95)
        browser.close()


def main():
    video_id = sys.argv[1]
    t = int(sys.argv[2])
    out_path = Path(sys.argv[3])
    capture_one(video_id, t, out_path)
    print(f"[진행] 캡처 완료: t={t}s -> {out_path.name}")


if __name__ == "__main__":
    main()
