"""후보 사진 한 장을 별도 프로세스로 캡처하는 워커.

capture_live.py가 후보 사진 개수(5장)만큼 이 스크립트를 매번 새로
subprocess.run(timeout=...)으로 실행한다. 완전히 새 프로세스+새 브라우저로
격리해서, 하나가 죽어도(외부에서 강제종료됨) 나머지 사진 캡처는 영향받지
않는다.

중요: URL에 &t=Ns 타임스탬프를 붙여서 여는 방식(wait_until을 domcontentloaded든
commit이든 뭘로 해도)은 이 실행 환경(GitHub Actions)에서 100% 재현되는
행(hang)이 있었음(2026-08-26 실측 — 새 브라우저로 5번씩, 두 번의 워크플로
실행 모두 매번 정확히 개별 타임아웃을 꽉 채움). 그래서 &t= 파라미터 없이
일반 영상 페이지만 열고(이 nav는 모든 테스트에서 항상 빠르고 안정적이었음),
JS로 직접 탐색한다. 처음엔 로드 직후 바로 currentTime을 옮겼다가 플레이어
자체 오류("문제가 발생했습니다")가 났는데, video.readyState가 아직 안 올라간
상태(HAVE_NOTHING)에서 seek해서 그런 것으로 확인됨 — play()를 먼저 부르고
readyState >= 2(HAVE_CURRENT_DATA)가 될 때까지 기다린 뒤에 seek하니 로컬
에서 에러 없이 정상 캡처됨.

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
        page.goto(f"https://www.youtube.com/watch?v={video_id}", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)
        dismiss_consent(page)
        try:
            page.evaluate("document.querySelector('video')?.play()")
        except Exception:
            pass
        try:
            page.wait_for_function(
                "document.querySelector('video') && document.querySelector('video').readyState >= 2",
                timeout=15000,
            )
        except Exception:
            pass  # 시간 안에 안 되면 그냥 진행 (최선을 다해본다)
        try:
            page.evaluate(f"const v = document.querySelector('video'); if (v) v.currentTime = {t};")
        except Exception:
            pass
        page.wait_for_timeout(3000)
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
