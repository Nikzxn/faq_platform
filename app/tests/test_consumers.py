from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase, override_settings

from DjangoProject.asgi import application
from app.models import Chat, Message


@override_settings(ROOT_URLCONF="app.urlconf_testing")
class TestChatConsumer(TransactionTestCase):
    def test_websocket_flow_saves_messages(self):
        chat = Chat.objects.create()

        async def scenario():
            communicator = WebsocketCommunicator(application, f"ws/chat/{chat.id}/")
            connected, _ = await communicator.connect()
            await communicator.send_json_to({"message": "Hello", "role": "user"})
            response = await communicator.receive_json_from()
            await communicator.disconnect()
            return connected, response

        connected, response = async_to_sync(scenario)()

        self.assertTrue(connected)
        self.assertEqual(response["message"], "Hello")
        self.assertEqual(response["role"], "user")
        self.assertEqual(Message.objects.filter(chat=chat).count(), 1)
