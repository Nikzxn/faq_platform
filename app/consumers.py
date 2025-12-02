import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.utils import timezone

from .models import Chat, Message


class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.chat_id = self.scope['url_route']['kwargs']['chat_id']
        self.room_group_name = f'chat_{self.chat_id}'

        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )

        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )

    async def receive(self, text_data):
        data = json.loads(text_data)
        message = data.get('message', '')
        role = data.get('role', 'user')

        await self.save_message(message, role)

        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'chat_message',
                'message': message,
                'role': role
            }
        )

    async def chat_message(self, event):
        message = event['message']
        role = event['role']

        await self.send(text_data=json.dumps({
            'message': message,
            'role': role
        }))

    @database_sync_to_async
    def save_message(self, content, role):
        chat = Chat.objects.get(id=self.chat_id)
        Message.objects.create(
            chat=chat,
            role=role,
            content=content,
            created_at=timezone.now()
        )
