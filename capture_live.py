"""예봄교회 유튜브 라이브 캡처 + 텔레그램 버튼 승인 스크립트.

Claude Code와 무관하게 단독 실행 가능. launchd가 매주 일요일 13:00에 실행한다.

흐름:
  1) 라이브 접속(최대 15분 재시도) → 후보 스틸컷 5장 캡처 → 제목에서 설교 정보 파싱
  2) 텔레그램으로 후보 사진 5장 전송, 각 사진에 "이 사진 선택" 버튼 부착
  3) 버튼 클릭을 최대 2시간 폴링 → 선택되면 generate_thumbnail.py로 최종 이미지 합성
  4) 최종 이미지를 "승인"/"거절" 버튼과 함께 전송 → 승인 시 실제 타임라인 폴더에 저장
     (YouTube API 자격증명이 준비되면 자동 업로드까지, 아직이면 수동 업로드 안내)
"""
import importlib.util
import json
import time
from datetime import datetime
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

CHANNEL_LIVE_URL = "https://www.youtube.com/@예봄교회0828/live"

HERE = Path(__file__).resolve().parent
ENV_PATH = HERE / ".env"
OUT_ROOT = HERE / "captures"

GENERATE_SCRIPT = HERE / "generate_thumbnail.py"
LOGO_PATH = HERE / "assets" / "logo.png"

# 클라우드 실행 환경에는 사용자 로컬 Google Drive가 마운트되어 있지 않다.
# 있으면(로컬 실행 시) 거기에도 저장하고, 없으면(클라우드 실행 시) 건너뛰고
# 텔레그램으로 전송된 파일이 사실상의 아카이브가 된다.
TIMELINE_DIR = (
    Path.home()
    / "Library/CloudStorage/GoogleDrive-john981414@gmail.com"
    / "내 드라이브/00. GD_mac/claude/예봄교회/01. 유튜브 썸네일 : 타임라인"
)

POLL_TIMEOUT_S = 2 * 60 * 60  # 버튼 대기 최대 2시간
POLL_INTERVAL_S = 3


def load_env():
    env = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def dismiss_consent(page):
    for text in ["동의함", "Accept all", "I agree", "모두 동의"]:
        try:
            btn = page.get_by_role("button", name=text)
            if btn.count() > 0:
                btn.first.click(timeout=3000)
                page.wait_for_timeout(1000)
                return
        except Exception:
            pass


def wait_for_live(page, max_wait_s=900, poll_s=30):
    """13시 정각엔 라이브가 아직 안 켜졌을 수 있으니 최대 15분까지 재시도.

    네트워크 타임아웃 등 goto 자체의 실패도 '아직 라이브 아님'과 동일하게 재시도한다
    (2026-08-02: 첫 goto가 타임아웃으로 예외를 던지면서 재시도 없이 통째로 죽은 버그 수정).
    """
    waited = 0
    while waited <= max_wait_s:
        try:
            page.goto(CHANNEL_LIVE_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)
            dismiss_consent(page)
            if "/watch" in page.url:
                return True
        except Exception as e:
            print(f"wait_for_live 재시도 중 오류(무시하고 계속): {e}")
        waited += poll_s
        time.sleep(poll_s)
    return False


def extract_info(page):
    raw_title = page.evaluate(
        "document.querySelector('meta[property=\"og:title\"]')?.content || document.title"
    )
    description = page.evaluate(
        "document.querySelector('meta[property=\"og:description\"]')?.content || ''"
    )
    video_id = None
    if "v=" in page.url:
        video_id = page.url.split("v=")[1].split("&")[0]

    # 예봄교회 영상 제목 규칙: "날짜 | (예배구분) | 설교제목 | 성경본문 | 설교자"
    # 예배구분(예: "주일2부예배")은 있을 때도 없을 때도 있어서 파이프 개수가
    # 4개 또는 5개 이상일 수 있다. 뒤에서부터 설교자/본문/제목, 맨 앞은 날짜로 고정.
    parts = [p.strip() for p in raw_title.split("|")]
    sermon_date = sermon_title = scripture = preacher = None
    if len(parts) >= 4:
        sermon_date = parts[0]
        preacher = parts[-1]
        scripture = parts[-2]
        sermon_title = parts[-3]

    return {
        "raw_title": raw_title,
        "description": description,
        "video_id": video_id,
        "url": page.url,
        "sermon_date": sermon_date,
        "sermon_title": sermon_title,
        "scripture": scripture,
        "preacher": preacher,
    }


def capture_stills(page, out_dir, count=5, interval_s=4):
    """유튜브 컨트롤 바(재생바/버튼)는 마우스가 몇 초간 안 움직이면 자동으로
    사라진다. 확장 프로그램 없이도 마우스를 플레이어 밖으로 옮기고 기다리면
    컨트롤 없는 깨끗한 프레임을 캡처할 수 있다."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    video = page.locator("video").first
    for i in range(count):
        page.wait_for_timeout(interval_s * 1000)
        # 마우스를 플레이어 바깥(좌상단 구석)으로 이동시켜 컨트롤 바가
        # 자동으로 숨겨지도록 유도
        page.mouse.move(2, 2)
        page.wait_for_timeout(3500)
        path = out_dir / f"candidate_{i + 1}.jpg"
        try:
            video.screenshot(path=str(path), type="jpeg", quality=95)
        except Exception:
            page.screenshot(path=str(path), type="jpeg", quality=95)
        paths.append(path)
    return paths


# ---------------- 텔레그램 ----------------

def tg_base(env):
    return f"https://api.telegram.org/bot{env.get('TELEGRAM_BOT_TOKEN')}"


def send_message(env, text, reply_markup=None):
    data = {"chat_id": env.get("TELEGRAM_CHAT_ID"), "text": text}
    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup)
    requests.post(f"{tg_base(env)}/sendMessage", data=data)


def send_photo(env, path, caption=None, reply_markup=None):
    data = {"chat_id": env.get("TELEGRAM_CHAT_ID")}
    if caption:
        data["caption"] = caption
    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup)
    with open(path, "rb") as f:
        requests.post(f"{tg_base(env)}/sendPhoto", data=data, files={"photo": f})


def send_document(env, path, caption=None):
    """원본 화질 그대로 파일로 전송 (sendPhoto는 압축되므로 아카이브용으로는 이걸 사용)."""
    data = {"chat_id": env.get("TELEGRAM_CHAT_ID")}
    if caption:
        data["caption"] = caption
    with open(path, "rb") as f:
        requests.post(f"{tg_base(env)}/sendDocument", data=data, files={"document": f})


def answer_callback(env, callback_query_id, text=None):
    data = {"callback_query_id": callback_query_id}
    if text:
        data["text"] = text
    requests.post(f"{tg_base(env)}/answerCallbackQuery", data=data)


def poll_for_callback(env, valid_data_prefixes, timeout_s):
    """callback_query 중 valid_data_prefixes로 시작하는 데이터를 최대 timeout_s초 대기."""
    token = env.get("TELEGRAM_BOT_TOKEN")
    chat_id = str(env.get("TELEGRAM_CHAT_ID"))
    offset = None
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        params = {"timeout": 20}
        if offset is not None:
            params["offset"] = offset
        try:
            resp = requests.get(f"{tg_base(env)}/getUpdates", params=params, timeout=25).json()
        except Exception:
            time.sleep(POLL_INTERVAL_S)
            continue
        for upd in resp.get("result", []):
            offset = upd["update_id"] + 1
            cq = upd.get("callback_query")
            if not cq:
                continue
            if str(cq["message"]["chat"]["id"]) != chat_id:
                continue
            data = cq.get("data", "")
            if any(data.startswith(p) for p in valid_data_prefixes):
                return data, cq["id"]
        time.sleep(POLL_INTERVAL_S)
    return None, None


# ---------------- 이미지 합성 ----------------

def load_generate_fn():
    spec = importlib.util.spec_from_file_location("generate_thumbnail", GENERATE_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.generate


def to_dot_date(yyyymmdd):
    y, m, d = yyyymmdd[:4], yyyymmdd[4:6], yyyymmdd[6:8]
    return f"{y}. {int(m)}. {int(d)}"


# ---------------- 메인 흐름 ----------------

def main():
    env = load_env()
    today = datetime.now().strftime("%Y%m%d")
    out_dir = OUT_ROOT / today

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 900})

        if not wait_for_live(page):
            send_message(
                env,
                "예봄교회 유튜브 라이브를 15분 넘게 기다렸는데 아직 시작되지 않았어요. "
                "나중에 Claude Code에서 직접 확인해주세요.",
            )
            browser.close()
            return

        page.wait_for_timeout(5000)
        info = extract_info(page)
        photos = capture_stills(page, out_dir)
        browser.close()

    (out_dir / "info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2))

    if info.get("sermon_title"):
        info_line = (
            f"제목: {info['sermon_title']}\n본문: {info['scripture']}\n설교자: {info['preacher']}"
        )
    else:
        info_line = f"방송 제목: {info['raw_title']} (자동 파싱 실패, Claude Code에서 직접 확인 필요)"

    send_message(
        env,
        f"예봄교회 주일 라이브 썸네일 후보 사진이 준비됐어요.\n\n{info_line}\n\n"
        "아래 사진들 중 마음에 드는 걸 골라 버튼을 눌러주세요 (2시간 이내).",
    )
    for i, path in enumerate(photos, start=1):
        send_photo(
            env, path, caption=f"후보 {i}",
            reply_markup={"inline_keyboard": [[{"text": f"이 사진 선택 ({i})", "callback_data": f"sel:{i}"}]]},
        )

    data, cq_id = poll_for_callback(env, ["sel:"], POLL_TIMEOUT_S)
    if data is None:
        send_message(env, "2시간 동안 사진 선택이 없어서 대기를 종료했어요. Claude Code에서 이어서 진행해주세요.")
        return
    answer_callback(env, cq_id, "선택 완료, 최종 이미지 만드는 중...")
    chosen_idx = int(data.split(":")[1])
    chosen_photo = photos[chosen_idx - 1]

    if not info.get("sermon_title") or not info.get("sermon_date"):
        send_message(env, "설교 정보 자동 추출에 실패해서 최종 이미지를 자동으로 못 만들어요. Claude Code를 열어주세요.")
        return

    generate = load_generate_fn()
    preview_path = out_dir / "final_preview.jpg"
    generate(
        photo_path=str(chosen_photo),
        logo_path=str(LOGO_PATH),
        date_str=to_dot_date(info["sermon_date"]),
        title=info["sermon_title"],
        scripture=info["scripture"],
        preacher=info["preacher"],
        output_path=str(preview_path),
    )

    send_photo(
        env, preview_path, caption="이렇게 만들어봤어요. 이대로 저장할까요?",
        reply_markup={
            "inline_keyboard": [[
                {"text": "승인", "callback_data": "approve"},
                {"text": "거절 (직접 수정할게요)", "callback_data": "reject"},
            ]]
        },
    )

    data, cq_id = poll_for_callback(env, ["approve", "reject"], POLL_TIMEOUT_S)
    if data is None:
        send_message(env, "2시간 동안 승인 응답이 없어서 대기를 종료했어요. Claude Code에서 이어서 진행해주세요.")
        return

    if data == "reject":
        answer_callback(env, cq_id, "알겠습니다")
        send_message(env, "알겠습니다. Claude Code를 열어서 직접 조정해주세요.")
        return

    answer_callback(env, cq_id, "저장 중...")
    saved_to_drive = TIMELINE_DIR.exists()
    if saved_to_drive:
        final_path = TIMELINE_DIR / f"{info['sermon_date']}.jpg"
        final_path.write_bytes(preview_path.read_bytes())
    else:
        # 클라우드 실행: 로컬 Google Drive 폴더가 없으므로 임시 경로만 사용하고
        # 완성 파일은 텔레그램 문서 전송이 사실상의 전달/보관 수단이 된다.
        final_path = out_dir / f"{info['sermon_date']}.jpg"
        final_path.write_bytes(preview_path.read_bytes())
        send_document(env, final_path, caption=f"{final_path.name} (원본 화질, 직접 다운받아 보관하세요)")

    youtube_ready = all(
        env.get(k) for k in ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN")
    )
    if youtube_ready and info.get("video_id"):
        try:
            upload = importlib.util.spec_from_file_location(
                "youtube_upload", Path(__file__).parent / "youtube_upload.py"
            )
            yt_mod = importlib.util.module_from_spec(upload)
            upload.loader.exec_module(yt_mod)
            yt_mod.set_thumbnail(info["video_id"], str(final_path))
            send_message(env, f"저장 완료: {final_path.name}\n유튜브 썸네일도 자동으로 교체했어요. ✅")
        except Exception as e:
            send_message(env, f"저장은 됐는데 유튜브 업로드에서 오류가 났어요: {e}\nClaude Code에서 확인해주세요.")
    elif youtube_ready:
        send_message(env, f"저장 완료: {final_path.name}\n(오늘 라이브 video_id를 못 찾아서 자동 업로드는 건너뛰었어요. 스튜디오에서 직접 업로드해주세요.)")
    else:
        send_message(env, f"저장 완료: {final_path.name}\n유튜브 스튜디오에서 직접 업로드해주세요 (API 자동 업로드는 아직 미설정).")

    print("done:", final_path)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        err = traceback.format_exc()
        print(err)
        try:
            send_message(
                load_env(),
                "썸네일 캡처 스크립트가 오류로 중단됐어요. Claude Code에서 로그를 확인해주세요.\n\n"
                f"{err[-500:]}",
            )
        except Exception:
            pass
