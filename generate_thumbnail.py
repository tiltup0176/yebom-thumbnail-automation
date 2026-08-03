"""예봄교회 유튜브 썸네일 합성 스크립트 (Pillow 기반, HTML/브라우저 렌더링 불필요).

'01. 유튜브 썸네일 : 타임라인/00. psd/20260503 설교 썸네일.psd' 원본 포토샵 파일을
직접 파싱해서 얻은 실제 좌표·폰트·크기를 그대로 사용한다 (추정치 아님).
"""
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1280, 720
FONT_DIR = "/Users/choieunkang/Library/Fonts"
LEFT_X = 187

# PSD 레이어 bbox 실측값 기준 그리드
LOGO_Y, LOGO_H = 109, 67          # bbox (187,109,337,176)
META_Y = 242                       # bbox (187,242,575,278), 폰트 Pretendard-Medium
META_SIZE = 40
TITLE_1LINE_Y = 370                # bbox (181,370,459,430), 폰트 Pretendard-Bold
TITLE_2LINE_Y = (317, 401)         # bbox (188,317,519,461) 기준 역산 (줄간격 84px)
TITLE_SIZE = 66
SCRIPTURE_Y = 501                  # bbox (186,501,469,537), 폰트 Pretendard-Medium
PREACHER_Y = 576                   # bbox (187,576,364,612), 폰트 Pretendard-Medium
BODY_SIZE = 40

# 좌측 검정 -> 사진 그라데이션: PSD의 실제 마스크 레이어(Layer 16의 레이어 마스크)에서
# 10px 간격으로 추출한 "사진 노출도(0=완전 검정, 255=사진 100%)" 곡선. 선형이 아니라
# 완만하게 휘는 실제 곡선이라 훨씬 더 진하고 넓게 검게 깔린다.
MASK_SAMPLES = [
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    1, 1, 2, 3, 5, 6, 7, 9, 12, 14, 16, 18, 22, 25, 28, 31, 35, 39, 43, 48, 51, 56,
    61, 65, 70, 75, 80, 86, 91, 97, 103, 107, 113, 119, 123, 129, 136, 141, 146,
    151, 158, 163, 170, 174, 177, 184, 189, 192, 198, 202, 207, 210, 214, 216,
    219, 221, 224, 225, 227, 230, 232, 234, 236, 237, 238, 240, 241, 243, 245,
    245, 247, 248, 249, 249, 250, 251, 252, 252, 253, 253, 254, 254, 255, 255,
    255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255,
    255, 255,
]  # 10px 간격, x=0..1280


def photo_alpha_at(x):
    """x(px)에서 사진이 얼마나 드러나는지(0~255). PSD 마스크 실측값 선형보간."""
    idx = x / 10
    i0 = int(idx)
    i1 = min(i0 + 1, len(MASK_SAMPLES) - 1)
    frac = idx - i0
    i0 = min(i0, len(MASK_SAMPLES) - 1)
    return MASK_SAMPLES[i0] * (1 - frac) + MASK_SAMPLES[i1] * frac

# 제목이 이 폭(px)을 넘으면 2줄로 분리 (PSD의 2줄 예시 폭 기준)
TITLE_MAX_W = 560

# PSD의 Layer 17(선명한 인물 사진)이 실제로 놓여 있던 비율.
# bbox (611,49)-(1280,720) -> 669 x 671, 거의 정사각형.
# 매주 캡처 사진마다 원본 가로세로 비율이 달라도, 항상 이 비율로 인물을
# 중앙 크롭해서 써야 인물 크기/확대율이 일정하게 유지된다.
PERSON_BOX_RATIO = (1280 - 611) / (720 - 49)  # 669/671

# 머리 위 여백만 잘라내는 비율 (아래쪽/상반신은 절대 안 건드림 - 그대로 유지).
# 0.116 = 원본 높이의 11.6%를 위에서만 잘라냄 -> 아래는 그대로 두고
# 머리만 로고 윗쪽 높이(y≈109)까지 올라오도록 실측 후 조정한 값.
PERSON_TOP_CROP_FRAC = 0.116

# 인물을 오른쪽으로 미는 픽셀 값 (양수 = 오른쪽). 손 등 오른쪽 끝이
# 캔버스 밖으로 살짝 잘려도 되니 인물 자체를 더 오른쪽으로 옮겨달라는
# 피드백을 반영한 값 (실측 후 조정).
PERSON_SHIFT_X = 70


def center_crop_to_ratio(img, ratio):
    """img를 가로세로비 ratio(width/height)에 맞춰 중앙 기준으로 크롭."""
    w, h = img.size
    if w / h > ratio:
        new_w = int(h * ratio)
        x0 = (w - new_w) // 2
        return img.crop((x0, 0, x0 + new_w, h))
    else:
        new_h = int(w / ratio)
        y0 = (h - new_h) // 2
        return img.crop((0, y0, w, y0 + new_h))


def font(name, size):
    return ImageFont.truetype(f"{FONT_DIR}/{name}", size)


def generate(photo_path, logo_path, date_str, title, scripture, preacher, output_path):
    # 1) 인물 사진 - 세로 기준으로 캔버스 높이(H)에 맞추고, 사람이 항상
    #    PERSON_CENTER_X 부근에 오도록 가로 위치를 맞춰 크롭한다 (가로로 늘리지 않음).
    #    "오른쪽 끝에서부터 자르기"가 아니라 "인물 중심 기준 정렬"이라, 매주 사진마다
    #    카메라 구도가 조금씩 달라도 인물 크기/위치가 일정하게 유지된다.
    photo = Image.open(photo_path).convert("RGB")

    # 1-a) 인물이 화면 중앙 부근에 서 있다는 전제로, PSD 인물 박스와 같은 비율로
    #      중앙 크롭 -> 매주 원본 사진 크기가 달라도 인물 확대율이 일정해진다.
    person_crop = center_crop_to_ratio(photo, PERSON_BOX_RATIO)

    # 1-a-1) 머리 위 여백만 제거 (아래쪽/상반신 프레이밍은 절대 그대로 유지).
    #        위에서만 잘라내고 아래쪽 경계는 원본 그대로 두기 때문에, 확대되면서도
    #        상반신이 잘리지 않고 아래쪽 구도는 이전 버전과 동일하게 유지된다.
    zw, zh = person_crop.size
    top_crop_px = int(zh * PERSON_TOP_CROP_FRAC)
    person_crop = person_crop.crop((0, top_crop_px, zw, zh))

    person_crop = person_crop.resize(
        (int(person_crop.width * H / person_crop.height), H)
    )

    if person_crop.width >= W:
        x0 = person_crop.width - W - PERSON_SHIFT_X
        x0 = max(0, min(x0, person_crop.width - W))
        canvas = person_crop.crop((x0, 0, x0 + W, H)).convert("RGBA")
    else:
        # 1-b) 인물 크롭이 캔버스보다 좁으면(대부분의 경우), 왼쪽 남는 공간은
        #      인물 크롭의 왼쪽 가장자리 벽 텍스처를 이어붙여 자연스럽게 채운다
        #      (사람을 다시 축소해서 채우면 이전 버전처럼 유령처럼 겹쳐 보이는
        #      문제가 생기므로, 사람은 그대로 두고 배경만 옆으로 늘린다).
        #      PERSON_SHIFT_X 만큼 오른쪽으로 더 밀어서, 필요하면 오른쪽 끝이
        #      캔버스 밖으로 살짝 잘려도 인물이 더 오른쪽에 오도록 한다.
        paste_x = W - person_crop.width + PERSON_SHIFT_X
        canvas_rgb = Image.new("RGB", (W, H))
        strip_w = 24
        strip = person_crop.crop((0, 0, strip_w, H))
        x = 0
        while x < paste_x:
            canvas_rgb.paste(strip, (x, 0))
            x += strip_w
        canvas_rgb.paste(person_crop, (paste_x, 0))
        canvas = canvas_rgb.convert("RGBA")

    # 2) 좌측 검정 오버레이: PSD 마스크 실측 곡선 그대로 적용
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for x in range(0, W):
        black_alpha = int(255 - photo_alpha_at(x))
        if black_alpha > 0:
            draw.line([(x, 0), (x, H)], fill=(0, 0, 0, black_alpha))
    canvas = Image.alpha_composite(canvas, overlay)
    draw = ImageDraw.Draw(canvas)

    # 3) 로고
    logo = Image.open(logo_path).convert("RGBA")
    logo_h = LOGO_H
    logo_w = int(logo.width * (logo_h / logo.height))
    logo = logo.resize((logo_w, logo_h))
    canvas.paste(logo, (LEFT_X, LOGO_Y), logo)

    # 4) 구분 + 날짜 (PSD 원본과 동일하게 "ㅣ" 기호 사용)
    f_meta = font("Pretendard-Medium.otf", META_SIZE)
    draw.text((LEFT_X, META_Y), f"주일말씀 ㅣ {date_str}", font=f_meta, fill=(255, 255, 255, 255))

    # 5) 설교 제목 - 폭 초과 시 PSD의 2줄 그리드로 자동 분리
    f_title = font("Pretendard-Bold.otf", TITLE_SIZE)
    if draw.textlength(title, font=f_title) <= TITLE_MAX_W or " " not in title:
        draw.text((LEFT_X, TITLE_1LINE_Y), title, font=f_title, fill=(255, 255, 255, 255))
    else:
        words = title.split(" ")
        mid = len(words) // 2
        line1, line2 = " ".join(words[:mid]), " ".join(words[mid:])
        draw.text((LEFT_X, TITLE_2LINE_Y[0]), line1, font=f_title, fill=(255, 255, 255, 255))
        draw.text((LEFT_X, TITLE_2LINE_Y[1]), line2, font=f_title, fill=(255, 255, 255, 255))

    # 6) 본문
    f_body = font("Pretendard-Medium.otf", BODY_SIZE)
    draw.text((LEFT_X, SCRIPTURE_Y), scripture, font=f_body, fill=(255, 255, 255, 255))

    # 7) 설교자
    draw.text((LEFT_X, PREACHER_Y), preacher, font=f_body, fill=(255, 255, 255, 255))

    canvas.convert("RGB").save(output_path, "JPEG", quality=92)
    print("saved:", output_path)


if __name__ == "__main__":
    # ↓↓↓ 매주 이 값들만 바꿔서 실행하면 됩니다 ↓↓↓
    import os
    HERE = os.path.dirname(os.path.abspath(__file__))
    CHURCH_DIR = os.path.dirname(HERE)

    generate(
        photo_path=f"{HERE}/예시_사진.jpg",
        logo_path=f"{CHURCH_DIR}/00. 예봄교회 로고/24.Yebom_logo_W.png",
        date_str="2026. 8. 2",
        title="기도하면 하나님이 일하신다",  # 2줄 자동 분리 테스트
        scripture="요한복음 18장 1-9절",
        preacher="최병희 목사",
        output_path=f"{HERE}/예시_결과.jpg",  # 실제 운영 시에는 이 경로를
        # "{CHURCH_DIR}/01. 유튜브 썸네일 : 타임라인/YYYYMMDD.jpg" 로 바꿔서 저장합니다.
    )
