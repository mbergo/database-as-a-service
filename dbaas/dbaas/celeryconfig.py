import os

REDIS_PORT = os.getenv('DBAAS_NOTIFICATION_BROKER_PORT', '6379')
BROKER_URL = os.getenv('DBAAS_NOTIFICATION_BROKER_URL', 'redis://localhost:%s/0' % REDIS_PORT)
CELERYD_TASK_TIME_LIMIT=10800
CELERY_TRACK_STARTED=True
CELERY_IGNORE_RESULT=False
CELERY_RESULT_BACKEND='djcelery.backends.cache:CacheBackend'
CELERYBEAT_MAX_LOOP_INTERVAL= 5
CELERY_TIMEZONE = os.getenv('DJANGO_TIME_ZONE', 'America/Sao_Paulo')
CELERYBEAT_SCHEDULER = 'djcelery.schedulers.DatabaseScheduler'
#CELERY_RESULT_BACKEND='djcelery.backends.database:DatabaseBackend'
#set this variable to True to run celery tasks synchronously
CELERY_ALWAYS_EAGER=False
CELERYD_LOG_COLOR=False
