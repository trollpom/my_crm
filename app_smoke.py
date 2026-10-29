from app import db
from app.main import app

db.init_db()

try:
    from starlette.testclient import TestClient
except Exception as e:
    print("TestClient недоступен:", e)
    raise SystemExit(1)

c = TestClient(app)
for path in ["/", "/api/tasks", "/api/projects", "/api/stats", "/api/gantt", "/api/notes"]:
    try:
        r = c.get(path)
        body = r.json() if path != "/" else r.text[:40]
        if isinstance(body, list):
            body = f"list[{len(body)}]"
        elif path != "/":
            body = str(body)[:120]
        print(f"GET {path:16} -> {r.status_code}  {body}")
    except Exception as e:
        print(f"GET {path:16} -> ERROR {e!r}")
