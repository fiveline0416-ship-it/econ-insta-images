"""
경제 1분 · 디자인팀 — 릴스 영상 만들기
카드 이미지(1080×1350) 여러 장을 넘기는 9:16 릴스 영상(1080×1920 MP4)으로 만든다.

사용법:  python make_reel.py 카드폴더 결과.mp4 [장당초]
  카드폴더 안의 card_01.png, card_02.png ... 순서대로 사용 (png 없으면 jpg)
필요:    ffmpeg (libx264), Pillow

인스타그램 릴스 규격: H.264 + AAC, 9:16, 30fps, 3초~15분.
음악은 넣지 않는다(저작권). 대신 무음 오디오 트랙을 넣어 규격을 맞춘다.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1920
CARD_W, CARD_H = 1080, 1350
INK = (19, 35, 63)        # render.py 의 남색과 같은 색
ACCENT = (232, 85, 58)
FADE = 0.45               # 장 사이 전환 시간(초)
HERE = Path(__file__).resolve().parent
FONT = HERE / "node_modules" / "pretendard" / "dist" / "public" / "static" / "Pretendard-ExtraBold.otf"


def load_font(size):
    for f in [FONT, *HERE.glob("node_modules/pretendard/**/Pretendard-ExtraBold.otf")]:
        if f.exists():
            return ImageFont.truetype(str(f), size)
    return ImageFont.load_default()


def frame(card: Path, i: int, total: int) -> Image.Image:
    """카드 한 장을 9:16 화면 가운데에 놓고, 위·아래 띠에 로고와 진행 표시를 그린다."""
    bg = Image.new("RGB", (W, H), INK)
    im = Image.open(card).convert("RGB").resize((CARD_W, CARD_H))
    top = (H - CARD_H) // 2
    bg.paste(im, (0, top))
    d = ImageDraw.Draw(bg)
    f = load_font(46)
    d.text((72, top // 2), "경제 ", font=f, fill=(255, 255, 255), anchor="lm")
    d.text((72 + d.textlength("경제 ", font=f), top // 2), "1분", font=f, fill=ACCENT, anchor="lm")
    # 아래 띠: 진행 막대
    bar_y, gap = top + CARD_H + (H - top - CARD_H) // 2, 12
    seg = (W - 144 - gap * (total - 1)) / total
    for k in range(total):
        x0 = 72 + k * (seg + gap)
        d.rounded_rectangle([x0, bar_y - 5, x0 + seg, bar_y + 5], radius=5,
                            fill=ACCENT if k <= i else (70, 86, 115))
    return bg


def make_reel(card_dir, out_path, per=3.5):
    card_dir, out_path = Path(card_dir), Path(out_path).resolve()
    cards = sorted(card_dir.glob("card_*.png")) or sorted(card_dir.glob("card_*.jpg"))
    if len(cards) < 2:
        raise SystemExit("카드가 2장 이상 필요해요")
    with tempfile.TemporaryDirectory() as tmp:
        frames = []
        for i, c in enumerate(cards):
            p = Path(tmp) / f"f{i:02d}.png"
            frame(c, i, len(cards)).save(p)
            frames.append(p)
        n = len(frames)
        last = per + 1.0                       # 마지막 장은 조금 더 오래
        cmd = ["ffmpeg", "-y", "-loglevel", "error"]
        for i, p in enumerate(frames):
            cmd += ["-loop", "1", "-t", str(last if i == n - 1 else per + FADE), "-framerate", "30", "-i", str(p)]
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
        # 장과 장 사이를 부드럽게 겹쳐 넘김 (xfade)
        chain, prev, offset = [], "[0:v]", 0.0
        for i in range(1, n):
            offset += per
            label = f"[v{i}]"
            chain.append(f"{prev}[{i}:v]xfade=transition=slideleft:duration={FADE}:offset={offset:.2f}{label}")
            prev = label
        total = per * (n - 1) + last
        chain.append(f"{prev}format=yuv420p[vout]")
        cmd += ["-filter_complex", ";".join(chain), "-map", "[vout]", "-map", f"{n}:a",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-r", "30",
                "-c:a", "aac", "-b:a", "128k", "-t", f"{total:.2f}",
                "-movflags", "+faststart", str(out_path)]
        subprocess.run(cmd, check=True)
    print(f"🎬 {out_path.name}  ({len(cards)}장, 약 {total:.0f}초, {out_path.stat().st_size / 1e6:.1f}MB)")
    return out_path


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit("사용법: python make_reel.py 카드폴더 결과.mp4 [장당초]")
    make_reel(sys.argv[1], sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else 3.5)
