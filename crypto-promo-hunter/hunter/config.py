import os
from dataclasses import dataclass

@dataclass(frozen=True)
class Settings:
    database_url: str
    bot_token: str
    admin_ids: frozenset[int]
    demo: bool
    poll_seconds: int
    auto_publish: bool
    reminder_fresh_hours: int

    @classmethod
    def load(cls):
        demo = os.getenv('DEMO_MODE', 'false').lower() == 'true'
        return cls(
            os.getenv('DATABASE_URL', 'postgresql+asyncpg://hunter:hunter@db:5432/hunter'),
            os.getenv('BOT_TOKEN', ''),
            frozenset(int(x.strip()) for x in os.getenv('ADMIN_IDS', '').split(',') if x.strip()),
            demo, max(60, int(os.getenv('POLL_INTERVAL_SECONDS', '900'))),
            os.getenv('AUTO_PUBLISH_VERIFIED', 'false').lower() == 'true',
            max(1, int(os.getenv('REMINDER_FRESH_HOURS', '24'))),
        )

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids
