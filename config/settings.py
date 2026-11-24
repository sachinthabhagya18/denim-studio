import os
from pathlib import Path
from dotenv import dotenv_values

BASE_DIR = Path(__file__).resolve().parent.parent
LOCAL_ENV = dotenv_values(BASE_DIR / '.env')


def project_setting(name, default=''):
    return os.environ.get(name, LOCAL_ENV.get(name) or default)


SECRET_KEY = project_setting('SECRET_KEY', 'django-insecure-your-secret-key-here')

DEBUG = project_setting('DEBUG', 'True') == 'True'

ALLOWED_HOSTS = ['*']

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'corsheaders',
    'catalog_pipeline',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': os.environ.get('DB_ENGINE', 'django.db.backends.sqlite3'),
        'NAME': os.environ.get('DB_NAME', BASE_DIR / 'db.sqlite3'),
        'USER': os.environ.get('DB_USER', ''),
        'PASSWORD': os.environ.get('DB_PASSWORD', ''),
        'HOST': os.environ.get('DB_HOST', ''),
        'PORT': os.environ.get('DB_PORT', ''),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

CORS_ALLOWED_ORIGINS = [
    'http://localhost:3000',
    'http://localhost:8000',
    'http://127.0.0.1:8000',
]

REST_FRAMEWORK = {
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 100,
    'DEFAULT_FILTER_BACKENDS': ['rest_framework.filters.SearchFilter'],
}

OPENROUTER_API_KEY = project_setting('OPENROUTER_API_KEY')
OPENROUTER_BASE_URL = 'https://openrouter.ai/api/v1'
OPENROUTER_CHART_MODEL = project_setting('OPENROUTER_CHART_MODEL', 'openai/gpt-4o-mini')
OPENROUTER_PRODUCT_VISION_MODEL = project_setting('OPENROUTER_PRODUCT_VISION_MODEL', 'openai/gpt-4o-mini')
OPENROUTER_TEXT_MODEL = project_setting('OPENROUTER_TEXT_MODEL', 'openai/gpt-oss-20b')
OPENROUTER_IMAGE_MODEL = project_setting('OPENROUTER_IMAGE_MODEL', 'bytedance-seed/seedream-5-0-pro')
IMAGE_GENERATION_PROVIDER = project_setting('IMAGE_GENERATION_PROVIDER', 'openrouter').strip().lower()
GEMINI_API_KEY = project_setting('GEMINI_API_KEY').strip()
GEMINI_IMAGE_MODEL = project_setting('GEMINI_IMAGE_MODEL', 'gemini-nano-banana-2.1').strip()
MODEL_TRYON_PROVIDER = project_setting('MODEL_TRYON_PROVIDER', 'openrouter_masked')
FASHN_API_KEY = project_setting('FASHN_API_KEY')

WOOCOMMERCE_URL = project_setting('WOOCOMMERCE_URL')
WOOCOMMERCE_CONSUMER_KEY = project_setting('WOOCOMMERCE_CONSUMER_KEY')
WOOCOMMERCE_CONSUMER_SECRET = project_setting('WOOCOMMERCE_CONSUMER_SECRET')
WORDPRESS_MEDIA_USER = project_setting('WORDPRESS_MEDIA_USER')
WORDPRESS_MEDIA_APP_PASSWORD = project_setting('WORDPRESS_MEDIA_APP_PASSWORD')
PUBLIC_MEDIA_BASE_URL = project_setting('PUBLIC_MEDIA_BASE_URL')

SOCIAL_CURRENCY = project_setting('SOCIAL_CURRENCY', 'LKR')
SOCIAL_BRAND_NAME = project_setting('SOCIAL_BRAND_NAME')
META_GRAPH_API_VERSION = project_setting('META_GRAPH_API_VERSION')
META_APP_ID = project_setting('META_APP_ID')
META_APP_SECRET = project_setting('META_APP_SECRET')
FACEBOOK_PAGE_ID = project_setting('FACEBOOK_PAGE_ID')
FACEBOOK_PAGE_ACCESS_TOKEN = project_setting('FACEBOOK_PAGE_ACCESS_TOKEN')
INSTAGRAM_ACCOUNT_ID = project_setting('INSTAGRAM_ACCOUNT_ID')
INSTAGRAM_ACCESS_TOKEN = project_setting('INSTAGRAM_ACCESS_TOKEN')
TIKTOK_ACCESS_TOKEN = project_setting('TIKTOK_ACCESS_TOKEN').strip()
TIKTOK_MEDIA_BASE_URL = project_setting('TIKTOK_MEDIA_BASE_URL').strip()
TIKTOK_MEDIA_SOURCE = project_setting('TIKTOK_MEDIA_SOURCE', 'storage').strip()

LANGGRAPH_CHECKPOINTER = 'postgres'

