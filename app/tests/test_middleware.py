from django.contrib.auth.models import Group, User
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings

from app.middleware import AccessMiddleware


@override_settings(ROOT_URLCONF="app.urlconf_testing")
class TestAccessMiddleware(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = AccessMiddleware(get_response=lambda request: HttpResponse("OK"))
        self.operators = Group.objects.create(name="Operators")
        self.operator = User.objects.create_user(username="operator", password="pwd")
        self.operator.groups.add(self.operators)
        self.regular = User.objects.create_user(username="regular", password="pwd")
        self.admin = User.objects.create_superuser(username="admin", password="pwd", email="admin@example.com")

    def test_operator_page_requires_operator_or_admin(self):
        request = self.factory.get("/operator/dashboard/")
        request.user = self.regular
        response = self.middleware(request)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/operator/", response.url)

        request = self.factory.get("/operator/dashboard/")
        request.user = self.operator
        response = self.middleware(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"OK")

    def test_admin_dashboard_requires_superuser(self):
        request = self.factory.get("/admin/dashboard/")
        request.user = self.operator
        response = self.middleware(request)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/admin/", response.url)

        request = self.factory.get("/admin/dashboard/")
        request.user = self.admin
        response = self.middleware(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"OK")
