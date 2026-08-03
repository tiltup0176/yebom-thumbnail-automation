"""YouTube Data API로 썸네일을 자동 업로드하는 스크립트.

.env에 YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET / YOUTUBE_REFRESH_TOKEN이
있어야 동작한다 (get_refresh_token.py로 최초 1회 발급).
"""
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

ENV_PATH = Path.home() / ".claude/yebom-youtube/.env"


def load_env():
    env = {}
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def get_client(env):
    creds = Credentials(
        token=None,
        refresh_token=env["YOUTUBE_REFRESH_TOKEN"],
        client_id=env["YOUTUBE_CLIENT_ID"],
        client_secret=env["YOUTUBE_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
    )
    return build("youtube", "v3", credentials=creds)


def find_latest_video_id(yt):
    """가장 최근(오늘) 라이브/업로드 영상의 videoId를 찾는다."""
    resp = yt.liveBroadcasts().list(part="id", broadcastStatus="active", broadcastType="all").execute()
    items = resp.get("items", [])
    if items:
        return items[0]["id"]
    # 라이브가 이미 끝났으면 채널의 최신 업로드 영상으로 폴백
    resp = yt.search().list(part="id", forMine=True, order="date", type="video", maxResults=1).execute()
    return resp["items"][0]["id"]["videoId"]


def set_thumbnail(video_id, image_path):
    env = load_env()
    yt = get_client(env)
    yt.thumbnails().set(videoId=video_id, media_body=image_path).execute()
    print(f"썸네일 업로드 완료: video_id={video_id}, file={image_path}")


if __name__ == "__main__":
    import sys
    env = load_env()
    yt = get_client(env)
    vid = sys.argv[2] if len(sys.argv) > 2 else find_latest_video_id(yt)
    set_thumbnail(vid, sys.argv[1])
