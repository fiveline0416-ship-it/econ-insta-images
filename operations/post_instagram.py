"""
경제 1분 · 운영팀
posts/<게시물>/ 폴더에 있는 카드 이미지(JPEG)와 캡션을 인스타그램 캐러셀로 게시한다.
깃허브 Actions에서 '사장 승인' 뒤에만 실행된다.

폴더 구성 (디자인팀이 만듦):
  posts/2026-11-09_2026-28/
    post.json      {"title": "...", "caption": "...", "images": ["card_01.jpg", ...]}
    card_01.jpg ... card_06.jpg
게시가 끝나면 같은 폴더에 published.json 을 남긴다 (중복 게시 방지).

필요한 비밀값 (GitHub → Environments → instagram 의 Secrets):  IG_ACCESS_TOKEN  (IG_USER_ID는 선택)
비밀값이 없으면 '시험 모드'로 돌아서, 실제로 올리지 않고 무엇을 올릴지만 보여준다.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
POSTS = ROOT / "posts"
KST = timezone(timedelta(hours=9))
TOKEN = os.environ.get("IG_ACCESS_TOKEN", "").strip()
# 열쇠 종류에 따라 주소가 다름: 인스타 로그인 열쇠(IG…) → graph.instagram.com,
# 페이스북/통합 로그인 열쇠(EAA…) → graph.facebook.com
FB_TOKEN = TOKEN.startswith("EAA")
API = f"https://graph.{'facebook' if FB_TOKEN else 'instagram'}.com/v25.0"
IG_ID = os.environ.get("IG_USER_ID", "").strip()
REPO = os.environ.get("GITHUB_REPOSITORY", "fiveline0416-ship-it/econ-insta-images")
SHA = os.environ.get("GITHUB_SHA", "main")
DRY = not TOKEN


def raw_url(path: Path) -> str:
    rel = path.relative_to(ROOT).as_posix()
    return f"https://raw.githubusercontent.com/{REPO}/{SHA}/{rel}"


def call(method, endpoint, **params):
    params["access_token"] = TOKEN
    r = requests.request(method, f"{API}/{endpoint}", data=params if method == "POST" else None,
                         params=params if method == "GET" else None, timeout=60)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"raw": r.text}
    if r.status_code >= 400 or "error" in body:
        msg = body.get("error", {}).get("message", body)
        raise RuntimeError(f"인스타그램 API 오류 ({endpoint}): {msg}")
    return body


def wait_ready(container_id, label):
    for _ in range(20):  # 최대 약 5분
        st = call("GET", container_id, fields="status_code").get("status_code")
        if st == "FINISHED":
            return
        if st in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"{label} 준비 실패: {st}")
        time.sleep(15)
    raise RuntimeError(f"{label} 준비 시간 초과")


def pending_posts(only=None):
    if not POSTS.exists():
        return []
    dirs = [POSTS / only] if only else sorted(p for p in POSTS.iterdir() if p.is_dir())
    return [d for d in dirs if (d / "post.json").exists() and not (d / "published.json").exists()]


def publish(folder: Path):
    post = json.loads((folder / "post.json").read_text(encoding="utf-8"))
    images = [folder / name for name in post["images"]]
    missing = [p.name for p in images if not p.exists()]
    if missing:
        raise RuntimeError(f"이미지 파일 없음: {missing}")
    if not 2 <= len(images) <= 10:
        raise RuntimeError(f"캐러셀은 2~10장이어야 함 (지금 {len(images)}장)")
    if any(p.suffix.lower() not in (".jpg", ".jpeg") for p in images):
        raise RuntimeError("인스타그램 API는 JPEG만 받음 (.jpg)")
    caption = post.get("caption", "")
    if len(caption) > 2200:
        raise RuntimeError(f"캡션이 2,200자를 넘음 ({len(caption)}자)")

    print(f"📮 게시물: {post.get('title', folder.name)}  ({len(images)}장)")
    for p in images:
        print("   -", raw_url(p))
    print("   캡션 앞부분:", caption[:80].replace("\n", " "), "…")

    if DRY:
        print("🧪 시험 모드: IG_ACCESS_TOKEN 비밀값이 없어서 실제로 올리지 않았어요.")
        return None

    children = []
    for i, p in enumerate(images, 1):
        cid = call("POST", f"{IG_ID}/media", image_url=raw_url(p), is_carousel_item="true")["id"]
        wait_ready(cid, f"{i}장")
        children.append(cid)
        print(f"   ✅ {i}장 준비 완료")
    carousel = call("POST", f"{IG_ID}/media", media_type="CAROUSEL",
                    children=",".join(children), caption=caption)["id"]
    wait_ready(carousel, "캐러셀")
    media_id = call("POST", f"{IG_ID}/media_publish", creation_id=carousel)["id"]
    info = call("GET", media_id, fields="permalink,timestamp")
    result = {"media_id": media_id, "permalink": info.get("permalink"),
              "published_at": datetime.now(KST).isoformat(timespec="seconds")}
    (folder / "published.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"🎉 게시 완료: {result['permalink']}")
    return result


def main():
    global IG_ID
    if TOKEN and not IG_ID:
        # IG_USER_ID 비밀값이 없으면 토큰으로 계정 번호를 직접 확인
        if FB_TOKEN:
            pages = call("GET", "me/accounts", fields="instagram_business_account{id,username}").get("data", [])
            igs = [p["instagram_business_account"] for p in pages if p.get("instagram_business_account")]
            if len(igs) != 1:
                raise RuntimeError("인스타 계정 번호를 자동으로 못 찾았어요. instagram 환경에 IG_USER_ID 비밀값을 넣어주세요.")
            IG_ID, name = igs[0]["id"], igs[0].get("username")
        else:
            me = call("GET", "me", fields="user_id,username")
            IG_ID, name = str(me.get("user_id") or me.get("id")), me.get("username")
        print(f"👤 게시 계정: @{name} (ID {IG_ID})")
    only = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else None
    todo = pending_posts(only)
    if not todo:
        print("올릴 게시물이 없어요.")
        return
    # 한 번 실행에 1건만 올린다 (실수로 여러 건이 한꺼번에 올라가는 것 방지)
    folder = todo[0]
    if len(todo) > 1:
        print(f"ℹ️ 대기 중인 게시물 {len(todo)}건 중 가장 오래된 1건만 올려요: {folder.name}")
    publish(folder)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("❌", e)
        sys.exit(1)
