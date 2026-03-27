"""BGF 자동발주 시스템 Windows 아이콘 생성기 v2
트렌디한 단일 형체 디자인 — 자동 순환 발주를 상징하는 하나의 글리프
"""
from PIL import Image, ImageDraw
import math
import os


def lerp_color(c1, c2, t):
    """두 색상 사이를 보간"""
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


def draw_gradient_rounded_rect(img, bbox, radius, color_top, color_bottom):
    """그라데이션 둥근 사각형"""
    x0, y0, x1, y1 = bbox
    w, h = x1 - x0, y1 - y0
    mask = Image.new("L", img.size, 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle(bbox, radius=radius, fill=255)

    for y in range(y0, y1):
        t = (y - y0) / max(1, h - 1)
        color = lerp_color(color_top, color_bottom, t)
        draw = ImageDraw.Draw(img)
        draw.line([(x0, y), (x1, y)], fill=(*color, 255))

    # 마스크 적용
    alpha = img.split()[3]
    from PIL import ImageChops
    new_alpha = ImageChops.multiply(alpha, mask.convert("L"))
    img.putalpha(new_alpha)


def draw_thick_arc(draw, cx, cy, radius, start_deg, end_deg, thickness, color, ss=1):
    """두꺼운 원호를 점으로 그리기 (안티앨리어싱은 슈퍼샘플링으로 해결)"""
    half_t = thickness / 2
    step = 0.5 / ss  # 슈퍼샘플링에 맞춰 촘촘하게
    angle = start_deg
    while angle <= end_deg:
        rad = math.radians(angle)
        x = cx + radius * math.cos(rad)
        y = cy + radius * math.sin(rad)
        draw.ellipse([x - half_t, y - half_t, x + half_t, y + half_t], fill=color)
        angle += step


def draw_glyph(draw, cx, cy, size, color):
    """
    단일 형체 글리프: 순환 화살표 + 중앙 큐브
    하나의 연결된 형태로 '자동 발주 순환'을 표현
    """
    s = size  # 기준 크기

    # ── 1) 메인 순환 아크 (270도) ──
    arc_r = s * 0.34
    arc_thickness = s * 0.085
    # 위에서 시작, 시계방향 270도 (gap은 오른쪽 위)
    draw_thick_arc(draw, cx, cy, arc_r, 135, 405, arc_thickness, color)

    # ── 2) 화살표 머리 (아크 끝점) ──
    # 아크 끝 = 405도 = 45도
    arrow_angle = math.radians(45)
    arrow_tip_x = cx + arc_r * math.cos(arrow_angle)
    arrow_tip_y = cy + arc_r * math.sin(arrow_angle)

    # 화살표 크기
    arr_len = s * 0.12
    arr_width = s * 0.10

    # 화살표 방향: 아크의 접선 방향 (45도에서의 접선 = 135도 방향)
    tangent = math.radians(135)

    # 화살표 팁에서 뒤로 두 점
    back_angle1 = tangent + math.radians(25)
    back_angle2 = tangent - math.radians(25)

    p1 = (arrow_tip_x, arrow_tip_y)
    p2 = (arrow_tip_x + arr_len * math.cos(back_angle1),
          arrow_tip_y + arr_len * math.sin(back_angle1))
    p3 = (arrow_tip_x + arr_len * math.cos(back_angle2),
          arrow_tip_y + arr_len * math.sin(back_angle2))

    draw.polygon([p1, p2, p3], fill=color)

    # ── 3) 중앙 큐브 (발주/패키지 상징) ──
    # 등축 투영 큐브를 하나의 형태로
    cube_s = s * 0.11
    # 큐브 중심을 약간 위로
    cube_cx = cx
    cube_cy = cy - s * 0.01

    # 등축 큐브 꼭짓점 (30도 각도)
    a30 = math.radians(30)
    # 위 꼭짓점
    top = (cube_cx, cube_cy - cube_s)
    # 왼쪽
    left = (cube_cx - cube_s * math.cos(a30), cube_cy - cube_s * math.sin(a30) + cube_s * 0.6)
    # 오른쪽
    right = (cube_cx + cube_s * math.cos(a30), cube_cy - cube_s * math.sin(a30) + cube_s * 0.6)
    # 아래
    bottom = (cube_cx, cube_cy + cube_s * 0.2 + cube_s * 0.6)
    # 왼쪽 위, 오른쪽 위 (뒤)
    back_left = (cube_cx - cube_s * math.cos(a30), cube_cy - cube_s * 0.4)
    back_right = (cube_cx + cube_s * math.cos(a30), cube_cy - cube_s * 0.4)

    # 큐브 면 (약간 밝은/어두운 변형)
    # 윗면
    draw.polygon([top, back_left, (cube_cx, cube_cy + cube_s * 0.05), back_right], fill=color)
    # 왼쪽면
    c_dark = tuple(max(0, c - 30) for c in color[:3]) + (color[3],) if len(color) == 4 else tuple(max(0, c - 30) for c in color[:3])
    draw.polygon([back_left, left, bottom, (cube_cx, cube_cy + cube_s * 0.05)], fill=c_dark)
    # 오른쪽면
    c_light = tuple(min(255, c + 20) for c in color[:3]) + (color[3],) if len(color) == 4 else tuple(min(255, c + 20) for c in color[:3])
    draw.polygon([back_right, right, bottom, (cube_cx, cube_cy + cube_s * 0.05)], fill=c_light)


def create_icon():
    """멀티 사이즈 ICO 생성 (슈퍼샘플링 안티앨리어싱)"""
    target_sizes = [256, 128, 64, 48, 32, 16]
    SS = 4  # 슈퍼샘플링 배율
    images = []

    for target in target_sizes:
        render_size = target * SS
        img = Image.new("RGBA", (render_size, render_size), (0, 0, 0, 0))

        # 패딩
        pad = render_size // 16
        s = render_size - pad * 2
        corner_r = render_size // 5

        # ── 배경 그라데이션 ──
        # 모던한 다크 그라데이션 (딥 네이비 → 보라)
        color_top = (20, 10, 50)
        color_bottom = (80, 30, 120)
        draw_gradient_rounded_rect(
            img,
            (pad, pad, pad + s, pad + s),
            corner_r,
            color_top,
            color_bottom
        )

        draw = ImageDraw.Draw(img)
        cx = render_size // 2
        cy = render_size // 2

        # ── 글리프 (밝은 흰색 단일 형체) ──
        glyph_color = (255, 255, 255, 245)
        draw_glyph(draw, cx, cy, s, glyph_color)

        # ── 다운샘플링 (안티앨리어싱) ──
        final = img.resize((target, target), Image.LANCZOS)
        images.append(final)

    # 저장
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.dirname(script_dir)
    ico_path = os.path.join(project_dir, "bgf_auto.ico")
    png_path = os.path.join(project_dir, "bgf_auto_icon_preview.png")

    images[0].save(
        ico_path,
        format="ICO",
        sizes=[(s, s) for s in target_sizes],
        append_images=images[1:]
    )
    print(f"ICO 생성: {ico_path}")

    images[0].save(png_path, format="PNG")
    print(f"PNG 미리보기: {png_path}")

    return ico_path


if __name__ == "__main__":
    create_icon()
