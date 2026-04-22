import os

from celery import Celery

from config.myloggerconfig import get_master_logger


logger = get_master_logger().getChild(__name__)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
logger.info('Initializing Celery app with DJANGO_SETTINGS_MODULE=%s', os.environ.get('DJANGO_SETTINGS_MODULE'))

app = Celery('config')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()
logger.info('Celery tasks autodiscovery completed')
