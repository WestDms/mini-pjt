"""preferences.py - 장기 기억 Store(user_id)의 사용자 선호(어종·권역·형태) 읽기·지우기"""
from langchain.tools import ToolRuntime


def store_pref(runtime: ToolRuntime) -> tuple:
    """runtime 에서 Store 와 user_id 를 꺼낸다."""
    cfg = (runtime.config or {}).get("configurable", {}) if runtime else {}
    return getattr(runtime, "store", None), cfg.get("user_id")


async def load_pref(runtime: ToolRuntime) -> dict:
    """Store(user_id)에서 선호(어종·권역·형태)를 읽는다."""
    store, uid = store_pref(runtime)
    if store is None or not uid:
        return {}
    item = await store.aget(("users", uid), "preference")
    return dict(item.value) if item else {}


async def clear_pref(runtime: ToolRuntime) -> bool:
    """Store(user_id)에 저장된 선호를 지운다. 지울 대상이 있었으면 True."""
    store, uid = store_pref(runtime)
    if store is None or not uid:
        return False
    had = await store.aget(("users", uid), "preference") is not None
    await store.adelete(("users", uid), "preference")
    return had
