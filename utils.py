from services.hh_api import hh_request_with_retry

# Поиск сообщений
def extract_cover_letter(item: dict, headers: dict) -> str:
    messages_url = item.get("messages_url")
    if messages_url:
        try:
            r = hh_request_with_retry("GET", messages_url, headers=headers, timeout=10)
            if r.status_code == 200:
                data = r.json()
                msgs = data.get("items", [])
                # Берём первый непустой текст
                for msg in msgs:
                    text = msg.get("text", "").strip()
                    if text:
                        return text
        except Exception as e:
            print(f"[У пользователя нет сообщений] {e}")

    return "Не предоставлено"