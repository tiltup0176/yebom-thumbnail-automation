"""예봄교회 유튜브 썸네일 캡처 + 텔레그램 버튼 승인 스크립트.

Claude Code와 무관하게 단독 실행 가능. 매주 일요일 13:03(KST)에 클라우드 routine이 실행한다.

중요: 예배는 11:00~12:30에 진행되고 13:00은 방송이 "끝난 뒤"다. 그래서 이 스크립트는
라이브 스트림을 잡는 게 아니라, 이미 종료되어 일반 다시보기(VOD)가 된 "오늘자 영상"을
채널에서 찾아 그 안에서 설교 구간을 찾아 스틸컷을 뜬다.

흐름:
  1) 채널에서 오늘 날짜(YYYYMMDD)가 제목에 들어간 최신 영상을 탐색 (최대 20분 재시도 —
     방송 종료 직후 YouTube가 다시보기로 전환 처리하는 데 시간이 걸릴 수 있음)
  2) 영상 시작 후 32~72분 구간(설교가 보통 있는 구간, 절대 시간 기준)에서
     후보 스틸컷 5장 캡처, 제목에서 설교 정보 파싱
  3) 텔레그램으로 후보 사진 5장 전송, 각 사진에 "이 사진 선택" 버튼 부착
  4) 버튼 클릭을 최대 2시간 폴링 → 선택되면 generate_thumbnail.py로 최종 이미지 합성
  5) 최종 이미지를 "승인"/"거절" 버튼과 함께 전송 → 승인 시 실제 타임라인 폴더에 저장
     (YouTube API 자격증명이 준비되면 자동 업로드까지, 아직이면 수동 업로드 안내)
"""
import importlib.util
import io
import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

CHANNEL_STREAMS_URL = "https://www.youtube.com/@예봄교회0828/streams"
# 설교가 보통 위치하는 구간: 영상 길이 비율이 아니라 "영상 시작 후 절대 시간".
# 실측 기준(2026-08-16) 설교는 대체로 시작 후 30~40분 사이에 시작한다.
# 그 이전(0~30분)은 찬양/광고 구간이라 안전하게 건너뛰고, 30~75분 사이에서 고른다.
SERMON_OFFSETS_MIN = [32, 42, 52, 62, 72]
DEFAULT_DURATION_S = 5400  # duration을 못 읽었을 때 쓸 기본값 (약 90분)

# [실험용] 대기화면 트림 지점 재조사(2026-08-19 계획, 아직 자동 판단 없음).
# 8/16 영상으로는 밝기가 10분 내내 불규칙해서 판단 불가였는데, 그 영상이
# 테스트 시점에 이미 사용자가 수동 트림한 뒤였을 가능성이 있어(원본이 아니었을
# 수 있음) 다음 방송 때 "수동 트림 전" 원본으로 다시 확인하기로 함.
# 여기서는 판단 없이 밝기 곡선만 뽑아 텔레그램으로 보낸다 — 사람이 보고
# 실제 트림 지점과 비교해서 탐지 로직을 설계한다.
TRIM_DIAG_SCAN_MAX_S = 600
TRIM_DIAG_INTERVAL_S = 15
TRIM_DIAG_YTDLP_CLIENT = "android"  # 기본 클라이언트는 403 남 (2026-08-19 확인)

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

# 텔레그램 API 호출 타임아웃. 원래 여기 없었던 게 버그였음(2026-08-23 발견) —
# api.telegram.org 연결이 조금만 지연돼도 requests가 무한 대기해서 워크플로
# 전체(150분 job timeout)가 통째로 멈추는 사고가 남. sendMessage/answerCallbackQuery는
# 텍스트라 짧게, sendPhoto/sendDocument는 업로드 시간 감안해 넉넉하게 잡는다.
TG_TEXT_TIMEOUT_S = 30
TG_UPLOAD_TIMEOUT_S = 60


REQUIRED_ENV_KEYS = [
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
    "YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN",
]


def load_env():
    """로컬 실행 시 .env 파일에서, GitHub Actions 등에서는 OS 환경변수(Secrets가
    주입된 값)에서 값을 읽는다. 둘 다 있으면 .env가 우선한다."""
    env = {k: v for k, v in ((k, os.environ.get(k)) for k in REQUIRED_ENV_KEYS) if v}
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


def find_todays_video(page, today):
    """채널의 다시보기 목록에서 제목에 오늘 날짜가 들어간 영상을 찾는다."""
    page.goto(CHANNEL_STREAMS_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    dismiss_consent(page)
    html = page.content()
    ids = []
    for vid in re.findall(r'"videoId":"([\w-]{11})"', html):
        if vid not in ids:
            ids.append(vid)
    for vid in ids[:8]:
        page.goto(f"https://www.youtube.com/watch?v={vid}", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2000)
        info = extract_info(page)
        if info.get("sermon_date") == today:
            return info
    return None


def most_recent_sunday_str():
    """가장 최근(오늘 포함) 주일 날짜를 YYYYMMDD로 반환.

    예배는 항상 일요일이라, 스크립트를 정확히 일요일에 돌리지 않아도(수동 테스트 등)
    "오늘 날짜"가 아니라 "가장 가까운 지난 주일"을 찾도록 한다. 실제 프로덕션 cron은
    일요일에만 도니 이 함수는 그날엔 오늘 날짜와 동일한 값을 반환한다.
    """
    from datetime import timedelta
    now = datetime.now()
    days_since_sunday = (now.weekday() + 1) % 7  # Mon=0..Sun=6 → 일요일이면 0
    return (now - timedelta(days=days_since_sunday)).strftime("%Y%m%d")


def wait_for_todays_video(page, max_wait_s=1200, poll_s=120):
    """방송 종료 직후엔 YouTube가 다시보기로 전환하는 데 시간이 걸릴 수 있어
    최대 20분까지 재시도한다. goto 자체의 네트워크 오류도 재시도 대상으로 취급한다."""
    today = most_recent_sunday_str()
    waited = 0
    while waited <= max_wait_s:
        try:
            found = find_todays_video(page, today)
            if found:
                return found
        except Exception as e:
            print(f"오늘자 영상 탐색 중 오류(무시하고 재시도): {e}")
        waited += poll_s
        time.sleep(poll_s)
    return None


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


def capture_stills_from_vod(page, video_id, out_dir):
    """다시보기 영상의 여러 시점(SERMON_OFFSETS_MIN)으로 이동하며 스틸컷을 뜬다.

    유튜브 컨트롤 바(재생바/버튼)는 마우스가 몇 초간 안 움직이면 자동으로 사라진다.
    확장 프로그램 없이도 마우스를 플레이어 밖으로 옮기고 기다리면 컨트롤 없는
    깨끗한 프레임을 캡처할 수 있다.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    page.goto(f"https://www.youtube.com/watch?v={video_id}", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    dismiss_consent(page)
    try:
        duration = page.evaluate("document.querySelector('video')?.duration")
    except Exception:
        duration = None
    if not duration or duration < 60:
        duration = DEFAULT_DURATION_S

    # 영상이 예상보다 짧으면(예: 다음 예배 순서가 달라짐) 오프셋이 영상 길이를
    # 넘지 않도록 duration의 85%로 상한을 둔다.
    max_t = duration * 0.85
    timestamps = [min(offset_min * 60, max_t) for offset_min in SERMON_OFFSETS_MIN]
    timestamps = sorted(set(int(t) for t in timestamps))

    paths = []
    for i, t in enumerate(timestamps, start=1):
        page.goto(f"https://www.youtube.com/watch?v={video_id}&t={t}s", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)
        dismiss_consent(page)
        try:
            page.evaluate("document.querySelector('video')?.play()")
        except Exception:
            pass
        page.wait_for_timeout(2000)
        # 마우스를 플레이어 바깥(좌상단 구석)으로 이동시켜 컨트롤 바가
        # 자동으로 숨겨지도록 유도
        page.mouse.move(2, 2)
        page.wait_for_timeout(3500)
        path = out_dir / f"candidate_{i}.jpg"
        video = page.locator("video").first
        try:
            video.screenshot(path=str(path), type="jpeg", quality=95)
        except Exception:
            page.screenshot(path=str(path), type="jpeg", quality=95)
        paths.append(path)
    return paths


# ---------------- [실험용] 대기화면 트림 진단 ----------------

def run_trim_diagnostic(env, video_id, out_dir):
    """대기화면 밝기 곡선을 뽑아 텔레그램으로 보낸다. 판단/안내 없이 raw 데이터만.

    실패해도 본 파이프라인(썸네일 캡처/승인/업로드)에 영향을 주면 안 되므로
    호출부에서 반드시 try/except로 감싸고, 여기서도 각 단계를 최대한 방어적으로 짠다.
    """
    from PIL import Image

    prefix_path = out_dir / f"trim_diag_{video_id}.mp4"
    subprocess.run(
        [
            "yt-dlp",
            "--extractor-args", f"youtube:player_client={TRIM_DIAG_YTDLP_CLIENT}",
            "-f", "best[height<=480]",
            "--download-sections", f"*0-{TRIM_DIAG_SCAN_MAX_S}",
            "-o", str(prefix_path),
            f"https://www.youtube.com/watch?v={video_id}",
        ],
        check=True, timeout=300, capture_output=True,
    )

    lines = []
    for t in range(0, TRIM_DIAG_SCAN_MAX_S + 1, TRIM_DIAG_INTERVAL_S):
        proc = subprocess.run(
            ["ffmpeg", "-ss", str(t), "-i", str(prefix_path), "-frames:v", "1",
             "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30,
        )
        try:
            img = Image.open(io.BytesIO(proc.stdout)).convert("L")
            px = list(img.getdata())
            b = sum(px) / len(px)
            lines.append(f"{t}s: {b:.0f}")
        except Exception:
            lines.append(f"{t}s: ?")

    prefix_path.unlink(missing_ok=True)

    send_message(
        env,
        "[실험용] 대기화면 밝기 진단 (트림 자동탐지 재검증용, 아직 판단 로직 없음)\n"
        "스튜디오에서 트림하기 *전에* 뽑은 원본 밝기 곡선이에요. 나중에 실제로 어디까지 "
        "잘랐는지 알려주시면 이 데이터랑 비교해서 탐지 로직을 만들게요.\n\n"
        + "\n".join(lines),
    )


# ---------------- 텔레그램 ----------------

def tg_base(env):
    return f"https://api.telegram.org/bot{env.get('TELEGRAM_BOT_TOKEN')}"


def send_message(env, text, reply_markup=None):
    data = {"chat_id": env.get("TELEGRAM_CHAT_ID"), "text": text}
    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup)
    requests.post(f"{tg_base(env)}/sendMessage", data=data, timeout=TG_TEXT_TIMEOUT_S)


def send_photo(env, path, caption=None, reply_markup=None):
    data = {"chat_id": env.get("TELEGRAM_CHAT_ID")}
    if caption:
        data["caption"] = caption
    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup)
    with open(path, "rb") as f:
        requests.post(f"{tg_base(env)}/sendPhoto", data=data, files={"photo": f}, timeout=TG_UPLOAD_TIMEOUT_S)


def send_document(env, path, caption=None):
    """원본 화질 그대로 파일로 전송 (sendPhoto는 압축되므로 아카이브용으로는 이걸 사용)."""
    data = {"chat_id": env.get("TELEGRAM_CHAT_ID")}
    if caption:
        data["caption"] = caption
    with open(path, "rb") as f:
        requests.post(f"{tg_base(env)}/sendDocument", data=data, files={"document": f}, timeout=TG_UPLOAD_TIMEOUT_S)


def answer_callback(env, callback_query_id, text=None):
    data = {"callback_query_id": callback_query_id}
    if text:
        data["text"] = text
    requests.post(f"{tg_base(env)}/answerCallbackQuery", data=data, timeout=TG_TEXT_TIMEOUT_S)


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

    # 진단용: 스크립트가 실제로 시작됐는지 최대한 빨리 알림 (여기까지도 안 오면
    # 문제는 이 스크립트가 아니라 그 이전 단계 - 의존성 설치 등 - 에 있다는 뜻)
    send_message(env, "썸네일 자동화 스크립트 시작했어요. 오늘자 영상을 찾는 중...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 900})

        info = wait_for_todays_video(page)
        if not info:
            send_message(
                env,
                "오늘자 영상을 20분 넘게 기다렸는데 아직 채널에 안 보여요 "
                "(다시보기 전환이 늦어지고 있을 수 있어요). Claude Code에서 직접 확인해주세요.",
            )
            browser.close()
            return

        send_message(env, f"영상 찾았어요: {info['raw_title']}\n후보 사진 캡처 중...")

        if info.get("video_id"):
            try:
                out_dir.mkdir(parents=True, exist_ok=True)
                run_trim_diagnostic(env, info["video_id"], out_dir)
            except Exception as e:
                print(f"[실험용] 트림 진단 실패(무시하고 계속): {e}")

        photos = capture_stills_from_vod(page, info["video_id"], out_dir)
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
        send_message(env, f"저장 완료: {final_path.name}\n(오늘자 영상 ID를 못 찾아서 자동 업로드는 건너뛰었어요. 스튜디오에서 직접 업로드해주세요.)")
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
