"""후보 사진 한 장을 별도 프로세스로 캡처하는 워커.

capture_live.py가 후보 사진 개수(5장)만큼 이 스크립트를 매번 새로
subprocess.run(timeout=...)으로 실행한다. 완전히 새 프로세스+새 브라우저로
격리해서, 하나가 죽어도(외부에서 강제종료됨) 나머지 사진 캡처는 영향받지
않는다.

중요: URL에 &t=Ns 타임스탬프를 붙여서 domcontentloaded까지 기다리는 방식은
이 실행 환경(GitHub Actions)에서 100% 재현되는 행(hang)이 있었음(2026-08-26
실측 — 새 브라우저로 5번 다 시도해도 매번 정확히 90초 강제종료 시간을 꽉
채움). JS로 video.currentTime을 직접 옮기는 대안도 시도했지만 로컬에서도
플레이어 자체 오류("문제가 발생했습니다")를 유발해서 폐기. 대신 goto의
wait_until을 "commit"(네비게이션 시작만 확인, 응답 바디는 안 기다림)으로
낮춰서 domcontentloaded 대기 중 걸리는 문제 자체를 우회하고, 그 뒤엔 고정
시간만큼 기다려서 페이지가 그려지게 한다.

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
        page.goto(f"https://www.youtube.com/watch?v={video_id}&t={t}s", wait_until="commit", timeout=60000)
        page.wait_for_timeout(6000)
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
