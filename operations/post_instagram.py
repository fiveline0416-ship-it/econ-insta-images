"""
경제 1분 · 운영팀 — 인스타그램 게시
posts/<게시물>/ 폴더의 카드 이미지(JPEG)를 캐러셀로, reel.mp4 를 릴스로 게시한다.
텔레그램에서 운영자(OS)가 [승인]을 누른 뒤에만 telegram_bot.py 가 이 기능을 부른다.

폴더 구성 (디자인팀이 만듦):
  posts/2026-11-09_2026-28/
    post.json   {"title": "...", "caption": "...", "images": ["card_01.jpg", ...], "reel": "reel.mp4"}
    card_01.jpg ... card_06.jpg, reel.mp4
게시가 끝나면 같은 폴더에 published.json 을 남긴다 (중복 게시 방지).
캐러셀만 올라가고 릴스가 실패하면, 다음 승인 때 릴스만 다시 시도한다.

필요한 비밀값 (GitHub → Environments → instagram):  IG_ACCESS_TOKEN
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
VER = "v25.0"
API = f"https://graph.{'facebook' if FB_TOKEN else 'instagram'}.com/{VER}"
IG_ID = os.environ.get("IG_USER_ID", "").strip()
REPO = os.environ.get("GITHUB_REPOSITORY", "fiveline0416-ship-it/econ-insta-images")
SHA = os.environ.get("GITHUB_SHA", "main")
DRY = not TOKEN


def now():
    return datetime.now(KST).isoformat(timespec="seconds")


def raw_url(path: Path) -> str:
    rel = path.relative_to(ROOT).as_posix()
    # 그 파일을 올린 커밋 주소를 쓴다 (운영팀이 대기 중에 새로 받아온 게시물도 깃허브에 있는 주소로)
    import subprocess
    sha = subprocess.run(["git", "log", "-1", "--format=%H", "--", rel], cwd=ROOT,
                         capture_output=True, text=True).stdout.strip() or SHA
    return f"https://raw.githubusercontent.com/{REPO}/{sha}/{rel}"


def call(method, endpoint, _retry=3, **params):
    params["access_token"] = TOKEN
    r = requests.request(method, f"{API}/{endpoint}", data=params if method == "POST" else None,
                         params=params if method == "GET" else None, timeout=120)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"raw": r.text}
    if r.status_code >= 400 or "error" in body:
        err = body.get("error", {}) if isinstance(body.get("error"), dict) else {}
        msg = err.get("message", body)
        # 호출 한도 초과(code 4·17·32·613)는 잠깐 쉬었다가 다시 시도
        if _retry and (err.get("code") in (4, 17, 32, 613) or "limit" in str(msg).lower()):
            wait = 120 * (4 - _retry)
            print(f"   ⏸ 인스타 호출 한도 — {wait}초 쉬고 다시 시도 ({endpoint})")
            time.sleep(wait)
            params.pop("access_token", None)
            return call(method, endpoint, _retry=_retry - 1, **params)
        raise RuntimeError(f"인스타그램 API 오류 ({endpoint}): {msg}")
    return body


def wait_ready(container_id, label, tries=20, every=15):
    for _ in range(tries):
        time.sleep(every)   # 바로 묻지 않고 먼저 기다림 → 호출 수 절약
        st = call("GET", container_id, fields="status_code").get("status_code")
        if st == "FINISHED":
            return
        if st in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"{label} 준비 실패: {st}")
    raise RuntimeError(f"{label} 준비 시간 초과")


def resolve_ig_id():
    """IG_USER_ID 가 없으면 열쇠로 계정 번호를 확인한다."""
    global IG_ID
    if IG_ID or DRY:
        return IG_ID
    if FB_TOKEN:
        pages = call("GET", "me/accounts", fields="instagram_business_account{id,username}").get("data", [])
        igs = [p["instagram_business_account"] for p in pages if p.get("instagram_business_account")]
        if len(igs) != 1:
            raise RuntimeError("인스타 계정 번호를 자동으로 못 찾았어요. IG_USER_ID 값을 넣어주세요.")
        IG_ID = igs[0]["id"]
    else:
        me = call("GET", "me", fields="user_id,username")
        IG_ID = str(me.get("user_id") or me.get("id"))
    return IG_ID


def load_post(folder: Path):
    post = json.loads((folder / "post.json").read_text(encoding="utf-8"))
    images = [folder / n for n in post.get("images", [])]
    reel = folder / post["reel"] if post.get("reel") else None
    missing = [p.name for p in images + ([reel] if reel else []) if not p.exists()]
    if missing:
        raise RuntimeError(f"파일 없음: {missing}")
    if images and not 2 <= len(images) <= 10:
        raise RuntimeError(f"캐러셀은 2~10장이어야 함 (지금 {len(images)}장)")
    if any(p.suffix.lower() not in (".jpg", ".jpeg") for p in images):
        raise RuntimeError("인스타그램 API는 사진을 JPEG만 받음 (.jpg)")
    if reel and reel.suffix.lower() != ".mp4":
        raise RuntimeError("릴스는 .mp4 파일이어야 함")
    if len(post.get("caption", "")) > 2200:
        raise RuntimeError(f"캡션이 2,200자를 넘음 ({len(post['caption'])}자)")
    return post, images, reel


def publish_container(cid, caption, kind):
    """media_publish 를 부른다. 인스타가 오류를 내도 실제로는 올라간 경우가 있어서, 최근 게시물에서 한 번 더 확인한다."""
    try:
        media_id = call("POST", f"{IG_ID}/media_publish", creation_id=cid)["id"]
        return media_id, call("GET", media_id, fields="permalink").get("permalink")
    except Exception as e:
        print(f"   ⚠️ 게시 단계 오류, 실제로 올라갔는지 확인해요: {e}")
        time.sleep(20)
        found = find_recent(caption, kind)
        if found:
            print("   ✅ 확인해보니 이미 올라가 있어요")
            return found
        raise


def find_recent(caption, kind, minutes=30):
    """최근 게시물 중 같은 캡션·종류(CAROUSEL_ALBUM / VIDEO)가 있으면 (id, 주소)를 돌려준다."""
    data = call("GET", f"{IG_ID}/media", fields="id,caption,media_type,timestamp,permalink", limit=10).get("data", [])
    head = caption.strip()[:60]
    for m in data:
        ts = datetime.strptime(m["timestamp"], "%Y-%m-%dT%H:%M:%S%z")
        if (m.get("media_type") == kind and (m.get("caption") or "").strip()[:60] == head
                and datetime.now(timezone.utc) - ts < timedelta(minutes=minutes)):
            return m["id"], m.get("permalink")
    return None


def post_carousel(images, caption):
    children = []
    for i, p in enumerate(images, 1):   # 사진은 금방 준비되므로 장마다 기다리지 않음
        children.append(call("POST", f"{IG_ID}/media", image_url=raw_url(p), is_carousel_item="true")["id"])
        print(f"   ✅ {i}장 올림")
    carousel = call("POST", f"{IG_ID}/media", media_type="CAROUSEL",
                    children=",".join(children), caption=caption)["id"]
    wait_ready(carousel, "캐러셀", every=10)
    return publish_container(carousel, caption, "CAROUSEL_ALBUM")


def post_reel(video: Path, caption):
    if FB_TOKEN:
        # 영상 파일을 직접 올리는 방식 (주소로 받아가는 방식보다 안정적)
        c = call("POST", f"{IG_ID}/media", media_type="REELS", upload_type="resumable",
                 caption=caption, share_to_feed="true")
        data = video.read_bytes()
        r = requests.post(c.get("uri") or f"https://rupload.facebook.com/ig-api-upload/{VER}/{c['id']}",
                          headers={"Authorization": f"OAuth {TOKEN}", "offset": "0", "file_size": str(len(data))},
                          data=data, timeout=300)
        if r.status_code >= 400:
            raise RuntimeError(f"릴스 영상 업로드 실패: {r.text[:200]}")
        cid = c["id"]
    else:
        cid = call("POST", f"{IG_ID}/media", media_type="REELS", video_url=raw_url(video),
                   caption=caption, share_to_feed="true")["id"]
    print("   ⏳ 릴스 영상 처리 중 (몇 분 걸릴 수 있어요)")
    wait_ready(cid, "릴스", tries=40, every=15)   # 최대 약 10분
    return publish_container(cid, caption, "VIDEO")


def publish(folder: Path):
    """캐러셀 → 릴스 순서로 게시하고 결과(dict)를 돌려준다. 이미 올라간 것은 건너뛴다."""
    post, images, reel = load_post(folder)
    caption = post.get("caption", "")
    done_file = folder / "published.json"
    done = json.loads(done_file.read_text(encoding="utf-8")) if done_file.exists() else {}

    print(f"📮 게시물: {post.get('title', folder.name)}  (카드 {len(images)}장{', 릴스 1개' if reel else ''})")
    if DRY:
        print("🧪 시험 모드: IG_ACCESS_TOKEN 비밀값이 없어서 실제로 올리지 않았어요.")
        return {"dry_run": True}
    resolve_ig_id()

    def save():
        done_file.write_text(json.dumps(done, ensure_ascii=False, indent=2), encoding="utf-8")

    if images and not done.get("permalink"):
        mid, link = post_carousel(images, caption)
        done.update(media_id=mid, permalink=link, published_at=now())
        save()
        print(f"🎉 카드뉴스 게시 완료: {link}")
    if reel and not done.get("reel_permalink"):
        try:
            mid, link = post_reel(reel, caption)
            done.update(reel_media_id=mid, reel_permalink=link, reel_published_at=now())
            done.pop("reel_error", None)
            print(f"🎉 릴스 게시 완료: {link}")
        except Exception as e:
            done["reel_error"] = str(e)[:300]
            print(f"⚠️ 릴스 실패: {e}")
        save()
    return done


def pending_posts(only=None):
    if not POSTS.exists():
        return []
    dirs = [POSTS / only] if only else sorted(p for p in POSTS.iterdir() if p.is_dir())
    return [d for d in dirs if (d / "post.json").exists() and not (d / "published.json").exists()]


if __name__ == "__main__":
    # 수동 점검용: python post_instagram.py <폴더이름>  (시험 모드에서 내용만 확인)
    try:
        for f in pending_posts(sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else None)[:1]:
            publish(f)
    except Exception as e:
        print("❌", e)
        sys.exit(1)
