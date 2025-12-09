from django.urls import path

from .views import *

urlpatterns = [
    # Авторизация
    path('login/', ChoiceView.as_view(), name='login'),
    path('login/operator/', OperatorLoginView.as_view(), name='login_operator'),
    path('login/admin/', AdminLoginView.as_view(), name='login_admin'),
    path('logout/', CustomLogoutView.as_view(), name='logout'),

    # Чат поддержки
    path('', ChatView.as_view(), name='chat'),
    path('chat/<uuid:chat_id>/history/', ChatHistoryView.as_view(), name='chat_history'),
    path('chat/<uuid:chat_id>/suggestions/', SuggestedResponsesView.as_view(), name='suggested_responses'),
    path('chat/<uuid:chat_id>/close/', CloseChatView.as_view(), name='close_chat'),

    # Оператор
    path('operator/', OperatorView.as_view(), name='operator'),

    # Админ-панель
    path('admin/', AdminDashboardView.as_view(), name='admin_dashboard'),
    path('admin/staff/', AdminStaffView.as_view(), name='admin_staff'),
    path('admin/knowledge/', AdminKnowledgeView.as_view(), name='admin_knowledge'),

    # API-маршруты для админ-панели
    path('admin/generate-pdf/', AdminGeneratePDFView.as_view(), name='admin_generate_pdf'),
    path('admin/api/knowledge/', AdminKnowledgeListView.as_view(), name='admin_knowledge_list'),
    path('admin/api/knowledge/<int:knowledge_id>/', AdminKnowledgeItemView.as_view(), name='admin_knowledge_item'),
    path('admin/api/staff/', AdminStaffListView.as_view(), name='admin_staff_list'),
    path('admin/api/staff/<int:user_id>/', AdminStaffUserView.as_view(), name='admin_staff_user'),
    path('admin/api/stats/', AdminStatsAPIView.as_view(), name='admin_stats_api'),
]
