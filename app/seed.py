"""演示数据：模型目录 + 预设音色。幂等，可反复调用。"""

from __future__ import annotations

from sqlalchemy import select

from app.db import session_scope
from app.models import VoiceProfile
from app.providers.catalog import PRESET_VOICES
from app.providers.manager import manager


def seed_all() -> None:
    with session_scope() as db:
        manager.seed(db)
        existing = {v.name for v in db.scalars(select(VoiceProfile)).all()}
        for item in PRESET_VOICES:
            if item["name"] in existing:
                continue
            db.add(
                VoiceProfile(
                    name=item["name"],
                    kind="preset",
                    pitch=item["pitch"],
                    speed=item["speed"],
                    timbre=item["timbre"],
                    note=item["note"],
                )
            )


if __name__ == "__main__":
    from app.db import init_db

    init_db()
    seed_all()
    print("seed done")
