"""후보 사진 캡처를 별도 프로세스로 실행하는 워커.

capture_live.py가 이 스크립트를 subprocess.run(timeout=...)으로 실행한다.
Playwright 동기 API의 내부 호출(page.evaluate 등)이 락/퓨처 대기 방식이라
signal.alarm 기반 타임아웃으로는 못 끊는 경우가 있어서(2026-08-25~26 실제
확인), 프로세스 자체를 외부에서 강제종료할 수 있도록 격리했다.

사용법: python capture_stills_worker.py <video_id> <out_dir>
성공하면 out_dir/candidate_1.jpg ... candidate_N.jpg 를 남기고 종료 코드 0.
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from capture_live import capture_stills_from_vod


def main():
    video_id = sys.argv[1]
    out_dir = Path(sys.argv[2])

    with sync_playwright() as p:
        # GitHub Actions 기본 /dev/shm이 64MB뿐이라 몇 번 navigation 후 Chromium이
        # 완전히 멈추는 경우가 흔함(2026-08-26 실제 확인: Playwright 자체 60초
        # timeout도 안 먹히고 외부 subprocess timeout까지 꽉 채움 = 브라우저 프로세스
        # 자체가 무응답 상태였다는 뜻). /tmp를 대신 쓰게 해서 회피.
        browser = p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        page = browser.new_page(viewport={"width": 1600, "height": 900})
        capture_stills_from_vod(page, video_id, out_dir)
        browser.close()


if __name__ == "__main__":
    main()
