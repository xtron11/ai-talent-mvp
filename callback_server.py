from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
import json, time, urllib.parse

app = FastAPI()

STREAMLIT_URL = "http://localhost:8501"

@app.get("/callback")
async def callback(request: Request):
    params = dict(request.query_params)

    # сохраняем как раньше
    with open("hh_code.json", "w", encoding="utf-8") as f:
        json.dump({"ts": time.time(), **params}, f, ensure_ascii=False, indent=2)

    # ПЕРЕДАЕМ code В STREAMLIT
    query = urllib.parse.urlencode(params)
    redirect_url = f"{STREAMLIT_URL}?{query}"
    print("REDIRECT TO:", redirect_url)
    return HTMLResponse(f"""
        <html>
        <body>
            <h3>Вход выполнен. Возвращаю в приложение...</h3>
            <script>
                window.location.href = "{redirect_url}";
            </script>
        </body>
        </html>
    """)