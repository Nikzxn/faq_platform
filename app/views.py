import asyncio
import io
import json
import os
import uuid
from datetime import datetime, timedelta

from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.models import Group, User
from django.contrib.auth.views import LoginView
from django.db.models import Avg
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import get_template
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView
from qdrant_client.http.models import PointIdsList
from qdrant_client.models import PointStruct
from xhtml2pdf import pisa

from app.models import Chat, Message
from assistant import Assistant


class CustomLogoutView(View):
    async def get(self, request, *args, **kwargs):
        await sync_to_async(logout)(request)
        return redirect('login')


class ChoiceView(TemplateView):
    template_name = 'auth/choice.html'


class BaseLoginView(LoginView):
    def form_valid(self, form):
        username = form.cleaned_data.get('username')
        password = form.cleaned_data.get('password')
        user = authenticate(self.request, username=username, password=password)

        if user is not None:
            if self.is_valid_user_type(user):
                login(self.request, user)
                return redirect(self.get_success_url())
            else:
                form.add_error(None, "У вас нет доступа к этой панели. Используйте соответствующую форму входа.")
                return self.form_invalid(form)
        return super().form_valid(form)

    def is_valid_user_type(self, user):
        return True


class OperatorLoginView(BaseLoginView):
    template_name = 'auth/login_operator.html'
    redirect_authenticated_user = True

    def get_success_url(self):
        next_url = self.request.GET.get('next')
        if next_url:
            return next_url
        return settings.OPERATOR_REDIRECT_URL

    def is_valid_user_type(self, user):
        is_operator = user.groups.filter(name='Operators').exists()
        return is_operator or user.is_superuser


class AdminLoginView(BaseLoginView):
    template_name = 'auth/login_admin.html'
    redirect_authenticated_user = True

    def get_success_url(self):
        next_url = self.request.GET.get('next')
        if next_url:
            return next_url
        return settings.ADMIN_REDIRECT_URL

    def is_valid_user_type(self, user):
        return user.is_superuser


class SuggestedResponsesView(View):
    async def get(self, request, chat_id, *args, **kwargs):
        try:
            chat = await database_sync_to_async(get_object_or_404)(Chat, id=chat_id)

            messages = await database_sync_to_async(
                lambda: list(chat.messages.filter(role="user").order_by("-created_at")))()

            if not messages:
                return JsonResponse({
                    'suggestions': [
                        "Добрый день! Благодарим за обращение в службу поддержки Ростелеком. Чем я могу вам помочь?",
                        "Для уточнения деталей по вашему вопросу, пожалуйста, укажите номер лицевого счета или номер телефона.",
                        "Понял вашу проблему. Давайте рассмотрим возможные решения.",
                        "Спасибо за ваше терпение.",
                        "Мне необходимо уточнить некоторые детали для решения вашего вопроса. Подскажите, пожалуйста..."
                    ]
                })

            last_message = messages[0]
            message_text = last_message.content.lower()

            suggestions = await Assistant().answers(message_text)

            return JsonResponse({'suggestions': suggestions})

        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)


class ChatHistoryView(View):
    async def get(self, request, chat_id, *args, **kwargs):
        try:
            chat = await database_sync_to_async(get_object_or_404)(Chat, id=chat_id)
            messages = await database_sync_to_async(lambda: list(chat.messages.all()))()

            if chat.is_closed:
                return JsonResponse({'error': 'Чат закрыт'}, status=404)

            return JsonResponse({
                'messages': [
                    {
                        'role': msg.role,
                        'content': msg.content,
                        'created_at': msg.created_at.isoformat()
                    }
                    for msg in messages
                ],
                'bot_active': chat.bot_active
            })
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=404)


class ChatView(View):
    template_name = "chat.html"

    async def get(self, request, *args, **kwargs):
        return render(request, self.template_name)

    async def post(self, request, *args, **kwargs):
        try:
            data = json.loads(request.body or b"{}")
            user_msg = data.get("message", "").strip()
            chat_id = data.get("chat_id")

            if not chat_id:
                return HttpResponseBadRequest("Chat ID is required")

            try:
                chat = await database_sync_to_async(Chat.objects.get)(id=chat_id)
            except Chat.DoesNotExist:
                chat = await database_sync_to_async(Chat.objects.create)(id=uuid.UUID(chat_id))

            await database_sync_to_async(Message.objects.create)(
                chat=chat,
                role="user",
                content=user_msg
            )

            user_msg_time = timezone.now()

            response: Assistant.Response = await Assistant()(user_msg)
            switch_to_operator = "оператор" in user_msg.lower() or "оператор" in response.answer.lower()

            if switch_to_operator and chat.bot_active:
                chat.bot_active = False
                await database_sync_to_async(chat.save)()

                reply = "Перевожу вас на оператора. Пожалуйста, ожидайте..."
                suggestions = []

                response_time = (timezone.now() - user_msg_time).total_seconds()

                await database_sync_to_async(Message.objects.create)(
                    chat=chat,
                    role="assistant",
                    content=reply,
                    response_time=response_time,
                )

                return JsonResponse(
                    {
                        "reply": reply,
                        "suggestions": suggestions,
                        "operator_mode": True
                    },
                    json_dumps_params={"ensure_ascii": False},
                )

            if not chat.bot_active:
                return JsonResponse(
                    {
                        "reply": "Ожидайте ответа оператора...",
                        "suggestions": [],
                        "operator_mode": True
                    },
                    json_dumps_params={"ensure_ascii": False},
                )

            reply = response.answer
            suggestions = response.related_questions

            response_time = (timezone.now() - user_msg_time).total_seconds()

            await database_sync_to_async(Message.objects.create)(
                chat=chat,
                role="assistant",
                content=reply,
                response_time=response_time,
            )

            return JsonResponse(
                {
                    "reply": reply,
                    "suggestions": suggestions,
                    "operator_mode": False
                },
                json_dumps_params={"ensure_ascii": False},
            )
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=400)


class OperatorView(LoginRequiredMixin, UserPassesTestMixin, View):
    template_name = "operator.html"
    login_url = settings.OPERATOR_LOGIN_URL

    async def dispatch(self, request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            return redirect(self.get_login_url())

        has_permission = await sync_to_async(self.test_func)()
        if not has_permission:
            return self.handle_no_permission()

        handler = getattr(self, request.method.lower(), self.http_method_not_allowed)
        return await handler(request, *args, **kwargs)

    def test_func(self):
        is_operator = self.request.user.groups.filter(name='Operators').exists()
        return is_operator or self.request.user.is_superuser

    async def get(self, request, *args, **kwargs):
        chats = await database_sync_to_async(list)(Chat.objects.filter(bot_active=False, is_closed=False))

        chat_data = []
        for chat in chats:
            count = await database_sync_to_async(lambda c=chat: c.messages.count())()
            chat_data.append({
                'chat': chat,
                'message_count': count
            })

        return render(request, self.template_name, {'chat_data': chat_data})


class CloseChatView(LoginRequiredMixin, UserPassesTestMixin, View):
    login_url = settings.OPERATOR_LOGIN_URL

    async def dispatch(self, request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            return redirect(self.get_login_url())

        has_permission = await sync_to_async(self.test_func)()
        if not has_permission:
            return self.handle_no_permission()

        handler = getattr(self, request.method.lower(), self.http_method_not_allowed)
        return await handler(request, *args, **kwargs)

    def test_func(self):
        is_operator = self.request.user.groups.filter(name='Operators').exists()
        return is_operator or self.request.user.is_superuser

    async def post(self, request, chat_id, *args, **kwargs):
        try:
            chat = await database_sync_to_async(get_object_or_404)(Chat, id=chat_id)
            chat.is_closed = True
            chat.closed_at = timezone.now()
            await database_sync_to_async(chat.save)()

            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)


class AdminStatsAPIView(LoginRequiredMixin, UserPassesTestMixin, View):
    login_url = settings.ADMIN_LOGIN_URL

    def test_func(self):
        return self.request.user.is_superuser

    def get(self, request, *args, **kwargs):
        period = int(request.GET.get('period', 7))

        period = max(1, min(period, 30))

        end_date = timezone.now().date()
        start_date = end_date - timedelta(days=period - 1)

        labels = []
        new_chats = []
        closed_chats = []

        current_date = start_date
        while current_date <= end_date:
            labels.append(current_date.strftime('%d.%m'))

            day_start = timezone.make_aware(timezone.datetime.combine(current_date, timezone.datetime.min.time()))
            day_end = timezone.make_aware(timezone.datetime.combine(current_date, timezone.datetime.max.time()))

            new_chat_count = Chat.objects.filter(created_at__gte=day_start, created_at__lte=day_end).count()
            new_chats.append(new_chat_count)

            closed_chat_count = Chat.objects.filter(
                closed_at__gte=day_start,
                closed_at__lte=day_end
            ).count()
            closed_chats.append(closed_chat_count)

            current_date += timedelta(days=1)

        return JsonResponse({
            'labels': labels,
            'new_chats': new_chats,
            'closed_chats': closed_chats
        })


class AdminDashboardView(LoginRequiredMixin, UserPassesTestMixin, View):
    template_name = "admin/dashboard.html"
    login_url = settings.ADMIN_LOGIN_URL

    async def dispatch(self, request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            return redirect(self.get_login_url())

        has_permission = await sync_to_async(self.test_func)()
        if not has_permission:
            return self.handle_no_permission()

        handler = getattr(self, request.method.lower(), self.http_method_not_allowed)
        return await handler(request, *args, **kwargs)

    def test_func(self):
        return self.request.user.is_superuser

    def handle_no_permission(self):
        return HttpResponseForbidden("У вас нет прав для доступа к этой странице")

    @database_sync_to_async
    def get_chat_stats(self):
        total_chats = Chat.objects.count()
        active_chats = Chat.objects.filter(is_closed=False).count()
        closed_chats = Chat.objects.filter(is_closed=True).count()

        avg_operator_time = Message.objects.filter(
            role='assistant',
            response_time__isnull=False
        ).aggregate(avg_time=Avg('response_time'))['avg_time'] or 0

        avg_operator_response_time = f"{avg_operator_time:.3f}s"

        end_date = timezone.now().date()
        start_date = end_date - timedelta(days=6)

        daily_stats = []

        current_date = start_date
        while current_date <= end_date:
            day_start = timezone.make_aware(timezone.datetime.combine(current_date, timezone.datetime.min.time()))
            day_end = timezone.make_aware(timezone.datetime.combine(current_date, timezone.datetime.max.time()))

            new_chats = Chat.objects.filter(created_at__gte=day_start, created_at__lte=day_end).count()

            closed_chats_count = Chat.objects.filter(
                closed_at__gte=day_start,
                closed_at__lte=day_end
            ).count()

            daily_stats.append({
                'date': current_date.strftime('%d.%m'),
                'new_chats': new_chats,
                'closed_chats': closed_chats_count
            })

            current_date += timedelta(days=1)
        return {
            'total_chats': total_chats,
            'active_chats': active_chats,
            'closed_chats': closed_chats,
            'avg_response_time': avg_operator_response_time,
            'daily_stats': daily_stats
        }

    async def get(self, request, *args, **kwargs):
        stats = await self.get_chat_stats()
        return render(request, self.template_name, {'stats': stats})


class AdminGeneratePDFView(LoginRequiredMixin, UserPassesTestMixin, View):

    async def dispatch(self, request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            return redirect(settings.ADMIN_LOGIN_URL)

        has_permission = await sync_to_async(self.test_func)()
        if not has_permission:
            return HttpResponseForbidden("У вас нет прав для доступа к этой странице")

        handler = getattr(self, request.method.lower(), self.http_method_not_allowed)
        return await handler(request, *args, **kwargs)

    def test_func(self):
        return self.request.user.is_superuser

    def render_to_pdf(self, template_src, context_dict={}):
        template = get_template(template_src)
        html = template.render(context_dict)
        result = io.BytesIO()

        pdf = pisa.pisaDocument(
            io.BytesIO(html.encode("UTF-8")),
            result,
            encoding='utf-8',
            link_callback=lambda uri, rel: os.path.join(settings.BASE_DIR, uri.replace(settings.STATIC_URL, 'static/'))
        )

        if not pdf.err:
            return result.getvalue()
        return None

    def create_pdf(self, stats):
        trend = 'рост' if stats['daily_stats'][-1]['new_chats'] > stats['daily_stats'][0]['new_chats'] else 'снижение'

        context = {
            'stats': stats,
            'trend': trend,
            'current_date': datetime.now().strftime("%d.%m.%Y"),
            'static_url': settings.STATIC_URL,
        }

        pdf = self.render_to_pdf('admin/report_template.html', context)
        return pdf

    async def get(self, request, *args, **kwargs):
        period = int(request.GET.get('period', 7))

        period = max(1, min(period, 30))

        stats = await self.get_chat_stats()

        pdf_content = await sync_to_async(self.create_pdf)(stats)

        if pdf_content:
            response = HttpResponse(pdf_content, content_type='application/pdf')
            response[
                'Content-Disposition'] = f'attachment; filename="rostelecom_chat_report_{datetime.now().strftime("%d%m%Y")}.pdf"'
            return response
        else:
            return HttpResponse("Ошибка при создании PDF", status=500)

    @database_sync_to_async
    def get_chat_stats(self):
        total_chats = Chat.objects.count()
        active_chats = Chat.objects.filter(is_closed=False).count()
        closed_chats = Chat.objects.filter(is_closed=True).count()

        avg_operator_time = Message.objects.filter(
            role='assistant',
            response_time__isnull=False
        ).aggregate(avg_time=Avg('response_time'))['avg_time'] or 0

        avg_operator_response_time = f"{avg_operator_time:.3f}s"

        end_date = timezone.now().date()
        start_date = end_date - timedelta(days=6)

        daily_stats = []

        current_date = start_date
        while current_date <= end_date:
            day_start = timezone.make_aware(timezone.datetime.combine(current_date, timezone.datetime.min.time()))
            day_end = timezone.make_aware(timezone.datetime.combine(current_date, timezone.datetime.max.time()))

            new_chats = Chat.objects.filter(created_at__gte=day_start, created_at__lte=day_end).count()

            closed_chats_count = Chat.objects.filter(
                closed_at__gte=day_start,
                closed_at__lte=day_end
            ).count()

            daily_stats.append({
                'date': current_date.strftime('%d.%m'),
                'new_chats': new_chats,
                'closed_chats': closed_chats_count
            })

            current_date += timedelta(days=1)

        return {
            'total_chats': total_chats,
            'active_chats': active_chats,
            'closed_chats': closed_chats,
            'avg_response_time': avg_operator_response_time,
            'daily_stats': daily_stats
        }


class AdminStaffUserView(LoginRequiredMixin, UserPassesTestMixin, View):

    async def dispatch(self, request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            return redirect(settings.ADMIN_LOGIN_URL)

        has_permission = await sync_to_async(self.test_func)()
        if not has_permission:
            return HttpResponseForbidden("У вас нет прав для доступа к этой странице")

        handler = getattr(self, request.method.lower(), self.http_method_not_allowed)
        return await handler(request, *args, **kwargs)

    def test_func(self):
        return self.request.user.is_superuser

    async def get(self, request, user_id, *args, **kwargs):
        try:
            user = await database_sync_to_async(lambda: User.objects.filter(id=user_id).first())()

            if not user:
                return JsonResponse({'error': 'Пользователь не найден'}, status=404)

            is_operator = await database_sync_to_async(lambda: user.groups.filter(name='Operators').exists())()
            user_type = 'operator' if is_operator else 'admin' if user.is_superuser else 'unknown'

            user_data = {
                'id': user.id,
                'username': user.username,
                'active': user.is_active,
                'created_at': user.date_joined.strftime('%Y-%m-%d'),
                'user_type': user_type
            }

            return JsonResponse(user_data)

        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)

    async def put(self, request, user_id, *args, **kwargs):
        try:
            data = json.loads(request.body)

            user = await database_sync_to_async(lambda: User.objects.filter(id=user_id).first())()

            if not user:
                return JsonResponse({'success': False, 'error': 'Пользователь не найден'}, status=404)

            @database_sync_to_async
            def update_user():
                if 'username' in data and data['username'] != user.username:
                    if User.objects.filter(username=data['username']).exclude(id=user.id).exists():
                        raise ValueError(f"Пользователь с именем {data['username']} уже существует")
                    user.username = data['username']

                if 'active' in data:
                    user.is_active = data['active']

                if 'password' in data and data['password']:
                    user.set_password(data['password'])

                if 'user_type' in data:
                    operators_group = Group.objects.filter(name='Operators').first()

                    if operators_group:
                        user.groups.remove(operators_group)

                    user.is_superuser = False
                    user.is_staff = False

                    if data['user_type'] == 'operator':
                        if operators_group:
                            user.groups.add(operators_group)
                    elif data['user_type'] == 'admin':
                        user.is_superuser = True
                        user.is_staff = True

                user.save()

                is_operator = user.groups.filter(name='Operators').exists()
                user_type = 'operator' if is_operator else 'admin' if user.is_superuser else 'unknown'

                return {
                    'id': user.id,
                    'username': user.username,
                    'active': user.is_active,
                    'created_at': user.date_joined.strftime('%Y-%m-%d'),
                    'user_type': user_type
                }

            try:
                updated_user = await update_user()
                return JsonResponse({'success': True, 'user': updated_user})
            except ValueError as e:
                return JsonResponse({'success': False, 'error': str(e)}, status=400)

        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=500)

    async def delete(self, request, user_id, *args, **kwargs):
        try:
            user = await database_sync_to_async(lambda: User.objects.filter(id=user_id).first())()

            if not user:
                return JsonResponse({'success': False, 'error': 'Пользователь не найден'}, status=404)

            if user.id == request.user.id:
                return JsonResponse({
                    'success': False,
                    'error': 'Невозможно удалить текущего пользователя'
                }, status=400)

            await database_sync_to_async(user.delete)()

            return JsonResponse({'success': True, 'id': user_id})

        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=500)


class AdminStaffView(LoginRequiredMixin, UserPassesTestMixin, View):
    template_name = "admin/staff.html"
    login_url = settings.ADMIN_LOGIN_URL

    async def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(settings.ADMIN_LOGIN_URL)

        is_superuser = await sync_to_async(lambda: request.user.is_superuser)()
        if not is_superuser:
            return HttpResponseForbidden("У вас нет прав для доступа к этой странице")

        handler = getattr(self, request.method.lower(), self.http_method_not_allowed)
        return await handler(request, *args, **kwargs)

    def test_func(self):
        return self.request.user.is_superuser

    def handle_no_permission(self):
        return HttpResponseForbidden("У вас нет прав для доступа к этой странице")

    async def get(self, request, *args, **kwargs):
        return render(request, self.template_name)


class AdminStaffListView(LoginRequiredMixin, UserPassesTestMixin, View):

    async def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(settings.ADMIN_LOGIN_URL)

        is_superuser = await sync_to_async(lambda: request.user.is_superuser)()
        if not is_superuser:
            return HttpResponseForbidden("У вас нет прав для доступа к этой странице")

        handler = getattr(self, request.method.lower(), self.http_method_not_allowed)
        return await handler(request, *args, **kwargs)

    def test_func(self):
        return self.request.user.is_superuser

    async def get(self, request, *args, **kwargs):
        try:
            operators_group = await database_sync_to_async(lambda: Group.objects.filter(name='Operators').first())()

            operators_list = []
            if operators_group:
                operators_qs = await database_sync_to_async(
                    lambda: User.objects.filter(groups=operators_group, is_superuser=False))()
                operators = await database_sync_to_async(list)(operators_qs)

                for operator in operators:
                    operators_list.append({
                        'id': operator.id,
                        'username': operator.username,
                        'active': operator.is_active,
                        'created_at': operator.date_joined.strftime('%Y-%m-%d')
                    })

            admins_qs = await database_sync_to_async(lambda: User.objects.filter(is_superuser=True))()
            admins = await database_sync_to_async(list)(admins_qs)

            admins_list = []
            for admin in admins:
                admins_list.append({
                    'id': admin.id,
                    'username': admin.username,
                    'active': admin.is_active,
                    'created_at': admin.date_joined.strftime('%Y-%m-%d')
                })

            return JsonResponse({'operators': operators_list, 'admins': admins_list})

        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)

    async def post(self, request, *args, **kwargs):
        try:
            data = json.loads(request.body)
            user_type = data.get('user_type')
            username = data.get('username')
            password = data.get('password')

            if not user_type or not username or not password:
                return JsonResponse({
                    'success': False,
                    'error': 'Не указаны обязательные поля'
                }, status=400)

            user_exists = await database_sync_to_async(
                lambda: User.objects.filter(username=username).exists()
            )()

            if user_exists:
                return JsonResponse({
                    'success': False,
                    'error': f'Пользователь с именем {username} уже существует'
                }, status=400)

            @database_sync_to_async
            def create_user():
                user = User.objects.create_user(
                    username=username,
                    password=password
                )

                if user_type == 'operator':
                    group, _ = Group.objects.get_or_create(name='Operators')
                    user.groups.add(group)
                elif user_type == 'admin':
                    user.is_superuser = True
                    user.is_staff = True

                user.save()
                return user

            new_user = await create_user()

            return JsonResponse({
                'success': True,
                'user': {
                    'id': new_user.id,
                    'username': new_user.username,
                    'active': new_user.is_active,
                    'created_at': new_user.date_joined.strftime('%Y-%m-%d')
                }
            })

        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=500)


class AdminKnowledgeView(LoginRequiredMixin, UserPassesTestMixin, View):
    template_name = "admin/knowledge.html"
    login_url = settings.ADMIN_LOGIN_URL

    async def dispatch(self, request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            return redirect(self.get_login_url())

        has_permission = await sync_to_async(self.test_func)()
        if not has_permission:
            return self.handle_no_permission()

        handler = getattr(self, request.method.lower(), self.http_method_not_allowed)
        return await handler(request, *args, **kwargs)

    def test_func(self):
        return self.request.user.is_superuser

    def handle_no_permission(self):
        return HttpResponseForbidden("У вас нет прав для доступа к этой странице")

    async def get(self, request, *args, **kwargs):
        return render(request, self.template_name)


class AdminKnowledgeListView(LoginRequiredMixin, UserPassesTestMixin, View):

    async def dispatch(self, request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            return redirect(settings.ADMIN_LOGIN_URL)

        has_permission = await sync_to_async(self.test_func)()
        if not has_permission:
            return HttpResponseForbidden("У вас нет прав для доступа к этой странице")

        handler = getattr(self, request.method.lower(), self.http_method_not_allowed)
        return await handler(request, *args, **kwargs)

    def test_func(self):
        return self.request.user.is_superuser

    async def get(self, request, *args, **kwargs):
        page = int(request.GET.get('page', 1))
        per_page = 10

        start_idx = (page - 1) * per_page
        if page > 1:
            start_idx += 1

        points, _ = settings.QDRANT.scroll(
            collection_name=settings.COLLECTION,
            limit=per_page,
            offset=start_idx,
            with_payload=True,
        )

        current_page_items = [
            {
                'id': point.id,
                'question': point.payload.get('question', '').split(" / "),
                'answer': point.payload.get('answer', ''),
                'related_questions': point.payload.get('related_questions', [])
            }
            for point in points
        ]

        total_items = settings.QDRANT.count(settings.COLLECTION).count
        total_pages = (total_items + per_page - 1) // per_page

        return JsonResponse({
            'items': current_page_items,
            'pagination': {
                'current_page': page,
                'total_pages': total_pages,
                'total_items': total_items,
                'per_page': per_page
            }
        })

    async def post(self, request, *args, **kwargs):
        try:
            data = json.loads(request.body)
            question = data.get('question', [])
            answer = data.get('answer', '')
            related_questions = data.get('related_questions', [])

            if len(question) == 0 or len(answer) == 0 or len(related_questions) == 0:
                return JsonResponse({
                    'success': False,
                    'error': 'Не указаны обязательные поля'
                }, status=400)

            question = " / ".join(question)

            settings.QDRANT.upsert(
                collection_name=settings.COLLECTION,
                points=[
                    PointStruct(
                        id=settings.QDRANT.count(settings.COLLECTION).count + 1,
                        vector=await Assistant().get_embedding(question),
                        payload={
                            "question": question,
                            "answer": answer,
                            "related_questions": related_questions,
                        },
                    )
                ],
            )

            return JsonResponse({'success': True})

        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)


class AdminKnowledgeItemView(LoginRequiredMixin, UserPassesTestMixin, View):

    async def dispatch(self, request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            return redirect(settings.ADMIN_LOGIN_URL)

        has_permission = await sync_to_async(self.test_func)()
        if not has_permission:
            return HttpResponseForbidden("У вас нет прав для доступа к этой странице")

        handler = getattr(self, request.method.lower(), self.http_method_not_allowed)
        return await handler(request, *args, **kwargs)

    def test_func(self):
        return self.request.user.is_superuser

    async def get(self, request, knowledge_id, *args, **kwargs):
        try:
            response = settings.QDRANT.retrieve(
                collection_name=settings.COLLECTION,
                ids=[knowledge_id],
                with_payload=True
            )
            point = response[0]
            item = {
                'id': point.id,
                'question': point.payload.get('question', '').split(" / "),
                'answer': point.payload.get('answer', ''),
                'related_questions': point.payload.get('related_questions', [])
            }

            return JsonResponse(item)

        except Exception as e:
            return JsonResponse({'error': str(e)}, status=404)

    async def put(self, request, knowledge_id, *args, **kwargs):
        try:
            data = json.loads(request.body)
            question = data.get('question', '')
            answer = data.get('answer', '')
            related_questions = data.get('related_questions', [])

            if len(question) == 0 or len(answer) == 0 or len(related_questions) == 0:
                return JsonResponse({
                    'success': False,
                    'error': 'Не указаны обязательные поля'
                }, status=400)

            question = " / ".join(question)
            settings.QDRANT.upsert(
                collection_name=settings.COLLECTION,
                points=[
                    PointStruct(
                        id=knowledge_id,
                        vector=await Assistant().get_embedding(question),
                        payload={
                            "question": question,
                            "answer": answer,
                            "related_questions": related_questions,
                        },
                    )
                ],
            )

            return JsonResponse({'success': True})

        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)

    async def delete(self, request, knowledge_id, *args, **kwargs):
        try:
            settings.QDRANT.delete(
                collection_name=settings.COLLECTION,
                points_selector=PointIdsList(points=[knowledge_id]),
            )

            return JsonResponse({'success': True, 'id': knowledge_id})

        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)
