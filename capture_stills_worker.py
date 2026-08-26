"""후보 사진 한 장을 별도 프로세스로 캡처하는 워커.

capture_live.py가 후보 사진 개수(5장)만큼 이 스크립트를 매번 새로
subprocess.run(timeout=...)으로 실행한다. 완전히 새 프로세스+새 브라우저로
격리해서, 하나가 죽어도(외부에서 강제종료됨) 나머지 사진 캡처는 영향받지
않는다.

중요(2026-08-26에 실제로 겪은 삽질 기록 — 다음에 비슷한 hang 만나면 여기부터
볼 것): 한동안 &t=Ns URL, /dev/shm, 브라우저 재사용 등을 의심하며 헤맸는데,
워커 내부에 단계별 print를 찍어보고 나서야 진짜 원인을 찾음 —
`page.evaluate("video.play()")` 이 한 줄이 84초 넘게 멈춰있었음.
video.play()는 Promise를 반환하고, Playwright의 page.evaluate()는 반환값이
Promise면 그게 끝날 때까지 자동으로 기다리는데, 이 실행 환경에서는 그
Promise가 영원히 settle이 안 됨(로컬 맥에서는 금방 resolve돼서 이 문제가
전혀 안 보였음). 고치는 법: JS를 화살표 함수로 감싸서 최종 반환값을
undefined로 만들면 evaluate가 그 안의 Promise를 기다리지 않고 바로 리턴함
— `"() => { v.play(); }"`처럼. &t=Ns URL 자체는 문제가 아니었을 수도 있음
(그때도 결국 play() 호출이 있었으니 같은 원인이었을 가능성이 큼) — 그래도
JS seek 방식(일반 페이지 열고 currentTime 직접 설정)은 그대로 유지한다.

사용법: python capture_stills_worker.py <video_id> <timestamp_seconds> <out_path>
성공하면 out_path에 스크린샷을 남기고 종료 코드 0.
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from capture_live import dismiss_consent


def capture_one(video_id, t, out_path):
    print(f"[워커] launch 시작 (t={t})", flush=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        print("[워커] 브라우저 launch 완료", flush=True)
        page = browser.new_page(viewport={"width": 1600, "height": 900})
        print("[워커] goto 시작", flush=True)
        page.goto(f"https://www.youtube.com/watch?v={video_id}", wait_until="domcontentloaded", timeout=60000)
        print("[워커] goto 완료", flush=True)
        page.wait_for_timeout(3000)
        dismiss_consent(page)
        print("[워커] play() 호출", flush=True)
        try:
            # 주의: video.play()는 Promise를 반환하고, page.evaluate()는 반환값이
            # Promise면 그게 끝날 때까지 자동으로 기다림. 이 환경에서는 그 Promise가
            # 영원히 안 끝나는 걸 실측으로 확인(2026-08-26 — 이 한 줄에서 84초 동안
            # 멈춰있었음, 로컬에선 빨리 resolve돼서 안 보였음). IIFE로 감싸서 반환값을
            # undefined로 만들면 evaluate가 Promise를 기다리지 않고 바로 리턴한다.
            page.evaluate("() => { document.querySelector('video')?.play(); }")
        except Exception as e:
            print(f"[워커] play() 예외: {e}", flush=True)
        print("[워커] readyState 대기 시작", flush=True)
        try:
            page.wait_for_function(
                "document.querySelector('video') && document.querySelector('video').readyState >= 2",
                timeout=15000,
            )
            print("[워커] readyState 도달", flush=True)
        except Exception as e:
            print(f"[워커] readyState 대기 타임아웃/예외: {e}", flush=True)
        print("[워커] currentTime 설정", flush=True)
        try:
            page.evaluate(f"const v = document.querySelector('video'); if (v) v.currentTime = {t};")
        except Exception as e:
            print(f"[워커] currentTime 예외: {e}", flush=True)
        page.wait_for_timeout(3000)
        page.mouse.move(2, 2)
        page.wait_for_timeout(3500)
        print("[워커] 스크린샷 시작", flush=True)
        video = page.locator("video").first
        try:
            video.screenshot(path=str(out_path), type="jpeg", quality=95)
        except Exception as e:
            print(f"[워커] video.screenshot 예외: {e}", flush=True)
            page.screenshot(path=str(out_path), type="jpeg", quality=95)
        print("[워커] 스크린샷 완료", flush=True)
        browser.close()
        print("[워커] 브라우저 종료", flush=True)


def main():
    video_id = sys.argv[1]
    t = int(sys.argv[2])
    out_path = Path(sys.argv[3])
    capture_one(video_id, t, out_path)
    print(f"[진행] 캡처 완료: t={t}s -> {out_path.name}")


if __name__ == "__main__":
    main()
