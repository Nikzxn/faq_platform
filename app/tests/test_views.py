import json
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.http import JsonResponse
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from qdrant_client.models import Distance, PointStruct, VectorParams

from app.models import Chat, Message
from assistant import Assistant


class FakeAssistant:
    async def __call__(self, message, max_related=5):
        return Assistant.Response(answer=f"echo:{message}", related_questions=["rel1", "rel2"][:max_related])

    async def answers(self, message):
        return [f"suggest:{message.lower()}"]

    async def get_embedding(self, message):
        return [0.1, 0.2, 0.3]


@override_settings(ROOT_URLCONF="app.urlconf_testing")
class TestViews(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.qdrant_ready = True
        try:
            settings.QDRANT.recreate_collection(
                collection_name=settings.COLLECTION,
                vectors_config=VectorParams(size=3, distance=Distance.COSINE),
            )
        except Exception as exc:
            cls.qdrant_ready = False
            cls.qdrant_error = exc

    def setUp(self):
        self.client = Client()
        def _render_stub(request, template_name, context=None):
            data = context or {}
            return JsonResponse(json.loads(json.dumps(data, default=str)))

        self.render_patch = patch("app.views.render", _render_stub)
        self.render_patch.start()
        self.addCleanup(self.render_patch.stop)

        self.assistant_patch = patch("app.views.Assistant", FakeAssistant)
        self.assistant_patch.start()
        self.addCleanup(self.assistant_patch.stop)

        self.operators = Group.objects.create(name="Operators")
        self.operator = User.objects.create_user(username="operator", password="pwd")
        self.operator.groups.add(self.operators)
        self.admin = User.objects.create_superuser(username="admin", password="pwd", email="admin@example.com")

    def _ensure_qdrant_ready(self):
        if not self.qdrant_ready:
            self.skipTest(f"Qdrant unavailable: {getattr(self, 'qdrant_error', '')}")

    def test_chat_view_creates_chat_and_returns_reply(self):
        payload = {"message": "Привет", "chat_id": str(uuid.uuid4())}
        response = self.client.post("/", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("reply", data)
        self.assertFalse(data["operator_mode"])
        self.assertEqual(Chat.objects.count(), 1)
        self.assertEqual(Message.objects.count(), 2)

    def test_chat_view_switches_to_operator(self):
        chat_id = str(uuid.uuid4())
        payload = {"message": "Позови оператора", "chat_id": chat_id}
        response = self.client.post("/", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        chat = Chat.objects.get(id=chat_id)
        self.assertTrue(data["operator_mode"])
        self.assertFalse(chat.bot_active)

    def test_chat_view_respects_operator_mode(self):
        chat = Chat.objects.create(bot_active=False)
        payload = {"message": "Еще вопрос", "chat_id": str(chat.id)}
        response = self.client.post("/", data=json.dumps(payload), content_type="application/json")
        data = response.json()
        self.assertTrue(data["operator_mode"])
        self.assertEqual(data["reply"], "Ожидайте ответа оператора...")

    def test_chat_history_view_handles_open_and_closed_chat(self):
        chat = Chat.objects.create()
        Message.objects.create(chat=chat, role="user", content="hi")

        open_resp = self.client.get(f"/history/{chat.id}/")
        self.assertEqual(open_resp.status_code, 200)
        self.assertEqual(len(open_resp.json()["messages"]), 1)

        chat.is_closed = True
        chat.save()
        closed_resp = self.client.get(f"/history/{chat.id}/")
        self.assertEqual(closed_resp.status_code, 404)

    def test_suggested_responses_default_and_custom(self):
        chat = Chat.objects.create()
        no_messages = self.client.get(f"/suggestions/{chat.id}/")
        self.assertEqual(no_messages.status_code, 200)
        self.assertGreaterEqual(len(no_messages.json()["suggestions"]), 1)

        Message.objects.create(chat=chat, role="user", content="Вопрос")
        with_answers = self.client.get(f"/suggestions/{chat.id}/")
        self.assertEqual(with_answers.json()["suggestions"], ["suggest:вопрос"])

    def test_operator_view_lists_active_chats(self):
        chat = Chat.objects.create(bot_active=False, is_closed=False)
        Message.objects.create(chat=chat, role="assistant", content="hi")
        self.client.force_login(self.operator)
        response = self.client.get("/operator/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json().get("chat_data", [])), 1)

    def test_close_chat_view_marks_chat_closed(self):
        chat = Chat.objects.create(bot_active=False, is_closed=False)
        self.client.force_login(self.operator)
        response = self.client.post(f"/operator/close/{chat.id}/")
        self.assertEqual(response.status_code, 200)
        chat.refresh_from_db()
        self.assertTrue(chat.is_closed)

    def test_admin_stats_api_returns_period_data(self):
        now = timezone.now()
        for days_ago in range(3):
            created = now - timedelta(days=days_ago)
            Chat.objects.create(created_at=created, is_closed=days_ago == 1, closed_at=created if days_ago == 1 else None)

        self.client.force_login(self.admin)
        response = self.client.get("/admin/dashboard/stats/?period=3")
        data = response.json()
        self.assertEqual(len(data["labels"]), 3)
        self.assertIn(1, data["new_chats"])

    def test_admin_dashboard_view_returns_stats(self):
        chat = Chat.objects.create(is_closed=False)
        Message.objects.create(chat=chat, role="assistant", content="hi", response_time=0.5)
        self.client.force_login(self.admin)
        response = self.client.get("/admin/dashboard/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("total_chats", payload.get("stats", {}))

    def test_admin_generate_pdf_returns_file(self):
        self.client.force_login(self.admin)
        with patch("app.views.AdminGeneratePDFView.render_to_pdf", return_value=b"pdf-bytes"):
            response = self.client.get("/admin/report/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")

    def test_admin_staff_user_get_put_delete(self):
        target = User.objects.create_user(username="target", password="pwd")
        self.client.force_login(self.admin)

        detail = self.client.get(f"/admin/staff/{target.id}/")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["username"], "target")

        update_payload = {
            "username": "updated",
            "active": False,
            "password": "newpwd",
            "user_type": "operator",
        }
        update = self.client.put(
            f"/admin/staff/{target.id}/",
            data=json.dumps(update_payload),
            content_type="application/json",
        )
        self.assertEqual(update.status_code, 200)
        target.refresh_from_db()
        self.assertEqual(target.username, "updated")
        self.assertFalse(target.is_active)
        self.assertTrue(target.groups.filter(name="Operators").exists())

        delete = self.client.delete(f"/admin/staff/{target.id}/")
        self.assertEqual(delete.status_code, 200)
        self.assertFalse(User.objects.filter(id=target.id).exists())

    def test_admin_staff_list_get_and_post(self):
        self.client.force_login(self.admin)
        response = self.client.get("/admin/staff/list/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("admins", response.json())

        payload = {"user_type": "operator", "username": "new-op", "password": "pwd"}
        created = self.client.post(
            "/admin/staff/list/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 200)
        self.assertTrue(User.objects.filter(username="new-op").exists())

    def test_admin_knowledge_endpoints(self):
        self._ensure_qdrant_ready()
        self.client.force_login(self.admin)
        settings.QDRANT.upsert(
            collection_name=settings.COLLECTION,
            points=[
                PointStruct(
                    id=1,
                    vector=[0.1, 0.2, 0.3],
                    payload={"question": "q1 / q1b", "answer": "a1", "related_questions": ["r1"]},
                )
            ],
        )

        list_resp = self.client.get("/admin/knowledge/list/?page=1")
        self.assertEqual(list_resp.status_code, 200)
        self.assertGreaterEqual(list_resp.json()["pagination"]["total_items"], 1)

        create_payload = {"question": ["q2"], "answer": "a2", "related_questions": ["r2"]}
        created = self.client.post(
            "/admin/knowledge/list/",
            data=json.dumps(create_payload),
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 200)

        item_resp = self.client.get("/admin/knowledge/1/")
        self.assertEqual(item_resp.status_code, 200)
        self.assertEqual(item_resp.json()["id"], 1)

        update_payload = {"question": ["q1", "q1b"], "answer": "updated", "related_questions": ["r1", "r2"]}
        updated = self.client.put(
            "/admin/knowledge/1/",
            data=json.dumps(update_payload),
            content_type="application/json",
        )
        self.assertEqual(updated.status_code, 200)

        deleted = self.client.delete("/admin/knowledge/1/")
        self.assertEqual(deleted.status_code, 200)

    def test_admin_and_operator_pages_render(self):
        self.client.force_login(self.admin)
        knowledge_page = self.client.get("/admin/knowledge/")
        self.assertEqual(knowledge_page.status_code, 200)

        self.client.force_login(self.operator)
        operator_page = self.client.get("/operator/")
        self.assertEqual(operator_page.status_code, 200)

    def test_custom_logout_redirects_to_login(self):
        self.client.force_login(self.operator)
        response = self.client.get("/logout/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)
