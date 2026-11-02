from services.hh_api import hh_request_with_retry

def load_items_from_collections(selected_collections, headers, progress_cb=None, status_cb=None):
    all_items = []
    seen_ids = set()

    collections_total = len(selected_collections)
    collections_done = 0

    for collection in selected_collections:
        url = collection.get("url")
        if not url:
            continue

        page = 0
        while True:
            r = hh_request_with_retry(
                "GET",
                url,
                headers=headers,
                params={"per_page": 50, "page": page}
            )

            if not r:
                raise RuntimeError("Ошибка HH API")

            data = r.json()
            batch = data.get("items", [])
            if not batch:
                break

            for item in batch:
                nid = item.get("id")
                if nid not in seen_ids:
                    seen_ids.add(nid)
                    all_items.append(item)

            page += 1

            # callbacks для UI (если переданы)
            if status_cb:
                status_cb(collections_done + 1, collections_total, page, len(all_items))

            if progress_cb:
                inner_progress = min(page / 10, 0.9)
                progress_cb((collections_done + inner_progress) / collections_total)

        collections_done += 1
        if progress_cb:
            progress_cb(collections_done / collections_total)

    return all_items