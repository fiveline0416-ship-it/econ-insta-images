"""운영팀 점검용: 최근 인스타 게시물과 API 호출 한도 사용량을 보여준다 (아무것도 올리지 않음)."""
import json, os, requests
TOKEN = os.environ["IG_ACCESS_TOKEN"].strip()
IG = os.environ.get("IG_USER_ID") or "17841426810097346"
API = "https://graph.facebook.com/v25.0"
r = requests.get(f"{API}/{IG}/media", params={"fields": "caption,timestamp,permalink,media_type",
                                               "limit": 8, "access_token": TOKEN}, timeout=60)
for k, v in r.headers.items():
    if "usage" in k.lower():
        print(f"📈 {k}: {v}")
body = r.json()
if "error" in body:
    print("❌", body["error"])
for m in body.get("data", []):
    print(f"🗂 {m['timestamp']} {m['media_type']:<15} {m['permalink']}  | {(m.get('caption') or '')[:40]!r}")
q = requests.get(f"{API}/{IG}/content_publishing_limit", params={"fields": "config,quota_usage", "access_token": TOKEN}, timeout=60).json()
print("📮 게시 한도:", json.dumps(q, ensure_ascii=False))
