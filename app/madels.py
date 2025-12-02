import uuid

from django.db import models
from django.utils import timezone


class Chat(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    bot_active = models.BooleanField(default=True, help_text="True – отвечает бот; False – оператор")
    is_closed = models.BooleanField(default=False, help_text="True – чат закрыт; False – чат открыт")
    created_at = models.DateTimeField(default=timezone.now, help_text="Время создания чата")
    closed_at = models.DateTimeField(null=True, blank=True, help_text="Время закрытия чата")

    def __str__(self) -> str:
        return str(self.id)
