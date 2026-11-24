"""
URL configuration for catalog_pipeline app
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ProductJobViewSet, ModelProfileViewSet, SizeChartViewSet, dashboard, image_provider, social_connections, social_tiktok_account, store_connection

router = DefaultRouter()
router.register(r'model-profiles', ModelProfileViewSet, basename='model-profile')
router.register(r'jobs', ProductJobViewSet, basename='job')
router.register(r'size-charts', SizeChartViewSet, basename='size-chart')

urlpatterns = [
    path('api/store/connection/', store_connection, name='store-connection'),
    path('api/social/connections/', social_connections, name='social-connections'),
    path('api/social/tiktok/', social_tiktok_account, name='social-tiktok-account'),
    path('api/image-provider/', image_provider, name='image-provider'),
    path('api/', include(router.urls)),
    path('dashboard/', dashboard, name='dashboard'),
]
