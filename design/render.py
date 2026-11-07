"""
경제 1분 · 디자인팀
카드뉴스 원고(JSON)를 1080×1350 PNG 이미지(+ 인스타 게시용 JPG)로 만든다.

사용법:  python render.py 원고.json 결과폴더
필요:    pip install playwright  /  npm i pretendard@1.3.9  (이 파일과 같은 폴더)

원고 JSON 모양:
{
  "meta": {"source": "한국은행 BOK 이슈노트 제2026-28호", "date": "2026.10.28"},
  "slides": [
    {"type": "cover",   "kicker": "...", "title": "...", "subtitle": "..."},
    {"type": "body",    "title": "...", "bullets": ["...", "<b>강조</b>"], "oneliner": "..."},
    {"type": "numbers", "title": "...", "stats": [{"value": "$1,400", "label": "..."}], "oneliner": "..."},
    {"type": "end",     "title": "...", "source": "...", "cta": "..."}
  ]
}
"""
import html
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
FONT_DIR = HERE / "node_modules" / "pretendard" / "dist" / "web" / "static" / "woff2"
W, H = 1080, 1350

# ── 색 ──────────────────────────────────────────────
INK = "#13233F"      # 남색 (글자, 표지 배경)
PAPER = "#F5F2EB"    # 종이색 (본문 배경)
ACCENT = "#E8553A"   # 강조 (숫자, 포인트)
MUTED = "#6B7280"    # 보조 글자
RULE = "#D9D3C7"     # 구분선


def fonts_css():
    faces = []
    for weight, name in [(400, "Regular"), (500, "Medium"), (600, "SemiBold"), (700, "Bold"), (800, "ExtraBold")]:
        f = FONT_DIR / f"Pretendard-{name}.woff2"
        if f.exists():
            faces.append(f"@font-face{{font-family:P;font-weight:{weight};src:url('{f.as_uri()}') format('woff2');}}")
    return "\n".join(faces)


BASE_CSS = f"""
{fonts_css()}
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:{W}px;height:{H}px}}
body{{font-family:P,'Noto Sans CJK KR',sans-serif;color:{INK};background:{PAPER};
  word-break:keep-all;-webkit-font-smoothing:antialiased;letter-spacing:-0.02em}}
.card{{position:relative;width:{W}px;height:{H}px;padding:96px 88px 0;display:flex;flex-direction:column}}
b{{color:{ACCENT};font-weight:800}}
.brand{{position:absolute;left:88px;bottom:64px;font-size:26px;font-weight:800;letter-spacing:-0.01em}}
.brand i{{font-style:normal;color:{ACCENT}}}
.src{{position:absolute;right:88px;bottom:66px;font-size:22px;color:{MUTED};font-weight:500;max-width:620px;text-align:right}}
.page{{font-size:26px;font-weight:700;color:{ACCENT};letter-spacing:0.04em}}
.page span{{color:{MUTED};font-weight:500}}
.title{{font-size:64px;font-weight:800;line-height:1.25;margin-top:28px}}
.rule{{width:72px;height:8px;background:{ACCENT};border-radius:4px;margin-top:40px}}
.bullets{{list-style:none;margin-top:56px;display:flex;flex-direction:column;gap:34px}}
.bullets li{{font-size:40px;font-weight:500;line-height:1.45;padding-left:44px;position:relative}}
.bullets li::before{{content:'';position:absolute;left:0;top:22px;width:16px;height:16px;border-radius:50%;background:{INK}}}
.oneliner{{margin-top:auto;margin-bottom:170px;background:{INK};color:#fff;border-radius:28px;
  padding:40px 48px;font-size:38px;font-weight:700;line-height:1.4}}
.oneliner small{{display:block;font-size:24px;font-weight:600;color:#F4B9AC;margin-bottom:10px;letter-spacing:0.02em}}
.oneliner b{{color:#FF8A70}}
/* 숫자 카드 */
.stats{{margin-top:56px;display:flex;flex-direction:column;gap:0}}
.stat{{padding:30px 0;border-top:3px solid {RULE}}}
.stat:last-child{{border-bottom:3px solid {RULE}}}
.stat .v{{font-size:72px;font-weight:800;color:{ACCENT};line-height:1.1;letter-spacing:-0.03em}}
.stat .l{{font-size:32px;font-weight:500;color:{INK};margin-top:10px;line-height:1.35}}
/* 표지 */
.cover{{background:{INK};color:#fff}}
.cover .kicker{{display:inline-block;align-self:flex-start;font-size:28px;font-weight:700;color:{INK};
  background:#fff;border-radius:999px;padding:14px 30px}}
.cover .title{{font-size:96px;line-height:1.18;margin-top:auto;letter-spacing:-0.035em}}
.cover .title b{{color:#FF8A70}}
.cover .subtitle{{font-size:40px;font-weight:500;color:#C9D2E3;margin-top:44px;line-height:1.45}}
.cover .rule{{margin-top:56px;margin-bottom:190px}}
.cover .brand{{color:#fff}}
.cover .src{{color:#8E9BB5}}
/* 마지막 */
.end .title{{font-size:76px;margin-top:auto;line-height:1.25}}
.end .cta{{font-size:38px;font-weight:600;margin-top:48px;color:{INK};line-height:1.45}}
.end .source{{font-size:28px;color:{MUTED};margin-top:56px;margin-bottom:190px;line-height:1.5;
  border-top:3px solid {RULE};padding-top:32px}}
"""

FIT_JS = """
() => {
  // 글이 넘치면 본문 글자를 조금씩 줄인다
  const card = document.querySelector('.card');
  const box = document.querySelector('.bullets, .stats, .cover .title, .end .title');
  if (!box) return 0;
  let n = 0;
  const over = () => card.scrollHeight > card.clientHeight + 1 ||
    [...card.children].some(c => c.getBoundingClientRect().bottom > card.clientHeight - 150 && !c.classList.contains('brand') && !c.classList.contains('src'));
  while (over() && n < 25) {
    box.querySelectorAll('li, .v, .l').forEach(e => {
      e.style.fontSize = (parseFloat(getComputedStyle(e).fontSize) * 0.95) + 'px'; });
    if (box.matches('.title')) box.style.fontSize = (parseFloat(getComputedStyle(box).fontSize) * 0.95) + 'px';
    n++;
  }
  return n;
}
"""


def clean(s):
    """<b>…</b>와 <br>만 살리고 나머지는 글자로 처리."""
    s = html.escape(s or "", quote=False)
    for tag in ("b", "/b"):
        s = s.replace(f"&lt;{tag}&gt;", f"<{tag}>")
    return s.replace("&lt;br&gt;", "<br>").replace("&lt;br/&gt;", "<br>").replace("\n", "<br>")


def footer(meta, show_src=True):
    src = f'<div class="src">{clean(meta.get("source", ""))}</div>' if show_src else ""
    return f'<div class="brand">경제 <i>1분</i></div>{src}'


def slide_html(s, i, total, meta):
    t = s.get("type", "body")
    page = f'<div class="page">{i:02d} <span>/ {total:02d}</span></div>'
    if t == "cover":
        inner = (f'<div class="kicker">{clean(s.get("kicker", ""))}</div>'
                 f'<div class="title">{clean(s["title"])}</div>'
                 f'<div class="subtitle">{clean(s.get("subtitle", ""))}</div><div class="rule"></div>'
                 + footer(meta))
        return f'<div class="card cover">{inner}</div>'
    if t == "end":
        inner = (page + f'<div class="title">{clean(s["title"])}</div>'
                 f'<div class="cta">{clean(s.get("cta", ""))}</div>'
                 f'<div class="source">{clean(s.get("source", ""))}</div>' + footer(meta, show_src=False))
        return f'<div class="card end">{inner}</div>'
    if t == "numbers":
        stats = "".join(f'<div class="stat"><div class="v">{clean(x["value"])}</div>'
                        f'<div class="l">{clean(x.get("label", ""))}</div></div>' for x in s.get("stats", []))
        body = f'<div class="stats">{stats}</div>'
    else:
        body = '<ul class="bullets">' + "".join(f"<li>{clean(b)}</li>" for b in s.get("bullets", [])) + "</ul>"
    one = s.get("oneliner")
    one_html = f'<div class="oneliner"><small>한 줄 정리</small>{clean(one)}</div>' if one else ""
    inner = page + f'<div class="title">{clean(s["title"])}</div><div class="rule"></div>' + body + one_html + footer(meta)
    return f'<div class="card">{inner}</div>'


def render(spec_path, out_dir):
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    slides, meta = spec["slides"], spec.get("meta", {})
    files = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        for i, s in enumerate(slides, 1):
            doc = f"<!doctype html><html><head><meta charset='utf-8'><style>{BASE_CSS}</style></head><body>{slide_html(s, i, len(slides), meta)}</body></html>"
            tmp = out / f"_slide{i}.html"
            tmp.write_text(doc, encoding="utf-8")
            page.goto(tmp.as_uri())
            page.evaluate("document.fonts.ready")
            shrunk = page.evaluate(FIT_JS)
            f = out / f"card_{i:02d}.png"
            page.screenshot(path=str(f), clip={"x": 0, "y": 0, "width": W, "height": H})
            # 인스타그램 API는 JPEG만 받으므로 같은 그림을 .jpg로도 저장
            page.screenshot(path=str(f.with_suffix(".jpg")), type="jpeg", quality=95,
                            clip={"x": 0, "y": 0, "width": W, "height": H})
            tmp.unlink()
            files.append(f)
            print(f"🖼  {f.name}" + (f"  (글자 {shrunk}단계 줄임)" if shrunk else ""))
        browser.close()
    return files


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("사용법: python render.py 원고.json 결과폴더")
    render(sys.argv[1], sys.argv[2])
