import uuid

from django.contrib.auth.models import User
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


class Message(models.Model):
    ROLE_CHOICES = [("user", "User"), ("assistant", "Assistant")]

    chat = models.ForeignKey(Chat, related_name="messages", on_delete=models.CASCADE)
    role = models.CharField(max_length=9, choices=ROLE_CHOICES)
    content = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)
    response_time = models.FloatField(null=True, blank=True, help_text="Время ответа в секундах")

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.role}: {self.content[:40]}"


class Operator(User):
    class Meta:
        proxy = True
        verbose_name = "Operator"
        verbose_name_plural = "Operators"


class SiteAdministrator(User):
    class Meta:
        proxy = True
        verbose_name = "Administrator"
        verbose_name_plural = "Administrators"
