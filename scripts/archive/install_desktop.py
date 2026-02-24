"""
바탕화면 바로가기 + 아이콘 생성

실행:
    python scripts/install_desktop.py

결과:
    1. scripts/bgf_report.ico  - 차트 아이콘 생성
    2. 바탕화면에 "BGF 발주 시스템.lnk" 바로가기 생성 (웹 대시보드)
"""

import subprocess
import sys
import shutil
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent


def create_icon() -> Path:
    """Pillow로 차트 아이콘 생성"""
    icon_path = SCRIPT_DIR / "bgf_report.ico"

    try:
        from PIL import Image, ImageDraw

        sizes = [16, 32, 48, 64, 128, 256]
        images = []

        for size in sizes:
            img = Image.new("RGBA", (size, size), (15, 15, 26, 255))
            draw = ImageDraw.Draw(img)

            margin = max(size // 8, 2)
            bar_area_w = size - margin * 2
            bar_area_h = size - margin * 2
            bar_count = 4
            gap = max(bar_area_w // 20, 1)
            bar_w = (bar_area_w - gap * (bar_count + 1)) // bar_count
            bottom = size - margin

            bar_heights = [0.5, 0.7, 0.9, 0.65]
            bar_colors = [
                (0, 210, 255, 255),     # cyan
                (105, 240, 174, 255),   # green
                (255, 214, 0, 255),     # yellow
                (255, 107, 107, 255),   # red
            ]

            for i in range(bar_count):
                x1 = margin + gap + i * (bar_w + gap)
                h = int(bar_area_h * bar_heights[i])
                y1 = bottom - h
                x2 = x1 + bar_w
                y2 = bottom

                # 바 그리기
                draw.rectangle([x1, y1, x2, y2], fill=bar_colors[i])

                # 상단 하이라이트
                if size >= 32:
                    hl = bar_colors[i][:3] + (80,)
                    draw.rectangle([x1, y1, x2, y1 + max(size // 16, 1)], fill=hl)

            # 하단 선
            draw.line(
                [(margin, bottom), (size - margin, bottom)],
                fill=(30, 48, 84, 255), width=max(size // 16, 1),
            )

            # 좌측 선
            draw.line(
                [(margin, margin), (margin, bottom)],
                fill=(30, 48, 84, 255), width=max(size // 16, 1),
            )

            images.append(img)

        # ICO 저장 (256 기본 + 나머지 사이즈)
        images[-1].save(
            str(icon_path), format="ICO",
            sizes=[(s, s) for s in sizes],
            append_images=images[:-1],
        )
        print(f"  아이콘 생성: {icon_path}")
        return icon_path

    except ImportError:
        print("  [경고] Pillow가 없어 아이콘을 생성할 수 없습니다.")
        print("         pip install Pillow 로 설치 후 다시 실행하세요.")
        return None
    except Exception as e:
        print(f"  [경고] 아이콘 생성 실패: {e}")
        return None


def create_shortcut(icon_path: Path = None) -> bool:
    """PowerShell로 바탕화면 바로가기 생성"""
    pythonw = shutil.which("pythonw")
    if not pythonw:
        print("  [오류] pythonw.exe를 찾을 수 없습니다.")
        return False

    launcher = SCRIPT_DIR / "run_dashboard.pyw"
    if not launcher.exists():
        # fallback: 기존 런처
        launcher = SCRIPT_DIR / "report_launcher.pyw"
    if not launcher.exists():
        print(f"  [오류] 런처 파일 없음: {launcher}")
        return False

    # 바로가기 경로
    desktop = Path.home() / "Desktop"
    if not desktop.exists():
        # 한글 Windows
        desktop = Path.home() / "바탕 화면"
    if not desktop.exists():
        desktop = Path.home() / "OneDrive" / "바탕 화면"
    if not desktop.exists():
        print(f"  [오류] 바탕화면 경로를 찾을 수 없습니다.")
        return False

    shortcut_path = desktop / "BGF 발주 시스템.lnk"

    icon_arg = ""
    if icon_path and icon_path.exists():
        icon_arg = f'$s.IconLocation = "{icon_path}"'

    ps_script = f"""
$WshShell = New-Object -ComObject WScript.Shell
$s = $WshShell.CreateShortcut("{shortcut_path}")
$s.TargetPath = "{pythonw}"
$s.Arguments = '"{launcher}"'
$s.WorkingDirectory = "{PROJECT_ROOT}"
{icon_arg}
$s.Description = "BGF 발주 시스템 (웹 대시보드)"
$s.Save()
"""

    try:
        result = subprocess.run(
            ["powershell", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print(f"  바로가기 생성: {shortcut_path}")
            return True
        else:
            print(f"  [오류] PowerShell 오류: {result.stderr}")
            return False
    except Exception as e:
        print(f"  [오류] 바로가기 생성 실패: {e}")
        return False


def main():
    print("=" * 44)
    print("  BGF 발주 시스템 - 바탕화면 바로가기 설치")
    print("=" * 44)
    print()

    # 1. 아이콘 생성
    print("[1/2] 아이콘 생성")
    icon_path = create_icon()
    print()

    # 2. 바로가기 생성
    print("[2/2] 바탕화면 바로가기 생성")
    success = create_shortcut(icon_path)
    print()

    if success:
        print("=" * 44)
        print("  설치 완료!")
        print("  바탕화면의 'BGF 발주 시스템' 아이콘을")
        print("  더블클릭하면 웹 대시보드가 실행됩니다.")
        print("=" * 44)
    else:
        print("바로가기 생성에 실패했습니다.")
        print(f"수동으로 다음 파일을 실행하세요:")
        print(f"  {SCRIPT_DIR / 'run_dashboard.pyw'}")

    try:
        input("\nEnter를 눌러 종료...")
    except EOFError:
        pass


if __name__ == "__main__":
    main()
